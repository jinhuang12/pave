"""Decode .bin instruction streams via neuron-disasm CLI."""

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Operand:
    """A decoded instruction operand."""
    name: str
    mem_space: str  # "SB", "PSUM", "HBM"
    address: int = 0
    shape: List[int] = field(default_factory=list)  # [partitions, free_dim] or more dims
    dtype: str = ""
    stride: int = 1


@dataclass
class SemaphoreWait:
    """Semaphore wait condition on an instruction."""
    sem_id: int
    value: int


@dataclass
class SemaphoreSignal:
    """Semaphore signal (increment) on instruction completion."""
    sem_id: int
    on_event: str = "complete"  # "complete" or "start"


@dataclass
class DecodedInstruction:
    """A single decoded ISA instruction."""
    index: int
    opcode: str
    operands: List[Operand] = field(default_factory=list)
    sem_wait: Optional[SemaphoreWait] = None
    sem_signal: Optional[SemaphoreSignal] = None
    raw_text: str = ""
    # DMA trigger fields
    queue_name: str = ""
    block_id: int = -1
    # Column group for PE matmul/ldweights parallelism
    col_grp: str = ""
    # Additional fields from instruction text
    extra: Dict[str, Any] = field(default_factory=dict)


# Regex patterns for parsing neuron-disasm output
_SEM_WAIT_PATTERN = re.compile(r"\$S\[(\d+)\]>=(\d+)")
_SEM_SIGNAL_PATTERN = re.compile(r"\$S\[(\d+)\]\+\+@(\w+)")
# EVENT_SEMAPHORE_RANGE_CLEAR: clear sem IDs in a range to 0.
_SEM_RANGE_CLEAR_PATTERN = re.compile(
    r"first=(\d+)\s+last=(\d+)")
