# Artifact layout reference — vllm-neuron-parity

Single authority for the artifact tree, write ownership, precedence,
supersession rules, and every shape the graph pins ONCE. The graph
(`workflow.pave.yaml`) stays the path authority; this reference adds only
what the graph cannot carry. Every consumer CITES an entry here and
restates no shape.

## 1. Tree

```
artifacts/
  cross-run/                      # COMMITTED to git (survives runs)
    parity-scorecard.yaml, backlog.yaml, debt-ledger.yaml
    failure-fingerprints.yaml     # absent file = legitimate empty set
  run/                            # this run only
    run-state.json                # lead-only; schemas/run-state.schema.json
    usage-log.md                  # lead-only; one section per run close
    intake-preflight/
    calibration/                  # hardware calibration record (§4.9)
    hardware-queue/               # per-host lock files of the queue tool
    delta/
      <target-id>/report.md       # + per-target event files (grants, verdicts)
      index/current/              # run-level delta index (only read path)
    backlog/
    closure/
  campaigns/<campaign>/
    kickoff/
    approvals/DECISIONS.md        # one dated section per user decision (§2)
    design/
      current/                    # LIVE round only; superseded artifacts are
                                  #   deleted at re-entry (§3)
    increments/
    attempts/                     # attempt, fingerprint, recovery records
      leases/                     # lease events, queue tool only
    measurements/
      procedures/
      baseline/
      runs/                       # run records + defect records
    verdicts/
    rederivations/
    pr/
    closure/
  reviews/<campaign>/             # reviewer stream + gate-2 objection mirror
```

## 2. Write ownership

One node per path. A write outside your row is an effect violation before
it is a count bug. Run state (`run/run-state.json`) is lead-only.

