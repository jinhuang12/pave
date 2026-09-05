# Request: make the parity guidance reusable

Prepare a reviewed proposal for vllm-neuron-parity at version 1.5.1. Improve the decisions future runs make without turning one campaign's remedies into universal requirements.

## User request and scope

The user asked: "Propose concrete changes that align with my intent & also aligns with [$pave-init:pave-evolve](/home/jinhun/.codex/plugins/cache/jinhuang12-plugins/pave-init/2.5.2/codex/skills/pave-evolve/SKILL.md) you know what i mean? Don’t increment the skill version, keep at 1.5.1 you get me?"

The preceding discussion established the intent: improve the skill's ability to learn reusable decision guidance, keep valid operational constraints, and avoid accumulating rules, checks, or documents for each incident. A successful fix is evidence for a possible lesson, not sufficient authority for a universal instruction.

This task prepares a proposal and independent review. It does not authorize landing, installation, a parity campaign, hardware access, or changes to pave-init. The source package currently declares 1.5.0; the proposal's requested version is 1.5.1, with no later increment. Do not overwrite the user's existing model-binding edits or other worktree changes.

## Verified base and authority

- Target package and delivered evolution root: `/home/jinhun/pave/generated-plugins/vllm-neuron-parity`.
- Live graph: `workflow.pave.yaml`; ledger: `revisions.yaml`; head: revision 5.
- Verified bundle digest: `sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7`.
- Verification: installed pave-init 2.5.2 `skills/pave-init/scripts/record_revision.py verify <target>` returned PASS in this session.
- PAVE installation: `/home/jinhun/.codex/plugins/cache/jinhuang12-plugins/pave-init/2.5.2`.
- Skill-quality authority: `/home/jinhun/.codex/skills/.system/skill-creator/SKILL.md`, especially the core principles.
- The target's current `skills/vllm-neuron-parity/SKILL.md`, Evolution contract, declares `landing: user` and `usage_ledger: kept`. The older `planning-records/vllm-neuron-parity/skill-package-plan.md` section 7 predates the append-only ledger and lacks those fields; disclose that history instead of treating it as current shape authority.
- The project-local `.vllm-neuron-parity` root is absent. The user deleted it intentionally in prior work. Do not recreate it. Review the delivered package; no active run is being migrated.

## Defect evidence from the current review

The release comparison is git `07158fd..54439f0`, scoped to the target package. The installed 1.5.0 graph and reference files match the release commit. Installed lead and Codex role files also carry later model-binding edits; retain them.

1. `references/toolchain-evidence-pitfalls.md:114-121` bans reducing instruction count to improve compile time and lowering parallelism to save memory. Its stated evidence names behavior of particular knobs and graph structures. A future run can reject a useful experiment without checking its effect. Preserve the need for evidence about composition and effective knob behavior; reassess the blanket prohibition.
2. `references/patch-mechanism-inventory.md:136` prescribes one floor-of-log2 scale recipe "in any low-bit encode" and one replacement for a literal constant tensor. The stated failures concern particular formats and tracing behavior. These instructions can select an implementation that does not fit a future representation. Preserve numerical and lowering constraints with conditions that delimit the remedy.
3. Review the other newly added reference duties for the same demonstrated scope defect. Examples worth testing, not assumed findings: requirements covering every compiler flag, every count, every kernel operation, every device-free probe, and a fixed timeout ordering. A change needs a concrete decision failure and a justified boundary; do not perform a general rewrite based on tone alone.
4. Dense grouped duty paragraphs and repeated mandates can obscure which condition activates which requirement. Inspect callers before restructuring. Use existing references and routing when sufficient; do not add a new learning system, standing ledger, role, graph node, or universal review gate.

The previous independent review confirmed items 1 and 2 as one material instruction-scope defect. It did not establish that the underlying compiler observations were false. No new hardware or compiler measurements were made.

## Improvements whose intent must survive

Revision 5 requires checker negative controls and evaluated-threshold records, scan-completeness evidence, and observed evidence for hardware refusals. These are reusable acceptance requirements. Preserve their strength, the three user gates, state authority, allowed and forbidden effects, and existing valid cache protections. Do not weaken them while removing overbroad reference instructions.

Specialization to Neuron is appropriate. Precise version-dependent facts can remain where they change a decision, with their applicability stated. Do not erase non-obvious constraints merely to reduce words. Do not replace precise requirements with generic advice that a capable agent already knows.

## Available usage and validation evidence

No raw predecessor parity usage ledger is available in this checkout. Available historical summaries are the target's `VERSION` entries 1.2.0 through 1.5.0 and `revisions.yaml` semantic diffs 1 through 5. They describe prior reductions in redundant design, preregistration, and scoping work. Read them and explain what they change in this proposal. They are summaries, not fresh campaign measurements; do not fabricate usage counts or claim new field validation.

The 1.5.0 lessons came from a sibling model-port campaign. The release's learning-count and provenance statements do not prove transfer to future parity campaigns.

Prior-turn checks: a clean archive of commit 54439f0 passed 42 pytest tests; the installed package passed 31 hook checks and graph/ledger validation. Installed pytest had 41 passes and one pre-existing path-name assumption failure: `tests/test_codex_port.py:51` compares the manifest name with the cache directory basename `1.5.0`. This test limitation is outside the learning-scope defect unless the proposal's validation requires addressing it.

## Requested hand-back

The workflow-updater owns the draft. Apply the PAVE layer test first. If only reference prose and release metadata change, say so and produce a release patch; do not invent a graph or binding revision to satisfy the proposal tool. If a graph or binding change is necessary, justify it and use revision 6 with the prescribed proposal format, without editing the live graph or ledger.

Write the reviewable proposal, exact patch, and validation outputs under this brief's directory. Use isolated scratch trees for edits and tests. Do not edit the target package, installed caches, or unrelated files.

Explain each proposed change as a decision it improves, its triggering condition, why it transfers, and its recurring cost. State what was removed or consolidated. Keep historical provenance in the release or review record rather than adding campaign recipes to runtime instructions.

Provide bounded behavioral validation where it adds confidence: a case where the old remedy applies and a case where it does not, judged by the resulting decision rather than text matching. This is proposal validation, not a new recurring campaign gate. Be explicit if no behavioral test was performed.

The independent update-reviewer must apply the exact patch to its own scratch copy, check scope and authority, verify every changed rule against its conditions, and return PASS or material findings. The final proposal must state classification, base and result digests, version 1.5.1, review round count, test limits, and landing requirements.
