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
    parity-scorecard.yaml
    backlog.yaml
    debt-ledger.yaml
    failure-fingerprints.yaml     # absent file = legitimate empty set
  run/                            # this run only
    run-state.json                # lead-only; shape authority is
                                  #   schemas/run-state.schema.json
    usage-ledger.md               # lead-only; one section per run close
                                  #   (evolution contract rule 8)
    intake-preflight/
    delta/
      <target-id>/report.md       # + per-target event files (grants,
                                  #   re-trace events, verdicts)
      index/current/              # run-level delta index (only read path;
                                  #   superseded content is deleted in place)
    backlog/
    closure/
  campaigns/<campaign>/
    kickoff/
    approvals/DECISIONS.md          # one file per campaign; each user
                                    #   decision is one dated section (§2)
    design/
      current/                    # design-lap artifacts of the LIVE lap;
                                  # superseded lap artifacts are deleted at
                                  # re-entry (no archive dirs - history is
                                  # the record's revision log + run state)
    increments/
    attempts/                     # attempt, fingerprint, lease,
                                  #   recovery records - one file/event
    measurements/
      procedures/
      baseline/
      runs/                       # run records + defect records
    verdicts/
    rederivations/
    pr/
    closure/
  reviews/<campaign>/             # reviewer stream + gate-2 objection
                                  #   mirror - reviewer-owned names
