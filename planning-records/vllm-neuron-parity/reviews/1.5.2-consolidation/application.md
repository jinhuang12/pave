# Apply the reviewed 1.5.2 proposal

Approval basis: the user's instruction "do 1.5.2", under the standing instruction to make goal-aligned choices without further approval prompts (the plan's `landing: user`). Push was not requested and did not happen.

Applied patch: `release-1.5.2.patch`, sha256 `4577a0b1dca5b88a26acb10959515819b955de7ec787d06f8a66577b03a0f9ae` (287 lines, 15 files, +78/−44). The named update-reviewer passed it in round 3 (record: `review-record.md`). Base: committed `2c1454c`; base file-set `sha256:3d01e408…`.

Release layer only. Graph revision 5 and its bundle digest `sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7` are unchanged; no ledger entry and no run migration.

## Landing recipe

The working tree carried 22 uncommitted files of the user's (codex model-token bindings, SKILL.md, README.md, test pin), which must not enter the commit and which break a plain `git apply` on the three codex TOML hunks. Landing therefore went through the index:

1. Precondition: index equals HEAD (`git diff --cached --quiet`), HEAD is `2c1454c`.
2. `git apply --cached release-1.5.2.patch` — the index now holds the patch on the clean base.
3. `git apply --exclude='*/codex/agents/*.toml' release-1.5.2.patch` — the twelve non-TOML files land in the working tree beside the user's edits.
4. For each of the three codex TOMLs, the HEAD→index change to the one-line `developer_instructions` value is transplanted into the working-tree line by escaped-line block substitution (each old block found exactly once); `tomllib` parses the result.
5. Proof: the index→working-tree diff after landing equals the HEAD→working-tree diff before landing, file by file (22 files; for the TOMLs compared inside the escaped value). So the working tree still carries exactly the user's edits and nothing of theirs is staged.
6. The index tree (`8d0867c84d8693bc755db972f6cde4f233bbd79a`) was exported with `git archive` and tested; then this record and the review bundle were staged and the index committed. No `git add` touched a user-edited file; no `-a`.

Tool: a throwaway script (`land_152.py`, kept outside the repository) whose full run is reproduced by the steps above; it was rehearsed first in a clone that mirrored the user's working tree.

## Evidence on the exported index tree

- File-set digest over the patch's scope — the plugin tree plus the repo-root `.claude-plugin/marketplace.json`, 55 files, repo-relative paths, newline-joined `<path> <sha256>` manifest, no trailing newline: `sha256:81f4e1b4cf97c62060b51dad86c7837899a133590594015c84ea266f5fa0005d`, identical to the reviewer's verified result tree and the updater's hand-back. All 15 changed-file hashes in `validation.md` §5 match the committed files. The committed plugin subtree is byte-identical to the tested index export.
- pytest: 42 passed. `tests/test_hooks.sh`: 31 passed, 0 failed. `validate_pave.py`: PASS, 32 nodes, 95 edges, 5 control endpoints. `record_revision.py verify`: PASS at revision 5, digest above.
- Both plugin manifests, VERSION, and the marketplace entry declare 1.5.2.
- `agents/measurer.md:34-37` carries the repaired clause; the two removed phrases have zero hits in the tree.

## Deferred

- Graph revision: `workflow.pave.yaml:2047-2050` (activity of `realize_measurement_procedures`) still carries the contradicted spec-decode guidance and the duplicated decode-connector guard; a release patch cannot touch the graph. Trigger: before the next spec-decode campaign briefs that node.
- 1.5.3: the census three-item list duplicated across `artifact-layout.md` §4.6 and `measurement-pitfalls.md`.
