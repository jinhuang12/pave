"""
DMA-Compute Ordering Verification Tool

Analyzes a post-scheduler BIR JSON to detect whether DMA loads complete
before their dependent compute instructions are ready to execute.

Algorithm:
  For each compute instruction C:
    C.dma_ready_time  = max(scheduled_end of all DMA Flow-dependencies of C)
    C.non_dma_ready   = max(scheduled_end of all non-DMA dependencies of C)
    C.start_ts        = scheduled_start of C

    if dma_ready_time > non_dma_ready:
        DMA was the bottleneck — compute stalled waiting for data
        stall = dma_ready_time - non_dma_ready

    if start_ts > dma_ready_time and start_ts > non_dma_ready:
        Both ready, but instruction started late — scheduling overhead
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class InstructionInfo:
    name: str
    engine: str
    opcode: str
    scheduled_start: int
    scheduled_end: int
    dependencies: list  # list of (name, dep_type)
    debug: dict = field(default_factory=dict)
    output_memrefs: list = field(default_factory=list)
    input_memrefs: list = field(default_factory=list)


@dataclass
class StallReport:
    instruction_name: str
    engine: str
    opcode: str
    scheduled_start: int
    dma_ready_time: int
    non_dma_ready_time: int
    stall: int  # dma_ready_time - non_dma_ready_time (>0 means DMA-blocked)
    scheduling_overhead: int  # start - max(dma_ready, non_dma_ready) (>0 means dispatch delay)
    blocking_dma_instructions: list  # names of DMA instructions that determined dma_ready_time
    debug: dict = field(default_factory=dict)


def _dma_has_no_dependencies(dma_names: list[str], instr_map: dict[str, InstructionInfo]) -> bool:
    """Check if ALL blocking DMAs have no dependencies (issuable at kernel start)."""
    for name in dma_names:
        dma = instr_map.get(name)
        if dma is None:
            continue
        if dma.dependencies:
            return False
    return True


def parse_bir_json(json_path: str) -> dict:
    with open(json_path) as f:
        return json.load(f)


def extract_instructions(bir_data: dict) -> list[InstructionInfo]:
    instructions = []
    for func in bir_data.get("functions", []):
        for block in func.get("blocks", []):
            for instr in block.get("instructions", []):
                instructions.append(InstructionInfo(
                    name=instr["name"],
                    engine=instr["engine"],
                    opcode=instr.get("opcode", ""),
                    scheduled_start=instr["scheduled_start"],
                    scheduled_end=instr["scheduled_end"],
                    dependencies=instr.get("dependencies", []),
                    debug=instr.get("debug", {}),
                    output_memrefs=[o.get("memref", "") for o in instr.get("outs", [])],
                    input_memrefs=[i.get("memref", "") for i in instr.get("ins", [])],
                ))
    return instructions


def analyze_dma_compute_ordering(
    instructions: list[InstructionInfo],
    dep_types_for_dma: Optional[set[str]] = None,
) -> list[StallReport]:
    if dep_types_for_dma is None:
        dep_types_for_dma = {"Flow"}

    instr_map = {instr.name: instr for instr in instructions}

    reports = []
    for instr in instructions:
        if instr.engine == "DMA":
            continue

        dma_ready_time = 0
        non_dma_ready_time = 0
        blocking_dma_names = []

        for dep_name, dep_type in instr.dependencies:
            dep_instr = instr_map.get(dep_name)
            if dep_instr is None:
                continue

            if dep_instr.engine == "DMA" and dep_type in dep_types_for_dma:
                if dep_instr.scheduled_end > dma_ready_time:
                    dma_ready_time = dep_instr.scheduled_end
                    blocking_dma_names = [dep_name]
                elif dep_instr.scheduled_end == dma_ready_time:
                    blocking_dma_names.append(dep_name)
            else:
                non_dma_ready_time = max(non_dma_ready_time, dep_instr.scheduled_end)

        if dma_ready_time == 0:
            continue

        stall = max(0, dma_ready_time - non_dma_ready_time)
        all_ready = max(dma_ready_time, non_dma_ready_time)
        overhead = max(0, instr.scheduled_start - all_ready)

        reports.append(StallReport(
            instruction_name=instr.name,
            engine=instr.engine,
            opcode=instr.opcode,
            scheduled_start=instr.scheduled_start,
            dma_ready_time=dma_ready_time,
            non_dma_ready_time=non_dma_ready_time,
            stall=stall,
            scheduling_overhead=overhead,
            blocking_dma_instructions=blocking_dma_names,
            debug=instr.debug,
        ))

    return reports


@dataclass
class DMAOrderingEntry:
    dma_name: str
    opcode: str
    scheduled_start: int
    scheduled_end: int
    output_memrefs: list  # what data this DMA produces (SBUF dst)
    input_memrefs: list   # what data this DMA loads from (HBM src)
    first_consumer_name: str  # earliest compute instruction that needs this DMA (by scheduled_start)
    first_consumer_engine: str
    first_consumer_start: int
    completion_rank: int = 0  # 1-based rank in DMA completion order
    consumption_rank: int = 0  # 1-based rank in first-consumer order
    debug: dict = field(default_factory=dict)


def analyze_dma_ordering(
    instructions: list[InstructionInfo],
    dep_types_for_dma: Optional[set[str]] = None,
) -> list[DMAOrderingEntry]:
    if dep_types_for_dma is None:
        dep_types_for_dma = {"Flow"}

    instr_map = {instr.name: instr for instr in instructions}
    dma_instrs = [i for i in instructions if i.engine == "DMA"]

    dma_to_consumers: dict[str, list[InstructionInfo]] = {d.name: [] for d in dma_instrs}
    for instr in instructions:
        if instr.engine == "DMA":
            continue
        for dep_name, dep_type in instr.dependencies:
            if dep_type in dep_types_for_dma and dep_name in dma_to_consumers:
                dma_to_consumers[dep_name].append(instr)

    entries = []
    for dma in dma_instrs:
        consumers = dma_to_consumers[dma.name]
        if not consumers:
            continue
        first = min(consumers, key=lambda c: c.scheduled_start)
        entries.append(DMAOrderingEntry(
            dma_name=dma.name,
            opcode=dma.opcode,
            scheduled_start=dma.scheduled_start,
            scheduled_end=dma.scheduled_end,
            output_memrefs=dma.output_memrefs,
            input_memrefs=dma.input_memrefs,
            first_consumer_name=first.name,
            first_consumer_engine=first.engine,
            first_consumer_start=first.scheduled_start,
            debug=dma.debug,
        ))

    by_completion = sorted(entries, key=lambda e: e.scheduled_end)
    for rank, e in enumerate(by_completion, 1):
        e.completion_rank = rank

    by_consumption = sorted(entries, key=lambda e: e.first_consumer_start)
    for rank, e in enumerate(by_consumption, 1):
        e.consumption_rank = rank

    return entries


@dataclass
class ProfiledInstruction:
    bir_instruction_name: str
    engine: str
    start_ts: int
    end_ts: int
    trigger_ts: int = 0        # DMA only: when the trigger was issued
    transfer_bytes: int = 0    # DMA only: total bytes transferred
    queue_idx: int = -1        # DMA only: which DMA queue
    queue_name: str = ""       # DMA only: queue name


def load_profiled_instructions(
    profiler_db_dir: str,
    bir_instructions: list[InstructionInfo],
    pcore_idx: int = 0,
) -> dict[str, ProfiledInstruction]:
    """Load profiled instruction timing from a local profiler_db/ parquet directory.

    Reads Instruction.parquet for compute instructions and DmaPacketAggregated.parquet
    for DMA instructions, mapping them back to BIR instruction names.
    """
    import pandas as pd

    profiler_db = profiler_db_dir

    # --- Compute instructions from Instruction.parquet ---
    inst_df = pd.read_parquet(os.path.join(profiler_db, "Instruction.parquet"))
    inst_df = inst_df[inst_df["pcore_idx"] == pcore_idx]
    bir_df = inst_df[inst_df["raw_bir_id"] != 0].copy()

    # Normalize names: strip suffixes like -trig, -PWP, -wait-0, -register-move
    bir_df["base_name"] = bir_df["bir_instruction_name"].str.replace(
        r"-(trig|PWP|wait-\d+|register-move)$", "", regex=True
    )
    bir_df["is_main"] = bir_df["bir_instruction_name"] == bir_df["base_name"]

    # For start_ts: use only main sub-instructions (exclude -PWP, -wait, -trig, etc.)
    # These setup instructions run before the data arrives and don't represent actual consumption.
    # For end_ts: include everything to capture the full span.
    main_df = bir_df[bir_df["is_main"]]
    main_grouped = main_df.groupby("base_name").agg(
        start_ts=("start_ts", "min"),
        eng=("engine", "first"),
    ).reset_index()
    all_grouped = bir_df.groupby("base_name").agg(
        end_ts=("end_ts", "max"),
    ).reset_index()
    compute_grouped = main_grouped.merge(all_grouped, on="base_name")

    result = {}
    for row in compute_grouped.itertuples():
        result[row.base_name] = ProfiledInstruction(
            bir_instruction_name=row.base_name,
            engine=row.eng,
            start_ts=int(row.start_ts),
            end_ts=int(row.end_ts),
        )

    # --- DMA instructions from DmaPacketAggregated.parquet ---
    agg_path = os.path.join(profiler_db, "DmaPacketAggregated.parquet")
    if not os.path.exists(agg_path):
        return result

    agg_df = pd.read_parquet(agg_path)
    agg_df = agg_df[agg_df["pcore_idx"] == pcore_idx]

    # Method 1: Match via -trig instruction timestamps → DmaPacketAggregated.trigger
    trig_df = bir_df[bir_df["bir_instruction_name"].str.endswith("-trig")].copy()
    matched_dma_names = set()
    if not trig_df.empty:
        trig_df["dma_base"] = trig_df["bir_instruction_name"].str.replace("-trig$", "", regex=True)
        for dma_name in trig_df["dma_base"].unique():
            trigger_tss = trig_df[trig_df["dma_base"] == dma_name]["start_ts"].tolist()
            matches = agg_df[agg_df["trigger"].isin(trigger_tss)]
            if not matches.empty:
                result[dma_name] = ProfiledInstruction(
                    bir_instruction_name=dma_name,
                    engine="DMA",
                    start_ts=int(matches["start_ts"].min()),
                    end_ts=int(matches["end_ts"].max()),
                    trigger_ts=int(min(trigger_tss)),
                    transfer_bytes=int(matches["transfer_bytes"].sum()),
                    queue_idx=int(matches.iloc[0]["queue_idx"]),
                    queue_name=str(matches.iloc[0]["queue_name"]),
                )
                matched_dma_names.add(dma_name)

    # Method 2: For DMAs without -trig entries (software_dynamic / indirect DMAs),
    # match by BIR input memref name → DmaPacketAggregated.variable
    bir_dma_instrs = [i for i in bir_instructions if i.engine == "DMA" and i.name not in matched_dma_names]
    used_agg_ids = set()
    for dma_instr in bir_dma_instrs:
        for memref in dma_instr.input_memrefs:
            # Match: BIR input memref may contain the variable name or be exactly it
            candidates = agg_df[
                (agg_df["variable"] == memref) &
                (~agg_df["id"].isin(used_agg_ids))
            ]
            if not candidates.empty:
                used_agg_ids.update(candidates["id"].tolist())
                trig_val = candidates["trigger"].dropna()
                trigger_ts = int(trig_val.min()) if not trig_val.empty else int(candidates["start_ts"].min())
                result[dma_instr.name] = ProfiledInstruction(
                    bir_instruction_name=dma_instr.name,
                    engine="DMA",
                    start_ts=int(candidates["start_ts"].min()),
                    end_ts=int(candidates["end_ts"].max()),
                    trigger_ts=trigger_ts,
                    transfer_bytes=int(candidates["transfer_bytes"].sum()),
                    queue_idx=int(candidates.iloc[0]["queue_idx"]),
                    queue_name=str(candidates.iloc[0]["queue_name"]),
                )
                break

    return result


def _trace_to_dma(
    start_name: str,
    instr_map: dict[str, InstructionInfo],
    profiled: dict[str, ProfiledInstruction],
    max_depth: int = 50,
) -> list[tuple[str, str, str, int, int]]:
    """Trace the critical path backward from an instruction until reaching a DMA.

    At each step, follows the dependency with the latest profiled end_ts.
    Stops at a DMA instruction or an instruction with no deps.

    Returns a list of (instr_name, engine, dep_type, profiled_start, profiled_end) tuples.
    """
    chain = []
    current = start_name

    for _ in range(max_depth):
        instr = instr_map.get(current)
        if instr is None or not instr.dependencies:
            break

        latest_dep = None
        latest_end = -1
        latest_type = ""
        for dep_name, dep_type in instr.dependencies:
            p_dep = profiled.get(dep_name)
            if p_dep and p_dep.end_ts > latest_end:
                latest_end = p_dep.end_ts
                latest_dep = dep_name
                latest_type = dep_type

        if latest_dep is None:
            break

        dep_instr = instr_map.get(latest_dep)
        if dep_instr is None:
            break

        p_dep = profiled.get(latest_dep)
        dep_start = p_dep.start_ts if p_dep else 0
        dep_end = p_dep.end_ts if p_dep else 0
        chain.append((latest_dep, dep_instr.engine, latest_type, dep_start, dep_end))

        if dep_instr.engine == "DMA":
            break

        current = latest_dep

    return chain


@dataclass
class DepPath:
    dep_type: str  # Flow, Anti, Output
    first_hop_name: str  # direct dep of the DMA
    first_hop_end: int  # profiled end_ts of the direct dep
    terminus_name: str  # DMA or root instruction at end of chain
    terminus_engine: str
    terminus_end: int
    chain: list  # full chain of (name, engine, dep_type, start, end)


def _trace_all_dep_paths(
    dma_name: str,
    instr_map: dict[str, InstructionInfo],
    profiled: dict[str, ProfiledInstruction],
) -> list[DepPath]:
    """Trace all direct dependencies of a DMA, following each to its DMA terminus.

    For each direct dep of the DMA, traces backward (following latest end_ts)
    until reaching another DMA or kernel start. Returns one DepPath per direct dep,
    sorted by first_hop_end descending (latest = most blocking first).
    """
    dma_instr = instr_map.get(dma_name)
    if dma_instr is None:
        return []

    paths = []
    for dep_name, dep_type in dma_instr.dependencies:
        dep_instr = instr_map.get(dep_name)
        if dep_instr is None:
            continue
        p_dep = profiled.get(dep_name)
        if p_dep is None:
            continue

        first_hop_end = p_dep.end_ts

        if dep_instr.engine == "DMA":
            paths.append(DepPath(
                dep_type=dep_type,
                first_hop_name=dep_name,
                first_hop_end=first_hop_end,
                terminus_name=dep_name,
                terminus_engine="DMA",
                terminus_end=first_hop_end,
                chain=[(dep_name, "DMA", dep_type, p_dep.start_ts, p_dep.end_ts)],
            ))
        else:
            chain = _trace_to_dma(dep_name, instr_map, profiled)
            full_chain = [(dep_name, dep_instr.engine, dep_type, p_dep.start_ts, p_dep.end_ts)] + chain
            if chain:
                t_name, t_engine, _, _, t_end = chain[-1]
            else:
                t_name, t_engine, t_end = dep_name, dep_instr.engine, first_hop_end
            paths.append(DepPath(
                dep_type=dep_type,
                first_hop_name=dep_name,
                first_hop_end=first_hop_end,
                terminus_name=t_name,
                terminus_engine=t_engine,
                terminus_end=t_end,
                chain=full_chain,
            ))

    paths.sort(key=lambda p: -p.first_hop_end)
    return paths


def _print_dependency_chain(
    dma_name: str,
    instr_map: dict[str, InstructionInfo],
    profiled: dict[str, ProfiledInstruction],
    indent: str = "    ",
):
    """Print all dependency paths from a DMA instruction, grouped by type.

    For each direct dep of the DMA, traces backward to a DMA terminus or kernel start.
    Shows which paths are fundamental (Flow-gated) vs actionable (Anti/Output-gated).
    """
    dma_instr = instr_map.get(dma_name)
    if dma_instr is None:
        return

    dma_p = profiled.get(dma_name)
    dma_start = dma_p.start_ts if dma_p else 0
    dma_end = dma_p.end_ts if dma_p else 0

    def _source_loc(instr):
        d = instr.debug if instr else {}
        if not d:
            return ""
        fn = d.get("filename", "")
        if fn:
            fn = fn.rsplit("/", 1)[-1]
        ln = d.get("lineno", "")
        op = d.get("op_name", "")
        parts = [f for f in [f"{fn}:{ln}" if fn else "", op] if f]
        return f"  [{', '.join(parts)}]" if parts else ""

    paths = _trace_all_dep_paths(dma_name, instr_map, profiled)
    if not paths:
        return

    dma_loc = _source_loc(dma_instr)
    print(f"{indent}Dependencies of {dma_name}{dma_loc}:")
    print(f"{indent}  start={dma_start}  end={dma_end}")

    # Group paths by dep type
    flow_paths = [p for p in paths if p.dep_type == "Flow"]
    anti_paths = [p for p in paths if p.dep_type == "Anti"]
    output_paths = [p for p in paths if p.dep_type == "Output"]

    latest_flow = max((p.first_hop_end for p in flow_paths), default=0)
    latest_anti = max((p.first_hop_end for p in anti_paths), default=0)
    latest_output = max((p.first_hop_end for p in output_paths), default=0)

    # Show the single most-blocking path (overall latest dep)
    most_blocking = paths[0]  # already sorted by first_hop_end desc
    terminus_label = (f"upstream DMA: {most_blocking.terminus_name}"
                      if most_blocking.terminus_engine == "DMA"
                      else "kernel start")
    print(f"{indent}  Most blocking path [{most_blocking.dep_type}] "
          f"(first_hop_end={most_blocking.first_hop_end}, -> {terminus_label}):")
    for i, (dep_name, dep_engine, dep_type, dep_start, dep_end) in enumerate(most_blocking.chain):
        arrow = "  └─" if i == len(most_blocking.chain) - 1 else "  ├─"
        ts_str = f"start={dep_start}  end={dep_end}" if dep_start else ""
        dep_i = instr_map.get(dep_name)
        loc = _source_loc(dep_i)
        print(f"{indent}  {arrow} [{dep_type}] {dep_name} ({dep_engine})  {ts_str}{loc}")

    # If Anti/Output is the most blocking, also show the longest Flow path for comparison
    if most_blocking.dep_type != "Flow" and flow_paths:
        longest_flow = flow_paths[0]
        f_terminus = (f"upstream DMA: {longest_flow.terminus_name}"
                      if longest_flow.terminus_engine == "DMA"
                      else "kernel start")
        print(f"{indent}  Longest Flow path (first_hop_end={longest_flow.first_hop_end}, -> {f_terminus}):")
        for i, (dep_name, dep_engine, dep_type, dep_start, dep_end) in enumerate(longest_flow.chain):
            arrow = "  └─" if i == len(longest_flow.chain) - 1 else "  ├─"
            ts_str = f"start={dep_start}  end={dep_end}" if dep_start else ""
            dep_i = instr_map.get(dep_name)
            loc = _source_loc(dep_i)
            print(f"{indent}  {arrow} [{dep_type}] {dep_name} ({dep_engine})  {ts_str}{loc}")

    # Summary of all paths
    print(f"{indent}  All deps: Flow({len(flow_paths)}, latest={latest_flow}) "
          f"Anti({len(anti_paths)}, latest={latest_anti}) "
          f"Output({len(output_paths)}, latest={latest_output})")


def print_profile_comparison(
    dma_ordering: list[DMAOrderingEntry],
    profiled: dict[str, ProfiledInstruction],
    instructions: list[InstructionInfo],
    profiler_db_dir: Optional[str] = None,
):
    def _memref_short(memrefs):
        if not memrefs:
            return ""
        short = memrefs[0].rsplit(":", 1)[-1] if ":" in memrefs[0] else memrefs[0]
        return short

    def _dma_src_dst(entry):
        """Format DMA source and destination variable names."""
        src = _memref_short(entry.input_memrefs)
        dst = _memref_short(entry.output_memrefs)
        parts = []
        if src:
            parts.append(f"src={src}")
        if dst:
            parts.append(f"dst={dst}")
        return "  ".join(parts) if parts else ""

    instr_map = {i.name: i for i in instructions}

    # Build profiled DMA completion order
    profiled_dma = []
    for e in dma_ordering:
        p = profiled.get(e.dma_name)
        if p is None:
            continue
        profiled_dma.append((e, p))

    profiled_dma_by_end = sorted(profiled_dma, key=lambda x: x[1].end_ts)
    profiled_completion_rank = {}
    for rank, (e, _p) in enumerate(profiled_dma_by_end, 1):
        profiled_completion_rank[e.dma_name] = rank

    # Build profiled consumption order (first consumer's actual start_ts)
    profiled_consumption = []
    dep_types_for_dma = {"Flow"}
    for e in dma_ordering:
        consumers = []
        for instr in instructions:
            if instr.engine == "DMA":
                continue
            for dep_name, dep_type in instr.dependencies:
                if dep_type in dep_types_for_dma and dep_name == e.dma_name:
                    p_consumer = profiled.get(instr.name)
                    if p_consumer:
                        consumers.append((instr.name, p_consumer.start_ts))
        if consumers:
            first_name, first_ts = min(consumers, key=lambda x: x[1])
            profiled_consumption.append((e, first_name, first_ts))

    profiled_consumption_sorted = sorted(profiled_consumption, key=lambda x: x[2])
    profiled_consumption_rank = {}
    for rank, (e, _name, _ts) in enumerate(profiled_consumption_sorted, 1):
        profiled_consumption_rank[e.dma_name] = rank

    # Compute earliest possible trigger for each DMA: max(end_ts of all its deps)
    def _earliest_trigger(dma_name):
        dma_instr = instr_map.get(dma_name)
        if dma_instr is None or not dma_instr.dependencies:
            return 0
        earliest = 0
        for dep_name, dep_type in dma_instr.dependencies:
            p_dep = profiled.get(dep_name)
            if p_dep:
                earliest = max(earliest, p_dep.end_ts)
        return earliest

    # Load collective ops from CcOp.parquet if available
    cc_events = []
    if profiler_db_dir:
        import pandas as pd
        ccop_path = os.path.join(profiler_db_dir, "CcOp.parquet")
        if os.path.exists(ccop_path):
            cc_df = pd.read_parquet(ccop_path)
            for _, row in cc_df.iterrows():
                cc_events.append({
                    "end_ts": int(row["end_ts"]),
                    "start_ts": int(row["start_ts"]),
                    "duration_ns": int(row["duration_ns"]),
                    "operation": row["operation"],
                    "input_size": int(row.get("input_size", 0)),
                    "trigger_start_ts": int(row["trigger_start_ts"]) if pd.notna(row.get("trigger_start_ts")) else None,
                })

    # Build unified timeline: DMA entries + collective ops, sorted by end_ts
    timeline = []
    for e, p in profiled_dma_by_end:
        timeline.append(("DMA", e, p, None))
    for cc in cc_events:
        timeline.append(("CC", None, None, cc))
    timeline.sort(key=lambda x: x[3]["end_ts"] if x[0] == "CC" else x[2].end_ts)

    # Print unified DMA + Collective timeline
    print(f"=== Profiled DMA & Collective Timeline (by end_ts) ===")
    dma_rank = 0
    for kind, e, p, cc in timeline:
        if kind == "DMA":
            dma_rank += 1
            sched_rank = e.completion_rank
            marker = " ***" if dma_rank != sched_rank else ""
            src_dst = _dma_src_dst(e)
            trigger_str = f"trigger={p.trigger_ts:8d}  " if p.trigger_ts else ""
            earliest = _earliest_trigger(e.dma_name)
            slack = p.trigger_ts - earliest if p.trigger_ts and earliest else 0
            slack_str = f"  slack={slack}" if slack > 100 else ""
            queue_str = f"  Q{p.queue_idx}" if p.queue_idx >= 0 else ""
            print(f"  #{dma_rank:2d}  {e.dma_name:45s}  {trigger_str}actual_end={p.end_ts:8d}  "
                  f"earliest_trigger={earliest:8d}{slack_str}{queue_str}  "
                  f"(sched_rank=#{sched_rank:2d})  {src_dst}{marker}")
        else:
            op = cc["operation"]
            start = cc["start_ts"]
            end = cc["end_ts"]
            dur = cc["duration_ns"]
            size = cc["input_size"]
            trigger = cc["trigger_start_ts"]
            trigger_str = f"trigger={trigger:8d}  " if trigger else ""
            size_str = f"  size={size}" if size > 0 else ""
            detail = f"[CC] {op:10s}  {trigger_str}start={start:8d}  end={end:8d}  dur={dur:6d}{size_str}"
            print(f"  {'─' * 120}")
            print(f"  {detail}")
            print(f"  {'─' * 120}")
    print()

    # Build unified consumption timeline: DMA entries + collective ops
    consumption_timeline = []
    for e, consumer_name, consumer_ts in profiled_consumption_sorted:
        consumption_timeline.append(("DMA", e, consumer_name, consumer_ts, None))
    for cc in cc_events:
        consumption_timeline.append(("CC", None, None, cc["end_ts"], cc))
    consumption_timeline.sort(key=lambda x: x[3])

    # Print comparison: scheduled vs profiled consumption order
    print(f"=== Profiled DMA & Collective Consumption Order (by first consumer start_ts / end_ts) ===")
    dma_rank = 0
    for kind, e, consumer_name, consumer_ts, cc in consumption_timeline:
        if kind == "DMA":
            dma_rank += 1
            sched_rank = e.consumption_rank
            marker = " ***" if dma_rank != sched_rank else ""
            src_dst = _dma_src_dst(e)
            p = profiled.get(e.dma_name)
            trigger_str = f"trigger={p.trigger_ts:8d}  " if p and p.trigger_ts else ""
            end_str = f"dma_end={p.end_ts:8d}  " if p else ""
            queue_str = f"Q{p.queue_idx}  " if p and p.queue_idx >= 0 else ""
            print(f"  #{dma_rank:2d}  {e.dma_name:45s}  {queue_str}{trigger_str}{end_str}first_consumer={consumer_name} "
                  f"at t={consumer_ts:8d}  (sched_rank=#{sched_rank:2d})  {src_dst}{marker}")
        else:
            op = cc["operation"]
            start = cc["start_ts"]
            end = cc["end_ts"]
            dur = cc["duration_ns"]
            size = cc["input_size"]
            trigger = cc["trigger_start_ts"]
            trigger_str = f"trigger={trigger:8d}  " if trigger else ""
            size_str = f"  size={size}" if size > 0 else ""
            detail = f"[CC] {op:10s}  {trigger_str}start={start:8d}  end={end:8d}  dur={dur:6d}{size_str}"
            print(f"  {'─' * 120}")
            print(f"  {detail}")
            print(f"  {'─' * 120}")
    print()

    # Queue-aware ordering analysis: detect DMAs that block more urgent ones on the same queue.
    # "Urgency" = non_dma_ready_time of the first consumer (when consumer would start if DMA were done).
    # This is the true demand time, not the observed consumer_start (which is after the DMA finished).
    dep_types_for_dma = {"Flow"}

    def _consumer_non_dma_ready(dma_name):
        """Find the earliest consumer's non_dma_ready_time for a DMA."""
        dma_instr = instr_map.get(dma_name)
        if dma_instr is None:
            return None
        # Find all compute instructions that depend on this DMA via Flow
        consumers = []
        for instr in instructions:
            if instr.engine == "DMA":
                continue
            for dep_name, dep_type in instr.dependencies:
                if dep_type in dep_types_for_dma and dep_name == dma_name:
                    # Compute non_dma_ready for this consumer
                    non_dma_ready = 0
                    for d_name, d_type in instr.dependencies:
                        if d_name == dma_name:
                            continue
                        p_d = profiled.get(d_name)
                        if p_d:
                            non_dma_ready = max(non_dma_ready, p_d.end_ts)
                    consumers.append(non_dma_ready)
                    break
        return min(consumers) if consumers else None

    # Group profiled DMAs by queue with urgency info
    queue_groups: dict[int, list] = {}
    for e in dma_ordering:
        p = profiled.get(e.dma_name)
        if p is None or p.queue_idx < 0:
            continue
        urgency = _consumer_non_dma_ready(e.dma_name)
        if urgency is None:
            continue
        earliest = _earliest_trigger(e.dma_name)
        transfer_dur = p.end_ts - p.start_ts
        queue_groups.setdefault(p.queue_idx, []).append((e, p, urgency, earliest, transfer_dur))

    # Iterative swap algorithm: find optimal DMA trigger order per queue
    def _simulate_queue(order):
        """Simulate FIFO queue execution. Returns list of (name, start, end) tuples."""
        timeline = []
        prev_end = 0
        for (e, p, urgency, earliest, dur) in order:
            start = max(earliest, prev_end)
            end = start + dur
            timeline.append((e.dma_name, start, end, urgency, dur))
            prev_end = end
        return timeline

    def _total_stall(timeline):
        """Sum of max(0, dma_end - urgency) for each DMA in timeline."""
        return sum(max(0, end - urgency) for _, _, end, urgency, _ in timeline)

    def _optimize_queue(entries):
        """Iteratively swap adjacent DMAs to reduce total stall. Returns optimized order."""
        order = list(entries)
        improved = True
        while improved:
            improved = False
            for i in range(len(order) - 1):
                e_a, p_a, urg_a, earliest_a, dur_a = order[i]
                e_b, p_b, urg_b, earliest_b, dur_b = order[i + 1]
                # Can we swap? B must be able to start at A's position
                # A's start in current order:
                prev_end = 0
                if i > 0:
                    # simulate up to position i
                    t = _simulate_queue(order[:i])
                    prev_end = t[-1][2] if t else 0
                a_start = max(earliest_a, prev_end)
                # B can go at position i if earliest_b <= a_start
                if earliest_b > a_start:
                    continue
                # Try swap
                new_order = order[:i] + [order[i + 1], order[i]] + order[i + 2:]
                # Verify A can still start after B in new order
                b_new_start = max(earliest_b, prev_end)
                b_new_end = b_new_start + dur_b
                a_new_start = max(earliest_a, b_new_end)
                if a_new_start < earliest_a:
                    continue  # A's deps not ready yet
                # Compare stall
                old_stall = _total_stall(_simulate_queue(order))
                new_stall = _total_stall(_simulate_queue(new_order))
                if new_stall < old_stall:
                    order = new_order
                    improved = True
                    break  # restart from beginning
        return order

    any_reordering = False
    for q_idx, entries in sorted(queue_groups.items()):
        # Sort by actual trigger order
        by_trigger = sorted(entries, key=lambda x: x[1].trigger_ts if x[1].trigger_ts else x[1].start_ts)
        original_stall = _total_stall(_simulate_queue(by_trigger))

        optimized = _optimize_queue(by_trigger)
        optimized_stall = _total_stall(_simulate_queue(optimized))

        if original_stall == optimized_stall:
            continue

        saving = original_stall - optimized_stall
        if saving <= 100:
            continue

        if not any_reordering:
            print(f"=== Recommended DMA Trigger Reordering ===")
            any_reordering = True

        orig_names = [e.dma_name for e, _, _, _, _ in by_trigger]
        opt_names = [e.dma_name for e, _, _, _, _ in optimized]

        print(f"  Queue Q{q_idx} ({entries[0][1].queue_name}): saving {saving} cycles")
        print(f"  Current order → Recommended order:")
        print()
        orig_timeline = _simulate_queue(by_trigger)
        opt_timeline = _simulate_queue(optimized)
        # Truncate long names for readability
        def _short_name(name, maxlen=40):
            if len(name) <= maxlen:
                return name
            return name[:maxlen - 3] + "..."

        print(f"  Current order:")
        print(f"  {'#':>3s}  {'Instruction':<42s}  {'start':>6s}  {'end':>6s}  {'dur':>5s}  {'stall':>6s}")
        for i, (name, start, end, urg, dur) in enumerate(orig_timeline):
            stall = max(0, end - urg) if urg > 0 else 0
            print(f"  #{i+1:2d}  {_short_name(name):<42s}  {start:6d}  {end:6d}  {dur:5d}  {stall:6d}")
        print()
        print(f"  Recommended order:")
        print(f"  {'#':>3s}  {'Instruction':<42s}  {'start':>6s}  {'end':>6s}  {'dur':>5s}  {'stall':>6s}  {'change'}")
        for i, (name, start, end, urg, dur) in enumerate(opt_timeline):
            stall = max(0, end - urg) if urg > 0 else 0
            o_name = orig_timeline[i][0]
            changed = "  <<<" if name != o_name else ""
            print(f"  #{i+1:2d}  {_short_name(name):<42s}  {start:6d}  {end:6d}  {dur:5d}  {stall:6d}{changed}")
        print()

    # Profiled mismatches — focus on DMAs that complete too LATE (completion_rank > consumption_rank)
    late_mismatches = []
    early_mismatches = []
    for e in dma_ordering:
        c_rank = profiled_completion_rank.get(e.dma_name)
        u_rank = profiled_consumption_rank.get(e.dma_name)
        if c_rank is None or u_rank is None:
            continue
        if c_rank > u_rank:
            late_mismatches.append((e, c_rank, u_rank))
        elif c_rank < u_rank:
            early_mismatches.append((e, c_rank, u_rank))

    if late_mismatches:
        print(f"=== DMAs Completing Too Late ({len(late_mismatches)}) — potential scheduling inefficiency ===")
        # Build lookup from dma_name -> profiled data for cross-reference
        profiled_dma_by_completion = {e.dma_name: (e, p) for e, p in profiled_dma}

        for e, c_rank, u_rank in sorted(late_mismatches, key=lambda x: x[1]):
            src_dst = _dma_src_dst(e)
            p = profiled.get(e.dma_name)
            trigger_str = f"trigger={p.trigger_ts:8d}  " if p and p.trigger_ts else ""
            bytes_str = f"bytes={p.transfer_bytes:8d}  " if p and p.transfer_bytes else ""
            print(f"  {e.dma_name:45s}  completes #{c_rank} but needed #{u_rank}  {src_dst}")
            if p:
                print(f"    {trigger_str}{bytes_str}actual_end={p.end_ts:8d}")

            cutters = []
            for other_e in dma_ordering:
                if other_e.dma_name == e.dma_name:
                    continue
                other_c = profiled_completion_rank.get(other_e.dma_name)
                other_u = profiled_consumption_rank.get(other_e.dma_name)
                if other_c is not None and other_u is not None:
                    if other_c < c_rank and other_u > u_rank:
                        other_p = profiled.get(other_e.dma_name)
                        cutters.append((other_e, other_c, other_u, other_p))
            if cutters:
                print(f"    Cutting in line (completed earlier, consumed later):")
                for ce, cc, cu, cp in sorted(cutters, key=lambda x: x[1]):
                    ce_src_dst = _dma_src_dst(ce)
                    ct = f"trigger={cp.trigger_ts:8d}  " if cp and cp.trigger_ts else ""
                    cb = f"bytes={cp.transfer_bytes:8d}  " if cp and cp.transfer_bytes else ""
                    ce_end = f"end={cp.end_ts:8d}  " if cp else ""
                    print(f"      #{cc:2d} {ce.dma_name:43s}  consumed #{cu:2d}  "
                          f"{ct}{ce_end}{cb}{ce_src_dst}")
            print()
    else:
        print("No DMAs completing later than needed — completion order matches consumption order.")
        print()

    if early_mismatches:
        print(f"=== DMAs Completing Too Early ({len(early_mismatches)}) — loaded before needed ===")
        for e, c_rank, u_rank in sorted(early_mismatches, key=lambda x: x[1]):
            src_dst = _dma_src_dst(e)
            p = profiled.get(e.dma_name)
            bytes_str = f"bytes={p.transfer_bytes:8d}  " if p and p.transfer_bytes else ""
            print(f"  {e.dma_name:45s}  completes #{c_rank} but needed #{u_rank}  "
                  f"{bytes_str}{src_dst}")
        print()

    # Profiled DMA-blocked analysis
    all_blocked = []
    for instr in instructions:
        if instr.engine == "DMA":
            continue
        p_instr = profiled.get(instr.name)
        if p_instr is None:
            continue

        profiled_dma_ready = 0
        profiled_non_dma_ready = 0
        blocking_dma = []

        for dep_name, dep_type in instr.dependencies:
            dep = instr_map.get(dep_name)
            if dep is None:
                continue
            p_dep = profiled.get(dep_name)
            if p_dep is None:
                continue

            if dep.engine == "DMA" and dep_type in dep_types_for_dma:
                if p_dep.end_ts > profiled_dma_ready:
                    profiled_dma_ready = p_dep.end_ts
                    blocking_dma = [dep_name]
                elif p_dep.end_ts == profiled_dma_ready:
                    blocking_dma.append(dep_name)
            else:
                profiled_non_dma_ready = max(profiled_non_dma_ready, p_dep.end_ts)

        if profiled_dma_ready == 0:
            continue

        stall = max(0, profiled_dma_ready - profiled_non_dma_ready)
        if stall > 0:
            all_blocked.append((instr, p_instr, profiled_dma_ready, profiled_non_dma_ready, stall, blocking_dma))

    pipeline_fill = [(i, p, dr, ndr, s, b) for i, p, dr, ndr, s, b in all_blocked
                     if _dma_has_no_dependencies(b, instr_map)]
    has_deps = [(i, p, dr, ndr, s, b) for i, p, dr, ndr, s, b in all_blocked
                if (i, p, dr, ndr, s, b) not in pipeline_fill]

    # Classify: for each blocking DMA, check if its latest-ending dep is Flow (fundamental)
    # or Anti/Output (actionable — eliminable via double-buffering or reordering)
    def _classify_dma_stall(dma_name):
        """Returns ('fundamental', latest_flow_end) or ('actionable', latest_anti_end, latest_flow_end)."""
        dma_instr = instr_map.get(dma_name)
        if dma_instr is None or not dma_instr.dependencies:
            return "fundamental", 0, 0

        latest_flow_end = 0
        latest_anti_output_end = 0
        for dep_name, dep_type in dma_instr.dependencies:
            p_dep = profiled.get(dep_name)
            if p_dep is None:
                continue
            if dep_type == "Flow":
                latest_flow_end = max(latest_flow_end, p_dep.end_ts)
            elif dep_type in ("Anti", "Output"):
                latest_anti_output_end = max(latest_anti_output_end, p_dep.end_ts)

        if latest_anti_output_end > latest_flow_end:
            return "actionable", latest_anti_output_end, latest_flow_end
        else:
            return "fundamental", latest_flow_end, latest_anti_output_end

    actionable_reports = []
    fundamental_reports = []
    for entry in has_deps:
        instr, p_instr, dma_ready, non_dma_ready, stall, blocking = entry
        classification = _classify_dma_stall(blocking[0])
        if classification[0] == "actionable":
            actionable_reports.append((*entry, classification))
        else:
            fundamental_reports.append((*entry, classification))

    print(f"=== Profiled DMA-Blocked Compute Instructions ===")
    print(f"  Actionable (Anti/Output dep is bottleneck): {len(actionable_reports)}")
    print(f"  Fundamental (Flow dep is bottleneck — address/data not ready): {len(fundamental_reports)}")
    print(f"  Pipeline-fill (blocking DMA has no deps): {len(pipeline_fill)}")
    print()

    if actionable_reports:
        actionable_reports.sort(key=lambda x: -x[4])
        total_stall = sum(r[4] for r in actionable_reports)
        print(f"  --- Actionable stalls (eliminable via double-buffering/reordering, total: {total_stall}) ---")
        seen_dma_chains = set()
        for instr, p_instr, dma_ready, non_dma_ready, stall, blocking, classification in actionable_reports:
            _, anti_end, flow_end = classification
            dbg = instr.debug
            loc = ""
            if dbg:
                fn = dbg.get("filename", "").rsplit("/", 1)[-1] if dbg.get("filename") else ""
                ln = dbg.get("lineno", "")
                op = dbg.get("op_name", "")
                parts = [f for f in [f"{fn}:{ln}" if fn else "", op] if f]
                if parts:
                    loc = f"  [{', '.join(parts)}]"
            print(f"  {instr.name} ({instr.engine}/{instr.opcode}){loc}")
            print(f"    actual_start={p_instr.start_ts}  actual_dma_ready={dma_ready}  "
                  f"actual_non_dma_ready={non_dma_ready}  stall={stall}")
            print(f"    blocking DMA: {blocking}")
            print(f"    bottleneck: Anti/Output dep ends at {anti_end}, Flow dep ends at {flow_end} "
                  f"(could save {anti_end - flow_end} cycles)")
            for b_name in blocking:
                if b_name not in seen_dma_chains:
                    seen_dma_chains.add(b_name)
                    _print_dependency_chain(b_name, instr_map, profiled)
                else:
                    print(f"    (chain for {b_name} shown above)")
        print()

    if fundamental_reports:
        fundamental_reports.sort(key=lambda x: -x[4])
        total_stall = sum(r[4] for r in fundamental_reports)
        print(f"  --- Fundamental stalls (Flow dep is bottleneck — data not ready, total: {total_stall}) ---")
        seen_dma_chains_f = set()
        for instr, p_instr, dma_ready, non_dma_ready, stall, blocking, classification in fundamental_reports:
            _, flow_end, anti_end = classification
            dbg = instr.debug
            loc = ""
            if dbg:
                fn = dbg.get("filename", "").rsplit("/", 1)[-1] if dbg.get("filename") else ""
                ln = dbg.get("lineno", "")
                op = dbg.get("op_name", "")
                parts = [f for f in [f"{fn}:{ln}" if fn else "", op] if f]
                if parts:
                    loc = f"  [{', '.join(parts)}]"
            print(f"  {instr.name} ({instr.engine}/{instr.opcode}){loc}")
            print(f"    actual_start={p_instr.start_ts}  actual_dma_ready={dma_ready}  "
                  f"actual_non_dma_ready={non_dma_ready}  stall={stall}")
            print(f"    blocking DMA: {blocking}")
            print(f"    bottleneck: Flow dep ends at {flow_end} (fundamental — address/data not ready)")
            for b_name in blocking:
                if b_name not in seen_dma_chains_f:
                    seen_dma_chains_f.add(b_name)
                    _print_dependency_chain(b_name, instr_map, profiled)
                else:
                    print(f"    (chain for {b_name} shown above)")
        print()

    if pipeline_fill:
        print()
        print(f"  Pipeline-fill stalls (blocking DMA has no deps — issuable at kernel start):")
        for instr, p_instr, dma_ready, non_dma_ready, stall, blocking in sorted(pipeline_fill, key=lambda x: -x[4]):
            print(f"  {instr.name} ({instr.engine}/{instr.opcode})")
            print(f"    actual_start={p_instr.start_ts}  actual_dma_ready={dma_ready}  stall={stall}")
            print(f"    blocking DMA: {blocking}")
    print()


