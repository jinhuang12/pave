#!/usr/bin/env python3
"""PreToolUse dispatch-time advisory (pave-init 2.3.0 H3.1).

Advisory only, edge-triggered: fires ONLY when the Agent/Task dispatch targets
a node that already has a completed traversal in the active run's state (a
re-entry dispatch), and asks whether the seat's question is already decided by
verified on-disk evidence. Never blocks. Silent without the run marker.
Throttled per node via its own counter file (H3.1: every-spawn firing is
noise).
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

    # The three nodes whose re-entry the workflow decides more cheaply than with a
    # seat, named here (the graph declares no field for them). Edge trigger = the
    # dispatch names a node that already completed at least one traversal this run.
    check_tools = {
        "design_campaign": "lead-mechanical re-entry when the campaign_target_pins "
        "entry equals its source with the standing note cited, the registration is "
        "byte-unchanged with no finding naming a registered value, and the only "
        "change is a block diff you have read (seat on a registered-value touch, a "
        "screen-fact finding, a self-check gap, or ambiguity); superseded round "
        "material deleted",
        "scope_next_increment": "lead-decided from the persisted inputs, round "
        "record carrying its commands and outputs (implementer seat only for a "
        "contradiction candidate or a findings-history versus lap-record "
        "disagreement)",
        "review_campaign_design": "one challenger retained across this design "
        "entry's rounds - continue the same seat, delta-scoped read after a "
        "block-scoped repair; no reviewer seat at a design_loop_within_bound "
        "re-entry (the lead presents it to the user)",
    }
    hits = []
    for node, check_tool in check_tools.items():
        if node in prompt:
            n = sum(1 for e in completed if e.get("node") == node)
            if n >= 1:
                hits.append((node, n, check_tool))
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
        "[dispatch-advisory] Re-entry dispatch detected - this node has a cheaper "
        "re-entry check than a seat while its inputs are unchanged since the last "
        "completed traversal:"
    ]
    for node, n, check_tool in fresh:
        lines.append(f"- {node}: {n} completed traversal(s) this run. Re-entry check: {check_tool}.")
    lines.append(
        "If the inputs are unchanged, decide mechanically and record the basis "
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
