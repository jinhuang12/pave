VERDICT: PASS
KIND: release/reference-only
REVIEW ROUND: 1

Material findings:
- None. The compile rule now permits instruction removal when it “reduces that stage's work” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/references/toolchain-evidence-pitfalls.md:109–110). The scale rule limits its recipe to “a format with that scale contract” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/references/patch-mechanism-inventory.md:144). These changes address the stated scope defect without changing acceptance.

Classification and envelope:
- This is outside the graph ledger. The patch changes reference methods and release metadata; its seven source paths are recorded in the independent census below. The tool copies only `graph_files(root)` in /tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/scripts/record_revision.py:286–290, and rejects “the patch changed no graph file; nothing to land” at line 330. No artificial graph or binding revision is needed.
- The declared envelope is “unchanged” (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/proposal.md:10). The independent byte comparison below confirms that the graph, ledger, lead, roles, schemas, and hooks remain intact. The existing graph still requires the procedure “FAILING on its registered tripwire input” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/workflow.pave.yaml:404–405) and an evaluated “threshold actually evaluated” (lines 334–337). These requirements remain stronger than syntax or isolated-probe evidence.
- The preamble identifies the actual base as the commit “plus the existing user edits” (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/proposal.md:6). All 54 base hashes matched live source. All 47 unpatched files remained byte-identical. The only changed existing test line expects “1.5.1” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/tests/test_codex_port.py:52). The user's model edits remain in the result.

Rejected hypotheses:
- **Lost import knowledge.** Resolved before verdict. The final reference retains “pins the XLA device to CPU,” “largest-sorted-last-dimension case,” and “a fused-softmax override does nothing without its own env flag” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/references/patch-mechanism-inventory.md:134). It also retains the functionalization, fast-math, all-reduce, first-import constant, lazy-override, and inherited-environment observations on that same line. The revised production condition still requires “match the runner's import order” (line 136).
- **A cheap probe can substitute for production proof.** Rejected. An isolated probe must state “which production behavior remains untested” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/references/patch-mechanism-inventory.md:138). Its caller still declares the venue “beside the deployed values” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/agents/implementer.md:30–32). The caller does not forbid an explicitly narrower claim.
- **Numerical remedies remain universal.** Rejected. Bounds must “preserve the producer's required range,” and signed sentinels require preserved “bit interpretation” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/references/patch-mechanism-inventory.md:142). Scale sharing follows the format (line 144). Boundary counting “fits an ordered-boundary lookup” (line 146). Checkpoint resolution now distinguishes “cache-backed checkpoints” from “a local checkpoint outside that cache” (line 148).
- **Evidence reuse weakens a gate.** Rejected. Kernel reuse requires the target “compiler, dtype, shape, and kernel context,” and “tracing alone does not establish backend support” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/references/toolchain-evidence-pitfalls.md:241–244). Flag reuse requires evidence for “that binary and path” (lines 57–64); delivery and pass execution still require confirmation from “the same log” (lines 85–90). The acceptance checks quoted above remain unchanged.
- **The patch adds another universal deadline order.** Rejected. It accounts for “when each clock starts” and orders bounds “only where those waits are nested” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/references/toolchain-evidence-pitfalls.md:273–277).
- **Generated cache content ships.** Resolved before verdict. The final independent header and file census below contains seven source paths, no binary hunk, and no new path. The final source bytes match the independently tested tree.

Cost and size:
- The independent count below gives +7 reference lines and −68 whitespace-separated words. The larger inventory separates conditions within its existing 180-line ceiling; the ceiling authority is /tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/tests/test_document_ceilings.py:34–36: measurement “200,” inventory “180,” toolchain “400.” Its tests passed. No ceiling increase is needed.
- Repeat syntax and lowering probes can be omitted only when their inputs match; the relevant clauses say “Reuse evidence for that binary and path” and “Reuse matching compile evidence” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/references/toolchain-evidence-pitfalls.md:58–59,243). This is consistent with the predecessor's reason that a repeated seat on “byte-unchanged inputs buys no evidence” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/revisions.yaml:86–87). It does not establish a measured time saving.
- Historical usage limits are explicit: “No raw predecessor parity usage ledger is available” (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/proposal.md:50). The revision-5 record also says “No usage record of a predecessor parity run is reachable” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/revisions.yaml:343). The proposal uses these summaries to avoid repeat work and does not retire any seat.
- The copy search and paragraph review found no new duplicate authority. The final old-clause search is recorded below. Dated VERSION text is explicitly bounded: “their remedy and learning counts are not current policy” (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/VERSION:28–29). The new VERSION claims correspond to the changed methods (/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity/VERSION:8–18).