def print_dma_ordering(entries: list[DMAOrderingEntry], verbose: bool = False):
    if not entries:
        print("No DMA instructions with compute consumers found.")
        return

    by_completion = sorted(entries, key=lambda e: e.completion_rank)
    by_consumption = sorted(entries, key=lambda e: e.consumption_rank)

    mismatches = [e for e in entries if e.completion_rank != e.consumption_rank]

    def _memref_short(memrefs):
        if not memrefs:
            return ""
        short = memrefs[0].rsplit(":", 1)[-1] if ":" in memrefs[0] else memrefs[0]
        return short

    def _dma_src_dst(entry):
        src = _memref_short(entry.input_memrefs)
        dst = _memref_short(entry.output_memrefs)
        parts = []
        if src:
            parts.append(f"src={src}")
        if dst:
            parts.append(f"dst={dst}")
        return "  ".join(parts) if parts else ""

    print(f"=== DMA Completion Order (by scheduled_end) ===")
    for e in by_completion:
        marker = " ***" if e.completion_rank != e.consumption_rank else ""
        src_dst = _dma_src_dst(e)
        print(f"  #{e.completion_rank:2d}  {e.dma_name:45s}  end={e.scheduled_end:6d}  "
              f"start={e.scheduled_start:6d}  {src_dst}{marker}")
    print()

    print(f"=== DMA Consumption Order (by first compute consumer's scheduled_start) ===")
    for e in by_consumption:
        marker = " ***" if e.completion_rank != e.consumption_rank else ""
        src_dst = _dma_src_dst(e)
        print(f"  #{e.consumption_rank:2d}  {e.dma_name:45s}  "
              f"first_consumer={e.first_consumer_name} ({e.first_consumer_engine}) "
              f"at t={e.first_consumer_start:6d}  {src_dst}{marker}")
    print()

    if mismatches:
        print(f"=== Ordering Mismatches ({len(mismatches)}) ===")
        print(f"  DMA instructions where completion rank differs from consumption rank:")
        for e in sorted(mismatches, key=lambda e: e.completion_rank):
            src_dst = _dma_src_dst(e)
            print(f"  {e.dma_name:45s}  "
                  f"completes #{e.completion_rank} but needed #{e.consumption_rank}  "
                  f"(end={e.scheduled_end}, consumer_start={e.first_consumer_start})  "
                  f"{src_dst}")
        print()
    else:
        print("DMA completion order matches consumption order — no ordering mismatches.")
        print()


