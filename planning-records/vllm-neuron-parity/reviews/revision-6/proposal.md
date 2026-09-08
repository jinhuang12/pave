# Revision 6 proposal — vllm-neuron-parity (graph layer)

Ten recorded graph-layer defects are fixed in one graph revision, and none of
them needs new structure: two checks asked the wrong question, and five node
bodies restated duties their references pin once — one of those restatements
carrying a remedy its own authority blames for the defect. Proposal file:
`generated-plugins/vllm-neuron-parity/history/v6.patch` (358 lines: 224-line
preamble, 134-line diff, 9 hunks).

- **kind**: `graph`. Nothing here is a seat, model, effort or instrument, so the
  §2.1 layer test puts every hunk on the graph layer and no binding successor is
  owed.
- **digest_before** `sha256:f4d76a53…879f7` (revision 5) → **digest_after**
  `sha256:d00e951b0e874ef88edbeae69798f1fcf5ff8694713212c99b21654806e35f12`.
- **envelope_check**: `changed_with_approval`. One check's floor moves upward
  (item 3). The plan says `landing: user`, so the user approves by number and
  digest either way.
- **plan_evidence**: `verified`. **usage_evidence**: `none` — see "Usage
  evidence" below.
- **outcome**: `draft_ready`.

## What is unchanged, proved by parsing and not by reading the diff

32 nodes / 95 edges / 16 checks / 24 evidence definitions / 5 control endpoints.
Every node id, outcome set, role list, allowed and forbidden effect, consumes
and produces list, `required_evidence` list, check style, evaluator and failure
route is byte-identical between the live graph and the patched graph. No node,
outcome, edge, check or evidence definition is added, removed or re-scoped, so
§4.11's burden of proof on added structure never comes due.

## The four changes

**1. The measurement-trap activity stops prescribing the instrument that
failed.** `realize_measurement_procedures` told the measurer that "the stock
serving-benchmark path undercounts speculative-decode configurations (use a
streaming harness for those)" and repeated the decode-connector rule
(`:2047-2050`). Its authority says the opposite about the remedy: the undercount
comes from deriving throughput out of stream chunks in *any* harness, a
campaign's own streaming harness reproduced it, and whether the stock tool
shares the failure is unverified at this pin
(`references/measurement-pitfalls.md:46-50`). Both clauses become one pointer
plus the clause that changes what the node does — realize no procedure that
reproduces a recorded trap, and where a trap is in what the procedure counts,
swapping the instrument that exposed it is not the remedy.

Why it holds for any run: it names no tool, no model and no metric, and it
corrects the reasoning error instead of the example. This is §5.3.1's
anti-backfire guard — a metric that does not measure the acceptance property is
as much a defect as missing evidence. Both readers of this node already resolve
the reference (`agents/measurer.md:33-37`, `SKILL.md:225`), so the equivalence
rule sends a multi-clause restatement to the pointer.

**2. The kickoff gate asks for the contract field its definer defines.**
`kickoff_contract_approved` required a "deepseek carry-over decision" (`:256`).
The node that assembles the contract (`:512`) and the node that surfaces it
(`:436`) both call the field "prior-run carry-over decisions", and the check now
uses that term. Why generally: a gate that names one campaign's target asks
every other campaign to approve a field that does not exist for it, so the lead
reads the clause as inapplicable and the field goes unchecked at the one gate
that owns it. The model name made a live check unfireable on every run but one.
No strength moves — the same artifact field is required, of every campaign
instead of one.

**3. The acceptance gate counts per criterion, against the registration.**
`acceptance_threshold_evaluated` accepted a bundle recording "at least one
pre-registered threshold actually evaluated" (`:334`). The pinned record shape is
per criterion the bundle realizes, and an empty record makes the bundle
incomplete (`references/artifact-layout.md:302-306`); the graph's own producing
node states the per-criterion duty (`:2226-2229`). The question now asks for one
evaluated threshold for every criterion **the acceptance registration records
for that measurement**. The denominator comes from the registration, never from
the bundle's own list, because a producer that supplies its own denominator
passes by naming fewer criteria — §9.14.1's evidence-gameability judgment,
answered at its hardening rung 2, a check the doer does not run over evidence
the doer cannot narrow. §4.5 already registers that list per criterion and
already names this check as its reader. The rationale gains one sentence saying
so.

