# Request: release 1.5.2 of vllm-neuron-parity, a net-negative consolidation

Restore two facts 1.5.1 dropped, fix one dangling pronoun, and collapse the restatements 1.5.0 introduced to bare pointers. The patch must remove more lines than it adds and change no graph, ledger, hook, test logic, seat, model, or effort.

## Authority and base

- Requester: the campaign lead (session dsv4-port). User instruction, verbatim, 2026-09-05: "do 1.5.2". Standing user instruction (Amendment 7): "stop asking for my approval, make the choice most aligned with the goal and assume that is my decision". Under `landing: user`, those two statements are the approval; the lead lands after the reviewer passes.
- pave-init installation: `/home/jinhun/pave` (skill dir `/home/jinhun/pave/skills/pave-init/`, agents `/home/jinhun/pave/agents/`).
- Evolution root and package: `/home/jinhun/pave/generated-plugins/vllm-neuron-parity`. Ledger head: revision 5, digest `sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7`. `scripts/record_revision.py verify` passes at HEAD.
- Base for the draft: committed HEAD `2c1454c` (release 1.5.1). The working tree carries the user's uncommitted model-binding edits (gpt-5.6-sol to gpt-6-astra in SKILL.md, README.md, the six codex TOML files, tests/test_codex_port.py). Draft against `git -C /home/jinhun/pave archive 2c1454c generated-plugins/vllm-neuron-parity | tar -x -C <scratch>`, never against the working tree, and never edit the live package or the working tree.
- Layer: release (reference and role-contract prose, VERSION, manifests). No graph or binding hunk; no revision 6. Mechanical proof is exact patch application to the archive, the package tests, and unchanged graph verification, as release 1.5.1 did (`planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/proposal.md`).

## Defect evidence

Lead assessment with the adversarial workflow's confirmed findings: `/home/jinhun/NeuronAgenticDevelopment/.vllm-neuron-port/dsv4-flash-trn2-20260818/artifacts/reference/learnings-for-parity-20260904/ASSESSMENT-1.5.1-and-refactor-first.md`. Field evidence by L-id: `.../learnings-for-parity-20260904/LEARNINGS-RECORD.md`. Line numbers below are at HEAD 2c1454c.

Restorations (1.5.1 dropped these without a defense in VERSION):
1. `references/toolchain-evidence-pitfalls.md:100-104` compile-cost Trap. 1.5.0 carried "the front end is a few percent of a good compile and a fraction of a percent of a failing one". L-036 (LEARNINGS-RECORD.md:631) gives about 5% of a good compile and under 0.5% of a failing one, still true at SDK 2.32. Restore as one clause of the Trap, as a measured observation at the cited pin.
2. `references/patch-mechanism-inventory.md:136` "Set import-read overrides before import". 1.5.0 carried the consequence: a late override freezes a platform constant at the wrong value and corrupts numerics silently (L-341). Restore the consequence in one clause.
3. `references/toolchain-evidence-pitfalls.md:8` "Read it before you credit..." lost its antecedent when 1.5.1 rewrote the header. Fix the referent.

Consolidations (1.5.0 restatements; the updater contract's equivalence rule applies: a multi-clause restatement in a file whose reader resolves the authority becomes a pointer; a one-clause application in a file whose reader never loads the authority stays):
4. `references/artifact-layout.md:394-397` in §4.10 restates the throwaway-worktree duty that §4.6 (:293-297) states with its reason, and cites §4.6 anyway. Collapse the §4.10 copy to a pointer.
5. `agents/implementer.md:217-221` restates three theses of `references/toolchain-evidence-pitfalls.md` (sections at :262, :288, :342) in the sentence that tells the reader to load that file. Reduce to the pointer plus at most one clause.
6. `agents/implementer.md:29-34` restates the venue rule of `references/patch-mechanism-inventory.md` ("Import time pins the venue") beside its pointer. Judge under the rule; the acceptance-command clause itself is the implementer's own duty and stays.
7. `agents/investigator.md:79-83` restates two theses (compile cost; second-hand claims) beside the pointer. Reduce to the pointer plus at most one clause; the surviving clause must not read stronger than the reference now does (1.5.1 made instruction removal conditional).
8. `agents/measurer.md:38-42` restates two `references/measurement-pitfalls.md` theses (tripwire proof; firing control) beside its pointer. Reduce to the pointer plus at most one clause.
9. `skills/vllm-neuron-parity/SKILL.md:224` stage-7 row restates the same three toolchain theses as item 5. The lead briefs seats with the file and may not load it; judge under the rule and keep the row's brief instruction.
10. Codex mirrors: every change to `agents/{implementer,investigator,measurer}.md` is mirrored into `codex/agents/vllm_neuron_parity_{implementer,investigator,measurer}.toml` `developer_instructions`, which is one long line per file. The mirror test (`tests/test_codex_port.py:99-121`) checks markers, not byte equality, so the mirror is your duty, not the test's.

Do not re-litigate 1.5.1's scoping. The refuters accepted the parse-text decode table drop and the "re-keys every cached graph" relocation as defended. The "Credit a flag" prefix quote is cosmetic; leave it unless the fix is one word.

## Release metadata

- `VERSION`: add a 1.5.2 entry at the top in the existing style (plain english, what changed and why, what was removed, the net line count, no ceiling raised). Set `version: 1.5.2`.
- `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json` in the package: 1.5.2. Repo root `.claude-plugin/marketplace.json`: this plugin's entry to 1.5.2 (the lead applies that one at landing; include it in the patch with its repo-relative path).
- `tests/test_codex_port.py:52` expected version string to 1.5.2.

## Usage evidence

No raw predecessor parity usage ledger exists in this checkout; the historical summaries are VERSION 1.2.0 through 1.5.1 and `revisions.yaml` semantic diffs 1 through 5. Say what they change in this proposal or why nothing changes.

## Hand-back

Write under this brief's directory: `proposal.md` (kind, base and result digests, the per-change table: decision improved, what was cut, net lines per file, every-copy dispositions), `release-1.5.2.patch` (unified diff with `a/generated-plugins/...` and `a/.claude-plugin/marketplace.json` paths so `git -C /home/jinhun/pave apply --check` passes against HEAD 2c1454c), `validation.md` (pasted outputs: `git apply --check`, `python3 -m pytest tests -q`, `bash tests/test_hooks.sh`, `python3 scripts/validate_pave.py workflow.pave.yaml`, `python3 scripts/record_revision.py verify .`, line counts before and after per changed file). Report the outcome as `draft_ready`, `envelope_exceeded`, or `no_change_warranted`.

Constraints: read-only outside your scratch directory and this brief's directory. Never read `/home/jinhun/.claude/secrets` or any file whose name contains key or token. Do not touch `/home/ubuntu`. Use `/usr/bin/grep`. Do not run `git checkout`, `stash`, `commit`, or `reset` in `/home/jinhun/pave`. Every document a person reads follows the write-for-the-reader duty: concise plain english, one lead sentence.
