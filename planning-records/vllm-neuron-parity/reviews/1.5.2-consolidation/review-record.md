# Review record — vllm-neuron-parity 1.5.2

Release-layer change (references, seat contracts, lead skill, codex mirrors, version metadata). No graph, ledger, hook, or model hunk. Base: committed `2c1454c` (release 1.5.1), archived with `git archive`, never the working tree. Reviewer: one named `pave-init:update-reviewer` (`reviewer-parity-152`) held through all rounds. Updater: one named `pave-init:workflow-updater` (`updater-parity-152`).

Approval basis: the user's instruction "do 1.5.2" plus the standing instruction to make goal-aligned choices without further approval prompts. The plan says `landing: user`; that instruction is the approval.

## Round 1 — REVISE

Patch `sha256:004b3a11…` minus its VERSION entry (identical runtime hunks).

- HIGH: the VERSION entry declared four glosses collapsed; the patch collapsed five (the implementer venue gloss was undeclared).
- LOW: the VERSION entry claimed "every pinned ceiling gains headroom"; toolchain-evidence-pitfalls.md grew 367 → 368 and three pinned files were flat.

Repair: VERSION entry only.

## Round 2 — PASS

Patch `sha256:004b3a11e9c6aebf55f1b28e2da3c1c500eb28f1cd5e9a9b7e34790483e3c51f`, result file-set `sha256:d2010cf7…`. KIND binding. Both round-1 findings closed. Residuals noted: landing hazard on the dirty working tree; bundle documents carried stale totals (later confirmed already refreshed).

## Independent adversarial pass (Workflow `wf_e2de94b4-e86`)

Five finders, nine raw findings, three-lens refutation (evidence, materiality, counter), majority survival. Reviewed the round-1 patch. Findings and votes: `adversarial-workflow-findings.json`.

| id | severity | disposition |
|---|---|---|
| copies-2, changelog-1 | HIGH | already fixed in round 2 (headroom sentence) |
| copies-1 | HIGH | pre-existing at base; folded into round 3 |
| changelog-2 | LOW | folded into round 3 |
| copies-3, copies-4, changelog-3, refactor-1, refactor-2 | LOW | rejected by majority |

copies-1: `agents/measurer.md:35-36` kept "the stock serving-benchmark path undercounts speculative-decode configurations (use a streaming harness for those)". Its authority `references/measurement-pitfalls.md:46-50` says the trap is chunk-derived throughput in any harness, that a custom streaming harness reproduced the undercount, that the stock tool's accounting is unverified at this pin, and prescribes end-to-end latency. The kept clause prescribed the instrument that failed in the field. The reviewer confirmed from disk and recorded that its round-1 check covered the three rephrased applications and skipped this kept one.

## Round 3 — PASS

Patch `sha256:4577a0b1dca5b88a26acb10959515819b955de7ec787d06f8a66577b03a0f9ae` (287 lines, 15 files, +78/−44), result file-set `sha256:81f4e1b4cf97c62060b51dad86c7837899a133590594015c84ea266f5fa0005d` (55 files). KIND binding.

Repair scope (reviewer criterion 1, verified by `diff -rq` of the round-2 and round-3 result trees): `agents/measurer.md`, `codex/agents/vllm_neuron_parity_measurer.toml`, `VERSION`. Every other hunk byte-identical to round 2.

- Spec-decode clause now: "chunk-derived throughput undercounts speculative decode in any harness". Blames the mechanism, not the stock tool and not streaming; does not restate the :50 procedure; decode-connector clause untouched (it agrees with :52-56).
- Mirror: new phrase 1 hit, both old phrases 0 hits, `tomllib` parses.
- VERSION: declares the measurer repair; "tests" removed from the unchanged list; "Test logic is unchanged — only the expected version pin in `tests/test_codex_port.py` moves"; "Both plugin manifests and the marketplace entry declare 1.5.2". Arithmetic recomputed independently: −7 lines, −89 words over the seven runtime documents.
- Invariants: `apply --check --cached` exit 0 against 2c1454c; no hunk carries the working-tree `gpt-6-astra` edit; graph, ledger, hooks untouched; `record_revision.py verify` PASS at revision 5, `sha256:f4d76a53…879f7`; CEILINGS unedited, all 13 pinned files under cap.
- Mechanicals: 42 pytest, 31 hooks, `validate_pave` PASS (32 nodes, 95 edges, 5 control endpoints), ceilings and codex tests.

## Residual risks (non-blocking)

1. **Graph copy of the defective guidance — deferred graph revision.** `workflow.pave.yaml:2047-2048` still reads "Avoid the known tool traps - the stock serving-benchmark path undercounts speculative-decode configurations (use a streaming harness for those)". It is the activity spec for `realize_measurement_procedures` and now contradicts `agents/measurer.md:34-35`. A release-layer patch cannot legally touch it (graph hunk → blended → REVISE; the hook guard denies live-graph edits in an evolution root). The VERSION entry discloses it. Recommendation: land the graph revision (revision 6) before the next spec-decode campaign briefs that node. `workflow.pave.yaml:2049-2050` duplicates the decode-connector guard and belongs in the same revision. Until then `agents/measurer.md:22` ("proceed on the artifact and disclose the disagreement in one line") is the catch.
2. 1.5.3 material: the census three-item list duplicated across `artifact-layout.md` §4.6 and `measurement-pitfalls.md`.
3. Restored wording equivalences: L-341 "with no error" vs source "silently"; L-036 "the front end" vs "the Torch-to-HLO-to-BIR front end".
4. Pre-existing skill-creator description-length failure (1096 > 1024), unchanged.
5. Landing hazard: the patch does not apply to the dirty working tree; landed from a clean index with the codex TOML hunks transplanted around the user's uncommitted model-token edits (see `application.md`).
