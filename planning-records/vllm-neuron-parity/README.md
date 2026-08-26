# Planning record — vllm-neuron-parity

The complete pave-init run workspace that produced
`generated-plugins/vllm-neuron-parity/` (run
`vllm-neuron-parity-2026-08-25`, delivered 2026-08-26). This is the
plugin's provenance: every gate, review round, and piece of evidence
from stated goal to delivered package. It is a record, not part of the
installable plugin — the marketplace installs only the plugin
directory.

## Reading order (the evidence chain)

```
goal-brief.md                 what the user asked for (v3, 24-decision
                              interview + 12-finding adversarial review)
requirements.md               the run's requirements record (UR/OF/A/OQ)
system-map.md + exploration/  what independent explorers verified
workflow.draft.pave.yaml      the approved v0 graph (authority)
traceability.md               graph object -> implementing file, 171 rows
skill-package-plan.md         the approved package plan
reviews/                      every gate: plan review, user approval
                              (verbatim), validation 10/10, final review
                              rounds, forward-test grade
run-state.json                128-entry traversal history, terminal
                              status "delivered"
build/ planning/ history/     lead working state (unit records, frontier)
```

## Provenance notes

- Original location: `NeuronAgenticDevelopment/.pave/vllm-neuron-parity/`
  (relocated here 2026-08-26 at the user's request, together with the
  plugin's move into this repo). Absolute paths inside these artifacts
  reflect the machine and layout at run time; they are part of the
  record and were not rewritten.
- `goal-brief.md` is a copy of
  `NeuronAgenticDevelopment/vllm-neuron-parity-goal-brief.md`, the
  source authority `requirements.md` cites. Post-approval, the approved
  bundle wins any wording conflict.
- `workflow-manifest.yaml` here records the v0 lineage for a later
  pave-init update run. Post-delivery, revision authority lives in the
  delivered plugin's own manifest
  (`generated-plugins/vllm-neuron-parity/workflow-manifest.yaml`).
- Nothing in this record is executed at plugin run time.
