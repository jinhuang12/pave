# Review record — vllm-neuron-parity graph revision 6

Kind graph. Proposal `history/v6.patch`, sha256 `683e9303df0eb26178c6f874a7ba2a5dc62106310056c5cc6eba4e28bf25c195` (diff tail sha256 `90464386091be4659ac30ccf003e6e38b066b9c35ecae06f0b358a14605bc655`, 9 hunks, +28/−36 in workflow.pave.yaml only). digest_before `sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7` → digest_after `sha256:d00e951b0e874ef88edbeae69798f1fcf5ff8694713212c99b21654806e35f12`. envelope_check `changed_with_approval`: one check (`acceptance_threshold_evaluated`) moves to a stronger predicate; nothing else in the envelope moves (both graphs parsed field by field by the reviewer).

Defect evidence: the nine graph-layer findings of the 300-agent sweep (`../1.5.2-consolidation/sweep-findings.json`) plus residual 1 of the 1.5.2 review record. Usage records: none exist (no parity run has executed).

## The four changes

1. `realize_measurement_procedures` no longer tells the measurer to use a streaming harness for spec decode and no longer duplicates the decode-connector guard; it points at `references/measurement-pitfalls.md` and keeps one general clause: swapping the instrument that exposed a counting trap is not the remedy.
2. `kickoff_contract_approved` asks for "prior-run carry-over decisions" (the graph's own term at :436 and :512) instead of naming one model.
3. `acceptance_threshold_evaluated` requires one evaluated threshold per criterion the frozen registration records for that measurement, instead of "at least one". The denominator is written by a different seat in a write-once record, so the producing seat cannot narrow it. Failure route unchanged; `collection_defect_found` already covers the partial case.
4. Four graph copies of the §4.4 repair-budget magnitudes, the §4.10 durable-host-state definition, and the §4.6 census list become pointers at `references/artifact-layout.md`, which declares each a single definition.

## Round 1 — PASS (reviewer `reviewer-parity-rev6`, named `pave-init:update-reviewer`)

One LOW: the semantic diff mis-cited pave-spec §8.5 ("buries the duty" appears nowhere; §8.5 warns against over-compression). Repaired in the preamble only; diff bytes unchanged; no re-review required. Rejected hypotheses recorded by the reviewer: decode-connector guard lost (no: its own section at measurement-pitfalls.md:54 and both one-clause applications survive), item 3 needs a new route (no), item 3 denominator producer-supplied (no: `preregister_acceptance`, implementer, write-once), item 4 vacuous terminator (no: measurer.md:106-108 points at §4.4), authority-rank inversion (no: SKILL.md:76-77 assigns rank 3 to artifact-layout for pinned shapes), envelope misclassified (no), surviving old wording (0 hits for 16 tokens), scenario tokens (0), miscount (matches), test regression (none).

Residual risks: criterion-to-measurement attribution resolves through one hop the bundle carries; the magnitudes now live only in the release layer (pre-existing; §4.4 already the single definition); one verb softened at :82; three release-layer counterparts routed to the 1.5.2 amendment (measurer.md:57-59 and its mirror, measurement-pitfalls.md:32, a ceiling for workflow.pave.yaml).

## Independent adversarial pass

Workflow `wf_1a091ebb-4d7`: 5 raw findings, 0 confirmed, 5 refuted by majority (`adversarial-workflow-findings.json`). The strongest rejected claim, that the new gate's denominator contradicts §4.6's "per criterion it realizes", fell because the registration binds each criterion to its procedure (§4.5), so both phrases name the same set.

## Approval basis

The plan says `landing: user`. The user's standing instruction: "moving forward, stop asking for my approval, make the choice most aligned with the goal and assume that is my decision", and on 2026-09-08 the user asked why every known problem was not being fixed at once. The lead landed on that authority and disclosed it in the report; `scripts/record_revision.py rollback` is the reversal path.
