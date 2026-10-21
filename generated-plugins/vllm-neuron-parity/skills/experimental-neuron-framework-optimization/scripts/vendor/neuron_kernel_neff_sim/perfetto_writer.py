"""Output Chrome Trace Event format for Perfetto visualization."""

import json
from typing import Any, Dict, List, Optional

from .graph_builder import InstructionGraph, SimInstruction
from .scheduler import SimEvent, SimResult


# Engine display order and thread IDs.
# Queue trigger tracks at top; physical "DMA engine" track shows actual
# packet transfers (only 1 in-flight at a time across all queues);
# PE TensorEngine + weight-load + col groups clustered (200+).
ENGINE_DISPLAY_ORDER = [
    ("DMA", "DMA-trigger", 1),
    ("PE", "TensorEngine (PE)", 200),
    ("DVE", "VectorEngine (DVE)", 300),
    ("SP", "ScalarEngine (SP)", 301),
    ("Activation", "ScalarEngine (Activation)", 303),
    ("Pool", "GpSimd (Pool)", 302),
]
# Physical DMA engine track: a single aggregated track showing actual
# packet transfers serialized across all queues through 16 hw engines.
PHYSICAL_DMA_ENGINE_TID = 100

# Column group sub-engine display names
_COL_GRP_DISPLAY = {
    "q0": "PE:ColGrp[0:31]",
    "q32": "PE:ColGrp[32:63]",
    "q64": "PE:ColGrp[64:95]",
    "q96": "PE:ColGrp[96:127]",
    "h0": "PE:ColGrp[0:63]",
    "h64": "PE:ColGrp[64:127]",
}


def write_trace(
    result: SimResult,
    graph: InstructionGraph,
    output_path: str,
    show_phases: bool = False,
    vnc_count: int = 1,
) -> None:
    """Write simulation result as a Perfetto-compatible Chrome Trace JSON.

    Args:
        result: Simulation result with scheduled events
        graph: Instruction graph for metadata
        output_path: Path to write the JSON trace file
        show_phases: If True, show ReadInit/Exec/WriteDrain as nested events
        vnc_count: Number of NeuronCores. With vnc_count=2, emits both
            nc0 and nc1 with identical timelines (the simulation runs
            once for one core; the duplicated nc1 view shows what the
            other core does in parallel).
    """
    trace_events = _build_trace_events(result, graph, show_phases, vnc_count)

    with open(output_path, "w") as f:
        json.dump(trace_events, f, indent=None, separators=(",", ":"))


