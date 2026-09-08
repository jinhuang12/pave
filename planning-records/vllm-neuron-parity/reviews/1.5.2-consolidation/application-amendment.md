# 1.5.2 amendment — application record

Status: review closed (three rounds, PASS; adversarial workflow findings
fixed). The landing step below ran against this record.

## What lands

- Patch: `release-1.5.2-amendment.patch`, sha256
  `825fb3fb407edfccc7866e144b2471451b3fecfe0cc0e20c55bca544707105bb`
  (1143 lines, 28 files, +427/−206). Earlier review rounds: `6ca89e6f`
  (draft), `2ac2ec90` (round 1, REVISE), `2b25520a` (round 2, PASS;
  adversarial workflow), `2a7fae79` (round 3, PASS with one LOW), `825fb3fb`
  (the LOW's one-word repair plus the word recount 251 → 250; the lead
  diffed the two result trees and found exactly those two lines).
- Base: repo commit `75a0775` (release 1.5.2 first commit `cf4fabb`, ledger
  revision 6 `f2e428d`, ledger hash record `75a0775`).
- Shape: a second commit under version 1.5.2. The VERSION entry is rewritten
  to describe the whole release. `cf4fabb` is not rewritten, because the
  ledger records revision 6's commit hash on top of it.

## Proof on a clean export of `75a0775` + patch

| Check | Result |
|---|---|
| `git apply --check --cached` against the index at 75a0775 | OK |
| `pytest -q tests` | 42 passed |
| `tests/test_hooks.sh` | 31 passed, 0 failed |
| `scripts/validate_pave.py workflow.pave.yaml` | PASS, 32 nodes, 95 edges, 5 control endpoints |
| `scripts/record_revision.py verify .` | PASS, revision 6, `sha256:d00e951b…35f12` |
| skill-creator `quick_validate.py` | valid; description 1010 chars |
| model-token hits in the plugin tree | 0 |
| `capture-class` survivors | VERSION changelog only |
| composition-block schema check in `validate_pave.py` | restored with the vendored `references/pave-composition.schema.json` |
| dangling `references/<file>` citations in hooks/ and agents/ | none inside the package; one citation scoped to pave-init |
| six codex mirrors vs `.md` twins (word diff after whitespace normalization) | only the 115-word Codex header and the harness substitutions |

## Result file-set digest convention

Manifest lines `<path> <sha256>` with paths relative to
`generated-plugins/vllm-neuron-parity`, plus `.claude-plugin/marketplace.json`,
sorted, newline-joined, no trailing newline, `__pycache__` excluded.
Result: `sha256:50de8bd0d7d23cd21926e47a354a28946dd336904b5c8a927d1b6cd1005d1990`
(57 files; the schema file is new). Base at 75a0775: `sha256:8dfa81eccf9fef3efc6c0785e31b5a62f2b78de8575c3575e2751b64fe3a0268`.
The 1.5.2 first-commit record (`application.md`) used repo-relative paths;
the two conventions differ only in the path prefix.

## Landing recipe (working tree carries 22 unrelated uncommitted files)

`land_release.py` (copied beside this record) lands the patch from a clean
index and merges the working tree without touching the user's edits:

1. Refuse unless the index equals HEAD.
2. `git apply --cached <patch>`.
3. Per patch file: `git apply --include=<file>` to the working tree. When a
   hunk fails because the same file carries a user edit, transplant the
   HEAD→index change into the working-tree copy: each changed block is
   located by exact match, widening with up to five lines of context until
   it matches once (pure insertions need at least one). Escaped one-line
   codex TOML values are split on `\n` first. Lines that carry both the
   patch and a user edit are merged token by token, one line at a time.
4. Proof: the index→worktree diff equals the HEAD→worktree diff recorded
   before landing, for every one of the 22 files (token-level compare).
5. Stage the review bundle, commit the index only.

Dry run in a clone of 75a0775 with the 22 user files copied in: 20 files
applied directly, 8 transplanted (README.md, six codex TOMLs, SKILL.md);
README lines 114 and 466 placed with one line of context, README rows 569-570
and SKILL.md line 101 merged token-level; proof held for all 22 files, and the
residual numstat per file equals the user's.

```
python3 planning-records/vllm-neuron-parity/reviews/1.5.2-consolidation/land_release.py \
  /home/jinhun/pave <patch> --commit <msgfile> --add <bundle files>
```

## Rollback

`git -C /home/jinhun/pave revert <amendment commit>` restores 75a0775
content for the 23 files. The user's uncommitted edits are unaffected either
way.
