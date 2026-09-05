# Validation output — release 1.5.2

The patch applies to HEAD `2c1454c`, reproduces the candidate tree byte for byte in a second clean archive, and leaves every check green with the graph digest unmoved.

Trees used: `/tmp/parity-152-updater` (drafted from `git archive 2c1454c`) and `/tmp/parity-152-verify` (a second clean archive with the patch applied — every check below ran there). The live working tree at `/home/jinhun/pave` was never written; the only non-scratch writes are the three files in this directory.

Round 2: the two round-1 findings were both inside the VERSION entry, so only the VERSION hunk moved. Round 3 answers finding A inside `agents/measurer.md`, so three files moved — that file, its codex mirror, and VERSION. Every output below is a re-run against the round-3 patch `sha256:4577a0b1…`, regenerated from a fresh `git archive 2c1454c`.

## 1. Patch application

```text
$ git -C /home/jinhun/pave apply --check --cached /home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.2-consolidation/release-1.5.2.patch
exit: 0

$ git -C /home/jinhun/pave status --porcelain | head -3      # index is clean at HEAD; the user's edits are unstaged
 M codex/README.md
 M codex/agents/pave_init_forward_tester.toml
 M codex/agents/pave_init_material_reviewer.toml

$ git -C /tmp/parity-152-verify apply release-1.5.2.patch     # second clean archive of 2c1454c
exit: 0

$ diff -r -x __pycache__ -x .pytest_cache /tmp/parity-152-verify /tmp/parity-152-updater
exit: 0    (identical — the patch reproduces the candidate exactly)
```

Landing hazard, recorded because the lead has to choose an order. The uncommitted model-binding edits (`gpt-5.6-sol` → `gpt-6-astra`) touch three files this patch touches, and in `codex/agents/vllm_neuron_parity_measurer.toml` they touch the same physical line 6:

```text
$ git -C /home/jinhun/pave apply --check release-1.5.2.patch          # against the DIRTY working tree
error: patch failed: …/codex/agents/vllm_neuron_parity_implementer.toml:3
error: …/codex/agents/vllm_neuron_parity_implementer.toml: patch does not apply
error: patch failed: …/codex/agents/vllm_neuron_parity_investigator.toml:3
error: …/codex/agents/vllm_neuron_parity_investigator.toml: patch does not apply
error: patch failed: …/codex/agents/vllm_neuron_parity_measurer.toml:3
error: …/codex/agents/vllm_neuron_parity_measurer.toml: patch does not apply
exit: 1

$ git -C /home/jinhun/pave apply --check --3way --cached release-1.5.2.patch
Applied patch to '.claude-plugin/marketplace.json' cleanly.
… (all 15 files) …
exit: 0
```

Cause: the three TOML hunks carry `model = "gpt-5.6-sol"` as context on line 3, and the measurer's line 6 carries both edits at once. Land the patch against a clean tree (or with `--cached`). If the binding edits land first, re-mirror the three TOML files instead of re-applying those hunks — the replacement is deterministic:

```python
# in each codex/agents/vllm_neuron_parity_<role>.toml, replace the escaped form
# (newline -> \n, " -> \") of the old md text with the new md text taken from
# agents/<role>.md after this patch. Verified counts: exactly 1 hit per pair.
esc = lambda s: s.replace('"', '\\"').replace('\n', '\\n')
raw = raw.replace(esc(old_md_block), esc(new_md_block))   # then tomllib.loads(raw) must parse
```

## 2. Package checks, in the patch-applied archive