def print_report(
    reports: list[StallReport],
    instructions: list[InstructionInfo],
    dma_ordering: Optional[list[DMAOrderingEntry]] = None,
    verbose: bool = False,
):
    if dma_ordering is not None:
        print_dma_ordering(dma_ordering, verbose=verbose)

    instr_map = {i.name: i for i in instructions}

    all_blocked = [r for r in reports if r.stall > 0]
    pipeline_fill = [r for r in all_blocked
                     if _dma_has_no_dependencies(r.blocking_dma_instructions, instr_map)]
    dma_blocked = [r for r in all_blocked if r not in pipeline_fill]
    with_overhead = [r for r in reports if r.scheduling_overhead > 0]

    print(f"Total compute instructions with DMA dependencies: {len(reports)}")
    print(f"DMA-blocked instructions: {len(dma_blocked)} actionable, "
          f"{len(pipeline_fill)} pipeline-fill (blocking DMA has no deps)")
    print(f"Instructions with scheduling overhead: {len(with_overhead)}")
    print()

    if dma_blocked:
        total_stall = sum(r.stall for r in dma_blocked)
        max_stall = max(r.stall for r in dma_blocked)
        print(f"=== DMA-Blocked Instructions (total stall: {total_stall}, max: {max_stall}) ===")
        for r in sorted(dma_blocked, key=lambda x: -x.stall):
            print(f"  {r.instruction_name} ({r.engine}/{r.opcode})")
            print(f"    scheduled_start={r.scheduled_start}  dma_ready={r.dma_ready_time}  "
                  f"non_dma_ready={r.non_dma_ready_time}  stall={r.stall}")
            print(f"    blocking DMA: {r.blocking_dma_instructions}")
            if verbose and r.debug:
                print(f"    source: {r.debug.get('filename', '?')}:{r.debug.get('lineno', '?')} "
                      f"({r.debug.get('op_name', '?')})")
            print()

    if pipeline_fill and verbose:
        print(f"=== Pipeline-Fill Stalls ({len(pipeline_fill)}, blocking DMA has no deps — issuable at kernel start) ===")
        for r in sorted(pipeline_fill, key=lambda x: -x.stall):
            print(f"  {r.instruction_name} ({r.engine}/{r.opcode})")
            print(f"    scheduled_start={r.scheduled_start}  dma_ready={r.dma_ready_time}  stall={r.stall}")
            print(f"    blocking DMA: {r.blocking_dma_instructions}")
        print()

    if verbose and with_overhead:
        total_oh = sum(r.scheduling_overhead for r in with_overhead)
        print(f"=== Scheduling Overhead (total: {total_oh}) ===")
        for r in sorted(with_overhead, key=lambda x: -x.scheduling_overhead):
            all_ready = max(r.dma_ready_time, r.non_dma_ready_time)
            print(f"  {r.instruction_name} ({r.engine}/{r.opcode})")
            print(f"    start={r.scheduled_start}  all_deps_ready={all_ready}  "
                  f"overhead={r.scheduling_overhead}")
            print()

    if not dma_blocked:
        print("No DMA ordering issues detected — all DMA loads complete before "
              "their dependent compute instructions are ready.")


