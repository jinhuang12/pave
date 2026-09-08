# 1.5.2 amendment — review record

Seats: `updater-parity-152b` (pave-init:workflow-updater, opus) and
`reviewer-parity-152b` (pave-init:update-reviewer, opus). Three-round bound.
Base for every round: repo commit `75a0775`.

## Round 1 — patch `2ac2ec90` (23 files, +246/−191) — REVISE

| # | Sev | Finding | Disposition |
|---|---|---|---|
| 1 | HIGH | `SKILL.md:226` stage-8 row said both liveness checks read the registration's value-plus-tripwire pair per §4.5. `artifact-layout.md:266-268` and `workflow.pave.yaml:348-349`: one reads the smoke record, the other the bundle's evaluated-threshold record (§4.6). The deleted "an exit status is not an evaluation" warning guards the silent pass the graph names at `:341-345`. | FIX: reviewer's narrow correction taken verbatim; R23 basis rewritten; VERSION records the row was also wrong about what its checks read. |
| 2 | HIGH | README hunk `@@ -567,7 +573,8 @@` (judges bind the top Claude model, recorded by release 1.4.0) is correct but appears in no disposition row and not in VERSION, so "Nine statements disagreed" undercounts. | FIX: disposition row E1 in a new "Fixes beyond the sweep's 35 findings" table; clause in the VERSION drift paragraph; count nine → ten in VERSION and proposal claim 5. |
| 3 | LOW | `agents/investigator.md` dropped "a settled target or route collapses to one row with its evidence pointer"; §4.12's five bullets do not carry it. | FIX at the authority: §4.12 gains the target/route bullet; investigator keeps its pointer; disclosed in R17, E2, VERSION. `artifact-layout.md` 477 → 480 (cap 500). |
| 4 | LOW | `SKILL.md:106-107` dispatch-table row split across two physical lines. | FIX: one line. |
| A | residual | `validation-amendment.md` §6 step 4 counted one mirror block with different TOML wrapping; three mirrors differ (implementer 2, investigator 2 incl. a dedent, measurer 1), whitespace-only. | FIX: count corrected. |
| B | residual | Investigator enumerated "event files" among §4.13's exempt working state; §4.13 does not list them. | FIX: pointer to §4.13 without enumeration; TOML re-mirrored. |

Reviewer confirmed in round 1 (unchanged since): base and `apply --check --cached`
at 75a0775; verify PASS at revision 6; plugin-relative digest matches the
updater; the revision-6 pointer test (nothing the graph now points at was cut:
§4.4:234/:241, §4.6:289-293, §4.10:363-395 untouched); every-copy clean incl.
six codex mirrors; description 1007 chars with all trigger phrases and the
eight-hook disclosure; ceiling pin 3100 vs 2803 measured; −23/−365 recount
exact; hook change, R13, R33, R34, residual 3b all survived attack. Nine
reviewer hypotheses were refuted on primary evidence and are listed in the
reviewer's round-1 message.

## Round 2 — patch `2b25520a` (24 files, +259/−191)

Lead's independent checks on a clean export: pytest 42, hooks 31,
validate_pave PASS 32/95/5, verify PASS revision 6, skill validator valid,
0 model-token hits, §4.12 bullet present, VERSION "Ten statements", digest
`966c48cc…` matches the updater. Sizes now 13 touched pinned docs,
−22 lines / −314 words. Verdict: PASS (all four findings and both residuals verified fixed; five files moved between rounds, all declared). Residual recorded: VERSION and proposal call the README judges site a table row; it is prose below the table — queued for round 3.

Lead's independent mirror check on the round-2 export (word-level diff of
each `agents/<role>.md` body against its codex `developer_instructions`
after whitespace normalization): the only differences are the 115-word Codex
header at the start of every mirror and the harness substitutions
(`fable`/`opus`/`sonnet` → `gpt-5.6-sol`/`gpt-5.6-terra`, `teammate` →
`custom-agent thread` ×11, `SendMessage` → `followup_task` ×5). No other
word differs in any of the six. The shipped `test_codex_port.py` checks
markers and forbidden tokens only; body containment is proven here and in
`validation-amendment.md` §3, not by a test.

## Adversarial workflow on `2b25520a` (frozen tree, 137 agents, 4 finder rounds + critic)

Seven lenses (fidelity ×3, every-copy, anti-accretion, content-correctness,
reader-path), models rotating opus / opus-4.8 / sonnet; every fresh finding
judged by three lenses (evidence sonnet, materiality opus, counter opus-4.8),
kept on 2 of 3. Record: `adversarial-amendment-findings.json`.