```

## 2. Write ownership

| Path | Sole writer(s) |
|---|---|
| cross-run/* | lead only, serialized, at campaign closure (intake reads) |
| run/intake-preflight/ | verify_run_preconditions |
| run/delta/<target>/ | that target's trace_target_delta instance (report landing paths: §4.7) |
| run/delta/index/ | assemble_delta_report |
| campaigns/*/kickoff, approvals | assemble_kickoff_contracts (+ gate records by review_campaign_design, close_campaign) — write-once per decision: a later user decision lands as a new dated section or addendum beside the recorded one, never as an edit of it |
| campaigns/*/design/ | the design sibling that produces each artifact; assemble_design_record writes the record and performs superseded-artifact deletions; the lead writes the bookkeeping edits recorded at review_campaign_design (one revision-log line in the document; the edit summary and the new digest ride the gate traversal's `completed_outcomes[].note` in run state, 500 characters — longer content stays in the document's revision log), the landed-block collapse at scope_next_increment (digest in the lap record), and the record delta on a verified block diff (digest in the record's revision log and the traversal note) |
| campaigns/*/increments/ | scope_next_increment (lap records — the lead's on lead-settled laps), realize_increment (evidence records), record_changeset (changeset) |
| campaigns/*/attempts/ | acquire_hardware_lease + lead (lease records), replicate_campaign_venv, execute_attempt_loop, recover_leased_host — one file per event, append-only |
| campaigns/*/measurements/procedures | realize_measurement_procedures |
| campaigns/*/measurements/baseline | capture_baseline_reference |
| campaigns/*/measurements/runs | run_candidate_measurements (run records AND defect records) |
| campaigns/*/measurements/ (bundles) | stabilize_and_package_evidence (defect records beside its bundles) |
| reviews/<campaign>/ | the adversarial reviewer seat only |

Run state (`artifacts/run/run-state.json`) is lead-only. No node writes another node's
directory (except a landing path §2 names); a stray write is an effect
violation before it is a count bug.

## 3. Precedence and supersession

- Which-file-wins: `current/` is the only read path. Archive and
  snapshot directories left by the earlier convention are frozen
  residue: never read them except for provenance, never add to them.
- On re-entry into a node, the declared evidence path keeps only the
  current revision's output; superseded files are DELETED (the deletion
  rule) - the supersession is recorded as the record's one-line
  revision-log entry plus the run-state entry, never as an archived
  copy. The current record carries an explicit current-record marker,
  so "current" is a file fact, not a directory-listing inference.
- Non-indexed intermediates (scratch, delegate transcripts not cited by
  any record) go under the owning node's directory in `scratch/` and are
  never cited as evidence.

## 4. Pinned shapes (single authority; consumers cite, never restate)

### 4.1 Findings-record shape — FIVE fields

Every adversarial-review finding record carries exactly:
1. stable per-finding label
2. cited location
3. defect class, prefixed `material` or `bookkeeping` (a bookkeeping
   finding names the surface it edits and never selects an outcome); a
   material finding also carries `repair-introduced` when the graph's
   `design_loop_within_bound` class applies
4. required change
5. measurement content hash(es) — review_measurement_verdict only

One findings record per campaign per review node lives under
`reviews/<campaign>/`: each round appends one dated section, each finding
is one line carrying these fields plus a pointer to its evidence, and a
recorded absence of findings is one line. A round never opens a new file.

All fingerprint-pair producers and consumers cite this entry; each
consumer declares the key its detector uses — a subset of these fields,
or the pair entry's enumerated fingerprint triple where the pair
declares one (pair 1).

### 4.2 The EIGHT producer/consumer pairs

1. **impl/review**: review_implementation emits findings records in the
   §4.1 shape; every material finding additionally carries the binding
   fingerprint triple **(increment id + surface + defect class)** —
   deliberately COARSER than fields 1-2, adopted at design so
   round-over-round comparison stays a mechanical string match even
   when labels or exact locations are re-minted between rounds.
   scope_next_increment's no-progress detector keys on exactly that
   triple plus the no-new-evidence qualifier (§4.3). Falsifier, both
   directions: keying on the per-finding label lets relabeled findings
   evade the detector (the loop's declared exit fires never); the
   coarse triple WITHOUT the no-new-evidence qualifier false-fires on
   converging work (two different findings sharing increment, surface,
   and defect class read as one repetition). The item-scoped qualifier
   NARROWS, never eliminates, that false fire — its anchor is the
   ANSWERING record (§4.3): findings separated by no answering record
   stay distinct through new passing evidence, while a triple re-raised
   AFTER a passing repair is DELIBERATELY read as repetition — the
   narrowed protection costs at most one operator-visible re-derivation
   round, against an unbounded loop invisible behind gate 2.
2. **adjudicate/verdict-review**: adjudication_verdict shape;
   review_measurement_verdict keys on fields 1-3 + 5.
3. **costing fingerprint** — CLOSED no-finding: costing's stall leg 2
   (set-not-materially-shrunk across two re-entries) is
   fingerprint-independent; recorded for completeness.
4. **prepare/pr-review**: findings-record fields 1-4; frozen build-unit
   interface.
5. **repo fingerprint file + this-run attempt-log**, split by horizon:
   `cross-run/failure-fingerprints.yaml` = prior runs, lead-merged at
   closure; `attempts/` = this run. Guards the identical-retry forbidden
   effect. Falsifier: a format mismatch between the two readers silently
   disables the tier-1 gate.
6. **recovery-record shape**: writer recover_leased_host; readers
   recover_leased_host (allowance derivation — never a stored counter)
   and acquire_hardware_lease (roster filtering, c10). Falsifier: two
   readers deriving allowance state differently re-lease a
   once-recovered host, voiding the flap bound.
7. **lease-record shape**: writers acquire_hardware_lease + lead;
   readers acquire, recover, lead. Carries host identity markers,
   markers-checked provenance, the boot-identifier field, and event
   ordering so record-before-report is checkable. Falsifier: divergent
   normalization confirms a lease the recovery pre-check later rejects.
8. **defect-record shape**: path pattern under `measurements/runs/`
   (and beside stabilize's bundles) with FIELDS: measurement id;
   content-derived defect identity (the procedure or comparison indicted
   plus the property mismatch observed — never a free label, never a
   budget key); the NEW-relative-to-prior file-list field (§4.4); the
   invalidated-comparison enumeration (re-capture scope); the defect
   each revision entry repairs. Writer: run_candidate_measurements
   (stabilize writes its own beside bundles). Readers:
   realize_measurement_procedures re-entry targeting; the tier-1 novelty
   predicate; stabilize's unfiltered count. Falsifier: divergent
   derivation splits or merges re-entry scopes.

### 4.3 No-progress detector predicate — one shape, two bindings

Shape: *same named key (per-boundary key definition) AND no new
gap-closing evidence of that boundary's declared subject since that
binding's declared ANCHOR EVENT* — never a lap counter, never a
directory conjunct. Each binding declares its anchor below: the lap
that named the key, or the key's last answering record. The anchor is
part of the pin — a consumer that swaps one anchor for the other
inverts the detector's behavior on exactly the laps it must catch.

- implement_increments binding: key = the pair-1 fingerprint triple
  (increment id + surface + defect class; a directory-unchanged
  conjunct is false on exactly the laps it must catch). ANCHOR = the
  key's last ANSWERING record, never the lap that named it: "no new
  PASSING increment evidence record for that item since the triple was
  last answered" — the answering record itself is the anchor, so a
  finding re-raised identically after its repair's passing record, with
  nothing newer, satisfies the conjunct and fires the detector (the
  graph's no_new_route anchor clause cites this; a doer-reviewer
  disagreement loop routes out instead of spinning behind gate 2). The
  answering-record anchor is needed HERE and not on the design side
  because an increment can pass its declared acceptance while the
  reviewer's finding still stands — passing and finding-closed are
  different predicates, so instrument-determination alone cannot filter
  the disagreement case. That pin binds limb 1 alone — the detector's
  findings-history limb. The graph's second limb reads this boundary's
  own lap records (key = the named gap or stuck increment) and anchors
  on the NAMING LAP, as on the design side and safely so: a stuck
  item's key and its clearing evidence are settled by the same
  instrument, the item's declared acceptance, so no
  answered-yet-recurring state can exist there — an item cannot
  simultaneously hold a passing record and be re-selected as stuck,
  and a coverage gap cannot recur against the very check that is its
  acceptance.
- assemble_design_record binding: key = the named gap; subject = the
  OWNING SIBLING'S produced artifact (unrelated siblings write every
  lap). Both qualifiers mandatory: "no new gap-closing artifact FOR
  THAT GAP". ANCHOR = the lap that named the gap — no answering event
  exists for gaps; a gap-closing artifact produced after naming IS
  progress, so the naming-lap anchor is correct here, and on the
  detector's lap-records limb on the ground given above.
- "Gap-closing" is INSTRUMENT-DETERMINED: defined by the
  completeness-checklist re-run (assemble_design_record's self-check),
  never the owning sibling's assertion. "Passing" is likewise
  instrument-determined.
- d7 exemption: the commitment-absent gap (registration artifact
  present, lead run-state commitment entry missing) is EXEMPT from the
  detector's key space — it is not a design gap and no design artifact
  can close it.

Falsifier beside the pin: dropping either qualifier makes the test
conjunctive-unbounded (any-new-artifact reading) or false-firing
(converging work exhausted early). For the implement binding the
retained, item-scoped qualifier NARROWS, never eliminates, the
converging-work false fire (pair 1's direction-2 falsifier states the
same): the residual fire — a different finding sharing the triple after
a passing repair — is deliberate, bounded at one operator-visible
re-derivation round; "fires never" is the worse failure.

### 4.4 Shared repair budget

PER-MEASUREMENT, TWO-TIER. One instrument, two predicates — both tiers
derive from the SAME defect-record directory by path:
- **Tier 1 (filtered)**: suggested three repair passes per declared
  measurement WITHOUT NEW EVIDENCE. Novelty is the recorded
  NEW-relative-to-prior file-list field on each defect record —
  RECOMPUTABLE by any reader by diffing the record's cited file set
  against the prior record's for that measurement (never a writer
  assertion; if it ships assertion-grade, tier 1 is advisory and this
  entry must say so).
- **Tier 2 (backstop)**: suggested NINE total passes per measurement,
  counted UNFILTERED by path — count all defect-record files for the
  measurement; tier 1 counts the subset whose novelty field is false.

SINGLE-DEFINITION rule: both contract-text budget readers
(run_candidate_measurements and stabilize_and_package_evidence) cite
THIS definition; two readers can never normalize the bound differently.
At either threshold: record the defect, complete forward; stabilize
settles declared_measurement_unproducible; never route backward on a
spent budget. At-ceiling behavior rides runs' forward-completion clause
— no separate machinery; the false-exhaustion falsifier reduces to
ceiling-count honesty. Defect identity is a record FIELD (§4.2 pair 8),
never a budget key — relabeling cannot extend either tier.

### 4.5 Registration record shape

One shape for all three idiom sites (intake criteria, gate-2 comparator
commitment, adjudicator reads): **subject, digest, timestamp** —
schema'd once. The comparators_preregistered check compares the
commitment timestamp against every measurement artifact.

**Instrument-liveness pair**, registered per criterion in this same
write-once record: the VALUE that must appear as evaluated in the
evidence for that criterion, and the TRIPWIRE input the procedure must
fail on. Both are adjudicable values, so the P9 digest fixes both.
`procedures_smoke_verified` reads the tripwire result out of the smoke
record; `acceptance_threshold_evaluated` reads the value out of the
evidence bundle (§4.6). A criterion for which no tripwire exists is
`criteria_unadjudicable`: an instrument nobody can make fail grades
nothing. Rationale and the measured cases:
`references/measurement-pitfalls.md`, "Prove the instrument before its
verdict counts".

### 4.6 Evidence index

Element set pinned here, three consumers (review_implementation a4/a5,
the coverage-gap sub-class (b) instrument, prepare_pr): every planned
increment resolves to a passing evidence record; the index binds
increment id -> evidence file(s) -> acceptance command + exit code.
- **Exit-code discipline** (all transcript-bearing artifacts, run-wide):
  every command transcript records the verbatim command line, raw
  output, and the numeric exit code; a missing exit code makes the
  transcript non-evidence.
- **"Ported code" / "added-modified lines"** (one definition for the
  NxDI import scan, review_implementation a3, and the (c) re-run
  instrument): the added and modified lines of the campaign branch diff
  against the pinned base — never whole files, never upstream context
  lines.
- **Scan-completeness discipline** (all census-bearing artifacts,
  run-wide, beside the exit-code discipline): a reported hit count — a
  zero above all — carries the commit scanned, the tree state it was
  scanned in (clean, or the untracked and modified paths), and the scan
  tool's own completion signal. A tool whose validity depends on a clean
  tree runs in a fresh throwaway worktree at the commit under test,
  because untracked build artifacts make a long-lived checkout
  permanently dirty and the tool then reports an invalid scan as a
  result. A zero nobody can show complete is not evidence. The controls that
  prove a channel fired at all are `references/measurement-pitfalls.md`, "A
  zero is evidence only with a firing control" — the implementer runs these
  scans, so read the duty there instead of assuming a measurer brief carries
  it.
- **Evaluated-threshold record** (every evidence bundle, per criterion it
  realizes): the value read, the threshold it was compared against, the
  result of that comparison, and the negative-control result from that
  procedure's smoke record (§4.5). An exit status is not an evaluation,
  and an empty record makes the bundle incomplete.
- **Revision phrasing rule**: provenance identifiers at record time,
  content-identity predicate downstream, SHA-equality nowhere.

### 4.7 Scan boundary conventions

- Per-target report path: `run/delta/<target-id>/report.md`; report
  metadata carries the scan-entry-id stamp.
- Grant files: one per re-trace grant under the target's directory,
  carrying the scan-entry id; re-trace counters are DERIVED by counting
  per-target event files under a scan-entry id — never a stored integer.
- Assembly record homes: coverage-diff, per-target verdicts, grants,
  index (current only) as laid out in §1.
- **Report-hash scope + write ownership live in THIS entry together**:
  the hash covers the report file and its cited transcripts; report
  content originates only from trace_target_delta instances — so "hash
  changed => a tracer produced new content" reads directly off this
  entry (the bound-exhaustion guard's soundness rests on that
  implication; drift between hash scope and ownership silently unbacks
  the guard). Two first-class landing paths carry that content: the
  seat writes its report file itself, or — when the harness refuses a
  seat's report write (observed: the harness's subagent report-file
  guard) — the LEAD transcribes the seat's handed-back report text
  verbatim, stamps the report's metadata with a transcription block
  naming the seat and citing the hand-back (teammate message or seat
  transcript reference — one citation per transcribed report, the
  preflight precedent), and records the transcription per target under
  run state's `notes` (the schema's free-form home). Either way the
  content author is the tracer, never the lead; a report-hash change
  with neither a seat write nor a recorded transcription behind it is
  a violation.

### 4.8 Design boundary conventions

- Every design-lap artifact is stamped with the design-entry id; the
  record assembler REFUSES TO RUN without one (external runtime
  dependency, carried in briefs).
- Superseded lap artifacts are deleted at re-entry (§3); the read
  path holds only the live lap, and lap history is the record's
  revision log plus run state.
- record_incomplete exhaustion predicate: cited from §4.3 (design
  binding) — the same named gap recurs AND no gap-closing artifact for
  that gap since the lap that named it.
- The consecutive-read stability count + MINIMUM RE-READ SPACING are
  declared in the campaign design record, authored by
  preregister_acceptance; stabilize reads them, never defaults.

### 4.9 Measurement smoke/run join

Smoke evidence is named per procedure REVISION; every run record cites
the procedure revision it executed. The smoke-before-run predicate is
therefore checkable record-side: a run record citing revision N is valid
only against a passing smoke record for revision N — the run-record half
of the smoke-check ordering join.

### 4.10 Durable host state

Operational definition, defined once: durable host state = mutations
that outlive the session — persistent writes INCLUDING CACHE WRITES,
restarts, reboots, resets. The ephemeral lifecycle of a serving process
launched for capture or smoke is NOT a durable mutation. Cited by
capture_baseline_reference's read-only prohibition and the i1
divergent-normalization falsifier.

**Boundary ruling (where the definition binds)**: a cache write
performed BY the capture-launched serving process is still a durable
mutation when it lands in a persistent cache location — the lifecycle
exemption covers the process itself (start, stop, in-memory state, and
process-scoped scratch removed at teardown), never a write that
outlives it. Capture must confine any cache the serving stack would
write to run-scoped scratch removed at teardown, or refuse and record
why — the graph door for that refusal is baseline_unusable's
unredirectable-persistent-cache-write disjunct (routed to
re-derivation). Rationale: without this ruling the definition is
ambiguous exactly where it binds (a capture launch typically wants to
write a cache), and either misreading is harmful — strict reading emits
a false baseline_unusable on lap one; loose reading permits the
inference-time cache write the prohibition was minted to forbid.

**Instances on this hardware class**, each measured on a prior campaign at
Neuron SDK 2.32 (L-136, L-139, L-147, L-159), so the definition binds where a
run actually meets it: the kernel toolchain's own
intermediate cache, written outside the run root and outside any cache
variable you set, and possibly holding a co-tenant's artifacts; a shared
compile cache whose key directories any re-trace rewrites; a long-lived
serving checkout that untracked build artifacts make permanently
git-dirty; and instance-store devices that are encrypted at rest, whose
never-written blocks read back as pseudorandom bytes. The duties that
follow: rename a cache partition aside inside a root you own and never
delete a shared one (P2's hook refuses both shared roots); run any tool
that gates on tree cleanliness in a fresh throwaway worktree at the
commit under test (§4.6); and prove a device unclaimed from the absence
of a partition table, filesystem signature, holder, and mount, swap or
fstab entry, plus unchanged write counters across a quiet window — never
from reading it back as zeros.

### 4.11 Cross-run artifacts

The four artifacts live at the §1 `cross-run/` paths, committed to git.
Lead is the only writer, serialized at campaign closure.
`failure-fingerprints.yaml` absent reads as a legitimate empty set,
never an error (first run bootstrap).

### 4.12 Living-document caps

A living document is edited in place and holds current state only: the
increment plan, the design record, the regression matrix draft (upgrade
route), the per-target delta report (`run/delta/<target-id>/report.md`),
the costing and backlog report (`run/backlog/`), and the PR evidence
package (`pr/`). Each has a cap of 400 lines AND 60 KB; one that needs
more declares its own cap here, with the reason. Three classes sit
outside the cap and are never shrunk: the write-once registration record
(§4.5 — the P9 digest fixes it), append-only records (`kickoff/` and
`approvals/` per §2, the reviewer stream under `reviews/` that the §4.3
detector reads across rounds), and
world-produced evidence (transcripts and probe records at their evidence
paths). The write-for-reader duty still covers every reader-facing
`.md`; the cap does not. The cap is kept by shrinking, never by an
archive:

- A landed increment collapses to one ledger row — id, plain name, tier,
  commit, evidence pointer. The row carries its full per-increment
  contract by that pointer (§4.6: acceptance command and exit code at
  the evidence record) and by the registration record; a planned
  increment's contract stays inline. Its frozen values stay in the
  registration record (§4.5), the single write-once file; the P9 digest
  binds that record only, never a block of the increment plan, which
  must stay free to shrink.
- A count table inside a living document is script output: it carries
  its recompute command and is never hand-edited.
- A living document carries no defensive prose: no argument history, no
  ruling quotes, no per-clause justification essays. A finding's
  disposition is one line that points at the findings record.
- A re-entry brief names the sections the seat may touch; a whole-file
  reconciliation is its own briefed lap.
- `scripts/measure_artifact.py <path>` is the one size instrument. The
  reviewer records each living document's lines and bytes every round;
  over cap is a material finding, and the next lap is a deletion lap
  before any new content. `write-for-reader.sh` names an over-cap
  document with its size at the write; the writer classifies it, and a
  record or transcript is left alone.

### 4.13 Write for the reader

The prose duty, defined once for every seat and every brief. It covers
every document a person will read: delta reports and the backlog, design
records and increment plans, verdicts, review and decision records,
rederivation records, PR packages, closure records.

- Concise simple plain english. Each entry leads with one sentence saying
  what happened and why.
- An identifier is a pointer, not a noun: pair it with its plain name at
  first use ("the rotary increment (`inc-025`)"), and never leave an
  identifier chain where a sentence should stand.
- Digests, counts, and checker output live in run state or the check's
  own file, cited in one line, never interleaved with the narrative.
- Every number lives in exactly one file that everything else cites.
- A reader learns what happened, what changed, and what is still open in
  one pass.
- Superseded prose is deleted in place with one revision-log line, never
  archived.

Exempt: working state written for the next agent — attempt, lease,
measurement, increment, index, and intake-preflight records, and run
state itself. The cap in §4.12 is a separate duty with a separate scope:
this one covers every reader-facing `.md`, the cap covers living
documents only.

Enforcement: `write-for-reader.sh` re-presents this duty on document
writes (advisory, never blocking) and names an over-cap document with its
size; the adversarial reviewer treats a reader-facing artifact that fails
either duty as a material finding.
