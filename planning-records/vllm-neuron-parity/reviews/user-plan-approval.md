# User plan approval — vllm-neuron-parity

- Date: 2026-08-26
- User response, VERBATIM: `approve plan`
- Node/outcome: approve_graph_plan.approved

## What the approval covers

The complete Stage-4 bundle as amended through run-state entry 108,
with R1's MEDIUM-4/LOW-14 prescribed fixes landed at approval time
(R1 ruled explicitly that neither blocks approval):

- requirements.md (incl. kernel-substrate amendment, user-directed
  "option 2, add nki rule")
- system-map.md
- workflow.draft.pave.yaml (31 nodes / 89 edges, validate PASS;
  f8 sweep 90 presence + 16 count + 12 negative, ALL PRESENT)
- traceability.md (rederiver fable/xhigh pin)
- skill-package-plan.md (13 prohibitions P1-P13; §6 default-recovery
  lead duty; §7 evolution contract at pave-revisions 2.2.9 rule-4
  wording; rederiver fable/xhigh; teammate dispatch mechanics)
- build/enforcement-record.md, build/artifact-layout-reference.md
- reviews/plan-brief.md (rendered in full in conversation; color-coded
  at user direction; §4/§5 amended rows)

## Pre-approval user amendments folded into the approved bundle

1. Diagram color coding (agent-seat fills, dashed recovery edges).
2. Kernel-substrate rule (NKI-only for new kernel-class work), split
   rung, HIGH-4/MEDIUM-2/LOW-11/LOW-12 hardening — both reviewers
   closed.
3. Lead carries pave-spec §9.8 default-recovery loop verbatim
   (MEDIUM-3 fidelity fix closed by R1).
4. Evolution successor rule 4 at 2.2.9 wording (generality
   justification; Unit-1 obligation).
5. Rederiver pinned fable/xhigh (supersedes R1 HIGH-1 on new user
   information; retry-identically + three-failure operator pause).
6. Per-node primary seats dispatched as named teammates; authority
   unchanged (MEDIUM-4 guardrail-scope widening landed).

## Context surfaced to the user before approval (R1)

"because `nki/` was gutted, the plugin-side NKI surface is nearly
empty, so more work qualifies as 'functionality the existing NKI
library does not already provide' than the rule's wording suggests.
The rule will bite harder than it reads." — surfaced 2026-08-26,
acknowledged by the user continuing to approval.

The draft is hereby designated v0. Stage 5 (build) begins from
skill-package-plan.md §6's four build units.
