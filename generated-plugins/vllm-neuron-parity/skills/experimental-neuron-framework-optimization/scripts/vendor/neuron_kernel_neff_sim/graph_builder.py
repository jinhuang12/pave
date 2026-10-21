"""Build dependency DAG from decoded instructions using semaphore analysis."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .isa_decoder import DecodedInstruction, get_engine_type
from .neff_parser import NEFFContents


class SimInstruction:
    """An instruction node in the simulation graph.

    Plain class with __slots__ — much faster construction and attribute
    access than a dataclass for the 200k+ instruction case.
    """
    __slots__ = (
        "index", "engine_index", "opcode", "engine_name", "engine_type",
        "elements_per_partition", "num_partitions", "dtype", "dst_dtype",
        "src_mem_space", "dst_mem_space", "src_stride",
        "transfer_bytes", "dma_queue", "dma_block_id",
        "intra_engine_dep", "cross_engine_deps",
        "sem_wait_id", "sem_wait_value", "sem_signal_id",
        "raw", "extra",
    )

    def __init__(self, *, index: int, engine_index: int, opcode: str,
                 engine_name: str, engine_type: str,
                 elements_per_partition: int = 0, num_partitions: int = 128,
                 dtype: str = "", dst_dtype: str = "",
                 src_mem_space: str = "", dst_mem_space: str = "",
                 src_stride: int = 1, transfer_bytes: int = 0,
                 dma_queue: str = "", dma_block_id: int = -1,
                 intra_engine_dep: Optional[int] = None,
                 cross_engine_deps: Optional[List[int]] = None,
                 sem_wait_id: int = -1, sem_wait_value: int = 0,
                 sem_signal_id: int = -1,
                 raw: Optional[DecodedInstruction] = None,
                 extra: Optional[Dict[str, Any]] = None):
        self.index = index
        self.engine_index = engine_index
        self.opcode = opcode
        self.engine_name = engine_name
        self.engine_type = engine_type
        self.elements_per_partition = elements_per_partition
        self.num_partitions = num_partitions
        self.dtype = dtype
        self.dst_dtype = dst_dtype
        self.src_mem_space = src_mem_space
        self.dst_mem_space = dst_mem_space
        self.src_stride = src_stride
        self.transfer_bytes = transfer_bytes
        self.dma_queue = dma_queue
        self.dma_block_id = dma_block_id
        self.intra_engine_dep = intra_engine_dep
        self.cross_engine_deps = cross_engine_deps if cross_engine_deps is not None else []
        self.sem_wait_id = sem_wait_id
        self.sem_wait_value = sem_wait_value
        self.sem_signal_id = sem_signal_id
        self.raw = raw
        self.extra = extra if extra is not None else {}


@dataclass
class InstructionGraph:
    """Complete instruction dependency graph for simulation."""
    instructions: List[SimInstruction]
    engines: Dict[str, List[int]]  # engine_name -> list of global instruction indices
    total_instructions: int = 0

    def get_engine_instructions(self, engine_name: str) -> List[SimInstruction]:
        """Get all instructions for a given engine in program order."""
        indices = self.engines.get(engine_name, [])
        return [self.instructions[i] for i in indices]

    def get_instruction(self, global_index: int) -> SimInstruction:
        return self.instructions[global_index]


_VNC_BYTE_DIVISOR = 1  # Set by build_graph based on VNC count


class DynamicLoopUnsupportedError(RuntimeError):
    """Raised when a NEFF contains runtime-conditional control flow
    (e.g., loops bounded by ``max_context_len`` or other runtime values).

    The simulator walks every instruction emitted by the compiler. Real
    hardware skips conditional blocks at runtime based on input values,
    which means the static instruction count over-estimates wall time
    by an unbounded factor. Determining the runtime trip count would
    require semantically simulating the program (or integrating a tool
    like birsim that performs that analysis and reports actual loop
    counts so we can replace induction variables).

    Until that integration exists, refuse to simulate these kernels
    rather than emit silently-wrong numbers.
    """


def _detect_dynamic_loops(decoded_engines: Dict[str, List[DecodedInstruction]]) -> Optional[str]:
    """Return a non-empty diagnostic string if the NEFF contains runtime-
    conditional control flow that the simulator can't model. Returns None
    if the NEFF is safe to simulate.

    Detection signal: presence of any ``COMPARE_BRANCH`` opcode. These are
    the conditional jumps the compiler emits to gate dynamic-loop bodies
    (e.g. ``mcl``-bounded attention sub-blocks). Static control flow
    (loop unrolling, fixed-trip loops) compiles to plain
    ``PSEUDO_BRANCH_LABEL`` markers without compares.
    """
    for engine_name, decoded_list in decoded_engines.items():
        for d in decoded_list:
            if d.opcode == "COMPARE_BRANCH":
                return (
                    f"NEFF contains COMPARE_BRANCH on engine '{engine_name}': "
                    "this is a runtime-conditional branch (e.g. a max_context_len "
                    "or other dynamic-loop bound). The simulator cannot determine "
                    "the runtime trip count without semantically executing the "
                    "program. Refusing to simulate to avoid emitting silently-wrong "
                    "numbers. See DynamicLoopUnsupportedError for context."
                )
    return None


def build_graph(
    neff: NEFFContents,
    decoded_engines: Dict[str, List[DecodedInstruction]],
) -> InstructionGraph:
    """Build the instruction dependency graph from decoded NEFF.

    Args:
        neff: Parsed NEFF contents (for DMA descriptor info)
        decoded_engines: Dict mapping engine_name -> decoded instruction list

    Returns:
        InstructionGraph with all dependencies resolved

    Raises:
        DynamicLoopUnsupportedError: if the NEFF contains runtime-conditional
            control flow (any COMPARE_BRANCH opcode). These kernels need a
            program-execution-aware tool (e.g. birsim) to determine real
            loop trip counts before this simulator can produce accurate
            numbers.
    """
    global _VNC_BYTE_DIVISOR
    _VNC_BYTE_DIVISOR = max(1, neff.vnc_count)

    diag = _detect_dynamic_loops(decoded_engines)
    if diag:
        raise DynamicLoopUnsupportedError(diag)

    instructions: List[SimInstruction] = []
    engines: Dict[str, List[int]] = {}
    global_index = 0

    # Precompute DGE-table maps and var_id lookups (used by every
    # DIRECT2D instruction). Hoisting saves O(N_DMA × N_vars) work.
    _var_by_id_cache: Dict[int, str] = {}
    _dge_table_cache: List[int] = []
    _output_name_cache: str = ""
    _output_size_cache: int = 0
    _vnc_count_cached = max(1, neff.vnc_count)  # property; cache once
    for vn, v in neff.def_json.get("var", {}).items():
        if not isinstance(v, dict):
            continue
        if "var_id" in v:
            _var_by_id_cache[v["var_id"]] = vn
        if v.get("type") == "dge-table" and not _dge_table_cache:
            _dge_table_cache = v.get("list", [])
        if v.get("type") == "output" and not _output_name_cache:
            _output_name_cache = vn
            _output_size_cache = v.get("size", 0) or 0

    # Hoisted _resolve closure (was defined per DIRECT2D inst).
    _dge_table_len = len(_dge_table_cache)

    def _resolve(vid, tbl_idx):
        if vid is not None and vid > 0:
            return _var_by_id_cache.get(vid, f"var{vid}")
        if tbl_idx is not None and tbl_idx < _dge_table_len:
            tvar = _dge_table_cache[tbl_idx]
            return _var_by_id_cache.get(tvar, f"var{tvar}")
        return ""

    # Pre-count Pool DIRECT2D instructions to distribute output bytes.
    _pool_direct2d_count = 0
    for ename, dlist in decoded_engines.items():
        if ename.startswith("Pool"):
            for d in dlist:
                if d.opcode in ("PSEUDO_DMA_DIRECT2D", "DMA_DIRECT2D"):
                    _pool_direct2d_count += 1

    # Phase 1: Create SimInstruction nodes for all engines
    for engine_name, decoded_list in decoded_engines.items():
        engine_type = get_engine_type(engine_name)

        if engine_type == "Tensor":
            # Split PE instructions by column group for parallel execution
            global_index = _build_tensor_engine_graph(
                decoded_list, engine_name, engine_type, neff,
                instructions, engines, global_index,
            )
        else:
            engine_indices: List[int] = []
            prev_index: Optional[int] = None

            for decoded in decoded_list:
                is_dma_trigger = decoded.opcode in ("PSEUDO_DMA_TRIGGER", "DMA_TRIGGER")
                is_dma_direct2d = decoded.opcode in (
                    "PSEUDO_DMA_DIRECT2D", "DMA_DIRECT2D"
                )
                is_runtime_dma = is_dma_trigger or is_dma_direct2d

                # For DMA triggers: the completion signal comes from either:
                # 1. The trigger instruction itself (decoded.sem_signal), or
                # 2. The DMA descriptor's semaphore field in the engine JSON
                trigger_sem_signal_id = -1
                if is_dma_trigger:
                    if decoded.sem_signal:
                        trigger_sem_signal_id = decoded.sem_signal.sem_id
                    else:
                        trigger_sem_signal_id = _get_descriptor_signal(
                            neff, engine_name, decoded.queue_name, decoded.block_id
                        )
                elif is_dma_direct2d:
                    # DIRECT2D embeds the semaphore directly in the
                    # instruction text (parsed into extra["dma_semaphore"]).
                    trigger_sem_signal_id = decoded.extra.get("dma_semaphore", -1)

                sim_inst = _create_sim_instruction(
                    decoded, global_index, engine_name, engine_type, neff, prev_index
                )

                if is_runtime_dma:
                    # Clear semaphore signal from trigger — DMA transfer will signal
                    sim_inst.sem_signal_id = -1

                # For DIRECT2D, populate hw_desc_bytes from src_pattern.
                if is_dma_direct2d:
                    elem_size = decoded.extra.get("src_elem_size", 0)
                    pat_dims = decoded.extra.get("src_pattern_dims", [])
                    sem_inc = decoded.extra.get("sem_increment", 0)
                    if elem_size > 0 and pat_dims:
                        # src_pattern=[d0, s0][d1, s1] — the inner-dim
                        # pat_dims[1] is the descriptor count per event,
                        # observed across all DIRECT2D variants in real
                        # DmaPacket profiles. pat_dims[0] is the outer
                        # iteration space and does NOT multiply descriptor
                        # count.
                        if len(pat_dims) >= 2:
                            n_hw_desc = max(1, pat_dims[1])
                        else:
                            n_hw_desc = max(1, pat_dims[0])
                        sim_inst.transfer_bytes = elem_size * n_hw_desc
                        sim_inst.extra["hw_desc_bytes"] = (
                            [elem_size] * n_hw_desc
                        )
                        # Cache total bytes (avoids O(n) sum() in perfetto).
                        sim_inst.extra["_hw_desc_total_bytes"] = (
                            elem_size * n_hw_desc
                        )
                        sim_inst.extra["sem_increment"] = sem_inc
                    # Identify queue from engine.
                    # SP DIRECT2D → qSPDynamicHW (real "qSyncDynamicHW")
                    # Pool DIRECT2D → qGpSimdDynamic (matches real)
                    if engine_name.startswith("SP"):
                        sim_inst.dma_queue = "qSPDynamicHW"
                    elif engine_name.startswith("Pool"):
                        sim_inst.dma_queue = "qGpSimdDynamic"
                    sim_inst.dma_block_id = decoded.index
                    # Use hoisted _resolve closure (defined once outside loop).
                    src_vid = decoded.extra.get("src_var_id")
                    dst_vid = decoded.extra.get("dst_var_id")
                    src_tbl = decoded.extra.get("src_table_index")
                    dst_tbl = decoded.extra.get("dst_table_index")
                    sim_inst.extra["from_var"] = _resolve(src_vid, src_tbl)
                    sim_inst.extra["to_var"] = _resolve(dst_vid, dst_tbl)
                    if engine_name.startswith("Pool"):
                        out_name = _output_name_cache
                        if out_name:
                            sim_inst.extra["to_var"] = out_name

                instructions.append(sim_inst)
                engine_indices.append(global_index)
                trigger_index = global_index
                prev_index = global_index
                global_index += 1

                # Create a synthetic DMA transfer instruction.
                # Each transfer dispatches across all 16 DMA engines in
                # parallel, sharing aggregate HBM bandwidth equally with
                # other concurrent transfers. Each transfer gets its own
                # unique engine track; bandwidth contention is modeled by
                # the scheduler's DMA bandwidth pool.
                if is_runtime_dma:
                    queue = sim_inst.dma_queue or "default"
                    dma_engine_name = f"DMA:{queue}:{sim_inst.dma_block_id}"

                    dma_inst = SimInstruction(
                        index=global_index,
                        engine_index=-1,
                        opcode="DMA_TRANSFER",
                        engine_name=dma_engine_name,
                        engine_type="DMA",
                        intra_engine_dep=None,
                        transfer_bytes=sim_inst.transfer_bytes,
                        src_mem_space=sim_inst.src_mem_space,
                        dst_mem_space=sim_inst.dst_mem_space,
                        dma_queue=sim_inst.dma_queue,
                        dma_block_id=sim_inst.dma_block_id,
                        sem_signal_id=trigger_sem_signal_id,
                        # Carry hw_desc_bytes from the trigger so the
                        # perfetto packetizer can replicate runtime
                        # descriptor unrolling. Share dict by reference
                        # — readers don't mutate it.
                        extra=sim_inst.extra,
                    )
                    dma_inst.cross_engine_deps = [trigger_index]
                    instructions.append(dma_inst)
                    if dma_engine_name not in engines:
                        engines[dma_engine_name] = []
                    engines[dma_engine_name].append(global_index)
                    global_index += 1

            engines[engine_name] = engine_indices

    # Phase 2: Resolve cross-engine dependencies via semaphores
    _resolve_semaphore_dependencies(instructions)

    # Phase 3: Merge Activation*N* into Scalar*N* (same physical Scalar engine
    # on Trainium2). The compiler emits them as separate .bin sections but
    # they share one execution unit; running them on parallel timelines
    # under-estimates kernel duration on Scalar-bound kernels (e.g. rmsnorm).
    _merge_activation_into_scalar(instructions, engines)

    return InstructionGraph(
        instructions=instructions,
        engines=engines,
        total_instructions=len(instructions),
    )


def _merge_activation_into_scalar(
    instructions: List["SimInstruction"],
    engines: Dict[str, List[int]],
) -> None:
    """Fold ``Activation<N>`` indices into ``SP<N>`` (Scalar engine).

    Both bin sections execute on the same physical engine. Index lists are
    merged in ascending global-index order, which preserves the compiler's
    program order and keeps all existing semaphore-based deps intact.
    """
    activation_engines = [n for n in list(engines) if n.startswith("Activation")]
    for act_name in activation_engines:
        # Activation0 -> SP0, Activation1 -> SP1, ...
        suffix = act_name[len("Activation"):]
        sp_name = f"SP{suffix}"
        act_idx = engines.pop(act_name)
        sp_idx = engines.get(sp_name, [])
        # Merge preserving ascending global-index order. The scheduler
        # enforces "one instruction at a time per engine" off the per-engine
        # index list, so simply concatenating + sorting yields a single
        # serialized timeline. We do NOT rewrite each instruction's
        # ``intra_engine_dep`` — that field expresses the original program-
        # order dependency *within its source bin*; rewriting it to a strict
        # global-index chain would force ordering between independent
        # SP-side and Activation-side streams that have no real data
        # dependency, over-serializing the kernel.
        merged = sorted(sp_idx + act_idx)
        engines[sp_name] = merged
        # Just rename the engine on each Activation instruction so its
        # cost-model lookups and timeline accounting target ``SP<N>``.
        for gidx in act_idx:
            instructions[gidx].engine_name = sp_name


def _build_tensor_engine_graph(
    decoded_list: List[DecodedInstruction],
    engine_name: str,
    engine_type: str,
    neff: NEFFContents,
    instructions: List["SimInstruction"],
    engines: Dict[str, List[int]],
    global_index: int,
) -> int:
    """Build graph for Tensor Engine with column group parallelism.

    Instructions with col_grp are assigned to per-column-group sub-engines
    (e.g., 'PE0:q0', 'PE0:q32'). Instructions without col_grp act as
    barriers that synchronize all column groups.

    LDWEIGHTS runs on a separate weight-load pipeline (e.g., 'PE0:wt')
    that overlaps with the MATMUL compute pipeline. Each MATMUL has a
    cross-engine dependency on the preceding LDWEIGHTS.
    """
    # Track last instruction index per column group sub-engine
    col_grp_prev: Dict[str, int] = {}  # col_grp -> last global_index
    # Track last LDWEIGHTS index per compute engine (for weight pipeline)
    wt_prev: Dict[str, int] = {}  # compute_engine -> last ldweights global_index
    # Track the shared (non-col_grp) prev index
    shared_prev: Optional[int] = None
    # All col groups seen
    all_col_grps: set = set()
    # Cache max(col_grp_prev.values()) — updated incrementally as new
    # col_grp_prev entries appear (always the most recent global_index).
    _max_col_grp_val: Optional[int] = None

    for decoded in decoded_list:
        col_grp = decoded.col_grp
        is_ldweights = decoded.opcode == "LDWEIGHTS"

        if col_grp:
            prev_index = col_grp_prev.get(col_grp, shared_prev)
            compute_engine = f"{engine_name}:{col_grp}"
            if col_grp not in all_col_grps:
                all_col_grps.add(col_grp)
        else:
            # Non-col_grp instructions sync all col groups: prev = max
            # of (shared_prev, max-over-col_grp_prev). Cached to avoid
            # building a list every iteration (O(N²) on 165k inst).
            if _max_col_grp_val is None:
                prev_index = shared_prev
            elif shared_prev is None:
                prev_index = _max_col_grp_val
            else:
                prev_index = _max_col_grp_val if _max_col_grp_val > shared_prev else shared_prev
            compute_engine = engine_name

        if is_ldweights:
            # LDWEIGHTS goes on weight-load sub-engine, overlapping with compute
            wt_engine = f"{compute_engine}:wt"
            wt_prev_index = wt_prev.get(compute_engine, shared_prev)
            sim_inst = _create_sim_instruction(
                decoded, global_index, wt_engine, engine_type, neff, wt_prev_index
            )
            instructions.append(sim_inst)

            if wt_engine not in engines:
                engines[wt_engine] = []
            engines[wt_engine].append(global_index)

            wt_prev[compute_engine] = global_index
        else:
            # Compute instructions: depend on previous compute AND the
            # last LDWEIGHTS on this track (data ready dependency)
            sim_inst = _create_sim_instruction(
                decoded, global_index, compute_engine, engine_type, neff, prev_index
            )
            # Add cross-engine dep on preceding LDWEIGHTS
            last_wt = wt_prev.get(compute_engine)
            if last_wt is not None:
                sim_inst.cross_engine_deps.append(last_wt)
                # Propagate the LDWEIGHTS dtype to the MATMUL so the cost
                # model can detect FP8 perf-mode (DoubleRow). MATMUL src
                # operand carries the activation dtype (often bf16) but
                # the FP8 perf mode is gated on the WEIGHT dtype.
                wt_inst = instructions[last_wt]
                if wt_inst.dtype:
                    sim_inst.extra["weight_dtype"] = wt_inst.dtype
                # Propagate the LDWEIGHTS dtype to the MATMUL so the cost
                # model can detect FP8 perf-mode (DoubleRow). MATMUL src
                # operand carries the activation dtype (often bf16) but
                # the FP8 perf mode is gated on the WEIGHT dtype.
                wt_inst = instructions[last_wt]
                if wt_inst.dtype:
                    sim_inst.extra["weight_dtype"] = wt_inst.dtype

            instructions.append(sim_inst)

            if compute_engine not in engines:
                engines[compute_engine] = []
            engines[compute_engine].append(global_index)

            # Update tracking
            if col_grp:
                col_grp_prev[col_grp] = global_index
                _max_col_grp_val = global_index  # always the latest
            else:
                shared_prev = global_index
                # Sync all col groups to the latest barrier.
                for cg in col_grp_prev:
                    col_grp_prev[cg] = global_index
                _max_col_grp_val = global_index if col_grp_prev else None

        global_index += 1

    return global_index


def _create_sim_instruction(
    decoded: DecodedInstruction,
    global_index: int,
    engine_name: str,
    engine_type: str,
    neff: NEFFContents,
    prev_index: Optional[int],
) -> SimInstruction:
    """Create a SimInstruction from a decoded instruction."""
    sim = SimInstruction(
        index=global_index,
        engine_index=decoded.index,
        opcode=decoded.opcode,
        engine_name=engine_name,
        engine_type=engine_type,
        intra_engine_dep=prev_index,
        raw=decoded,
    )

    # Extract semaphore info
    if decoded.sem_wait:
        sim.sem_wait_id = decoded.sem_wait.sem_id
        sim.sem_wait_value = decoded.sem_wait.value
    if decoded.sem_signal:
        sim.sem_signal_id = decoded.sem_signal.sem_id

    # Extract operand info for cost model
    _extract_operand_info(sim, decoded, engine_name, neff)

    return sim


def _extract_operand_info(
    sim: SimInstruction,
    decoded: DecodedInstruction,
    engine_name: str,
    neff: NEFFContents,
) -> None:
    """Extract shape/dtype/memory info from operands for cost modeling."""
    if decoded.opcode in ("PSEUDO_DMA_TRIGGER", "DMA_TRIGGER"):
        sim.dma_queue = decoded.queue_name
        sim.dma_block_id = decoded.block_id
        _extract_dma_transfer_info(sim, engine_name, neff)
        return

    # For PSEUDO_TRIGGER_COLLECTIVE_2: propagate cc_type, cc_dtype, cc_n_elems
    # so the cost model can look up alpha/beta from the calibrated table.
    if decoded.opcode.startswith("PSEUDO_TRIGGER_COLLECTIVE"):
        for k in ("cc_type", "cc_op", "cc_dtype", "cc_dim", "cc_n_elems"):
            if k in decoded.extra:
                sim.extra[k] = decoded.extra[k]
        return

    # For PSEUDO_EXTENSION: propagate dge_mode so the cost model can charge
    # indirection (gather/indirect DMA descriptor-gen) its real active time
    # while leaving plain DGE extensions near-free.
    if decoded.opcode == "PSEUDO_EXTENSION":
        if "dge_mode" in decoded.extra:
            sim.extra["dge_mode"] = decoded.extra["dge_mode"]
        return

    # For MATMUL/MATMUL_MX/LDWEIGHTS: reference operand is dst per
    # nisa-performance-model.md.
    if decoded.opcode in ("MATMUL", "MATMUL_MX", "LDWEIGHTS"):
        # K-dim from the src operand's free dimension (shape[1]).
        for op in decoded.operands:
            if op.name == "src" and op.shape:
                sim.num_partitions = op.shape[0] if len(op.shape) > 0 else 128
                sim.dtype = op.dtype
                sim.src_mem_space = op.mem_space
                # Store K dimension in extra for cost model.
                if len(op.shape) > 1:
                    sim.extra["matmul_k"] = op.shape[1]
                break
        matmul_n = decoded.extra.get("matmul_n", 0)
        sim.elements_per_partition = max(matmul_n, 1)
        for op in decoded.operands:
            if op.name == "dst":
                sim.dst_mem_space = op.mem_space
                break
        return

    # For compute instructions, extract from operands
    if not decoded.operands:
        return

    # Use first source operand for elements_per_partition
    for op in decoded.operands:
        if op.name in ("src", "src0", "storage") and op.shape:
            sim.num_partitions = op.shape[0] if len(op.shape) > 0 else 128
            sim.elements_per_partition = op.shape[1] if len(op.shape) > 1 else 0
            sim.src_mem_space = op.mem_space
            sim.dtype = op.dtype
            sim.src_stride = op.stride
            break

    # Capture src1 (second TENSOR_TENSOR input) mem-space and dtype so the
    # perf-mode speedup gate can require ALL operands SBUF + same 16-bit dtype.
    for op in decoded.operands:
        if op.name == "src1":
            sim.extra["src1_mem_space"] = op.mem_space
            if op.dtype:
                sim.extra["src1_dtype"] = op.dtype
            break

    # Propagate strided flag from any operand with non-contiguous access.
    if decoded.extra:
        for k in decoded.extra:
            if k.endswith("_strided"):
                sim.extra["any_strided"] = 1
                break

    # Use dst operand for dst_mem_space, dst_dtype, and fallback elements.
    for op in decoded.operands:
        if op.name in ("dst",) and op.shape:
            sim.dst_mem_space = op.mem_space
            if op.dtype:
                sim.dst_dtype = op.dtype
            # If no src operand was found, use dst for elements_per_partition
            if sim.elements_per_partition == 0:
                sim.elements_per_partition = op.shape[1] if len(op.shape) > 1 else 0
                sim.num_partitions = op.shape[0] if len(op.shape) > 0 else 128
                if op.dtype:
                    sim.dtype = op.dtype
            break


def _extract_dma_transfer_info(
    sim: SimInstruction,
    engine_name: str,
    neff: NEFFContents,
) -> None:
    """Extract DMA transfer size from NEFF DMA descriptors."""
    engine_data = neff.engines.get(engine_name)
    if engine_data is None or not engine_data.dma_descriptors:
        return

    for dma_desc in engine_data.dma_descriptors:
        if dma_desc.block_id == sim.dma_block_id and dma_desc.queue == sim.dma_queue:
            total_bytes = 0
            hw_desc_bytes: List[int] = []
            for desc in dma_desc.descriptors:
                total_bytes += _compute_descriptor_bytes(desc)
                hw_desc_bytes.extend(_unroll_to_hw_descriptors(desc))
            # Trainium 2 chips have 2 physical NeuronCores. The compiled
            # NEFF DMA descriptors describe the whole-chip transfer;
            # per-core (per pcore_idx) bytes = total / 2 for HBM-side
            # queues regardless of LNC mode:
            #   VNC=1 (LNC2): two physical cores cooperate as one logical
            #     core; each loads half the bytes.
            #   VNC=2 (LNC1, virtualized into 2 logical): each logical
            #     core runs on half the physical core's bandwidth and
            #     also sees half the bytes (since each kernel only
            #     processes its half of the work).
            # SpillReload is intra-SBUF, additionally subject to a
            # ping-pong split on VNC=2.
            CORES_PER_CHIP = 2
            vnc = max(1, neff.vnc_count)
            if dma_desc.queue == "qSPIO0":
                total_bytes //= CORES_PER_CHIP
                hw_desc_bytes = hw_desc_bytes[: len(hw_desc_bytes) // CORES_PER_CHIP]
            elif dma_desc.queue == "qSPSpillReload0":
                divisor = CORES_PER_CHIP * vnc  # extra ping-pong on VNC=2
                total_bytes //= divisor
                hw_desc_bytes = hw_desc_bytes[: max(1, len(hw_desc_bytes) // divisor)]
            sim.transfer_bytes = total_bytes
            sim.extra["hw_desc_bytes"] = hw_desc_bytes

            # Capture (partition, free_bytes_per_partition) of the first
            # descriptor so the scheduler can pick the right HBM2SB BW
            # from the silicon ubench curve. NEFF descriptors use
            # to_sizes=[free_elements, partitions] with last dim being
            # partition count.
            if dma_desc.descriptors:
                first_desc = dma_desc.descriptors[0]
                ts = first_desc.get("to_sizes") or first_desc.get("from_sizes") or []
                dt = first_desc.get("from_dtype") or first_desc.get("to_dtype") or ""
                dsize = {"bfloat16": 2, "float8_e4m3": 1, "float32": 4,
                         "uint8": 1, "int8": 1, "int32": 4, "uint16": 2,
                         "uint32": 4, "fp32": 4, "fp16": 2}.get(dt, 1)
                if len(ts) >= 2:
                    free_elem, part = int(ts[0]), int(ts[1])
                    sim.extra["dma_partition"] = part
                    sim.extra["dma_free_bytes"] = free_elem * dsize

            # Infer memory spaces from descriptor
            if dma_desc.descriptors:
                first_desc = dma_desc.descriptors[0]
                sim.src_mem_space = _infer_mem_space(first_desc.get("from", ""))
                sim.dst_mem_space = _infer_mem_space(first_desc.get("to", ""))
                # Variable names for visualization
                sim.extra["from_var"] = first_desc.get("from", "")
                sim.extra["to_var"] = first_desc.get("to", "")
            return


def _unroll_to_hw_descriptors(desc: Dict[str, Any]) -> List[int]:
    """Per KaenaRuntime tdrv/dma_ring_setup.c::drs_expand_data_desc_model:
    a stride-non-unit DMA descriptor is unrolled into multiple hardware
    descriptors. Each HW descriptor moves a contiguous run.

    For from_sizes=[s0, s1, s2, ...] with from_steps=[1, t1, t2, ...]:
      * Innermost dim (s0) is contiguous (step=1) — one HW descriptor
        carries s0 * elem_bytes contiguous bytes (clamped to MAX_DMA_DESC_SIZE).
      * Outer dims contribute prod(s1, s2, ...) HW descriptors.

    If the innermost step is not 1, we degenerate to one HW descriptor
    per element pair; this is a degenerate case rarely seen.
    """
    MAX_DMA_DESC_SIZE = 1024 * 1024 - 1  # ~1MB per UDMA hw limit
    from_sizes = desc.get("from_sizes", [])
    from_steps = desc.get("from_steps", [])
    elem_bytes = _dtype_bytes(desc.get("from_dtype", "bf16"))
    if not from_sizes:
        return []

    inner_size = from_sizes[0]
    inner_step = from_steps[0] if from_steps else 1
    if inner_step != 1:
        # Innermost stride non-unit: each element is its own descriptor.
        n_total = 1
        for s in from_sizes:
            n_total *= s
        return [elem_bytes] * n_total

    # Number of HW descriptors = prod of outer dims
    n_hw = 1
    for s in from_sizes[1:]:
        n_hw *= s
    bytes_per_desc = inner_size * elem_bytes
    # Clamp each descriptor to MAX_DMA_DESC_SIZE; a single inner run
    # exceeding this gets split across multiple HW descriptors.
    if bytes_per_desc <= MAX_DMA_DESC_SIZE:
        return [bytes_per_desc] * n_hw
    # Split each contiguous run into chunks of MAX_DMA_DESC_SIZE
    chunks_per_run = (bytes_per_desc + MAX_DMA_DESC_SIZE - 1) // MAX_DMA_DESC_SIZE
    last_chunk = bytes_per_desc - (chunks_per_run - 1) * MAX_DMA_DESC_SIZE
    out = []
    for _ in range(n_hw):
        for _ in range(chunks_per_run - 1):
            out.append(MAX_DMA_DESC_SIZE)
        out.append(last_chunk)
    return out


def _compute_descriptor_bytes(desc: Dict[str, Any]) -> int:
    """Compute total transfer bytes from a DMA descriptor."""
    from_sizes = desc.get("from_sizes", [])
    dtype = desc.get("from_dtype", "bf16")
    elem_bytes = _dtype_bytes(dtype)

    if not from_sizes:
        return 0

    total_elements = 1
    for s in from_sizes:
        total_elements *= s

    return total_elements * elem_bytes


def _dtype_bytes(dtype: str) -> int:
    """Get byte size of a data type."""
    dtype_map = {
        "fp32": 4, "float32": 4, "f32": 4,
        "bf16": 2, "bfloat16": 2, "fp16": 2, "float16": 2, "f16": 2,
        "fp8": 1, "float8": 1, "fp8_e4m3": 1, "fp8_e5m2": 1,
        "int8": 1, "uint8": 1,
        "int16": 2, "uint16": 2,
        "int32": 4, "uint32": 4,
    }
    return dtype_map.get(dtype.lower(), 2)  # default bf16


def _infer_mem_space(tensor_ref: str) -> str:
    """Infer memory space from a tensor reference string."""
    ref_lower = tensor_ref.lower()
    if "sb" in ref_lower or "sbuf" in ref_lower:
        return "SB"
    elif "psum" in ref_lower:
        return "PSUM"
    else:
        return "HBM"


def _get_descriptor_signal(
    neff: "NEFFContents", engine_name: str, queue_name: str, block_id: int
) -> int:
    """Get the semaphore signal ID from a DMA descriptor in the engine JSON."""
    engine_data = neff.engines.get(engine_name)
    if engine_data is None or not engine_data.dma_descriptors:
        return -1
    for desc in engine_data.dma_descriptors:
        if desc.block_id == block_id and desc.queue == queue_name:
            sem = desc.semaphore
            if isinstance(sem, int):
                return sem
            if isinstance(sem, dict):
                return sem.get("id", sem.get("sem_id", -1))
            return -1
    return -1


def _resolve_semaphore_dependencies(instructions: List[SimInstruction]) -> None:
    """Resolve cross-engine dependencies by matching semaphore wait/signal pairs.

    For each semaphore S, we track a list of instructions that signal S (in order).
    When instruction B waits on $S[x]>=N, it depends on the Nth signaler of semaphore x.
    """
    # Build signaler lists: sem_id -> list of global_index entries.
    # DMA transfers signal once per DMA engine (16 engines), so each
    # DMA_TRANSFER contributes 16 signals to its semaphore.
    sem_signalers: Dict[int, List[int]] = {}

    for inst in instructions:
        if inst.sem_signal_id >= 0:
            if inst.sem_signal_id not in sem_signalers:
                sem_signalers[inst.sem_signal_id] = []
            signal_count = 16 if inst.opcode == "DMA_TRANSFER" else 1
            for _ in range(signal_count):
                sem_signalers[inst.sem_signal_id].append(inst.index)

    # Resolve waits
    for inst in instructions:
        if inst.sem_wait_id < 0:
            continue

        signalers = sem_signalers.get(inst.sem_wait_id, [])
        if not signalers:
            continue

        wait_value = inst.sem_wait_value
        # The instruction waits for sem >= wait_value, meaning it needs
        # at least wait_value signals to have completed.
        # The wait_value-th signaler (0-indexed: wait_value - 1) is the dependency.
        if wait_value > 0 and wait_value <= len(signalers):
            dep_index = signalers[wait_value - 1]
            # Only add cross-engine deps (same-engine is already intra-engine sequential)
            dep_inst = instructions[dep_index]
            if dep_inst.engine_name != inst.engine_name:
                inst.cross_engine_deps.append(dep_index)