Confirmed 7, rejected 29.

| Sev | Site | Finding |
|---|---|---|
| MEDIUM | `VERSION:70` | The release record misstates what the mirror cleanup cut. Of the four Codex-mirror-only clauses the patch removed (verified by normalized decode of BASE vs RESU… |
| MEDIUM | `VERSION:70` | The 1.5.2 entry gives one rationale for all four cut Codex-mirror-only clauses -- "a decision the seat cannot act on, held by the lead" -- but only three of the… |
| MEDIUM | `scripts/validate_pave.py:334` | The fix for sweep idx37 deletes the only structural validation of the composition block, and the replacement comment states the opposite. Two verified facts. (1… |
| MEDIUM | `VERSION:91` | R6's rewritten 1.5.2 entry makes a false completeness claim about the ceiling ratchet. `tests/test_document_ceilings.py` does NOT pin "every prose file except w… |
| LOW | `README.md:450` | The file-tree gloss keeps the pre-patch framing. The guard no longer restricts itself to irreversible verbs — it refuses every destructive verb on all four root… |
| LOW | `tests/test_codex_port.py:289` | The rewritten guard test lost its branch-identifying assertion. BASE asserted `assertIn("rename it aside", blocked.stderr)`, a string that existed only in the k… |
| LOW | `skills/vllm-neuron-parity/hooks/compile-cache-guard.sh:143` | R27 widened the guard so `mv` is now refused on the kernel intermediate cache, but the static block message still justifies every refusal with irreversibility —… |

Lead re-adjudication of the 2-1 rejections: nine forwarded as fixes
(implementer row lacks close_campaign; SKILL.md judges sentence not aligned
with the README rewrite; two README Model cells contradict the new header;
§4.10 root count wording; write-for-reader hook and adversarial-reviewer
cite a paragraph that is now a pointer; VERSION:19 investigator wording;
dangling `references/enforcement-record.md` citation in three guards; two
unwrapped lines). The rest were judged permitted keeps or accurate text.
All items sent to the updater as the round-3 (final) lap.

## Round 3 — patch `2a7fae79` (28 files, +427/−206) — PASS, one LOW

All 16 items (A1–A6 workflow-confirmed, B1–B9 lead-adjudicated) and the
location-word residual verified at their sites; the optional delegate-count
check was refuted (SKILL.md never counts delegates). Reviewer's answers to the
lead's two questions: A2 justified — the vendored
`references/pave-composition.schema.json` is byte-identical to pave-init's
(sha256 `672e6963…`), and the round-2 validator passed a block with
`realisations` misspelled, missing `version`/`kind`, wrong `version`, and an
unknown key, all of which the round-3 validator rejects; B8 acceptable —
`enforcement-record.md` has 0 hits in the package, all three guards cite an
existing P-row in SKILL.md, and the one scoped pave-init citation is
provenance-only. Size recount −13 lines / −251 words matches VERSION. No
stray edits between the round-2 and round-3 trees. Digest `2c822347…`
matches across updater, lead, and reviewer.

| # | Sev | Finding | Disposition |
|---|---|---|---|
| 1 | LOW | `SKILL.md:178` B9 re-wrap traded "this node" for "it", leaving two "it"s in adjacent sentences; B9 was declared whitespace-only. | FIX (one word, restored before landing); validation note corrected. |

Residual risks recorded by the reviewer: no usage records exist yet, so the
route fixes are verified against contracts and graph only; the scoped
pave-init citation in `protected-branch-guard.sh:15` is a cross-package
pointer no in-package test holds; the codex mirrors are held by the
containment proof, and nothing in the suite enforces the four substitutions
plus the header as a closed set.

## Landing sha `825fb3fb`

The one-word repair regenerated the patch once more. Lead's diff of the
`2a7fae79` and `825fb3fb` result trees: exactly two lines changed —
`SKILL.md:178` "Never dispatch it to" → "Never dispatch this node to", and
`VERSION:111` "251 words" → "250 words" (the restored noun adds one word to a
pinned document; the recount was corrected rather than left false). All
mechanicals green on a clean export; digest `50de8bd0…` (57 files) matches the
updater. Reviewer confirmation on this sha: see below.
Reviewer CONFIRM on `825fb3fb` (own fresh export): exactly two lines differ
from the round-3 tree; recount reproduced at −13 lines / −250 words over 13
touched pinned documents; 14/14 ceilings under cap; apply --check --cached OK;
pytest 42; hooks 31; validate_pave PASS 32/95/5; verify PASS revision 6; 0
graph paths; base and result digests match. No material findings. Landing
precondition: apply against the index, not the dirty working tree.
