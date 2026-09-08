# Validation: amendment to release 1.5.2 of vllm-neuron-parity

The patch applies to a fresh `75a0775` export and to the live index, leaves every check green, leaves the graph intact at ledger revision 6, and makes the packaged skill pass the validator it failed before. Read §6 first if you are landing it: the working tree is dirty, and 8 files need the substitution recipe there instead of the hunk.

## 1. Mechanicals, as run

Every command below ran against a clean `git archive 75a0775` export in `/tmp/upd152b/verify9` with the patch applied. Nothing in `/home/jinhun/pave` was edited, staged, or committed; the only command that touched it is the read-only `apply --check --cached`.

```text
$ git -C /home/jinhun/pave archive 75a0775 | tar -x -C /tmp/upd152b/verify9
$ cd /tmp/upd152b/verify9
$ git apply --check release-1.5.2-amendment.patch; echo exit=$?
exit=0
$ git apply release-1.5.2-amendment.patch; echo exit=$?
exit=0

$ git -C /home/jinhun/pave apply --check --cached release-1.5.2-amendment.patch   # index at 75a0775
exit=0

$ cd generated-plugins/vllm-neuron-parity
$ python3 -m pytest tests -q
..........................................                               [100%]
42 passed in 4.43s

$ bash tests/test_hooks.sh | tail -1
31 passed, 0 failed

$ python3 scripts/validate_pave.py workflow.pave.yaml; echo exit=$?
PASS workflow.pave.yaml: 32 nodes, 95 edges, 5 control endpoints
exit=0

$ python3 scripts/record_revision.py verify .; echo exit=$?
PASS: . is intact at revision 6 (7 ledger entries) sha256:d00e951b0e874ef88edbeae69798f1fcf5ff8694713212c99b21654806e35f12
exit=0

$ python3 quick_validate.py <base 75a0775>/skills/vllm-neuron-parity; echo exit=$?
Description is too long (1096 characters). Maximum is 1024 characters.
exit=1

$ python3 quick_validate.py <patched>/skills/vllm-neuron-parity; echo exit=$?
Skill is valid!
exit=0
   (measured description length: 1096 base -> 1007 patched)

$ /usr/bin/grep -c gpt-6-astra release-1.5.2-amendment.patch
0
   (grep exit=1 -- no match)

$ git diff --cached --shortstat        # the staged amendment, from which the patch is cut
 28 files changed, 427 insertions(+), 206 deletions(-)
```

## 2. Sizes and the ratchet

Base is `75a0775` (the ledger head after revision 6 landed); "now" is the patched tree. No existing ceiling is raised. One ceiling is added — `workflow.pave.yaml`, measured at 2803 and pinned at 3100.

| file | base | now | cap | headroom | touched |
|---|---|---|---|---|---|
| `README.md` | 652 | 660 | 700 | 40 | yes |
| `skills/vllm-neuron-parity/SKILL.md` | 460 | 455 | 500 | 45 | yes |
| `references/artifact-layout.md` | 477 | 481 | 500 | 19 | yes |
| `references/collision-ranking.md` | 133 | 132 | 160 | 28 | yes |
| `references/measurement-pitfalls.md` | 158 | 158 | 200 | 42 | yes |
| `references/patch-mechanism-inventory.md` | 160 | 160 | 180 | 20 | yes |
| `references/toolchain-evidence-pitfalls.md` | 368 | 371 | 400 | 29 | yes |
| `agents/adjudicator.md` | 132 | 129 | 150 | 21 | yes |
| `agents/adversarial-reviewer.md` | 218 | 215 | 240 | 25 | yes |
| `agents/implementer.md` | 345 | 344 | 380 | 36 | yes |
| `agents/investigator.md` | 178 | 169 | 200 | 31 | yes |
| `agents/measurer.md` | 169 | 166 | 190 | 24 | yes |
| `agents/rederiver.md` | 146 | 143 | 165 | 22 | yes |
| `workflow.pave.yaml` | 2803 | 2803 | 3100 | 297 | no |

Totals: pinned lines 6399 → 6386. Across the 13 touched pinned documents: -13 lines, -250 words. `references/pave-composition.schema.json` is new and uncapped: the ratchet globs `references/*.md`, and a vendored schema is machine input, not prose a reader works from.

## 3. Codex mirror parity

