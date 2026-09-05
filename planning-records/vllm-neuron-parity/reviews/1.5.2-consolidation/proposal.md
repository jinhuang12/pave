# Proposal: release 1.5.2 of vllm-neuron-parity

Restore two measured facts release 1.5.1 dropped, fix one dangling referent, repair one seat clause that contradicts its own reference, and collapse release 1.5.0's restatements to pointers — the runtime documents lose 7 lines and 89 words, and nothing in the graph, the ledger, the hooks, the tests, or the bindings moves.

## Header

- Outcome: `draft_ready`.
- Kind: `release` (reference and role-contract prose, VERSION, manifests). No graph or binding hunk, so no ledger entry and no revision 6. The evolution root stays at revision 5, digest `sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7`.
- Base: committed HEAD `2c1454c` (release 1.5.1). Drafted and tested in `/tmp/parity-152-updater` from `git archive 2c1454c`; the live working tree was never read as a source and never written.
- Patch: `release-1.5.2.patch`, `sha256:4577a0b1dca5b88a26acb10959515819b955de7ec787d06f8a66577b03a0f9ae`, 287 lines, 15 files, +78 / −44, regenerated from a fresh `git archive 2c1454c` (no working-tree edit appears in any hunk). Round 3 answers finding A by repairing `agents/measurer.md`; only that file, its codex mirror, and `VERSION` moved from round 2 (`sha256:004b3a11e9c6…`), and the other twelve result files hash identically — see `validation.md` §5.
- File-set digests (sha256 over the sorted `path sha256` manifest of all 55 archived files, caches excluded):
  - base `sha256:3d01e408c0d2a512cbc274c6caaef2ab045eeb81ad94e8152ae6fe81470fe038`
  - result `sha256:81f4e1b4cf97c62060b51dad86c7837899a133590594015c84ea266f5fa0005d`
- Envelope: unchanged. Root goal and acceptance, allowed and forbidden effects, external access, interfaces, evidence and check strength, state authority, and budgets are all untouched — this patch changes no node, outcome, edge, check, seat, model, effort, or instrument. `landing: user` still applies, and the recorded user instruction "do 1.5.2" plus Amendment 7 is the approval the lead records verbatim.

## The ten changes