# Sem write/set: `$S[id]=value`
_SEM_SET_PATTERN = re.compile(r"\$S\[(\d+)\]=(\d+)")
# Sem add: `$S[id]+=value` (e.g. sem_increment in DMA descriptors)
_SEM_ADD_PATTERN = re.compile(r"\$S\[(\d+)\]\+=(\d+)")
# Operand pattern: name=dtype@addr[stride_bracket][size_bracket]
# neuron-disasm outputs dtype (fp32, bfloat16, fp8e4, etc.) before @, not mem_space.
# Brackets contain comma-separated values: [a,b,c][d,e,f]
_OPERAND_PATTERN = re.compile(
    r"(\w+)=(\w+)@(0x[0-9a-fA-F]+)\[([^\]]+)\]\[([^\]]+)\]"
)
# PE dst operand has no dtype prefix: dst=addr[stride][size]
_DST_NO_DTYPE_PATTERN = re.compile(
    r"dst=(0x[0-9a-fA-F]+)\[([^\]]+)\]\[([^\]]+)\]"
)
# MATMUL/LDWEIGHTS trailing M*N tile dimension
_MATMUL_DIM_PATTERN = re.compile(r"(\d+)\*(\d+)\s*;?\s*$")
# channels=N for DVE/Activation/Pool partition count
_CHANNELS_PATTERN = re.compile(r"channels=(\d+)")
_STORAGE_PATTERN = re.compile(
    r"storage=(\w+)@(0x[0-9a-fA-F]+)\[(\d+)\]\[(\d+)\]"
)
_DMA_TRIGGER_PATTERN = re.compile(r"(q\w+)\s+block_id=(\d+)")
# PSEUDO_DMA_DIRECT2D fields (src/dst patterns and elem sizes)
_DMA_SRC_ELEM_SIZE_PATTERN = re.compile(r"src_elem_size=(\d+)")
_DMA_DST_ELEM_SIZE_PATTERN = re.compile(r"dst_elem_size=(\d+)")
_DMA_SRC_PATTERN = re.compile(r"src_pattern=\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_DMA_DST_PATTERN = re.compile(r"dst_pattern=\[(\d+),(\d+)\]\[(\d+),(\d+)\]")
_DMA_SEM_INCREMENT_PATTERN = re.compile(r"sem_increment=(\d+)")
_DMA_DGE_OP_PATTERN = re.compile(r"dge_op=(\w+)")
_DMA_SEMAPHORE_PATTERN = re.compile(r"\bsemaphore=(\d+)")
_DMA_SRC_VAR_PATTERN = re.compile(r"src_var_id=(\d+)")
_DMA_DST_VAR_PATTERN = re.compile(r"dst_var_id=(\d+)")
_DMA_SRC_TABLE_PATTERN = re.compile(r"src_table_index=(\d+)")
_DMA_DST_TABLE_PATTERN = re.compile(r"dst_table_index=(\d+)")
# PSEUDO_EXTENSION mode: SRC_INDIRECTION / DST_INDIRECTION mark an indirect or
# gather DMA (HW DMA_INDIRECT / DMA_GATHER_TRANSPOSE); a plain DGE extension has
# no mode= field. Used to charge indirection descriptor-gen its real cost.
_EXT_MODE_PATTERN = re.compile(r"\bmode=(\w+)")
_CYCLE_PATTERN = re.compile(r"cycle=(\d+)")
_LOCATION_PATTERN = re.compile(r"location=(\w+)")
_COL_GRP_PATTERN = re.compile(r"col_grp=(\w+)")
# Collective-operation fields (PSEUDO_TRIGGER_COLLECTIVE_2)
_CC_TYPE_PATTERN = re.compile(r"ctype=(\w+)")
_CC_OP_PATTERN = re.compile(r"\bop=(\w+)")
_CC_DTYPE_PATTERN = re.compile(r"dtype=(\w+)")
_CC_DIM_PATTERN = re.compile(r"cc_dim=(\d+)")
_CC_SRC0_PATTERN = re.compile(r"src0_pattern=\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

# Engine name to standard mapping. Per the trn2 architecture:
#   - Tensor / PE: systolic matmul engine
#   - Vector / DVE: vector arithmetic engine
#   - Scalar / Activation: activation function + scalar arithmetic engine
#     (real `OpcodeSummary` reports these as "scalar")
#   - GpSimd / Pool: general-purpose SIMD (descriptor expansion, etc.)
#   - Sync / SP: scheduling/sync engine that triggers DMAs
ENGINE_NAME_MAP = {
    "Pool": "GpSimd",
    "Activation": "Scalar",
    "PE": "Tensor",
    "DVE": "Vector",
    "SP": "Sync",
}

# neuron-disasm arch flag mapping
TARGET_TO_ARCH = {
    "trn1": "sunda",
    "trn2": "sunda",
    "trn3": "sunda",  # TODO: verify if trn3 needs different arch flag
}


def find_neuron_disasm() -> str:
    """Find the neuron-disasm binary in the environment."""
    import shutil

    path = shutil.which("neuron-disasm")
    if path:
        return path

    candidates = [
        Path("/local/home/shchung/ws/nkilib-tmp/env/KaenaNeuronKernelLibrary-1.0/test-runtime/bin/neuron-disasm"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError(
        "neuron-disasm not found. Ensure test-runtime is in PATH or set NEURON_DISASM_PATH."
    )


def decode_binary(bin_data: bytes, engine_name: str, target: str = "trn2") -> List[DecodedInstruction]:
    """Decode a binary instruction stream using neuron-disasm."""
    disasm_path = find_neuron_disasm()
    arch = TARGET_TO_ARCH.get(target, "sunda")

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=True) as tmp:
        tmp.write(bin_data)
        tmp.flush()

        cmd = [disasm_path, "--arch", arch, "-l", tmp.name]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            raise RuntimeError(
                f"neuron-disasm failed (rc={result.returncode}): {result.stderr[:500]}"
            )

    return parse_disassembly(result.stdout, engine_name)


def parse_disassembly(text: str, engine_name: str) -> List[DecodedInstruction]:
    """Parse neuron-disasm text output into structured instructions.

    Args:
        text: Raw text output from neuron-disasm
        engine_name: Engine name for context

    Returns:
        List of DecodedInstruction in program order
    """
    instructions = []
    index = 0

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue

        inst = _parse_instruction_line(line, index, engine_name)
        if inst is not None:
            instructions.append(inst)
            index += 1

    return instructions


def _parse_instruction_line(line: str, index: int, engine_name: str) -> Optional[DecodedInstruction]:
    """Parse a single disassembly line into a DecodedInstruction."""
    line = line.rstrip(" ;").strip()
    if not line:
        return None

    tokens = line.split()
    if not tokens:
        return None

    opcode = tokens[0]

    inst = DecodedInstruction(
        index=index,
        opcode=opcode,
        raw_text=line,
    )

    # Parse semaphore wait
    sem_wait_match = _SEM_WAIT_PATTERN.search(line)
    if sem_wait_match:
        inst.sem_wait = SemaphoreWait(
            sem_id=int(sem_wait_match.group(1)),
            value=int(sem_wait_match.group(2)),
        )

    # Parse semaphore signal
    sem_signal_match = _SEM_SIGNAL_PATTERN.search(line)
    if sem_signal_match:
        inst.sem_signal = SemaphoreSignal(
            sem_id=int(sem_signal_match.group(1)),
            on_event=sem_signal_match.group(2),
        )

    # Parse operands: name=dtype@addr[stride_bracket][size_bracket]
    # Hoist channels= match — same value per line for all operands.
    _channels_match = _CHANNELS_PATTERN.search(line)
    _line_partitions = int(_channels_match.group(1)) if _channels_match else 128
    for op_match in _OPERAND_PATTERN.finditer(line):
        name = op_match.group(1)
        dtype_str = op_match.group(2)
        address = int(op_match.group(3), 16)
        stride_bracket = op_match.group(4)
        size_bracket = op_match.group(5)
        # Compute total elements as product of size bracket values
        size_vals = [int(x) for x in size_bracket.split(",")]
        stride_vals = [int(x) for x in stride_bracket.split(",")]
        total_elements = 1
        for v in size_vals:
            total_elements *= v
        # Detect strided pattern: not all strides are sequential.
        is_strided = False
        if len(stride_vals) >= 2 and len(size_vals) >= 2:
            expected_step = size_vals[0]
            if stride_vals[1] != expected_step:
                is_strided = True
        # Infer mem_space from address (PSUM range starts at 0x2000000)
        mem_space = "PSUM" if address >= 0x2000000 else "SB"
        num_partitions = _line_partitions
        operand = Operand(
            name=name,
            mem_space=mem_space,
            address=address,
            shape=[num_partitions, total_elements],
            dtype=dtype_str,
            stride=max(stride_vals[1:]) if len(stride_vals) > 1 else 1,
        )
        # Stash strided flag in instruction extra
        if is_strided:
            inst.extra[f"{name}_strided"] = 1
        inst.operands.append(operand)

    # Parse PE dst operand without dtype prefix: dst=addr[stride][size]
    # Quick check: only matches lines containing 'dst=' AND no
    # already-parsed 'dst' operand.
    if "dst=" in line and not any(op.name == "dst" for op in inst.operands):
        dst_match = _DST_NO_DTYPE_PATTERN.search(line)
        if dst_match:
            address = int(dst_match.group(1), 16)
            size_bracket = dst_match.group(3)
            size_vals = [int(x) for x in size_bracket.split(",")]
            total_elements = 1
            for v in size_vals:
                total_elements *= v
            mem_space = "PSUM" if address >= 0x2000000 else "SB"
            operand = Operand(
                name="dst",
                mem_space=mem_space,
                address=address,
                shape=[128, total_elements],
            )
            inst.operands.append(operand)

    # Parse MATMUL/LDWEIGHTS trailing M*N tile dimension
    if opcode in ("MATMUL", "MATMUL_MX", "LDWEIGHTS"):
        dim_match = _MATMUL_DIM_PATTERN.search(line)
        if dim_match:
            inst.extra["matmul_m"] = int(dim_match.group(1))
            inst.extra["matmul_n"] = int(dim_match.group(2))

    # Parse storage= style operands (from CUSTOM_OP_PAYLOAD etc.)
    # Quick check: skip regex if "storage=" not in line.
    for stor_match in (_STORAGE_PATTERN.finditer(line) if "storage=" in line else ()):
        dtype_str = stor_match.group(1)
        loc_match = _LOCATION_PATTERN.search(line)
        mem_space = loc_match.group(1).upper() if loc_match else "SB"
        if mem_space == "SBUF":
            mem_space = "SB"
        operand = Operand(
            name="storage",
            mem_space=mem_space,
            address=int(stor_match.group(2), 16),
            shape=[int(stor_match.group(3)), int(stor_match.group(4))],
            dtype=dtype_str,
        )
        inst.operands.append(operand)

    # Parse DMA trigger / DIRECT2D — only check if opcode includes "DMA"
    has_dma = "DMA" in opcode
    if has_dma and ("PSEUDO_DMA_TRIGGER" in opcode or "DMA_TRIGGER" in opcode):
        trigger_match = _DMA_TRIGGER_PATTERN.search(line)
        if trigger_match:
            inst.queue_name = trigger_match.group(1)
            inst.block_id = int(trigger_match.group(2))

    # Parse PSEUDO_DMA_DIRECT2D — these are runtime-emitted dynamic DMAs
    # that the compiler embeds directly in the SP/Pool/Sync engine
    # streams, not as static descriptor blocks. The src_pattern and
    # src_elem_size encode the access pattern; the runtime expands
    # this into N hardware descriptors at trigger time.
    if has_dma and "DMA_DIRECT2D" in opcode:
        src_elem = _DMA_SRC_ELEM_SIZE_PATTERN.search(line)
        if src_elem:
            inst.extra["src_elem_size"] = int(src_elem.group(1))
        dst_elem = _DMA_DST_ELEM_SIZE_PATTERN.search(line)
        if dst_elem:
            inst.extra["dst_elem_size"] = int(dst_elem.group(1))
        src_pat = _DMA_SRC_PATTERN.search(line)
        if src_pat:
            # src_pattern=[a,b][c,d] → outer dims (a,c), strides (b,d)
            inst.extra["src_pattern_dims"] = [int(src_pat.group(1)), int(src_pat.group(3))]
            inst.extra["src_pattern_strides"] = [int(src_pat.group(2)), int(src_pat.group(4))]
        dst_pat = _DMA_DST_PATTERN.search(line)
        if dst_pat:
            inst.extra["dst_pattern_dims"] = [int(dst_pat.group(1)), int(dst_pat.group(3))]
            inst.extra["dst_pattern_strides"] = [int(dst_pat.group(2)), int(dst_pat.group(4))]
        sem_inc = _DMA_SEM_INCREMENT_PATTERN.search(line)
        if sem_inc:
            inst.extra["sem_increment"] = int(sem_inc.group(1))
        dge_op = _DMA_DGE_OP_PATTERN.search(line)
        if dge_op:
            inst.extra["dge_op"] = dge_op.group(1)
        sem_match = _DMA_SEMAPHORE_PATTERN.search(line)
        if sem_match:
            inst.extra["dma_semaphore"] = int(sem_match.group(1))
        sv = _DMA_SRC_VAR_PATTERN.search(line)
        if sv:
            inst.extra["src_var_id"] = int(sv.group(1))
        dv = _DMA_DST_VAR_PATTERN.search(line)
        if dv:
            inst.extra["dst_var_id"] = int(dv.group(1))
        st = _DMA_SRC_TABLE_PATTERN.search(line)
        if st:
            inst.extra["src_table_index"] = int(st.group(1))
        dt = _DMA_DST_TABLE_PATTERN.search(line)
        if dt:
            inst.extra["dst_table_index"] = int(dt.group(1))

    # Parse PSEUDO_EXTENSION mode (SW-DGE descriptor generation). An indirection
    # mode (SRC_INDIRECTION / DST_INDIRECTION) is a gather/indirect DMA that HW
    # reports as DMA_INDIRECT / DMA_GATHER_TRANSPOSE with substantial active
    # time; a plain DGE extension (no mode=) is near-free.
    if opcode == "PSEUDO_EXTENSION":
        mode_match = _EXT_MODE_PATTERN.search(line)
        if mode_match:
            inst.extra["dge_mode"] = mode_match.group(1)

    # Parse column group for PE matmul/ldweights — only present in
    # lines containing "col_grp=".
    if "col_grp=" in line:
        col_grp_match = _COL_GRP_PATTERN.search(line)
        if col_grp_match:
            inst.col_grp = col_grp_match.group(1)

    # Parse cycle count for NOP
    if opcode == "NOP":
        cycle_match = _CYCLE_PATTERN.search(line)
        if cycle_match:
            inst.extra["cycle"] = int(cycle_match.group(1))

    # Parse collective-operation fields
    if "PSEUDO_TRIGGER_COLLECTIVE" in opcode:
        m = _CC_TYPE_PATTERN.search(line)
        if m:
            inst.extra["cc_type"] = m.group(1)  # ALL_REDUCE, ALL_GATHER, etc.
        m = _CC_OP_PATTERN.search(line)
        if m:
            inst.extra["cc_op"] = m.group(1)
        m = _CC_DTYPE_PATTERN.search(line)
        if m:
            inst.extra["cc_dtype"] = m.group(1)
        m = _CC_DIM_PATTERN.search(line)
        if m:
            inst.extra["cc_dim"] = int(m.group(1))
        m = _CC_SRC0_PATTERN.search(line)
        if m:
            # src0_pattern=[outer,?][free_dim,stride] — total elems = outer * free_dim
            outer = int(m.group(1))
            free_dim = int(m.group(3))
            inst.extra["cc_n_elems"] = outer * free_dim

    return inst


def get_engine_type(engine_name: str) -> str:
    """Map engine file name to standard engine type.

    Handles numeric suffixes: 'Pool0' -> 'GpSimd', 'DVE0' -> 'Vector', etc.
    """
    if engine_name in ENGINE_NAME_MAP:
        return ENGINE_NAME_MAP[engine_name]
    # Strip trailing digits and retry
    base = engine_name.rstrip("0123456789")
    return ENGINE_NAME_MAP.get(base, engine_name)