Stronger than the shipped marker test, and stronger than the previous round of this amendment: every mirror's `developer_instructions` is now rebuilt from its Markdown twin, so after the four harness substitutions each twin's body is **byte-exact** inside its mirror, not merely contained under whitespace normalization. The only other text in any mirror is the 115-word Codex header (`words before` = 115, `words after` = 0). Zero forbidden tokens, and `tomllib` parses all six.

| mirror | lines | tomllib | twin match | header words before | words after | forbidden tokens |
|---|---|---|---|---|---|---|
| `vllm_neuron_parity_adjudicator.toml` | 6 | OK | byte-exact | 115 | 0 | none |
| `vllm_neuron_parity_adversarial_reviewer.toml` | 6 | OK | byte-exact | 115 | 0 | none |
| `vllm_neuron_parity_implementer.toml` | 6 | OK | byte-exact | 115 | 0 | none |
| `vllm_neuron_parity_investigator.toml` | 6 | OK | byte-exact | 115 | 0 | none |
| `vllm_neuron_parity_measurer.toml` | 6 | OK | byte-exact | 115 | 0 | none |
| `vllm_neuron_parity_rederiver.toml` | 6 | OK | byte-exact | 115 | 0 | none |

Forbidden set: `SendMessage`, `named teammate`, ` opus`, ` fable`, ` sonnet`, `Re-entry instrument:`, `narrow_delta_scoped`, `gpt-6-astra`.

## 4. Every changed file

| file | lines base → now | words Δ | sha256 (first 12) base → now |
|---|---|---|---|
| `README.md` | 652 → 660 | +66 | bab57fa3041a → 354622a88848 |
| `VERSION` | 485 → 592 | +1266 | cc9c55e36c19 → 896b217c1db8 |
| `agents/adjudicator.md` | 132 → 129 | -49 | 229af31bbcdb → 0995bf12b213 |
| `agents/adversarial-reviewer.md` | 218 → 215 | -51 | 3e32c16ae7f5 → 52ddb7fb813f |
| `agents/implementer.md` | 345 → 344 | -42 | bdc83f168826 → a153ccbe6b71 |
| `agents/investigator.md` | 178 → 169 | -98 | bc1cdc9ebaa8 → 551a61c0f706 |
| `agents/measurer.md` | 169 → 166 | -28 | 23a9b51c3346 → 5b90352e567c |
| `agents/rederiver.md` | 146 → 143 | -49 | 04df8b7ddb00 → 519fe2e8dedc |
| `codex/agents/vllm_neuron_parity_adjudicator.toml` | 6 → 6 | -46 | c9487eab4258 → 86e4df1de058 |
| `codex/agents/vllm_neuron_parity_adversarial_reviewer.toml` | 6 → 6 | -105 | 843c715b83bc → 13d589f9435e |
| `codex/agents/vllm_neuron_parity_implementer.toml` | 6 → 6 | -154 | 7fef02d2aa6b → a3897e6d7b92 |
| `codex/agents/vllm_neuron_parity_investigator.toml` | 6 → 6 | -129 | 0f0088495475 → 919c625063ec |
| `codex/agents/vllm_neuron_parity_measurer.toml` | 6 → 6 | -28 | df7576d42f98 → e6e1711dbd2e |
| `codex/agents/vllm_neuron_parity_rederiver.toml` | 6 → 6 | -46 | 227ba2e7b4ec → 8d221e2467f5 |
| `references/artifact-layout.md` | 477 → 481 | +31 | d5c07706f68e → 8c91e7fc627a |
| `references/collision-ranking.md` | 133 → 132 | -5 | f95e8a1ad619 → a4fc82e485b1 |
| `references/measurement-pitfalls.md` | 158 → 158 | -12 | 51222854f371 → df398bada9e4 |
| `references/patch-mechanism-inventory.md` | 160 → 160 | +14 | 28cad0cbcc8a → 771b47d97229 |
| `references/pave-composition.schema.json` | new, 107 | +224 | — → 672e6963b41c |
| `references/toolchain-evidence-pitfalls.md` | 368 → 371 | +22 | 8216660e42e1 → a1ba0b821911 |
| `scripts/validate_pave.py` | 470 → 471 | +11 | 810b30790bfa → e2bcf64741f2 |
| `skills/vllm-neuron-parity/SKILL.md` | 460 → 455 | -49 | 45aefd169be5 → f336cca9d66e |
| `skills/vllm-neuron-parity/hooks/compile-cache-guard.sh` | 156 → 163 | +69 | 924abf0225d5 → 878675ca9e77 |
| `skills/vllm-neuron-parity/hooks/protected-branch-guard.sh` | 195 → 196 | +6 | a7a1fd18a897 → 9b572755e32d |
| `skills/vllm-neuron-parity/hooks/venv-opt-guard.sh` | 150 → 151 | +5 | a8d6f38b535e → 52ab27c000cf |
| `skills/vllm-neuron-parity/hooks/write-for-reader.sh` | 235 → 235 | -7 | c917d09938bc → 829355591a2b |
| `tests/test_codex_port.py` | 425 → 425 | +16 | 7ab6d85ee1a7 → b33a556d8ae1 |
| `tests/test_document_ceilings.py` | 85 → 95 | +33 | 74437cfe7fbd → 7f817b4a4ac0 |