**4. Five restatements collapse to pointers they already carried.** In each case
the clause that changes what the node does stays and the reference's shape goes.
The three-and-nine repair-budget magnitudes leave all four graph sites
(`:2162`, `:2165`, `:2256`, `:2279`); the durable-host-state enumeration leaves
`:2094`; the scan-completeness census list leaves `:1672`. Two vague pointers
("the consolidated artifact-layout entry") become numbered ones — section 4.4 and
section 4.10 — matching the style the graph already uses at `:1304` and `:1671`,
and the 4.10 pointer also names the capture-launch boundary ruling the deleted
copy omitted, so the pointer is more complete than the text it replaces. Why
generally: §4.11 says remove any element whose absence does not change required
routing, authority, evidence, recovery, or acceptance, and every clause cut here
passes that test — a reader of these nodes now gets the one authority the
reference itself declares single, instead of a copy that can drift from it.

## Findings disposition — all ten fixed, none declined

| Site | Finding | Disposition |
|---|---|---|
| `:2047`, `:2048` | streaming-harness remedy contradicts its authority (HIGH) | fixed, item 1 |
| `:2049-2050` | decode-connector guard duplicated (1.5.2 residual 1) | fixed, item 1 |
| `:256` | `deepseek` scenario token in a generic gate (HIGH) | fixed, item 2 |
| `:334` | "at least one" below the pinned per-criterion shape (HIGH) | fixed, item 3 |
| `:2161`, `:2256`, `:2279` | three graph copies of §4.4's magnitudes (LOW) | fixed, item 4 (plus the fourth copy at `:2248-2251`) |
| `:2093` | §4.10 durable-host-state definition restated (LOW) | fixed, item 4 |
| `:1671` | §4.6 census list restated (LOW) | fixed, item 4 |

## The two claims most worth attacking

**Item 3 against its refuting vote.** One of the three sweep lenses refuted the
`:334` finding: the gate is deliberately the narrow empty-record guard, and
per-criterion completeness is caught later. Read from disk, that save fails three
ways. `stabilize_and_package_evidence`'s completeness activity (`:2245`) checks
the bundle set against the route-scoped declared measurement list — per
measurement, not per criterion. `collection_defect_found` (`:2271`) is settled by
the seat that produced the bundles, which §5.3.1 puts below the ladder as doer
self-report; this check exists to be the read that seat does not perform. And the
adjudicator's `evidence_unstable` sits *after* this gate, on the seat already
told to take each measured value from that same record
(`agents/adjudicator.md:36`). So this is the only independent per-criterion read
on the path into the verdict. Provenance sharpens it: the at-least-one wording landed with
revision 5 (`history/v5.patch:164`), whose stated reason was that the graph had
believed an instrument nobody had ever seen fail. An at-least-one floor leaves
that same blind spot over the criteria a bundle silently skips.

**Item 4's trade-off.** The magnitudes and the census items now live only in the
release layer, outside the pinned graph bundle, so a release can move a suggested
bound without a graph revision. I judge that right, and the reasoning is
attackable: `references/artifact-layout.md:245-247` already declares itself the
single definition and names these two nodes as its citers, and
`agents/measurer.md:106-108` already orders the seat to cite it and not restate
the numbers. Four graph copies did not protect the bound — they created the
divergent normalization that rule was minted to prevent, and an amendment to §4.4
had to find four graph sites to stay consistent. Both magnitudes are advisory
("suggested"), the release layer is line-ratcheted and tested, and it lands under
the same user gate. If the lead would rather have the numbers inside the digest,
the coherent move is the opposite one — delete them from §4.4 and pin them in the
graph — never two homes for one bound.