def _resolve_profiler_db(profile_path: str) -> str:
    """Resolve a --profile argument to a profiler_db directory.

    Accepts:
      - A profiler_db/ directory directly
      - An .ntff file path (sibling profiler_db/ is used)
      - An infer_result/ directory (profiler_db/ inside it)
    """
    p = profile_path
    if os.path.isdir(p):
        if os.path.exists(os.path.join(p, "Instruction.parquet")):
            return p
        candidate = os.path.join(p, "profiler_db")
        if os.path.isdir(candidate):
            return candidate
    elif os.path.isfile(p) and p.endswith(".ntff"):
        candidate = os.path.join(os.path.dirname(p), "profiler_db")
        if os.path.isdir(candidate):
            return candidate
    print(f"Error: Cannot find profiler_db from '{profile_path}'.", file=sys.stderr)
    print("  Pass a profiler_db/ directory, an .ntff file (with sibling profiler_db/),", file=sys.stderr)
    print("  or an infer_result/ directory.", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="DMA-Compute ordering verification")
    parser.add_argument("bir_json", help="Path to post-scheduler BIR JSON file")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show scheduling overhead and source locations")
    parser.add_argument("--json-output", help="Write results as JSON to this path")
    parser.add_argument("--profile",
                        help="Path to profiler_db/ directory, .ntff file, or infer_result/ directory "
                             "for actual device trace comparison")
    parser.add_argument("--show-compiler-estimate", action="store_true",
                        help="Show the scheduled (compiler estimate) analysis section")
    args = parser.parse_args()

    bir_data = parse_bir_json(args.bir_json)
    instructions = extract_instructions(bir_data)
    reports = analyze_dma_compute_ordering(instructions)
    dma_ordering = analyze_dma_ordering(instructions)

    if args.show_compiler_estimate or not args.profile:
        print("=" * 80)
        print("SCHEDULED (compiler estimate)")
        print("=" * 80)
        print()
        print_report(reports, instructions, dma_ordering=dma_ordering, verbose=args.verbose)

    if args.profile:
        profiler_db = _resolve_profiler_db(args.profile)
        if args.show_compiler_estimate:
            print()
        print("=" * 80)
        print(f"PROFILED (actual device trace: {profiler_db})")
        print("=" * 80)
        print()
        profiled = load_profiled_instructions(profiler_db, instructions)
        print(f"Loaded {len(profiled)} profiled instructions from {profiler_db}")
        print()
        print_profile_comparison(dma_ordering, profiled, instructions, profiler_db_dir=profiler_db)

    if args.json_output:
        output = []
        for r in reports:
            output.append({
                "instruction": r.instruction_name,
                "engine": r.engine,
                "opcode": r.opcode,
                "scheduled_start": r.scheduled_start,
                "dma_ready_time": r.dma_ready_time,
                "non_dma_ready_time": r.non_dma_ready_time,
                "stall": r.stall,
                "scheduling_overhead": r.scheduling_overhead,
                "blocking_dma": r.blocking_dma_instructions,
            })
        with open(args.json_output, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nJSON output written to {args.json_output}")


if __name__ == "__main__":
    main()