One file is added (`references/pave-composition.schema.json`, vendored from pave-init's copy so the checker `scripts/validate_pave.py` advertises can run); nothing is deleted. The file set goes from 56 to 57 entries.

## 5. Digests

```text
patch    sha256:825fb3fb407edfccc7866e144b2471451b3fecfe0cc0e20c55bca544707105bb   (1143 lines, 231211 bytes, 28 files, +427/-206)
base     sha256:8dfa81eccf9fef3efc6c0785e31b5a62f2b78de8575c3575e2751b64fe3a0268   (file set, 56 files, 75a0775)
result   sha256:50de8bd0d7d23cd21926e47a354a28946dd336904b5c8a927d1b6cd1005d1990   (file set, 57 files)
graph    sha256:d00e951b0e874ef88edbeae69798f1fcf5ff8694713212c99b21654806e35f12   (ledger revision 6, unmoved by this patch)
```

File-set digest recipe, unchanged from the earlier rounds: sha256 over the newline-joined, path-sorted `<plugin-relative path> <file sha256>` manifest of every archived file in the plugin tree plus the repo-root `.claude-plugin/marketplace.json`, `__pycache__` and `.pytest_cache` excluded, no trailing newline.

## 6. Landing hazard: the patch does not apply to the dirty working tree

`git apply --check --cached` passes against the index at `75a0775`. `git apply --check` against the **working tree** fails, because the uncommitted local files carry a model-token edit. Measured by `git apply --reject` against a copy of the live tree: 9 hunks in 8 files are rejected, and every other hunk applies clean.

| file | rejected hunks | hunks that apply clean |
|---|---|---|
| `README.md` | 1 of 6 | 5 |
| `codex/agents/vllm_neuron_parity_adjudicator.toml` | 1 of 1 | 0 |
| `codex/agents/vllm_neuron_parity_adversarial_reviewer.toml` | 1 of 1 | 0 |
| `codex/agents/vllm_neuron_parity_implementer.toml` | 1 of 1 | 0 |
| `codex/agents/vllm_neuron_parity_investigator.toml` | 1 of 1 | 0 |
| `codex/agents/vllm_neuron_parity_measurer.toml` | 1 of 1 | 0 |
| `codex/agents/vllm_neuron_parity_rederiver.toml` | 1 of 1 | 0 |
| `skills/vllm-neuron-parity/SKILL.md` | 2 of 6 | 4 |

Land it this way:

1. This lands as a second commit under version 1.5.2 on top of `75a0775`; rewriting `cf4fabb` is off the table because the ledger records revision 6's commit hash. Apply the patch to the index from clean (`git apply --cached`), or apply it in a clean worktree and merge — never `git checkout` over the user's edits.
2. Never transplant a whole line into the dirty tree. Every rejected hunk changes a line whose live copy carries the user's token, or sits within three lines of one. Apply each as a one-hit text substitution instead; the fragments below contain no model token, so they compose with the rename whatever it settles on.
3. `README.md` — three substitutions, one hit each. A blank `new` means delete the `old` text, its leading space included.

```text
old:   (Codex); fable (Claude, `agents/adjudicator.md`)
new:

old:   (Codex); fable (Claude, `agents/adversarial-reviewer.md`)
new:

old:  Claude judges bind fable (a runtime-binding change recorded by this release,
new:  Claude judges bind the top Claude model, as the rederiver does for the reason
      its own contract gives (a runtime-binding change recorded by release 1.4.0,
```

4. `skills/vllm-neuron-parity/SKILL.md` — five substitutions, one hit each. The first two are on the implementer dispatch row, the third on the lead row. The fifth changes no word: it expands the citation and re-wraps, keeping "Never dispatch this node to" so the following line — one the user's tree also edits — stays byte-identical. (In patch `2a7fae79` that site also swapped "this node" for "it"; round 3 found the antecedent ambiguous beside "disclaims it", so the noun is restored and the site is now a citation change plus whitespace. The line runs to 94 columns, 7 past the base's, because the alternatives are splitting the user-held line below it or leaving a 21-column orphan.)

