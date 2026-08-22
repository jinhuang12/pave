# Goal and PAVE fitness

The lead runs this whole stage itself: only the lead can reach the user, so the interview is never dispatched. Do not design the graph during the interview.

## Contents

1. Establish the goal
2. Close sufficiency gaps
3. Write requirements
4. Judge fitness
5. Get approval

## 1. Establish the goal

Confirm that the user explicitly invoked pave-init through the active harness mechanism. Then obtain the goal — the single statement the whole run serves:

- If the invocation text states a goal, extract it and every requirement it implies. Record each as a `USER REQUIREMENT`.
- If the user invoked the skill bare, ask for the goal directly in plain text before any other question: what system, what workflow it should produce, and what "done" means.

Verify the validation runtime now: `python3 -c "import yaml, jsonschema"`. Validation fails closed at Stage 3, so resolve a missing package with the user (or record an approved alternate interpreter) before requirements approval, not after planning is spent.

A goal is sufficient for graph design when it settles, or lets the sources settle:

- target system and source locations;
- the result the generated workflow must produce;
- the acceptance boundary — what outcome counts as done;
- requested generated-skill name and output location;
- whether the system is implemented, partial, or conceptual;
- whether private access or live mutations can become relevant.

For a repository, default the planning workspace to `.pave/<workflow-name>/`. If the generated skill already exists, inspect it and tell the user that this run will plan an update. Do not overwrite it before plan approval.

## 2. Close sufficiency gaps

Inspect local sources before asking anything: they answer most sufficiency gaps without a question. Then interview in rounds of one bounded user-question interaction with two to four questions, using the active harness mechanism in the lead contract, and re-inspect between rounds. Stop interviewing as soon as the goal is sufficient — the interview exists to make the goal designable, not to fill a form. Never ask what the invocation or the sources already answered. Confirm an extracted requirement only when it is ambiguous.

Lead with gaps that can change graph topology or acceptance. A goal statement usually settles purpose, target, and naming but rarely authority, recovery, acceptance, or forbidden effects — check those first.

Shape each question for the tool: a bounded decision with two to four concrete options, a recommended option first when one exists, and honest descriptions of tradeoffs. The user can always pick "Other" for free text. When a gap needs open-ended discovery rather than a choice — for example, "describe the current process" — ask in plain conversation text instead.

Sufficiency checklist — a gap in any category blocks graph design only if the goal, the sources, and sensible defaults leave it open:

| Category | Sufficient when |
|---|---|
| Purpose | Concrete result and why it matters |
| Scope | Included work, exclusions, and target boundary |
| Authority | Incumbent, policy owners, and conflict precedence |
| Effects | What actors may read, change, approve, or invoke |
| Evidence | Sources, artifacts, freshness, and claim authority |
| Roles | Responsibilities, independence, and decision authority |
| Current workflow | Existing stages, tools, handoffs, and known gaps |
| Recovery | Repair, investigation, replan, rollback, pause, and exhaustion |
| Parallel work | Independent lenses, build units, joins, and resource isolation |
| State | Resume data, ownership, schema, and history |
| Completion | Accepted, closed-unaccepted, blocked, incomplete, and exhausted |
| Runtime | Harness features, external services, hardware, and deployment |
| Risk | Costly failures and proportionate enforcement |

Mark a category irrelevant or default-resolved with a one-line reason in `requirements.md`; only open topology-changing or acceptance-changing gaps earn a question.

Separate four statement classes: `USER REQUIREMENT`, `OBSERVED FACT`, `ASSUMPTION`, `OPEN QUESTION`. Do not convert an assumption into a fact through repetition. A bounded-choice selection is a `USER REQUIREMENT` only for what the selected option actually stated.

## 3. Write requirements

Use this structure:

```markdown
# Requirements - <workflow>

## Goal
## Run identity
## Purpose and target
## Scope and exclusions
## Authority and conflict order
## Allowed and forbidden effects
## Evidence and artifacts
## Roles and independence
## Current workflow
## Recovery and parallelism
## State and resume
## Acceptance and closure
## Runtime constraints
## Risks and enforcement
## Assumptions
## Open questions
## Irrelevant categories
## PAVE fitness
```

## 4. Judge fitness

Use Socratic judgment. Do not compute a score.

PAVE is useful when the system has most of these characteristics:

- a concrete target or purpose;
- observable evidence that changes decisions;
- several meaningful work states;
- conditional, recovery, or iteration paths;
- explicit completion or acceptance meaning;
- state that must survive between nodes;
- useful role or authority separation.

Return one verdict:

- `fit`: PAVE is appropriate.
- `fit_with_gaps`: PAVE is useful, but named gaps require resolution or acceptance.
- `not_fit`: A simpler skill or procedure is more appropriate.

Explain which characteristics matter, cite observed facts, and identify the simpler option for `not_fit`.

## 5. Get approval

Render the requirements brief per `references/approval-briefs.md` — goal verbatim, what this approval covers, requirements summary table, fitness verdict with the deciding characteristics, open gaps — persist it to `reviews/requirements-brief.md`, and present it in full in the conversation before asking. The brief is a rendered view of `requirements.md`, never a second authority; self-check it against the file before presenting (no reviewer exists yet at this gate).

Then ask one bounded approval question through the active harness mechanism. Its options state exactly what is being approved — for example "Approve goal, requirements, and fit verdict", "Request changes", "Stop". The approval option label must name the artifact and the verdict; a generic "Continue" is not approval. The approved goal is frozen: later stages serve it, and changing it returns here.

For a `not_fit` override, present the verdict and risks first, then ask a separate approval question. Record:

```yaml
fitness_override:
  original_verdict: not_fit
  missing_characteristics: []
  user_rationale: ""
  accepted_risks: []
  approved_at: ""
```

Keep the original verdict. Do not present an override as a positive fitness result. Continue only after the user explicitly approves the override and requirements.

Stop the stage when the user declines, or when a missing decision would materially change the graph.
