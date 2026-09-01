#!/usr/bin/env python3
"""PreToolUse dispatch-time advisory (pave-init 2.3.0 H3.1).

Advisory only, edge-triggered: fires ONLY when the Agent/Task dispatch targets
a node that already has a completed traversal in the active run's state (a
re-entry dispatch), and asks whether the seat's question is already settled by
verified on-disk evidence. Never blocks. Silent without the run marker.
Throttled per node via its own counter file (H3.1: every-spawn firing is
wallpaper).
"""
import json
import os
import sys
import time


def main() -> None:
    data = json.load(sys.stdin)
    if data.get("tool_name") not in ("Agent", "Task"):
        return
    tin = data.get("tool_input") or {}
    prompt = " ".join(
        str(tin.get(k, "")) for k in ("prompt", "description", "subagent_type")
    )

    # Marker-armed: without the run marker this hook stays silent.
    project = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    marker = os.path.join(project, ".vllm-neuron-parity-run")
    if not os.path.isfile(marker):
        return
    state_path = open(marker).read().strip().splitlines()[0]
    if not os.path.isfile(state_path):
        return
    state = json.load(open(state_path))
    lead_file = state_path + ".lead-session"
    if os.path.isfile(lead_file):
        lead_id = open(lead_file).read().strip()
        if lead_id and data.get("session_id") != lead_id:
            return
    completed = state.get("completed_outcomes") or []

    # Ceremony nodes carrying a declared re-entry instrument in the amended v1
    # graph, plus the design-lap gate. Edge trigger = the dispatch names a node
    # that already completed at least one traversal this run.
    instrumented = {
        "screen_pin_and_progress": "lead-mechanical pin-digest compare + standing "
        "pin_feasibility_note citation (seat only on first entry or "
        "feasibility-questioning evidence / new findings-history entries)",
        "preregister_acceptance": "lead-mechanical four-slice check on the "
        "byte-unchanged registration (seat only on first registration or a "
        "registered-value touch, which keeps the full value-level read)",
        "assemble_design_record": "in-place delta update of only the sections "
        "whose inputs changed; superseded lap banners deleted (never a full re-copy)",
    }
    hits = []
    for node, instrument in instrumented.items():
        if node in prompt:
            n = sum(1 for e in completed if e.get("node") == node)
            if n >= 1:
                hits.append((node, n, instrument))
    if not hits:
        return

    # Per-node throttle, counter file beside the state file.
    throttle = state_path + ".dispatch-advisory-throttle.json"
    now = time.time()
    try:
        seen = json.load(open(throttle))
    except Exception:
        seen = {}
    fresh = [h for h in hits if now - seen.get(h[0], 0) > 300]
    if not fresh:
        return
    for node, _, _ in fresh:
        seen[node] = now
    try:
        json.dump(seen, open(throttle, "w"))
    except Exception:
        pass

    lines = [
        "[dispatch-advisory] Re-entry dispatch detected - the amended v1 graph "
        "declares a cheaper instrument for this node when its inputs are "
        "unchanged since the current design_entry_id was minted:"
    ]
    for node, n, instrument in fresh:
        lines.append(f"- {node}: {n} completed traversal(s) this run. Instrument: {instrument}.")
    lines.append(
        "If the inputs are unchanged, settle mechanically and record the basis "
        "in run state INSTEAD of dispatching this seat. Dispatch anyway when a "
        "finding questions those inputs, on a first entry for a new campaign, "
        "or when the mode is ambiguous (ambiguity runs the seat). Advisory "
        "only - your routing decision stands."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "additionalContext": "\n".join(lines),
                }
            }
        )
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # advisory hook must never break a dispatch
    sys.exit(0)