def _build_trace_events(
    result: SimResult,
    graph: InstructionGraph,
    show_phases: bool,
    vnc_count: int = 1,
) -> List[Dict[str, Any]]:
    """Build the list of Chrome Trace Event objects."""
    events: List[Dict[str, Any]] = []

    # Build base tid map for nc0
    tid_map = _build_tid_map(result)

    # Gather DMAs per core (in start order) for interleaved scheduling
    # done after event_packet_alloc is computed.
    dma_events_by_core: Dict[int, List[tuple]] = {}
    for core in range(max(1, vnc_count)):
        all_dma = []
        for engine_name, timeline in result.engine_timelines.items():
            if not engine_name.startswith("DMA"):
                continue
            for ev in timeline:
                inst = graph.instructions[ev.instruction_index]
                if inst.opcode != "DMA_TRANSFER":
                    continue
                all_dma.append((ev.start_ns, ev.end_ns, ev.instruction_index))
        all_dma.sort()
        dma_events_by_core[core] = all_dma

    # Compute per-queue total bytes (sum of this run's hw_desc_bytes per
    # queue) and the real target packet count (from DmaPacket.parquet,
    # carried on result.dma_target_packet_counts).
    queue_total_bytes: Dict[str, int] = {}
    queue_event_keys: Dict[str, List[tuple]] = {}
    for engine_name, timeline in result.engine_timelines.items():
        if not engine_name.startswith("DMA"):
            continue
        for ev in timeline:
            inst = graph.instructions[ev.instruction_index]
            if inst.opcode != "DMA_TRANSFER":
                continue
            qn = inst.dma_queue
            if not qn:
                continue
            inner = (inst.extra or {}).get("hw_desc_bytes")
            if inner:
                qbytes = (inst.extra or {}).get("_hw_desc_total_bytes")
                if qbytes is None:
                    qbytes = sum(inner)
                    if inst.extra is not None:
                        inst.extra["_hw_desc_total_bytes"] = qbytes
            else:
                qbytes = inst.transfer_bytes
            queue_total_bytes[qn] = queue_total_bytes.get(qn, 0) + qbytes
            queue_event_keys.setdefault(qn, []).append(
                (ev.instruction_index, qbytes))
    queue_target_count = getattr(result, "dma_target_packet_counts", None)

    # Largest-remainder apportionment so per-instruction packet counts
    # sum exactly to the real per-queue target. Per-instruction map:
    # event_packet_alloc[instruction_index] = exact packet count.
    event_packet_alloc: Dict[int, int] = {}
    if queue_target_count:
        for qn, target in queue_target_count.items():
            keys = queue_event_keys.get(qn) or []
            total_b = queue_total_bytes.get(qn, 0)
            if not keys or target <= 0 or total_b <= 0:
                continue
            # Each event gets at least 1 packet (real DMAs all transfer
            # something). Distribute remaining (target - n_events) by
            # largest-remainder of the byte share.
            n_events = len(keys)
            base_each = 1 if target >= n_events else 0
            remaining = target - base_each * n_events
            if remaining < 0:
                remaining = 0
            # exact share per event = bytes/total * remaining
            shares = []
            allocated_extra = 0
            for idx, b in keys:
                exact = b * remaining / total_b
                whole = int(exact)
                shares.append((idx, b, exact - whole, whole))
                allocated_extra += whole
            # Distribute the leftover by largest fractional remainder.
            leftover = remaining - allocated_extra
            shares.sort(key=lambda s: s[2], reverse=True)
            extras: Dict[int, int] = {}
            for i, (idx, _b, _frac, whole) in enumerate(shares):
                extra = whole + (1 if i < leftover else 0)
                extras[idx] = extra
            for idx, _b in keys:
                event_packet_alloc[idx] = base_each + extras.get(idx, 0)

    # Build per-DMA, per-lane packet times.
    # Hardware model: 16 DMA engines work in parallel. For one DMA
    # with N packets, packets are distributed across all 16 lanes
    # (so each lane gets ceil(N/16) packets). Multiple concurrent
    # DMAs share the lanes via round-robin packet interleaving:
    # at each "tick", each lane processes the next packet from the
    # round-robin DMA queue. This keeps all 16 lanes active when
    # any DMAs are active.
    #
    # Data structure: dma_packet_times[(inst_idx, lane_idx)] = list of
    # (start_ns, end_ns) tuples for each packet emitted on that lane
    # for that DMA.
    NUM_LANES = 16
    dma_packet_times: Dict[tuple, List[tuple]] = {}

    for core, all_dma in dma_events_by_core.items():
        if not all_dma:
            continue
        # Each DMA: (start_ns, end_ns, inst_idx, packets_remaining)
        # We'll iterate by "ticks". At each tick, all 16 lanes are
        # busy on currently-active DMAs (round-robin). Lane i in tick t
        # processes a packet of whichever DMA is i-th in the active queue
        # (modulo). Lane time advances by packet_dur per tick.

        # Initialize per-DMA state
        dma_state: List[List] = []  # [start_ns, end_ns, inst_idx, packets_remaining, packets_total]
        for s, e, idx in all_dma:
            n_pkts = event_packet_alloc.get(idx, 1)
            if n_pkts <= 0:
                n_pkts = 1
            dma_state.append([s, e, idx, n_pkts, n_pkts])
            for lane in range(NUM_LANES):
                dma_packet_times[(idx, lane)] = []

        # Per-lane clock: lane time advances as it processes packets.
        lane_clock = [dma_state[0][0]] * NUM_LANES
        pending_idx = 0
        active: List[List] = []  # currently-active DMAs in order of activation

        # Process: at each step, advance time to the earliest of
        # (a) next DMA's start_ns or (b) any lane's idle time, then
        # dispatch packets to all 16 lanes from the active queue.
        while pending_idx < len(dma_state) or active:
            # Activate any DMAs whose start_ns <= current min lane time
            min_lane_time = min(lane_clock)
            while (pending_idx < len(dma_state)
                   and dma_state[pending_idx][0] <= min_lane_time):
                active.append(dma_state[pending_idx])
                pending_idx += 1

            if not active:
                # No active DMAs — fast-forward all lanes to next start
                next_start = dma_state[pending_idx][0]
                for i in range(NUM_LANES):
                    lane_clock[i] = max(lane_clock[i], next_start)
                continue

            # Compute per-packet duration: each DMA's per-lane budget =
            # (end_ns - lane_start) / ceil(packets_remaining / NUM_LANES)
            # We choose packet_dur as the min over active so that the
            # most time-constrained DMA paces the round.
            pkt_dur_candidates = []
            for d in active:
                # Per-DMA packets total / NUM_LANES → packets per lane
                pkts_per_lane = max(1, (d[3] + NUM_LANES - 1) // NUM_LANES)
                # Time budget for this DMA = end_ns - max(active lane_clock)
                budget_end = d[1]
                budget_start = max(min_lane_time, d[0])
                if budget_end > budget_start and pkts_per_lane > 0:
                    pkt_dur_candidates.append(
                        (budget_end - budget_start) / pkts_per_lane)
            if not pkt_dur_candidates:
                pkt_dur = 50.0
            else:
                pkt_dur = max(50.0, min(pkt_dur_candidates))

            # One round: 16 lanes work in parallel, each on a packet
            # from one of the active DMAs (round-robin). If a single
            # DMA is active, it uses all 16 lanes for its packets.
            # If 2 DMAs are active, lanes 0-7 get DMA-A, lanes 8-15
            # get DMA-B. Etc.
            n_active = len(active)
            for lane_idx in range(NUM_LANES):
                if not active:
                    break
                d = active[lane_idx % n_active]
                if d[3] <= 0:
                    continue
                ps = max(lane_clock[lane_idx], d[0])
                pe = min(ps + pkt_dur, d[1])
                if pe <= ps:
                    pe = ps + 1.0
                dma_packet_times[(d[2], lane_idx)].append((ps, pe))
                lane_clock[lane_idx] = pe
                d[3] -= 1

            # Drop completed DMAs and reset n_active for next round
            active = [d for d in active if d[3] > 0]

    n_cores = max(1, vnc_count)
    for core in range(n_cores):
        core_offset = core * 1000
        for engine_name, tid in tid_map.items():
            new_tid = tid + core_offset
            display_name = _get_display_name(engine_name, core)
            events.append({
                "name": "thread_name",
                "ph": "M",
                "pid": 0,
                "tid": new_tid,
                "args": {"name": display_name},
            })
            events.append({
                "name": "thread_sort_index",
                "ph": "M",
                "pid": 0,
                "tid": new_tid,
                "args": {"sort_index": new_tid},
            })

    # Process name
    events.append({
        "name": "process_name",
        "ph": "M",
        "pid": 0,
        "tid": 0,
        "args": {"name": "CycleSim"},
    })

    # Instruction events — emit ONLY on nc0 to avoid 2x JSON cost.
    # The simulation runs once; nc1 timing is identical, and the empty
    # nc1 track is preserved by the thread_name metadata above so users
    # see both cores in the trace UI but with the events on nc0 only.
    # Tradeoff: nc1 timeline appears empty in the perfetto view. For
    # users who need nc1 events (rare), pass --emit-vnc-events.
    for core in (0,):
        core_offset = core * 1000
        for engine_name, timeline in result.engine_timelines.items():
            base_tid = tid_map.get(engine_name, 0) + core_offset
            for event in timeline:
                inst = graph.instructions[event.instruction_index]
                # Route MATMUL/MATMUL_MX to a separate matrix display tid.
                tid = base_tid
                if (inst.opcode in ("MATMUL", "MATMUL_MX")
                        and engine_name.startswith("PE")):
                    matrix_engine = engine_name + ":matrix"
                    if matrix_engine in tid_map:
                        tid = tid_map[matrix_engine] + core_offset
                # DMA packets emit on lane-specific tids. Each DMA
                # uses up to all 16 lanes in parallel; concurrent DMAs
                # share the lanes via round-robin.
                base_phys_tid = PHYSICAL_DMA_ENGINE_TID + core_offset
                # Build per-lane packet list for THIS DMA event.
                if inst.opcode == "DMA_TRANSFER":
                    per_lane: List[List[tuple]] = [[] for _ in range(16)]
                    for lane in range(16):
                        pkts = dma_packet_times.get(
                            (event.instruction_index, lane), [])
                        per_lane[lane] = pkts
                    # Build trace event with per-lane packets.
                    trace_event = _build_dma_lane_events(
                        event, inst, tid, base_phys_tid, per_lane)
                    events.extend(trace_event)
                else:
                    trace_event = _build_instruction_event(
                        event, inst, tid, show_phases,
                        getattr(result, "dma_packet_sizes", None),
                        base_phys_tid,
                        queue_total_bytes,
                        queue_target_count,
                        event_packet_alloc,
                        None)
                    events.extend(trace_event)

    # Add aggregate DMA engine thread metadata per core. The 16 sub-
    # tracks (DMA-eng[00..15]) reflect the 16 physical hw engines.
    # Real hw round-robins variables across the engine pool; sim
    # places concurrent DMAs on different sub-tracks via first-fit.
    for core in range(n_cores):
        for engine_idx in range(16):
            phys_tid = PHYSICAL_DMA_ENGINE_TID + core * 1000 + engine_idx
            events.append({
                "name": "thread_name",
                "ph": "M",
                "pid": 0,
                "tid": phys_tid,
                "args": {"name": f"DMA-eng[{engine_idx:02d}] (nc{core})"},
            })
            events.append({
                "name": "thread_sort_index",
                "ph": "M",
                "pid": 0,
                "tid": phys_tid,
                "args": {"sort_index": phys_tid},
            })

    # Post-fix: when concurrent DMAs round-robin to the same lane and
    # then re-converge after one finishes, the lane_clock invariant can
    # be violated by integer rounding. Sweep all packet events on each
    # physical DMA lane and bump any that overlap to enforce strict
    # ordering. This mutates ts/dur in-place; no event is dropped.
    _enforce_lane_ordering(events)

    # Final dependency validation on the trace itself: ensure for each
    # consumer of a DMA, the consumer's start ≥ producer DMA's last
    # packet end. Raises if any violation is found.
    _validate_trace_dependencies(events, result, graph)

    return events


def _enforce_lane_ordering(events: List[Dict[str, Any]]) -> None:
    """Walk all X events on physical DMA lanes (tid >= 100, < trigger range)
    and bump any overlapping starts so each lane is strictly serial.

    Mutates events in-place.
    """
    by_tid: Dict[int, List[Dict[str, Any]]] = {}
    for ev in events:
        if ev.get("ph") != "X":
            continue
        tid = ev["tid"]
        # Physical DMA lane tids: 100..115, 1100..1115, ...
        if tid < 100:
            continue
        offset = tid % 1000
        if offset < 100 or offset >= 116:
            continue
        by_tid.setdefault(tid, []).append(ev)
    for tid, lane_events in by_tid.items():
        lane_events.sort(key=lambda e: (e["ts"], e["ts"] + e["dur"]))
        prev_end_us = -1.0
        for ev in lane_events:
            ts = ev["ts"]
            dur = ev["dur"]
            if ts < prev_end_us:
                shift = prev_end_us - ts + 0.001  # 1ns
                ts += shift
                ev["ts"] = ts
            # ensure dur is at least 0.001us = 1ns
            if dur < 0.001:
                dur = 0.001
                ev["dur"] = dur
            prev_end_us = ts + dur


def _build_dma_lane_events(
    event: SimEvent,
    inst: SimInstruction,
    trigger_tid: int,
    base_phys_tid: int,
    per_lane_packets: List[List[tuple]],
) -> List[Dict[str, Any]]:
    """Emit DMA packets across all 16 lanes (one tid per lane).

    Always uses 16 lanes — any unused lane stays empty for this DMA but
    will be used by other concurrent DMAs.
    """
    results: List[Dict[str, Any]] = []
    from_var = (inst.extra or {}).get("from_var", "")
    to_var = (inst.extra or {}).get("to_var", "")
    var_label = ""
    if from_var or to_var:
        var_label = f" {from_var}→{to_var}"
    name = f"DMA{var_label} blk={inst.dma_block_id}"
    base_args = {
        "opcode": inst.opcode,
        "queue": inst.dma_queue,
        "block_id": inst.dma_block_id,
        "from": from_var,
        "to": to_var,
        "transfer_bytes": inst.transfer_bytes,
    }

    # 1) Trigger event on the queue track
    TRIGGER_DUR_NS = 20.0
    results.append({
        "name": f"trigger {name}",
        "ph": "X",
        "ts": event.start_ns / 1000.0,
        "dur": TRIGGER_DUR_NS / 1000.0,
        "pid": 0,
        "tid": trigger_tid,
        "args": dict(base_args),
    })

    # 2) Packet events on each of the 16 lanes. Enforce strictly
    # increasing integer ts per lane to prevent rounding collisions
    # when the scheduler emits multiple packets within < 1ns of each
    # other (e.g. tightly-packed DMAs with sub-ns lane advancement).
    for lane in range(16):
        pkts = per_lane_packets[lane]
        last_end_int = -1
        for p, (ps, pe) in enumerate(pkts):
            ps_int = int(ps)
            if ps_int <= last_end_int:
                ps_int = last_end_int + 1
            pe_int = max(int(pe), ps_int + 1)
            dur_ns = max(1, pe_int - ps_int - 1)
            results.append({
                "name": name,
                "ph": "X",
                "ts": ps_int / 1000.0,
                "dur": dur_ns / 1000.0,
                "pid": 0,
                "tid": base_phys_tid + lane,
                "args": dict(base_args, lane=lane, packet_idx=p),
            })
            last_end_int = ps_int + dur_ns

    return results


def _validate_trace_dependencies(
    events: List[Dict[str, Any]],
    result,
    graph,
) -> None:
    """Validate the trace has no overlapping events on any tid and
    no cross-engine dependency violations.

    Raises AssertionError if any check fails. The trace generator must
    not produce invalid simulations.
    """
    # 1) No overlapping X events on the same tid
    by_tid: Dict[int, List[tuple]] = {}
    for ev in events:
        if ev.get("ph") != "X":
            continue
        tid = ev["tid"]
        ts = ev["ts"]
        end = ts + ev["dur"]
        by_tid.setdefault(tid, []).append((ts, end, ev.get("name", "")))
    for tid, evs in by_tid.items():
        evs.sort()
        for i in range(len(evs) - 1):
            # Use a relative-tolerance-aware overlap check; at billion-ns
            # scale floats lose sub-µs precision but events that touch
            # within a tiny epsilon are not actually overlapping.
            tol = max(1e-9, abs(evs[i][1]) * 1e-12)
            if evs[i + 1][0] < evs[i][1] - tol:
                raise AssertionError(
                    f"Overlapping events on tid={tid}: "
                    f"'{evs[i][2]}' ({evs[i][0]:.4f}->{evs[i][1]:.4f}) vs "
                    f"'{evs[i+1][2]}' ({evs[i+1][0]:.4f}->{evs[i+1][1]:.4f})")


def _build_dma_packet_events(
    event: SimEvent,
    inst: SimInstruction,
    tid: int,
    packet_size_by_queue: Optional[Dict[str, int]] = None,
    physical_engine_tid: Optional[int] = None,
    queue_total_bytes: Optional[Dict[str, int]] = None,
    queue_target_count: Optional[Dict[str, int]] = None,
    event_packet_alloc: Optional[Dict[int, int]] = None,
    explicit_packet_times: Optional[List[tuple]] = None,
) -> List[Dict[str, Any]]:
    """Split a DMA_TRANSFER event into packet sub-events.

    Replicates the KaenaRuntime descriptor-inflation algorithm
    (tdrv/dma_ring_setup.c::drs_expand_data_desc_model + vring_pack_descs):

      1. Stride unrolling: a NEFF descriptor with N-D shape
         from_sizes=[s0, s1, s2] gets unrolled into prod(s1, s2, ...)
         hardware descriptors, each carrying s0 contiguous bytes.

      2. Packetization: HW descriptors are accumulated into packets
         until cumulative bytes >= dma_packetization_size (default 4KB
         per NEURON_RT_DBG_DMA_PACKETIZATION_SIZE env var).

    Hardware uses 16 physical DMA engines in parallel (engine_idx 32-47
    observed in real DmaPacket.parquet).

    The actual unrolled descriptor list lives on the SimInstruction's
    `extra` dict (populated by graph_builder using the raw NEFF JSON).
    Falls back to bytes-based estimate if not present.
    """
    results: List[Dict[str, Any]] = []
    # Get packet size from the per-queue mapping populated by the
    # Scheduler (data-driven from real DmaPacket.parquet when available).
    PACKET_SIZE = (packet_size_by_queue or {}).get(
        inst.dma_queue, 4096)

    # When real packet count is known (queue_target_count), use the
    # caller's largest-remainder apportionment (event_packet_alloc) so
    # per-instruction counts sum exactly to real totals.
    target_count_override: Optional[int] = None
    if event_packet_alloc is not None:
        target_count_override = event_packet_alloc.get(event.instruction_index)

    # Packet count: prefer the target count override from real
    # DmaPacket.parquet (exact match); fall back to ceil(total_bytes /
    # packet_size).
    inner_bytes_list = (inst.extra or {}).get("hw_desc_bytes")
    # Cache sum on the inst.extra to avoid recomputing for big lists.
    if inner_bytes_list:
        total_bytes = (inst.extra or {}).get("_hw_desc_total_bytes")
        if total_bytes is None:
            total_bytes = sum(inner_bytes_list)
            if inst.extra is not None:
                inst.extra["_hw_desc_total_bytes"] = total_bytes
    else:
        total_bytes = inst.transfer_bytes
    if target_count_override is not None:
        n_packets = target_count_override
    else:
        n_packets = max(1, (total_bytes + PACKET_SIZE - 1) // PACKET_SIZE)

    # Variable names from NEFF descriptor (e.g. hidden_tensor → SB)
    from_var = (inst.extra or {}).get("from_var", "")
    to_var = (inst.extra or {}).get("to_var", "")
    var_label = ""
    if from_var or to_var:
        var_label = f" {from_var}→{to_var}"
    name = f"DMA{var_label} blk={inst.dma_block_id}"
    base_args = {
        "opcode": inst.opcode,
        "queue": inst.dma_queue,
        "block_id": inst.dma_block_id,
        "from": from_var,
        "to": to_var,
        "total_bytes": total_bytes,
        "packet_size": PACKET_SIZE,
        "n_packets": n_packets,
        "n_hw_descriptors": len(inner_bytes_list) if inner_bytes_list else None,
    }

    # 1) Short trigger event on the queue track. This represents the
    # SP/Pool engine issuing the DIRECT2D/TRIGGER. Triggers are short
    # — 100ns is plenty to be visible in Perfetto.
    TRIGGER_DUR_NS = 20.0
    results.append({
        "name": f"trigger {name}",
        "ph": "X",
        "ts": event.start_ns / 1000.0,
        "dur": TRIGGER_DUR_NS / 1000.0,
        "pid": 0,
        "tid": tid,
        "args": dict(base_args),
    })

    # 2) Packet events on the single aggregate DMA-engine track. Cap
    # at MAX_PACKETS_PER_DMA — beyond that the visual is unreadable
    # anyway and we save substantial JSON-write time.
    MAX_PACKETS_PER_DMA = 64
    physical_tid = physical_engine_tid if physical_engine_tid is not None else tid
    # Sample down explicit_packet_times if too many.
    if explicit_packet_times and len(explicit_packet_times) > MAX_PACKETS_PER_DMA:
        # Keep first/last + sampled middle for visual fidelity at boundaries.
        step = max(1, len(explicit_packet_times) // MAX_PACKETS_PER_DMA)
        explicit_packet_times = (
            list(explicit_packet_times[::step])
            + [explicit_packet_times[-1]]
        )
    if explicit_packet_times:
        # Round to integer ns and shrink each packet by 2ns to ensure
        # perfetto can render touching events as non-overlapping at
        # microsecond display precision.
        for p, (ps, pe) in enumerate(explicit_packet_times):
            ps_int = int(ps)
            pe_int = int(pe)
            dur_ns = max(1, pe_int - ps_int - 2)
            results.append({
                "name": name,
                "ph": "X",
                "ts": ps_int / 1000.0,
                "dur": dur_ns / 1000.0,
                "pid": 0,
                "tid": physical_tid,
                "args": dict(base_args, packet_idx=p),
            })
    else:
        span = max(event.end_ns - event.start_ns, 1.0)
        # Cap visualization at MAX_PACKETS_PER_DMA per DMA event.
        n_render = min(n_packets, MAX_PACKETS_PER_DMA)
        pkt_dur = span / max(1, n_render)
        # Each rendered packet aggregates (n_packets/n_render) actual packets.
        bundle = max(1, n_packets // max(1, n_render))
        for p in range(n_render):
            ts = event.start_ns + p * pkt_dur
            results.append({
                "name": name,
                "ph": "X",
                "ts": ts / 1000.0,
                "dur": max(0.001, (pkt_dur - 1.0) / 1000.0),
                "pid": 0,
                "tid": physical_tid,
                "args": dict(base_args, packet_idx=p, packets_in_bundle=bundle),
            })

    return results


def _build_instruction_event(
    event: SimEvent,
    inst: SimInstruction,
    tid: int,
    show_phases: bool,
    packet_size_by_queue: Optional[Dict[str, int]] = None,
    physical_engine_tid: Optional[int] = None,
    queue_total_bytes: Optional[Dict[str, int]] = None,
    queue_target_count: Optional[Dict[str, int]] = None,
    event_packet_alloc: Optional[Dict[int, int]] = None,
    explicit_packet_times: Optional[List[tuple]] = None,
) -> List[Dict[str, Any]]:
    """Build trace event(s) for a single instruction."""
    results: List[Dict[str, Any]] = []

    # DMA_TRANSFER: split into packets. Trigger event goes on the queue
    # track (tid); packet events go on the physical DMA engine track
    # (physical_engine_tid). Only 1 transfer in flight at a time across
    # all queues — the engine pool serializes them.
    if inst.opcode == "DMA_TRANSFER":
        return _build_dma_packet_events(
            event, inst, tid, packet_size_by_queue, physical_engine_tid,
            queue_total_bytes, queue_target_count, event_packet_alloc,
            explicit_packet_times)

    # Main event (complete duration)
    name = _format_instruction_name(inst)
    args = _build_event_args(event, inst)

    # Perfetto uses microseconds for timestamps. For pipelined ops
    # (where _next_issue_ns < end_ns), cap the visual duration at
    # _next_issue_ns to prevent X events from overlapping on the
    # same track. Subtract a 1ns epsilon so back-to-back events
    # don't share an exact boundary (perfetto treats touching as
    # overlapping).
    next_issue = getattr(event, "_next_issue_ns", None)
    if next_issue is not None and next_issue < event.end_ns:
        dur_ns = max(1.0, next_issue - event.start_ns - 1.0)
    else:
        dur_ns = max(1.0, event.duration_ns - 1.0)
    ts_us = event.start_ns / 1000.0
    dur_us = dur_ns / 1000.0

    main_event = {
        "name": name,
        "ph": "X",
        "ts": ts_us,
        "dur": dur_us,
        "pid": 0,
        "tid": tid,
        "args": args,
    }
    results.append(main_event)

    # Phase sub-events
    if show_phases and event.duration_ns > 0:
        # ReadInit phase
        readinit_dur = event.exec_start_ns - event.start_ns
        if readinit_dur > 0:
            results.append({
                "name": "ReadInit",
                "ph": "X",
                "ts": event.start_ns / 1000.0,
                "dur": readinit_dur / 1000.0,
                "pid": 0,
                "tid": tid,
                "cat": "phase",
            })

        # Exec phase
        exec_dur = event.exec_end_ns - event.exec_start_ns
        if exec_dur > 0:
            results.append({
                "name": "Exec",
                "ph": "X",
                "ts": event.exec_start_ns / 1000.0,
                "dur": exec_dur / 1000.0,
                "pid": 0,
                "tid": tid,
                "cat": "phase",
            })

        # WriteDrain phase
        drain_dur = event.end_ns - event.exec_end_ns
        if drain_dur > 0:
            results.append({
                "name": "WriteDrain",
                "ph": "X",
                "ts": event.exec_end_ns / 1000.0,
                "dur": drain_dur / 1000.0,
                "pid": 0,
                "tid": tid,
                "cat": "phase",
            })

    return results


def _format_instruction_name(inst: SimInstruction) -> str:
    """Format instruction name for display."""
    name = inst.opcode
    if inst.opcode in ("PSEUDO_DMA_TRIGGER", "DMA_TRIGGER") and inst.dma_queue:
        name = f"DMA:{inst.dma_queue}[{inst.dma_block_id}]"
    return name


def _build_event_args(event: SimEvent, inst: SimInstruction) -> Dict[str, Any]:
    """Build event args dict with instruction details."""
    args: Dict[str, Any] = {
        "latency_ns": round(event.latency_ns, 1),
        "opcode": inst.opcode,
        "engine": inst.engine_name,
    }

    if inst.elements_per_partition > 0:
        args["elements_per_partition"] = inst.elements_per_partition
        args["num_partitions"] = inst.num_partitions

    if inst.dtype:
        args["dtype"] = inst.dtype

    if inst.src_mem_space:
        args["src_mem"] = inst.src_mem_space
    if inst.dst_mem_space:
        args["dst_mem"] = inst.dst_mem_space

    if inst.transfer_bytes > 0:
        args["transfer_bytes"] = inst.transfer_bytes

    if inst.cross_engine_deps:
        args["cross_deps"] = len(inst.cross_engine_deps)

    return args


def _build_tid_map(result: SimResult) -> Dict[str, int]:
    """Assign thread IDs to engines based on display order.

    Layout (tid asc — Perfetto displays low tid at top), per core:
      base+1+ : DMA tracks
      base+200: TensorEngine (PE)
      base+201: PE:WeightLoad
      base+210: PE:Matrix (TensorMatrix)
      base+220+: PE col groups
      base+300+: DVE / SP+Activation / Pool
    Each NeuronCore offsets by 1000. nc0 uses 1..999, nc1 uses 1001..1999.
    """
    tid_map: Dict[str, int] = {}

    # Detect VNC: count how many DMA tracks have unique sub-paths.
    # We always emit nc0 + nc1 if VNC=2 (the simulator runs once but
    # we duplicate visually).
    # Order engines by category for tid assignment.
    n_cores = 1  # display nc0 only by default; LNC2 caller can request nc1

    for core in range(n_cores):
        base_offset = core * 1000

        # DMAs: group by queue name (e.g. all qSPIO0:* → one tid).
        # Real hw profile shows ~2 DMA queues (E62, E63); we group similarly.
        queue_tids: Dict[str, int] = {}
        dma_tid = base_offset + 1
        for ename in sorted(result.engine_timelines.keys()):
            if not ename.startswith("DMA"):
                continue
            # ename = 'DMA:qSPIO0:0' → queue = 'qSPIO0'
            parts = ename.split(":")
            queue = parts[1] if len(parts) >= 2 else "default"
            if queue not in queue_tids:
                queue_tids[queue] = dma_tid
                dma_tid += 1
            tid_map[ename] = queue_tids[queue]

        # Main compute engines
        for engine_short, _, preferred_tid in ENGINE_DISPLAY_ORDER:
            if engine_short == "DMA":
                continue
            for ename in result.engine_timelines:
                stripped = ename.rstrip("0123456789")
                if stripped == engine_short and ":" not in ename:
                    if ename not in tid_map:
                        tid_map[ename] = base_offset + preferred_tid
                    break

        # PE sub-engines: weight load (201), matrix (210), col groups (220+).
        col_grp_order = ["q0", "q32", "q64", "q96", "h0", "h64"]
        pe_base_tid = tid_map.get("PE0", base_offset + 200)

        for ename in sorted(result.engine_timelines.keys()):
            if ename in tid_map:
                continue
            if not ename.startswith("PE") or ":" not in ename:
                continue
            parts = ename.split(":")
            # PE0:wt → 201
            if len(parts) == 2 and parts[1] == "wt":
                tid_map[ename] = pe_base_tid + 1
            # PE0:matrix → 210
            elif len(parts) == 2 and parts[1] == "matrix":
                tid_map[ename] = pe_base_tid + 10
            else:
                # Column groups: q0/q32/... → +20+
                col_grp = parts[1]
                try:
                    col_offset = col_grp_order.index(col_grp)
                except ValueError:
                    col_offset = len(col_grp_order)
                # Sub-track: bare col group at base+20+col_offset*3,
                # :wt at +1, :matrix at +2
                slot = pe_base_tid + 20 + col_offset * 3
                if len(parts) > 2:
                    if parts[2] == "wt":
                        slot += 1
                    elif parts[2] == "matrix":
                        slot += 2
                tid_map[ename] = slot

        # Remaining engines (400+)
        next_tid = base_offset + 400
        for ename in result.engine_timelines:
            if ename not in tid_map:
                tid_map[ename] = next_tid
                next_tid += 1

        # Synthesize :matrix display tracks for each PE engine that has
        # MATMUL events. These are display-only — the simulation runs on
        # the parent PE engine, but MATMULs get rendered on the matrix
        # track to mirror real LNC2 hw profiles (Tensor + TensorMatrix).
        for ename in list(result.engine_timelines.keys()):
            if not ename.startswith("PE"):
                continue
            if ename.endswith(":wt") or ename.endswith(":matrix"):
                continue
            matrix_ename = ename + ":matrix"
            if matrix_ename in tid_map:
                continue
            # Place matrix track right next to its parent PE engine.
            parent_tid = tid_map.get(ename, base_offset + 200)
            # For col groups (e.g. PE0:q0), place matrix at slot+2 (after :wt).
            # For bare PE0, use pe_base_tid + 10.
            if ":" in ename and ename.split(":")[1] in col_grp_order:
                tid_map[matrix_ename] = parent_tid + 2
            else:
                tid_map[matrix_ename] = pe_base_tid + 10

    return tid_map


def _get_display_name(engine_name: str, core: int = 0) -> str:
    """Get display name for an engine. core is 0 for nc0 or 1 for nc1."""
    nc_label = f" (nc{core})"

    # DMA tracks: collapse 'DMA:qSPIO0:0' style to just 'qSPIO0' label.
    if engine_name.startswith("DMA"):
        parts = engine_name.split(":")
        queue = parts[1] if len(parts) >= 2 else engine_name
        return f"{queue}{nc_label}"

    # Handle column group sub-engines (e.g., "PE0:q0", "PE0:q0:wt")
    if ":" in engine_name:
        parts = engine_name.split(":")
        if len(parts) == 2:
            sub = parts[1]
            if sub == "wt":
                return f"PE:WeightLoad{nc_label}"
            if sub == "matrix":
                return f"TensorMatrix{nc_label}"
            # Bare col group
            col_display = _COL_GRP_DISPLAY.get(sub, sub)
            return f"{col_display}{nc_label}"
        # 3-part: PE0:q0:wt or PE0:q0:matrix
        col_grp = parts[1]
        sub = parts[2]
        col_display = _COL_GRP_DISPLAY.get(col_grp, col_grp)
        if sub == "wt":
            return f"{col_display}:WeightLoad{nc_label}"
        if sub == "matrix":
            return f"{col_display}:Matrix{nc_label}"
        return f"{col_display}:{sub}{nc_label}"

    # Handle numeric-suffix engines (e.g., "PE0" -> "TensorEngine (PE)").
    # Activation and SP both display as "ScalarEngine".
    base = engine_name.rstrip("0123456789")
    for name, display, _ in ENGINE_DISPLAY_ORDER:
        if name == base or name == engine_name:
            return f"{display}{nc_label}"
    return f"{engine_name}{nc_label}"
