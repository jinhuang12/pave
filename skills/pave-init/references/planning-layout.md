# Planning workspace layout

Single path-and-ownership authority for `planning/` under the run workspace, active during Stage 3. Shape authority: `schemas/run-state.schema.json` (`$defs.frontier`, `$defs.fragment`). Checked by `scripts/validate_run_state.py --frontier`. Warned by `hooks/planning-layout-warn.sh`.

## Layout

```text
<run-workspace>/planning/
  root-contract.md                 lead       frozen five-part contract for the root
  frontier.yaml                    lead       queue, lifecycle, conflict register
  root.draft.pave.yaml             planner    root skeleton (full profile)
  <node>.draft.pave.yaml           planner    one fragment per dispatched boundary
  <node>.v2.draft.pave.yaml        planner    redispatch = new path, never a reused one
```

The per-redispatch draft path is collision-safety working state under the
scope carve-out in `references/pave-spec.md` §8.4, not a standing document:
a fresh path exists so a zombie completion cannot overwrite the live
dispatch, and everything under `planning/` is working state, never part of
the approval bundle.

## Who writes what

| Actor | Writes | Never writes |
|---|---|---|
| Lead | `root-contract.md`: the root's frozen contract, written before the queue opens. `frontier.yaml`: entries, lifecycle, the conflict register and its `c<N>` ids. Mints each dispatch's draft path before dispatch and records it in the entry. | Draft content — a defective return goes back to a planner dispatch. |
| Node planner | Exactly the one draft path its brief names. | `frontier.yaml`, any sibling or parent draft, any second path. |
| Reviewer, planner-spawned explorers | Nothing under `planning/`. Findings return in the reply; the lead records. | Everything here. |

## Which file wins

1. **A dispatched node's five-part contract** — the parent draft (`root-contract.md` for the root). A fragment carries no copy: it references the contract via `extensions.x_planning` (`dispatched_node`, `frozen_contract_reference`), and the fragment schema has no slots for those fields.
2. **Entry lifecycle** — `frontier.yaml`. A draft file's existence never implies `planned`; the entry's `status` does.
3. **Conflict identity** — the frontier's conflict register. A `c<N>` id exists there first or not at all.
4. **Planning verdicts and predictions** — the draft's `extensions.x_planning.elaboration` on disk. The planner's reply is a notification, never the source.

## Prohibited patterns

1. Two dispatches sharing a draft path. A redispatch — including after a dead or timed-out planner session — mints a new path, so a zombie completion cannot collide with the live one.
2. A fragment re-authoring its dispatched node under `nodes`. Frozen fields are referenced, never copied; a copy goes stale silently on the next resynchronization.
3. A planner minting a `c<N>` id. Report the conflict without an id; node-local labels (`n1`, `e1`, ...) are fine.
4. Anyone but the lead editing `frontier.yaml`.
5. Lifecycle or position recorded only in prose. Status, contract, draft, and dependencies live in schema fields; prose lives in `notes`.

## The rule no shape can check

A resynchronization that changes a frozen interface marks every `reviewed` entry that depends on it `stale` (`orchestration/explore-and-plan.md` §4.4). Each entry's `dependencies` field exists so that check is mechanical: when an interface changes, walk the entries that list its node and flip them.

## Enforcement

- **Validator** — `scripts/validate_run_state.py --frontier <path>` validates the frontier and every dispatched entry's fragment against the schema, then applies the hand rules: unique draft paths, draft named past `pending`, no re-authored dispatched node, no minted `c<N>` ids. Run it whenever the queue changes state.
- **Hook** — `hooks/planning-layout-warn.sh` (PostToolUse) emits a non-blocking warning when a write lands under `planning/` at a path matching no allowed pattern, or when a subagent writes `frontier.yaml`. It warns, never blocks: layout drift is detectable and cheap to repair, so an observing rung is sufficient.