```text
$ python3 -m pytest tests -q
..........................................                               [100%]
42 passed in 4.43s
exit: 0

$ bash tests/test_hooks.sh
PASS  reader: first campaigns/ .md write reminds
PASS  reader: 2nd write same session is silent
PASS  reader: 3rd write same session is silent
PASS  reader: 4th write same session reminds
PASS  reader: different session reminds immediately
PASS  reader: campaigns/*/attempts/ write is silent
PASS  reader: run/delta/index/ write is silent
PASS  reader: run/backlog/ write reminds
PASS  reader: .yaml write is silent
PASS  reader: no marker is silent
PASS  reader: terminal_classification.status set is silent
PASS  reader: <root>/README.md outside workspace is silent
PASS  reader: garbage stdin exits 0 with empty stdout
PASS  reader: Edit payload reminds
PASS  reader: marker via payload cwd reminds
PASS  reader: campaigns/index/design/ write reminds (campaign name not tested)
PASS  reader: over-cap document is named past the throttle
PASS  reader: over-cap notice fires once per session and file
PASS  reader: cap follows VLLM_NEURON_PARITY_CAP_LINES
PASS  guard: editing the live graph in an evolution root is denied
PASS  guard: a landing in progress passes
PASS  guard: a child graph beside the ledger is guarded too
PASS  guard: a .pave.yaml with no ledger beside it passes
PASS  guard: a non-graph path in the root passes
PASS  guard: editing the ledger itself is denied
PASS  guard: the ledger under a landing in progress passes
PASS  guard: creating a ledger where none exists passes
PASS  guard: a payload without file_path passes
PASS  guard: a subagent editing the live graph is denied too
PASS  guard: unparsable payload fails open
PASS  guard: registered in hooks/hooks.json under PreToolUse Edit|Write|MultiEdit

31 passed, 0 failed
exit: 0

$ python3 scripts/validate_pave.py workflow.pave.yaml
PASS workflow.pave.yaml: 32 nodes, 95 edges, 5 control endpoints
exit: 0

$ python3 scripts/record_revision.py verify .
PASS: . is intact at revision 5 (6 ledger entries) sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7
exit: 0

$ python3 -m pytest tests/test_document_ceilings.py tests/test_codex_port.py -q
..................                                                       [100%]
18 passed in 3.29s
exit: 0
```

The ceilings test passes with `CEILINGS` unedited — the patch touches no line of `tests/test_document_ceilings.py`.

Pre-existing failure, unchanged by this patch (release 1.5.1 recorded the same pair):

```text
$ python3 …/skill-creator/scripts/quick_validate.py skills/vllm-neuron-parity   # base 2c1454c
Description is too long (1096 characters). Maximum is 1024 characters.
exit: 1

$ python3 …/skill-creator/scripts/quick_validate.py skills/vllm-neuron-parity   # result
Description is too long (1096 characters). Maximum is 1024 characters.
exit: 1
```

## 3. Line counts before and after, per changed file

`wc -l` of `git show 2c1454c:<path>` against the patched tree. Digests are the first 12 hex of each file's sha256.

| File | base | result | Δ lines | Δ chars | sha256 base → result |
|---|---|---|---|---|---|
| `references/toolchain-evidence-pitfalls.md` | 367 | 368 | +1 | +94 | `6e5e803d6b90` → `8216660e42e1` |
| `references/patch-mechanism-inventory.md` | 160 | 160 | 0 | +103 | `f4d6a3a20bee` → `28cad0cbcc8a` |
| `references/artifact-layout.md` | 477 | 477 | 0 | −31 | `9d17b00a63c4` → `d5c07706f68e` |
| `agents/implementer.md` | 350 | 345 | −5 | −276 | `00e5ba62db45` → `bdc83f168826` |
| `agents/investigator.md` | 179 | 178 | −1 | −86 | `6e024640d563` → `bc1cdc9ebaa8` |
| `agents/measurer.md` | 171 | 169 | −2 | −198 | `affa6788a8cb` → `23a9b51c3346` |
| `skills/vllm-neuron-parity/SKILL.md` | 460 | 460 | 0 | −138 | `4b8e58d77a55` → `45aefd169be5` |
| `codex/agents/vllm_neuron_parity_implementer.toml` | 6 | 6 | 0 | −281 | `599670f1226b` → `7fef02d2aa6b` |
| `codex/agents/vllm_neuron_parity_investigator.toml` | 6 | 6 | 0 | −87 | `3bbc09fb14a8` → `0f0088495475` |
| `codex/agents/vllm_neuron_parity_measurer.toml` | 6 | 6 | 0 | −203 | `8141fed45b2a` → `df7576d42f98` |
| `VERSION` | 444 | 485 | +41 | +2467 | `ca798d98982d` → `cc9c55e36c19` |
| `.claude-plugin/plugin.json` | 9 | 9 | 0 | 0 | `9ed2871038fb` → `c2d64cd56bcd` |
| `.codex-plugin/plugin.json` | 32 | 32 | 0 | 0 | `2e0c63665779` → `cfe0335016f2` |
| `tests/test_codex_port.py` | 425 | 425 | 0 | 0 | `5b0ec3ffc49e` → `7ab6d85ee1a7` |
| `.claude-plugin/marketplace.json` (repo root) | 21 | 21 | 0 | 0 | `bfdf4ec45e8c` → `10d54296b054` |