| # | Site | What changed | Decision it improves | Lines |
|---|---|---|---|---|
| 1 | `references/toolchain-evidence-pitfalls.md` compile-cost Trap | Restored the measured front-end share as one Trap clause: "the front end measured about 5% of a good compile and under 0.5% of a failing one" (L-036, already in the section's evidence list) | Where to attack a slow compile. Without the figure a costing seat can spend a device grant shrinking a stage worth 5% | +1 |
| 2 | `references/patch-mechanism-inventory.md:136` | Restored the consequence of a late import-read override: "freezes a platform constant at the wrong value and corrupts numerics with no error" (L-341, already cited two paragraphs up) | Whether a probe's import order is a real duty or a style note. The failure is silent, so the reason is the whole warning | 0 (+107 chars) |
| 3 | `references/toolchain-evidence-pitfalls.md:8` | "Read it before you credit…" → "Read this file before you credit…" | The nearest antecedent of "it" was `references/measurement-pitfalls.md` on line 4, so the header pointed at the wrong file | 0 |
| 4 | `references/artifact-layout.md` §4.10 | The throwaway-worktree restatement became a pointer: "§4.6's throwaway-worktree duty for any tool that gates on tree cleanliness" | §4.6 (:293-297) states the same duty with its reason, and the §4.10 copy already cited §4.6 — two statements, one of them reasonless, is a drift hazard | 0 (−31 chars) |
| 5 | `agents/implementer.md:217` | Three restated theses cut to the pointer plus one clause (the bound that fires names the waiter that gave up, not the component that failed) | Failure attribution at triage. The surviving clause is the one the seat acts on at the moment it reads the sentence; the knob-delivery and next-wall theses are read from the file it is told to open | −3 |
| 6 | `agents/implementer.md:29` | The venue enumeration and its consequence clause cut; the declaration duty and the pointer stay: "AND the venue that command grades beside the deployed values (`references/patch-mechanism-inventory.md`, "Import time pins the venue before your code runs")" | What an increment must declare. The cut list named three of the reference's five venue components and misnamed one ("construction device" for the reference's "construction context"), so a seat that trusted it would declare an incomplete venue; the surviving pointer names the exact section. The consequence clause ("a probe that differs in any of them grades a configuration the port never runs") is deliberately not restored anywhere — it is the reference's own thesis, and the pointer is the fix | −2 |
| 7 | `agents/investigator.md:79` | Two restated theses cut to the pointer plus one clause, rephrased to the reference's own rule: "Cost a compile from the structures its expensive stage consumes, never from graph size; … carries that rule and the second-hand-claim duty with their cheap probes" | Route costing. The old wording ("Compile cost is composition and not graph size") was a universal the reference states only for the cited pin — the drift risk the 1.5.1 refuters recorded | −1 |
| 8 | `agents/measurer.md:33-42` | Two edits. (a) The firing-control restatement cut; the tripwire duty kept as one clause. (b) The spec-decode trap clause repaired to the reference's own mechanism: "chunk-derived throughput undercounts speculative decode in any harness" (was "the stock serving-benchmark path undercounts speculative-decode configurations (use a streaming harness for those)"). The decode-connector clause is untouched | (a) Procedure realization. The firing control belongs to the census scans, and `references/artifact-layout.md` §4.6 already routes those to the implementer with "read the duty there instead of assuming a measurer brief carries it". (b) Which harness a measurer builds. The old clause blamed the stock path, which `references/measurement-pitfalls.md:48` records as unverified at this pin, and prescribed the streaming harness that :48 records as having reproduced the undercount — so a seat that trusted the gloss built the anti-pattern. The repaired clause carries the mechanism only and names no procedure, so the reference stays the single authority for the graded procedure at :50 | −2 |
| 9 | `skills/vllm-neuron-parity/SKILL.md:224` stage-7 row | Same three theses cut to the brief instruction plus one clause, and the clause corrected to the reference: "a late watchdog names the stage where it ran and not the stage that failed" (was "the stage that gave up") | The lead's briefing decision. The row keeps its instruction because the lead briefs seats with the file and may not open it; the old clause misstated the Trap it summarized | 0 (−138 chars) |
| 10 | `codex/agents/vllm_neuron_parity_{implementer,investigator,measurer}.toml` | `developer_instructions` mirrors changes 5-8 byte for byte, applied by escaped-string replacement against the same before/after text | Codex-harness parity. The mirror test checks markers, not equality, so an unmirrored change would ship two different contracts for one seat | 0 (−532 chars) |

Release metadata: `VERSION` gains a 1.5.2 entry and `version: 1.5.2`; both package manifests and the repo-root `.claude-plugin/marketplace.json` entry declare 1.5.2; `tests/test_codex_port.py:52` expects 1.5.2. No other file in the tree names 1.5.1 outside VERSION history.

## Line accounting

| File | Lines | Δ lines | Δ chars |
|---|---|---|---|
| `references/toolchain-evidence-pitfalls.md` | 367 → 368 | +1 | +94 |
| `references/patch-mechanism-inventory.md` | 160 → 160 | 0 | +107 |
| `references/artifact-layout.md` | 477 → 477 | 0 | −31 |
| `agents/implementer.md` | 350 → 345 | −5 | −278 |
| `agents/investigator.md` | 179 → 178 | −1 | −90 |
| `agents/measurer.md` | 171 → 169 | −2 | −198 |
| `skills/vllm-neuron-parity/SKILL.md` | 460 → 460 | 0 | −138 |
| **ceiling-pinned runtime documents** | | **−7** | **−532** |
| `codex/agents/*.toml` (3 files, one long line each) | 6 → 6 | 0 | −571 |
| `VERSION` (append-only history) | 444 → 485 | +41 | +2467 |
| manifests, marketplace, `tests/test_codex_port.py` | unchanged | 0 | 0 |
| **all 15 files** | | **+34** | **+1364** |

Words over the seven runtime documents: 21,546 → 21,457 (−89). The restorations add 38 words; the consolidations and the measurer repair remove 127.

No pinned ceiling is raised and every pinned file stays under its cap: toolchain 368/400, inventory 160/180, layout 477/500, implementer 345/380, investigator 178/200, measurer 169/190, SKILL 460/500. Four of the seven gain headroom; toolchain gives one line back to the restoration and inventory is unchanged.

## Every-copy dispositions

`/usr/bin/grep -rn` over the whole package (references, agents, skill, README, `workflow.pave.yaml`, `revisions.yaml`, codex TOMLs, hooks, tests, schemas) for each changed clause. Every hit and what happened to it:

| Clause | Hits at base | Disposition |
|---|---|---|
| watchdog / "bound that fires" thesis | `references/toolchain-evidence-pitfalls.md:262-286` (authority), `agents/implementer.md:218`, `codex/…_implementer.toml:6`, `SKILL.md:224` | authority untouched; the three copies reduced to one clause and mirrored |
| "runtime knob is delivered" thesis | authority §"A runtime knob is delivered…", `agents/implementer.md:219`, `codex/…_implementer.toml:6`, `SKILL.md:224` | all three copies removed |
| "cleared compiler wall" thesis | authority §"The pipeline aborts…", `agents/implementer.md:220`, `codex/…_implementer.toml:6`, `SKILL.md:224` | all three copies removed |
| compile-cost thesis | authority §"Cost a compile…", `agents/investigator.md:79`, `codex/…_investigator.toml:6` | copies rephrased to the authority's own rule and mirrored |
| second-hand-claim thesis | authority §"Re-derive every second-hand claim…", `agents/investigator.md:80-82`, `codex/…_investigator.toml:6` | copies replaced by a named pointer |
| firing-control duty | `references/measurement-pitfalls.md:28-32` (authority), `references/artifact-layout.md:299` (one-clause application for the implementer, kept), `agents/measurer.md:41`, `codex/…_measurer.toml:6` | measurer copy and its mirror removed; the §4.6 application stays because its reader is the scan-running implementer |
| tripwire duty | authority `measurement-pitfalls.md:22`, graph `workflow.pave.yaml:2053-2054`, `revisions.yaml:251`, `artifact-layout.md:266-268`, `SKILL.md:225`, `agents/measurer.md:39-40`, mirror | graph, ledger, layout and lead untouched (each is a different reader's duty); the measurer's clause kept as its one clause and mirrored |
| spec-decode undercount trap | `references/measurement-pitfalls.md:46-50` (authority), `agents/measurer.md:34-35`, `codex/…_measurer.toml:6`, `workflow.pave.yaml:2047-2049` | authority untouched; the seat clause repaired to the authority's mechanism and mirrored. **The graph activity of `realize_measurement_procedures` still carries the contradicted wording — a release layer cannot touch the graph, so it is owed a graph revision (see judgment 5)** |
| decode-connector trap | `references/measurement-pitfalls.md:54-60` (authority), `agents/measurer.md:36-37`, `codex/…_measurer.toml:6`, `workflow.pave.yaml:2049-2050` | kept verbatim everywhere: the clause states the authority's own conclusion ("never a correctness procedure") and a briefed measurer acts on it before opening the file, so under the equivalence rule it is a one-clause application |
| throwaway-worktree duty | `artifact-layout.md:294` (authority), `:396` (restatement) | restatement collapsed to a pointer; no other copy exists |
| venue rule | `patch-mechanism-inventory.md:134-136` (authority), `agents/implementer.md:30-33`, mirror | gloss removed, pointer and declaration duty kept, mirrored |
| import-order consequence | nowhere at base (that is the defect) | restored once, at the authority |
| front-end share | nowhere at base (that is the defect) | restored once, at the authority |
| "Read it" referent | `toolchain-evidence-pitfalls.md:8` only | fixed in place |
| version string `1.5.1` | marketplace, two manifests, `tests/test_codex_port.py:52`, VERSION | all four bumped; VERSION keeps 1.5.1 as history |

`README.md:646-648` and `SKILL.md:78-81` name the same references but carry only topic labels and bare pointers, so they hold no clause to collapse and are untouched.

## Usage evidence

No raw predecessor usage ledger exists in this checkout, so the record I read is the historical one: VERSION entries 1.2.0 through 1.5.1 and `revisions.yaml` semantic diffs 1 through 5. They change nothing in this proposal and here is why. They record the opposite trigger from the one rule 8 watches for: repeated seat work on inputs that verified evidence had already settled — which is why revisions 1-5 converted re-entry laps to lead-mechanical settlement and why 1.5.1 scoped remedies rather than adding any. Nothing in them names a seat whose priced judgment never fired, and nothing in them names a defect in the four reference documents. This patch adds no seat, no lap, no gate, and no artifact, so it cannot move a traversal count either way. `usage_evidence` for this release is `none`: no parity run has closed, so no field record exists to answer.

## Judgments I made that the brief left open, and one I made against it

1. **"Net negative overall" is reported two ways, and the literal total is +34 lines.** The runtime documents the ceilings test governs — references, seat contracts, lead skill — lose 7 lines, 89 words and 532 characters, and the codex mirrors lose 571 characters more. `VERSION` gains 41 history lines, which is what makes the 15-file total positive. I did not shrink the changelog entry below what states the release honestly, and I did not touch older entries, because VERSION is append-only history and is deliberately not ceiling-pinned. Release 1.5.1's own proposal reported it the same way ("The three reference files total 678 → 685 lines… VERSION gains 28 history lines"). Round 3 added 13 more VERSION lines, all of them the finding-A repair, the graph survivor it exposes, and the finding-B metadata sentences. If the lead wants a literally negative 15-file total, the only honest route is a further consolidation outside this brief's ten changes — see item 4 below.
2. **Change 9's surviving clause got a wording fix.** The stage-7 row said the late watchdog "names the stage that gave up"; the reference says it "names the stage where the watchdog ran, not the stage that failed". Keeping a clause I know misstates its authority would ship the drift the collapse exists to remove.
3. **Change 7's surviving clause got the same treatment.** "Compile cost is composition and not graph size" became "Cost a compile from the structures its expensive stage consumes, never from graph size", which is the reference's own Rule and an instruction rather than a universal fact claim. The 1.5.1 refuters recorded this exact drift risk (3/3 rejected it as a contradiction, but named the risk).
4. **One confirmed 1.5.0 duplication is deliberately NOT in this patch.** The assessment (Verdict 2, item 2) names two duplications: the §4.10 worktree copy, which change 4 fixes, and the census three-item list stated in both `artifact-layout.md` §4.6 and `references/measurement-pitfalls.md`. The brief's ten changes do not include the second, and it is not a one-line collapse — the two statements have different readers and I would have to settle which one is the authority before cutting either. I left it, flagged here, as the natural body of a 1.5.3. **Reversed in round 3 for one of the two clauses.** I had argued that the measurer's two named traps (`agents/measurer.md:34-38`) were one-clause applications a briefed seat acts on before it opens the file, so neither should move. That defence fails for the spec-decode clause, because a one-clause application must still be *true*: `references/measurement-pitfalls.md:46` names chunk-derived counting, not the stock tool, as the mechanism; `:48` records that avoiding the stock tool does not avoid the trap and that the stock tool's own accounting is unverified at this pin; and `:50` puts the graded procedure in the reference. The gloss therefore mislabelled the cause and prescribed the very harness that reproduced the undercount, so change 8b repairs it. The decode-connector clause survives the same test — it states its authority's own conclusion (`:54-60`) — and stays verbatim. The census three-item list is still deferred to a 1.5.3 for the reason above.

5. **The same wrong guidance survives in the graph, and this layer cannot fix it.** My every-copy sweep found a third copy at `workflow.pave.yaml:2047-2050`, an activity of `realize_measurement_procedures`: "the stock serving-benchmark path undercounts speculative-decode configurations (use a streaming harness for those)". Editing it would make this a `kind: graph` change, add revision 6, and move the digest the brief requires unmoved — so I left it and recorded it in the VERSION entry instead. After 1.5.2 lands, the graph is the last carrier of the contradiction and a measurer reads the graph as the authority over its contract, so I recommend commissioning a graph revision that replaces that activity's wording with the same mechanism. Drafting it is one round of my seat's work on request. `/usr/bin/grep -rln` over the whole tree confirms the graph is the only remaining hit.
6. **A landing hazard the brief did not mention.** The uncommitted model-binding edits touch three of the files this patch touches, and in `codex/agents/vllm_neuron_parity_measurer.toml` they touch the *same physical line* (line 6, `developer_instructions`, in its "## Effort pins" text). So `git apply` and `git apply --3way` against the dirty working tree both fail; only `--cached` (index, clean at HEAD) succeeds. `validation.md` records both results and a deterministic re-mirror recipe for whichever order the lead lands in.

## Mechanical proof

All in `validation.md`, run in a second clean archive with the patch applied: `git apply --check --cached` against HEAD exit 0; the applied archive is byte-identical to my scratch tree; pytest 42 passed; hooks 31 passed; `validate_pave` PASS at 32 nodes and 95 edges; `record_revision.py verify` PASS at revision 5 with digest f4d76a53 unchanged; the ceilings test passes with no ceiling edited.