```text
old:  preregister_acceptance and the capture-class activities
new:  preregister_acceptance

old:  recover_leased_host, prepare_pr | `vllm-neuron-parity:implementer`
new:  recover_leased_host, prepare_pr, close_campaign (execution half) | `vllm-neuron-parity:implementer`

old:  close_campaign (gate halves)
new:  close_campaign (the gate half only — the implementer executes the approved closure)

old:  `agents/adversarial-reviewer.md` and `agents/adjudicator.md` bind fable under
      this rule; the Codex table above already satisfies it.
new:  `agents/adversarial-reviewer.md` and `agents/adjudicator.md` bind the top
      Claude model under this rule, as `agents/rederiver.md` does; the Codex table
      above already satisfies it.

old:  `agents/vllm_neuron_parity_investigator.toml` disclaims it. Never dispatch this node to
new:  the investigator's own contract (`agents/investigator.md`, mirrored in
      `codex/agents/vllm_neuron_parity_investigator.toml`) disclaims it. Never dispatch this node to
```

5. For the six mirrors, do not transplant line 6. Rebuild it, or substitute block by block:
   - **Rebuild (preferred):** `developer_instructions` is exactly the 115-word Codex header followed by the twin's body under the harness substitutions. Keep the live header bytes, replace everything after them with the landed `agents/<seat>.md` body, and apply the substitutions with the tokens the live tree uses — read the top-tier token from that file's own `model` field, and note that the live tree has collapsed the second Codex tier onto the same token (0 hits for `gpt-5.6-terra` in the whole live plugin), so `sonnet` maps there too.
   - **Block substitution (fallback):** for each Markdown hunk in this patch, replace the escaped old block with the escaped new block (newline → `\n`, `"` → `\"`), one hit per pair. Every block this amendment changes is model-token-free, so it matches the dirty file unchanged.
   - Either way `tomllib` must parse and §3 must reproduce. Block substitution leaves the investigator and measurer mirrors wrapped as they are today rather than as the patch wraps them; the difference is whitespace-only and §3's containment holds under normalization, so it is not a defect to chase.
6. Re-run §1 after landing. The two counts a hook change must hold are 42 pytest cases and 31 hook checks; this patch changes three guards and their test file and holds both counts.

## 7. The state of the working-tree rename

The user's uncommitted edit renames the Codex model token, and in the live tree it is already complete in source: 27 hits for `gpt-6-astra` across 9 files, 0 for `gpt-5.6-terra`, and the single remaining `gpt-5.6-sol` is at `VERSION`:312, inside the append-only 1.4.0 entry that records the binding of the day. Both Codex tiers now read as one token, which is why §6 step 5 says to read the mapping out of the live file rather than assume the committed one. This patch touches no `VERSION` entry older than 1.5.2, so it leaves that historical token alone, as the rename did.

This patch is written against the index, where the committed token still stands, so `/usr/bin/grep -c gpt-6-astra` on the patch is 0 and the patch proposes no value for a line the user is editing. The four Markdown-absent clauses cut from the mirrors, for step 5 of §6, are: the adversarial reviewer's `narrow_delta_scoped` round-classification paragraph; the implementer's two `Re-entry instrument:` clauses; and the investigator's one `Re-entry instrument:` clause. The fourth mirror-only clause — the implementer's design-record re-entry duty — is not cut but moved into `agents/implementer.md`, so a rebuild carries it and a block substitution must not delete it.

## 8. What a reviewer should re-run

The whole of §1 against a fresh `75a0775` export, then four checks that the patch is what it claims: `/usr/bin/grep -c gpt-6-astra` on the patch (0), the changed-file list limited to `generated-plugins/vllm-neuron-parity` (28 files, no `workflow.pave.yaml`, no `revisions.yaml`, no `history/`), the parity proof in §3 recomputed from the patched tree, and `git apply --reject` against a copy of the live tree to reproduce the 9-hunk collision inventory in §6.