## Cost per run

No new seat, node, edge, outcome, check, artifact or lap. Item 3 changes what the
lead counts at a gate it already runs: instead of finding one evaluated-threshold
record per bundle it counts them against the criteria the registration already
lists — a read of two persisted artifacts, on the lead-mechanical rung. Items 1,
2 and 4 cost nothing; they remove text or correct a term. Against that, item 3
closes the only path by which a bundle realizing several criteria reaches a
parity verdict with one of them evaluated, and item 1 stops the graph from
telling a measurer to build the harness that produced this domain's worst
recorded measurement defect.

## What grew, what was cut

The only addition is item 3's question and rationale, +4 lines. Everything else
is a cut: −11 lines over the six restatement hunks and −1 at the trap activity.
28 added against 36 removed, net −8 on a 2811-line graph. Nothing new is pinned.

## Copy census — every hit of every clause this patch touches

- "streaming harness": `measurement-pitfalls.md:48` is the authority narrative and
  stands; `workflow.pave.yaml:2048` is cut; `VERSION:29` is the append-only
  disclosure of this defect and stands.
- "stock serving-benchmark": graph only, cut.
- Decode-connector clause: cut here; the one-clause applications in
  `agents/measurer.md:36`, its codex mirror and `SKILL.md:225` stand, each already
  naming the reference.
- "at least one pre-registered": graph only, reworded.
- Three-and-nine magnitudes: `artifact-layout.md:234` and `:241` are the
  authority; all four graph copies are cut.
- Durable-host-state enumeration: graph `:2094` cut; `agents/measurer.md:57-59`
  and its codex mirror still carry it — release layer, below.
- Census three-item list: `artifact-layout.md:289` is the authority, graph `:1672`
  cut, `measurement-pitfalls.md:32` still carries it — release layer, below.
- "deepseek": graph `:256` only. `revisions.yaml:243` and `history/v5.patch:10`
  are provenance citations in append-only records and are never edited.

## Cross-layer counterparts for the lead to route

Deliberately not in this patch — a blended patch is unreviewable.

1. `agents/measurer.md:57-59` and
   `codex/agents/vllm_neuron_parity_measurer.toml` restate the §4.10
   durable-host-state enumeration beside their own "lives once in §4.10" pointer:
   the same defect this patch cuts from the graph, one layer over.
2. `references/measurement-pitfalls.md:32` restates the §4.6 census list (1.5.2
   review-record residual 2). After this revision that pair is the last copy.
3. `tests/test_document_ceilings.py` pins every README, reference, seat contract
   and SKILL.md, but not `workflow.pave.yaml` — the graph is the one prose file
   in this package that can grow silently.

None of the three blocks this revision.

## Usage evidence (evolution contract rule 8)

No usage record exists for this workflow: no parity run has executed against it
and the root holds no usage ledger, so `usage_evidence` is `none`, not the
`field` revision 5 could claim from a sibling campaign's verified learnings. What
I read instead: the five semantic diffs in `revisions.yaml` and the 1.5.2 review
record. They changed this proposal twice. Revisions 1 through 4 each cut priced
judgment that never fired, and the one revision that added a seat added twenty to
fifty-five dispatches per campaign — so this proposal adds no seat and no lap and
puts its single strengthening on the lead-mechanical rung. And revision 5's own
diff states the pattern this patch applies, "the graph states the duty and cites
the shape" — which is why three sites collapse to pointers rather than being
reworded. Nothing in the ledger shows a measurement-boundary seat whose priced
judgment never fired, so nothing here retires one.

## Landing is not mine

The lead lands, the user approves revision 6 by number and digest first, and the
`approval` and `review` fields are the lead's to fill. The live root is untouched:
`record_revision.py verify` still reports revision 5 at
`sha256:f4d76a53…879f7`, and the only file this seat wrote inside the root is
`history/v6.patch`.