- Ceiling-pinned runtime documents (the first seven rows): **−7 lines, −89 words, −532 characters** (21,546 → 21,457 words).
- Codex mirrors: 0 lines, −571 characters (one long line per file).
- All 15 files: **+34 lines**, and `VERSION`'s 41 history lines are the whole of that. See `proposal.md`, judgment 1.

Ceiling headroom after the patch, all unchanged caps: toolchain 368/400, inventory 160/180, layout 477/500, implementer 345/380, investigator 178/200, measurer 169/190, SKILL 460/500, README 652/700 (untouched).

## 4. Digests

```text
patch   sha256:4577a0b1dca5b88a26acb10959515819b955de7ec787d06f8a66577b03a0f9ae   (117470 bytes, 287 lines)
base    sha256:3d01e408c0d2a512cbc274c6caaef2ab045eeb81ad94e8152ae6fe81470fe038   (file set, 55 files)
result  sha256:81f4e1b4cf97c62060b51dad86c7837899a133590594015c84ea266f5fa0005d   (file set, 55 files)
graph   sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7   (revision 5, unmoved)
```

The file-set digest is sha256 over the newline-joined, path-sorted `<relative path> <file sha256>` manifest of every archived file, `__pycache__` and `.pytest_cache` excluded.

## 5. Round 2 → round 3: only three files moved

Result-file sha256 (first 12 hex) of the round-3 tree against the round-2 figures recorded in §3 above. Twelve of fifteen are byte-identical, which is the reviewer's acceptance criterion 1:

```text
same     .claude-plugin/marketplace.json         10d54296b054
same     .claude-plugin/plugin.json              c2d64cd56bcd
same     .codex-plugin/plugin.json               cfe0335016f2
same     agents/implementer.md                   bdc83f168826
same     agents/investigator.md                  bc1cdc9ebaa8
same     codex/…_implementer.toml                7fef02d2aa6b
same     codex/…_investigator.toml               0f0088495475
same     references/artifact-layout.md           d5c07706f68e
same     references/patch-mechanism-inventory.md 28cad0cbcc8a
same     references/toolchain-evidence-pitfalls.md 8216660e42e1
same     skills/vllm-neuron-parity/SKILL.md      45aefd169be5
same     tests/test_codex_port.py                7ab6d85ee1a7
CHANGED  agents/measurer.md         735aa8eb3f0e -> 23a9b51c3346   (finding A)
CHANGED  codex/…_measurer.toml      f4eb1909ac4c -> df7576d42f98   (mirror of finding A)
CHANGED  VERSION                    237356029a01 -> cc9c55e36c19   (findings A and B)
```

Working-tree isolation and mirror fidelity:

```text
$ /usr/bin/grep -c "gpt-6-astra" release-1.5.2.patch
0

$ /usr/bin/grep -c "^diff --git.*\(workflow.pave.yaml\|revisions.yaml\|hooks/\)" release-1.5.2.patch
0

$ python3 -c "check the measurer mirror"                       # in the patched archive
clean 'stock serving-benchmark path undercounts'
clean 'use a streaming harness for those'
clean 'end-to-end latency'
present 'chunk-derived throughput undercounts speculative decode in any harness'
present 'decode-only bench connector produces no correctness signal'
tomllib OK; mirror == md for the changed block
```

One survivor, outside this layer's reach:

```text
$ /usr/bin/grep -rln "stock serving-benchmark\|use a streaming harness" /tmp/parity-152-verify/
/tmp/parity-152-verify/generated-plugins/vllm-neuron-parity/workflow.pave.yaml
```

`workflow.pave.yaml:2047-2048` is an activity of `realize_measurement_procedures`. Editing it makes the change `kind: graph`, adds revision 6, and moves the digest this release must leave unmoved, so it stays and is disclosed in the VERSION entry. See `proposal.md`, judgment 5.