Behavioral evidence:
- I read all ten paired responses against the supplied cases. The clearest demonstrated change is G: the baseline says the independent printed check “remains unmet” (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/baseline-case-responses.md:13); the candidate says “no second printed total is required” (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/candidate-case-responses.md:13). That matches the case's schema, code, and sample evidence (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/decision-cases.md:33: “producer code and a controlled sample agree”).
- The two passes also reach the same key choices in A, C, and E: both pilot B, keep format B's ceiling rule, and allow the isolated arithmetic probe (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/baseline-case-responses.md:1,5,9: “Pilot candidate B,” “retain its stated ceiling rule,” “A needs no runner setup change”; /home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/candidate-case-responses.md:1,5,9: “Pilot candidate B,” “retain its required ceiling scale,” “needs no production setup change”). Thus the exercise does not establish a broad improvement across cases.
- Both reject the broken acceptance instrument: “Do not proceed to acceptance” (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/baseline-case-responses.md:19 and /home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/candidate-case-responses.md:19). The candidate's reuse guidance did not waive those requirements in this exercise.

Residual risks:
- The exercise uses “synthetic cases” and is “not campaign evidence” (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/decision-cases.md:3). It proves neither hardware behavior nor campaign savings. The proposal makes the same limit explicit (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/proposal.md:63: “does not demonstrate improvement on all ten cases or campaign performance”).
- Skill-creator's description-length validator fails equally on untouched base and result. Independent outputs are below; the updater discloses the same failure at /home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/validation.md:66,70: “Description is too long (1096 characters). Maximum is 1024 characters.” This unchanged lead-description issue is outside this reference patch.
- Landing remains a separate user decision. The proposal requests “explicit user approval under `landing: user`” (/home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/proposal.md:65). PASS approves this proposal for hand-back; it does not authorize landing or installation.

Independent command record:

The following records are direct outputs from this reviewer’s scratch checks. The final patch was applied to `/tmp/parity-151-review-source-dm4i1g68/vllm-neuron-parity` after removal of the bytecode hunk. The source comparison against `/tmp/parity-151-review-final-vsz401ax/vllm-neuron-parity`, where the tests ran, returned no differences across all 54 manifest files.

```text
Patch SHA256: 02926c35f6fe08e90fdc1407f80bf128e090b021db444e7c9ce37e2961f4fad3
git apply --check release-1.5.1.patch: exit 0
git apply release-1.5.1.patch: exit 0
Base manifest mismatches: []
Result manifest mismatches: []
Differences from tested source: []
Unexpected new paths: []
Unchanged manifest files: 47
Changed source paths:
  .claude-plugin/plugin.json
  .codex-plugin/plugin.json
  VERSION
  references/measurement-pitfalls.md
  references/patch-mechanism-inventory.md
  references/toolchain-evidence-pitfalls.md
  tests/test_codex_port.py
Binary patch hunks: 0
Result file-set digest: sha256:0b514498dc8e8cebb6f48cf6be1251ff7b67fea45ecf30e66f9a1b345cc9cddf

python3 -m pytest -q tests
42 passed in 4.44s
exit 0

bash tests/test_hooks.sh
31 passed, 0 failed
exit 0

python3 scripts/validate_pave.py workflow.pave.yaml
PASS workflow.pave.yaml: 32 nodes, 95 edges, 5 control endpoints
exit 0

python3 scripts/record_revision.py verify .
PASS: . is intact at revision 5 (6 ledger entries) sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7
exit 0
The installed pave-init verifier also passed on the live base at that same revision and digest.

quick_validate.py, untouched live lead:
Description is too long (1096 characters). Maximum is 1024 characters.
exit 1
quick_validate.py, final applied lead:
Description is too long (1096 characters). Maximum is 1024 characters.
exit 1

Reference line and word counts, base -> result:
measurement-pitfalls.md: 158 -> 158 lines; 3491 -> 3491 words
patch-mechanism-inventory.md: 148 -> 160 lines; 2846 -> 2875 words
toolchain-evidence-pitfalls.md: 372 -> 367 lines; 3452 -> 3355 words

Old-clause search over current Markdown, TOML, YAML, Python, and JSON:
in any low-bit encode: []
construct every device-free probe: []
give every clamp: []
classify it device-free: []
Validate each candidate kernel operation: []
tracer admission below: []
confirm its role against a second: []
Do not attack compile time: []
```