| Path | Sole writer(s) |
|---|---|
| cross-run/* | lead only, serialized, at campaign closure (intake reads) |
| run/intake-preflight/ | verify_run_preconditions |
| run/calibration/ | calibrate_hardware (suite rows); design_campaign (anchor rows); measure_candidate (served cap rows) — one file per row event, append-only |
| run/delta/<target>/ | that target's trace_target_delta instance (report landing paths: §4.7) |
| run/delta/index/, run/backlog/ | cost_routes_and_rank_backlog |
| run/hardware-queue/ | the hardware queue tool (per-host lock files only) |
| run/closure/ | verify_run_closure (the lead) |
| campaigns/*/kickoff, approvals | assemble_kickoff_contracts; gate records by review_campaign_design and close_campaign; write-once per decision (§3) |
| campaigns/*/design/ | design_campaign (each step its own artifact; the assemble step writes the record and deletes superseded artifacts); the lead's bookkeeping edits (§3) |
| campaigns/*/increments/ | scope_next_increment (round records), realize_increment (evidence, oracle, ratio and maintainer-pass records), the lead (changeset, on the plan_satisfied round) |
| campaigns/*/attempts/ | prepare_host (recovery records in its recovery mode), execute_attempt_loop (attempt and fingerprint records) |
| campaigns/*/attempts/leases/ | the hardware queue tool alone, invoked by the seat that needs the grant; one file per event, append-only |
| campaigns/*/measurements/ | measure_candidate (procedure, baseline, run, defect, and bundle records; defect records live under runs/) |
| campaigns/*/verdicts/ | adjudicate_results |
| campaigns/*/rederivations/ | rederive_approach |
| campaigns/*/pr/ | prepare_pr |
| campaigns/*/closure/ | close_campaign |
| reviews/<campaign>/ | the adversarial reviewer seats only (route review, design review, kernel reviews, whole-campaign review) |
| `<revision-folder>/audit-findings.md` | the `pave-init:workflow-updater` seat, edited in place, plus the `pave-init:update-reviewer` seat's one `review:` line (§4.14) |
| `<run-state>.audit-checkpoint.json`, `<run-state>.write-log*.jsonl`, `<run-state>.audit-write-report-*.txt` | the hooks only (the stop guard and the PreToolUse router); the lead is denied (§4.14) |

## 3. Precedence and supersession

- Which-file-wins: `current/` is the only read path. Never read or add
  to an archive directory.
- On re-entry into a node, the declared evidence path keeps only the
  current revision's output; superseded files are DELETED (the deletion
  rule) - the supersession is recorded as the record's one-line
  revision-log entry plus the run-state entry, never as an archived
  copy. The current record carries an explicit current-record marker,
  so "current" is a file fact, not a directory-listing inference.
- Non-indexed intermediates (scratch, delegate transcripts not cited by
  any record) go under the owning node's directory in `scratch/` and are
  never cited as evidence.
- The lead's three bookkeeping edits to a design document (the
  review_campaign_design edit, the landed-block collapse, the record
  delta on a read block diff) are one revision-log line each, named in
  the traversal note or round record; the note holds 500 characters.
- To supersede a campaign rule, APPEND a supersession section to the
  append-only decision record (`approvals/DECISIONS.md`, §2). Name the
  section it supersedes and why. Never edit the creating section, so the
  record still shows what was decided and when it stopped holding.

## 4. Pinned shapes (single authority; consumers cite, never restate)

### 4.1 Findings-record shape — FIVE fields

Every adversarial-review finding record carries exactly:
1. stable per-finding label
2. cited location
3. defect class, `material` or `bookkeeping` (a bookkeeping finding names
   the surface it edits and never selects an outcome)
4. required change
5. measurement content hash(es) — review_pr_evidence's verdict check only

One findings record per campaign per review node lives under
`reviews/<campaign>/`: each round appends one dated section, each finding
is one line carrying these fields plus a pointer to its evidence, and a
recorded absence of findings is one line. A round never opens a new file.

Fingerprint consumers cite this entry and declare the subset of fields
their detector keys on, or the pair's fingerprint triple (§4.2 pair 1).

### 4.2 The SEVEN producer/consumer pairs

1. **impl/review**: the retained kernel reviewers (realize_increment) and
   the whole-campaign review (review_pr_evidence) emit findings records in
   the §4.1 shape; every material finding also carries the fingerprint triple
   **(increment id + surface + defect class)**, coarser than fields 1-2 so
   round-over-round comparison stays a string match when labels or
   locations are re-minted. scope_next_increment's no-progress detector
   keys on that triple plus the no-new-evidence qualifier (§4.3). The
   qualifier's anchor is the ANSWERING record: findings with no answering
   record between them stay distinct, and a triple re-raised AFTER a
   passing repair is read as repetition on purpose.
2. **adjudicate/pr-review**: adjudication_verdict shape;
   review_pr_evidence keys on fields 1-3 + 5 when it checks the verdict.
3. **prepare/pr-review**: PR description sections, in order
   (`pr_evidence_package`): a short lead with the mechanism and the paired
   headline result; the mechanism and what it does not approximate; scope
   and activation; the paired measurements over the declared context
   lengths and concurrency levels, with the registered protocol (arms as
   commits, hardware and parallelism, pairing order, warm-ups, repeats);
   correctness and accuracy as counts, with their non-claims; how tested
   (whole-tree test command + counts, acceptance measurement + verdict);
   reproduction per arm; the authorship disclosure; what stays open. Each
   claim links once to its record; findings-record fields 1-4.
4. **repo fingerprint file + this-run attempt-log**, split by horizon:
   `cross-run/failure-fingerprints.yaml` = prior runs, lead-merged at
   closure; `attempts/` = this run. Guards the identical-retry forbidden
   effect. An attempt record carries `class` — `serving`, `diagnostic`,
   `kernel_alone`, or `cpu_mode`, the job's declared purpose at launch, never a note added
   after the run — and, on a served tip, `launch_reading`, the queue tool's
   remaining-capacity reading at launch; only `serving` enters
   `hardware_attempt_counts`. A record's measurement outcome (served or
   failed, and what the request path returned) stands apart from hygiene
   findings on the host after it (a leftover process, an open device
   handle), which never set the outcome. The tier-0 bring-up rungs on a tip
   are records of class `cpu_mode` or `diagnostic`, each naming its rung. A localization record names the compare tool
   it ran (path and revision) and the reference it compared against.
   Failure case: a format mismatch between the two readers silently
   disables the tier-1 gate.
5. **recovery-record shape**: writer prepare_host in recovery mode;
   reader prepare_host (allowance derivation — never a stored counter —
   and roster filtering in full mode). Failure case: two
   readers deriving allowance state differently re-lease a
   once-recovered host, voiding the flap bound.
6. **lease-record shape**: writer `scripts/hardware_queue.py` only (per-host
   lock, one file per event: campaign lease, job lease, release, amendment);
   readers prepare_host, execute_attempt_loop, lead. A
   campaign lease carries host, markers verified, markers unavailable, deltas
   explained, grant reference, and event ordering so record-before-report is
   checkable; the boot identifier arrives as an amendment event applying to
   every campaign lease on the host; a job lease carries the job, its class (`serving`,
   `diagnostic`, `kernel_alone`, `cpu_mode`), on a serving job its tip, its client leg and
   its bring-up record, on a `kernel_alone` job (no campaign lease) its
   pre-registration (form or increment, shapes and arms, the bar, wall-clock
   cap, pinned resources), the pools and amounts reserved, grant time, and its
   release; remaining capacity is
   derived from open job leases, never stored. Failure case: divergent
   normalization confirms a lease the recovery pre-check later rejects.
7. **defect-record shape**: path pattern under `measurements/runs/`
   with FIELDS: measurement id;
   content-derived defect identity (the procedure or comparison indicted
   plus the property mismatch observed — never a free label, never a
   budget key); the NEW-relative-to-prior file-list field (§4.4); the
   invalidated-comparison enumeration (re-capture scope); the defect
   each revision entry repairs. Writer: measure_candidate (its defect
   records live under runs/). Readers: its procedures and baseline steps'
   repair targeting; the tier-1 novelty
   predicate; the bundle step's unfiltered count. Failure case: divergent
   derivation splits or merges re-entry scopes.

### 4.3 No-progress detector predicate — one shape, two bindings

Shape: *same named key AND no new gap-closing evidence for that key since
the binding's ANCHOR* — never a round counter, never a directory conjunct.
The anchor is part of the pin: swapping one anchor for the other inverts
the detector on exactly the rounds it must catch.

- implement binding (scope_next_increment): key = the pair-1 triple
  (increment id + surface + defect class); ANCHOR = the triple's last
  ANSWERING record. A triple re-raised after its repair's passing record,
  with nothing newer, fires (the graph's `no_new_route`): an increment can
  pass its acceptance while the reviewer's finding still stands, so the
  answering record, not the naming round, is the anchor here. The
  detector's second limb (the boundary's own gap and stuck entries)
  anchors on the naming round; there key and clearing evidence are decided
  by the same check tool, so no answered-yet-recurring state exists.
- design binding (design_campaign): key = the named gap; subject = the
  owning step's artifact; ANCHOR = the round that named the gap (no
  answering event exists for gaps; a gap-closing artifact after naming IS
  progress).
- "Gap-closing" and "passing" are check-tool-determined (the completeness
  self-check, the declared acceptance), never a sibling's assertion.
- d7 exemption: the commitment-absent gap (registration artifact present,
  lead run-state commitment entry missing) is outside the key space.

Failure case: dropping either qualifier makes the test never fire or
false-fire on converging work; a false fire costs one visible
re-derivation round, which beats a loop that never fires.

### 4.4 Shared repair budget

PER-MEASUREMENT, TWO-TIER. One check tool, two predicates, both derived
from the SAME defect-record directory by path, never a stored counter:
- **Tier 1 (filtered)**: three repair passes per declared measurement
  WITHOUT NEW EVIDENCE. Novelty is the NEW-relative-to-prior file-list
  field on each defect record, recomputable by any reader by diffing the
  record's cited file set against the prior record's for that measurement
  (never a writer assertion).
- **Tier 2 (backstop)**: nine passes per measurement, counted
  UNFILTERED — every defect-record file for the measurement; tier 1
  counts the subset whose novelty field is false.

Both budget readers (measure_candidate's run step and bundle step) cite
THIS definition, so two readers can never normalize the bound differently.
At either threshold: record the defect, complete forward; the bundle step
decides declared_measurement_unproducible; never route backward on a
spent budget. Defect identity is a record FIELD (§4.2 pair 7), never a
budget key — relabeling cannot extend either tier.

### 4.5 Registration record shape

One write-once shape for all three idiom sites (intake criteria, gate-2
comparator commitment, adjudicator reads): **subject, timestamp**. The
`comparators_preregistered` check compares the commitment timestamp
against every measurement artifact.

**Check-tool liveness pair**, registered per criterion in the same
record: the VALUE that must appear as evaluated in the evidence for that
criterion, and the KNOWN-BAD input the procedure must fail on.
`procedures_smoke_verified` reads the known-bad result out of the smoke
record; `acceptance_threshold_evaluated` reads the value out of the
evidence bundle (§4.6). A criterion with no known-bad input is a
design stop (`design_blocked`): a check tool nobody can make fail grades
nothing. Rationale and measured cases: `references/measurement-pitfalls.md`,
"Prove the check tool before its verdict counts".

### 4.6 Evidence index

Element set pinned here; consumers are the kernel and whole-campaign
reviews, the coverage-gap check, and prepare_pr: every planned increment
resolves to a passing evidence record; the index binds increment id ->
evidence file(s) -> acceptance command + exit code (glue: the CPU-mode
record; kernel-class: the oracle, ratio, and kernel review records).
- **Exit-code discipline** (every transcript, run-wide): the verbatim
  command line, raw output, and numeric exit code; a missing exit code
  makes the transcript non-evidence.
- **"Ported code" / "added-modified lines"** (one definition for the NxDI
  and process-vocabulary scans, the reviews, the re-run check): the
  added and modified lines of the campaign branch diff against the
  pinned base — never whole files, never upstream context lines.
- **Scan-completeness discipline** (every scan, run-wide): a reported
  hit count — a zero above all — carries the commit scanned, the tree
  state (clean, or the untracked and modified paths), and the scan
  tool's own completion signal. A tool that needs a clean tree runs in a
  fresh throwaway worktree at the commit under test. A zero nobody can
  show complete is not evidence; the firing controls are
  `references/measurement-pitfalls.md`, "A zero is evidence only with a
  firing control".
- **Evaluated-threshold record** (every evidence bundle, per criterion):
  the value read, the threshold, the comparison result, and the
  negative-control result from the procedure's smoke record (§4.5). An
  exit status is not an evaluation; an empty record makes the bundle
  incomplete.
- **Phrasing rule**: a record names its sources; downstream checks
  compare content identity, never a hash.

### 4.7 Scan phase conventions

- Per-target report: `run/delta/<target-id>/report.md`.
- Grant files: one per re-trace grant under the target's directory; the
  re-trace bound is the count of those files, never a stored integer.
- Report content originates only from the tracer seat; a report change
  with no tracer write behind it is a violation.

### 4.8 Measurement smoke/run join

Smoke evidence is named per procedure REVISION and every run record cites
the revision it executed, so smoke-before-run is checkable record-side: a
run record citing revision N is valid only against a passing smoke record
for revision N.

### 4.9 Run-shape records

- **Calibration record** (`run/calibration/`): one file per row event —
  hardware class, pin (SDK and compiler), cost class, access pattern or
  lowering, value, reproduction command, and source (`suite`, `anchor`, or
  `served_profile`). A served profile writes one row file per profile with
  its run record, so concurrent campaigns never share a file.
- **Op inventory** (the delta report's inventory section): per component
  block, each op class at the declared served configuration, its parallel
  and scheduling forms, and its state against the NKI library and the
  plugin at the pin (present, partial, absent).
- **Execution map** (the design record, per served phase): each block's
  ops in execution order with input and output shapes at the declared
  shapes; the cost table's rows key on its op ids.
- **Kernel-call round row**: the pull list in order (item ids), the review
  capacity, and the idle `kernel_alone` capacity read at the round.
- **Maintainer-pass record** (`increments/`): the diff read (clean diff,
  or the delta since the newest pass record's tip), the branch-diff digest,
  the AST or token diff showing no behavior change, the process-vocabulary
  scan, and the whole-tree pass on the new tip.
- **Not-run bundle record**: behind a failed ladder rung, each later rung
  and performance procedure's bundle holds "not run after rung n failed",
  naming the rung and its bundle; it is complete for adjudication.

### 4.10 Durable host state

Defined once: durable host state = mutations that outlive the session —
persistent writes INCLUDING CACHE WRITES, restarts, reboots, resets. The
lifecycle of a serving process launched for capture or smoke (start,
stop, in-memory state, process-scoped scratch removed at teardown) is
not a durable mutation. Cited by measure_candidate's read-only
baseline step.

**Boundary ruling**: a cache write performed BY the capture-launched
serving process is still a durable mutation when it lands in a
persistent location. Capture confines any cache the serving stack would
write to run-scoped scratch removed at teardown, or refuses and records
why (the graph door: `baseline_unusable`'s unredirectable persistent
cache write, routed to re-derivation).

**Instances on this hardware class** (measured at Neuron SDK 2.32: L-136,
L-139, L-147, L-159; roots re-read at the 0.29 line and SDK 2.33):
- the kernel toolchain's intermediate cache (`/var/tmp/nki-intermediate-cache`),
  written outside the run root and outside any cache variable you set,
  possibly holding a co-tenant's artifacts;
- a shared compile cache whose key directories any re-trace rewrites. XLA
  route roots: `$VLLM_CACHE_ROOT/neuron/compile_cache`,
  `~/.cache/vllm/neuron/compile_cache`, `/var/tmp/neuron-compile-cache`.
  Native route: NEFF roots `native_neff_cache` beside `compile_cache`,
  `~/.cache/neuron_libtorch` (`NEURON_LIBTORCH_CACHE_ROOT`),
  `TORCH_NEURONX_NEFF_CACHE_DIR`; precompile packages under
  `$VLLM_CACHE_ROOT/neuron/torch_compile`. The root moved at 0.24 and at
  0.29: the guard matches the family, and this list is the record;
- a long-lived serving checkout that untracked build artifacts make
  permanently git-dirty;
- instance-store devices encrypted at rest, whose never-written blocks
  read back as pseudorandom bytes.

Duties: rename a cache partition aside inside a root you own and never
delete a shared one (P2's hook refuses every destructive verb on the
shared roots); run any tool that gates on tree cleanliness in a throwaway
worktree (§4.6); prove a device unclaimed from the absence of a partition
table, filesystem signature, holder, and mount, swap, or fstab entry, plus
unchanged write counters across a quiet window — never from reading it
back as zeros.

### 4.12 Living-document caps

A living document is edited in place and holds current state only: the
increment plan, the design record, the regression matrix draft (upgrade
route), the per-target delta report (`run/delta/<target-id>/report.md`),
the costing and backlog report (`run/backlog/`), and the PR evidence
package (`pr/`). Each has a cap of 400 lines AND 60 KB; one that needs
more declares its own cap here, with the reason. Outside the cap and
never shrunk: the write-once registration record (§4.5), append-only
records (`kickoff/`, `approvals/`, the reviewer stream under `reviews/`),
and external evidence at its evidence path. The write-for-reader duty
(§4.13) still covers every reader-facing `.md`. The cap is kept by
shrinking, never by an archive:

- A landed increment collapses to one plan row — id, plain name, tier,
  commit, evidence pointer; its contract lives at the evidence record
  (§4.6) and the registration record (§4.5). A planned increment's
  contract stays inline. A block-scoped round is checked by its diff.
- A decided target or route collapses to one row: the decision and where
  its evidence lives, never the analysis that reached it.
- A count table inside a living document is script output: it carries
  its recompute command and is never hand-edited.
- A living document carries no defensive prose: no argument history, no
  ruling quotes, no per-clause justification. A finding's disposition is
  one line that points at the findings record.
- A re-entry brief names the sections the seat may touch; a whole-file
  reconciliation is its own briefed round.
- `scripts/measure_artifact.py <path>` is the one size check tool. The
  reviewer records each living document's lines and bytes every round;
  over cap is a material finding, and the next round is a trim round
  before any new content. `write-for-reader.sh` names an over-cap
  document at the write; the writer classifies it; a record or
  transcript is left alone.
- `campaigns/*/increments/` working files: an evidence record 200 lines;
  a round record 100 lines; a script header (shebang, comments, module
  docstring before the first code line) 20 lines. External evidence is
  the bytes a command printed, kept whole. A script and its builder,
  self-test, or control are working state: one current revision, edited
  in place, the superseded copy deleted in the same round, never renamed
  `-rN`. `write-for-reader.sh` names an over-cap file, header, or
  round-suffixed name at the write; the whole-campaign review records
  `measure_artifact.py --tree` and `--classify`; the repair is a trim
  round.

### 4.13 Write for the reader

The plain-writing rule, defined once for every seat and every brief. It
covers every document a person will read (reports, records, plans,
verdicts, PR packages, closure records) and every message to the user.

- Concise simple plain english. Each entry leads with one sentence saying
  what happened and why.
- An identifier is a pointer, not a noun: pair it with its plain name at
  first use ("the rotary increment (`inc-025`)"); never leave an
  identifier chain where a sentence should stand.
- Counts and checker output live in run state or the check's own file,
  cited in one line, never interleaved with the narrative.
- Every number lives in exactly one file that everything else cites.
- A reader learns what happened, what changed, and what is still open in
  one pass.
- Superseded prose is deleted in place with one revision-log line, never
  archived.

Exempt: working state written for the next agent — attempt, lease,
measurement, index, and intake-preflight records, and run state itself.
Increment evidence and round records are read by the reviewer and the
next scoper, so the duty reaches them.

Enforcement: `write-for-reader.sh` re-presents this duty on document
writes (advisory, never blocking); the adversarial reviewer treats a
reader-facing artifact that fails it as a material finding.

### 4.14 Audit checkpoint: paths, scratch roots, enforcement table

The audit runs on three files and one write report, with `<state>` = this
run's `run-state.json` and `<revision-folder>` =
`<project-root>/.vllm-neuron-parity/evolution`:

- `<revision-folder>/audit-findings.md`: the findings record, living
  (§4.12 caps it). Written by the `pave-init:workflow-updater` seat; the
  `pave-init:update-reviewer` seat appends only its one `review: PASS|REVISE`
  line. First line `checkpoint: <checkpoint_id>`, then one section per
  file-name group and a `## Trend` table (`checkpoint_id | outcomes |
  bytes | bytes_per_outcome`).
- `<state>.audit-checkpoint.json`, `<state>.write-log.jsonl` (one JSON
  record per path a tool call named, with `written`, `size`, `mtime`; one
  rotated generation), and `<state>.audit-write-report-<checkpoint_id>.txt`:
  hook-owned scratch beside run state. Only the hooks write them.
- `<revision-folder>/proposals/<checkpoint_id>-run-setup.patch` and
  `-graph.patch`: the updater's two proposals, never mixed;
  `record_revision.py propose` and `apply` read them.

**Declared scratch roots.** The write report scans the run workspace root
(the parent of the run-state directory, `increments/` included) and the
revision root, and nothing else. A bare `/tmp` or `$TMPDIR` path is not a
declared root: work written there is a finding at the next checkpoint.

**Enforcement table** for the four controls this section owns, in the
`references/pave-spec.md` §9.14.1 form. Field basis: the 2026-09 live run,
recorded in the revision log.

| Control | Level | Why this level; known limit |
|---|---|---|
| Audit-due block (stop guard) | REMINDER, block-once | The advisory version was answered "lgtm" almost every time while invented form grew for two weeks; a late audit loses nothing, so it blocks at most one stop per cooldown and never a traversal |
| Lead-only write deny (`write_limits.deny`) | BLOCKING, reversible by an apply step | The cut form is the lead's own habit and the cost is paid at the write; a landed glob against a Write/Edit path or Bash argument is exact. Binds the lead alone: a seat gets the reason as advice, never a block. Paths compare by on-disk spelling plus casefolded basename |
| No-retry copy rule (`<stem>-r<N>.<ext>`) | BLOCKING, remedy in the block text | Retry copies are what every later reader loads, and most were first written through shell commands the file-write matcher never saw. Limit: a path built inside a script body is invisible before the write; the write log catches it after, and the report's regrowth count trips the next checkpoint |
| Cycle state: sidecar, write log, findings record, live graph and `revisions.yaml` outside an apply step | BLOCKING on the named writes | The audited actor must not be able to type the cycle state, or a blocked lead closes its own audit. Limit: `apply --review` is lead-typed, so a landing binds the drafter's identity and the patch bytes, never the reviewer's verdict; pave-revisions rule 8 (two flat checkpoints pause the loop) covers this |
