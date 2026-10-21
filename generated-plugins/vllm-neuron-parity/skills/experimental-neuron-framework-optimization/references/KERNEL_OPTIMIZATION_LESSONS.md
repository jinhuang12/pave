# Kernel Optimization Lessons

Merged from `KERNEL_OPT_LESSONS_gaal.md`, `KERNEL_OPTIM_lucy.md`, and
`KERNEL_OPTIMIZATION_LESSONS.md`. Overlapping entries have been consolidated into one statement each;
contradictions between the sources were tracked in `KERNEL_MERGE_CONFLICTS.md`, resolved there, and are
now integrated here — the resolutions are recorded at the end of the provenance appendix.

**Purpose.** A transfer-learning checklist of *recurring mistakes* in custom-kernel performance
analysis and optimization — how to read per-engine/DMA execution from a profile, classify the binding
resource, construct a defensible floor, and rank kernel-level levers. Each entry is a mistake
pattern, its mechanism, how to detect it, and a rule. Serving/deployment/benchmarking lessons live in
the companion `FRAMEWORK_MODEL_OPTIMIZATION_LESSONS.md`.

**Scope and notation.** Hand-written accelerator kernels (an NKI-style tiled DSL over on-chip
buffers) backing transformer inference: a context/prefill kernel family and a token-generation/decode
kernel family, with quantized weights and tensor parallelism. Symbolic parameters: `L` layers,
`H` hidden, `I` intermediate, `S` sequence/token tile, `B` batch, `TP` degree, `n_kv_heads` KV heads.
Engine names (matrix/vector/scalar/DMA), on-chip buffer (SBUF-style) and accumulator (PSUM-style)
terminology are used generically.

**Severity.** 🔴 changes a conclusion / misleads · 🟠 wastes time or risks correctness · 🟡 sloppy but
usually caught fast.

---

## How to read this document — the transfer contract

**This is a model of failure *classes*, not a record of any run.** No entry names a campaign, model,
host, kernel, or target, by design — such details let a reader reconstruct "which run was this?" and
inherit conclusions from a regime that may share nothing with their own.

**Numbers here illustrate a mechanism; they are never results and never expectations.** This matters
acutely at the kernel layer, where a magnitude drives a go/no-go: figures like "a cache dtype flag
paid off" or "a config sweep beat every kernel change" are exactly what a reader lifts to justify
skipping or ordering kernel work. Read them as *directions worth testing*, never as payoffs to
expect. **A lever earns a place in your plan by measurement in your own regime** — D2 is the
in-document demonstration that the binding-op *ranking* itself inverts between two parallelism
degrees, so a magnitude certainly does not survive a change of regime.

- **A magnitude never orders your work.** It may justify running an experiment, never skipping one,
  ranking one, or recording a decision (E6 requires floor terms, not precedent).
- **Nothing here says a target is unachievable or that an approach is settled.**
- **Never infer the source run** from incidental detail. If you conclude "this came from a run like
  mine, so its outcome predicts mine," you have left the document's scope.

The full statement of this failure class is L23 in the companion document; the entry template is at
the end of that file and applies here too.

**Layout.**

| Part | Question it answers |
|---|---|
| A | Does the artifact exist, and is it valid for the topology and phase? |
| B | Am I reading the profile correctly? |
| C | Is this number attributable to a kernel at all? |
| D | What actually binds? |
| E | Is the floor I am dividing by sound? |
| F | Layout, engines, and on-chip resources |
| G | Which levers exist, and which layer do they belong to? |
| H | Is my experiment capable of detecting what it tests? |
| I | Environment, toolchain, and infrastructure failures |
| J | Decision trees |
| K | Staged pre-flight checklists |
| L | Summary table |

---

## Part A — The artifact must exist and be valid for the topology and phase

Every item here sits *upstream* of any analysis. None of them announces itself; each yields either no
artifact or a plausible-looking partial one. Before the first ranking query: confirm the artifact
exists and is non-trivially sized, confirm the capture method is valid at your shard degree, confirm
the graph is the one you deploy, and confirm the target phase's instructions are present.

### A1 🔴 Letting the serving layer starve the kernel layer of its one required input

**Pattern.** Kernel analysis has exactly one hard input: a phase-verified device trace of the target
graph. When the serving/deployment layer is allowed to consume the entire device-time budget on
end-to-end configuration sweeps, that input is never produced and the kernel stage **silently never
happens** — no error, no missing-step warning, just an effort that ends with a scaling curve and no
mechanism. It reads as "we ran out of time" when it is really an ordering defect: the cheapest
artifact in the whole effort (one capture on an already-warm server) was scheduled behind the most
expensive activity.

**Prevention.**
- Capture the profile as the **first** device action after a single baseline launch, and capture it
  *inside* that already-warm server rather than as a separate launch. Kernel analysis then has its
  input regardless of what happens to the rest of the budget. Nothing that costs device time — no
  configuration sweep, no capacity probe, no A/B — is scheduled ahead of it.
- State the dependency explicitly when planning: no profile ⇒ no bottleneck class ⇒ no defensible
  kernel lever. If the plan does not contain a capture step before the sweep, the plan cannot produce
  a kernel conclusion.
- The free per-phase serving-log read (A2) costs **no device time**, so it is not competing for this
  budget and is not what the ordering rule is about: read it from the same warm server's log, and use
  it to validate the capture rather than to defer it.
- Before reporting "no kernel headroom found," confirm a trace was actually produced and read.
  **Absence of a finding and absence of a measurement are different results** and must not be
  reported the same way.

### A2 🟠 Paying device time for a per-phase measurement the serving log already gives you

**Pattern.** Standing up an extra capture, an extra launch, or a bespoke benchmark to obtain per-phase
capability that the serving engine already prints — or inferring it by **subtracting across separate
captures**, which is not a measurement at all (B8, C5).

**Mechanism.** Where prefill and decode are mutually exclusive phases, the engine's periodic metrics
lines are a zero-instrumentation measurement of each phase in isolation:

```
prompt throughput: <P> tok/s, generation throughput: ~0 tok/s   <- pure prefill window
prompt throughput: 0 tok/s,   generation throughput: <D> tok/s   <- pure decode window
... running: <B> reqs, waiting: <W> reqs, cache usage: <U>%
```

From `P`, `D`, `B`, `W`, `U` alone you can derive the decode **step time** as `B / D` and cross-check
it against the client's inter-token latency (agreement validates both, disagreement is a stop); build
a serving model `throughput = OUT / (IN/P + OUT/D)` and validate it against the end-to-end benchmark;
and detect a capacity cap (running batch far below configured, with a waiting queue) with no memory
profiling at all (see D6).

**Rule.** Grep the serving log of the same warm baseline server for phase-separated windows and the
running/waiting/cache-usage fields, and paste them beside every kernel number you report. This read
costs **no device time**, so it neither competes with nor defers the capture (A1) — take both off the
one baseline launch. Use the phase numbers to build and validate the serving model, and to cross-check
the capture; use the profile to explain the gap between measured phase capability and its floor. Never
substitute a subtraction across captures for either.

### A3 🟠 Choosing a capture method that is invalid for the parallelism degree in use

**Pattern.** The convenient capture path — replaying one compiled graph standalone in an isolated
harness — is chosen without checking whether it is valid for the topology being studied. For
multi-shard graphs it is not: an isolated replay **undercounts** real cross-rank collective traffic
(observed on one stack as ~12.9 ms replay vs ~18 ms live for the same graph), captures no host-side
dispatch events at all, and at high shard degree can simply fail to reconstruct the baked-in replica
groups. Conclusions about a "device floor," and any serving-overhead figure derived by subtracting it
from a wall-clock, are then wrong in a direction that flatters the device.

**Prevention.**
- For any multi-shard graph, plan the **live** capture path (attach to the running server) from the
  start, and quote per-request device time from the live execution window. Reserve isolated replay for
  per-op *structure* only, never for absolute time or for a floor something else is subtracted from.
- Budget the profiler's own resources into the **first** compile: device profiling needs a fixed
  memory pool plus a per-core notification buffer, and on a near-full-memory model it will silently
  fail to start — generation succeeds, no trace is written, the output directory stays suspiciously
  small. Lowering the memory-utilization knob carves out the headroom, but that knob changes the
  graph's cache key and forces a recompile, so set it on the first launch rather than discovering the
  need on the third.
- Treat a suspiciously small trace directory as a **failed capture**, not a small workload.

### A4 🔴 Instrumenting a capture against a default watchdog, then blaming the graph

**Pattern.** Instrumenting a subset of ranks — typically the rank-selection field's default of **rank
0 only** — while leaving the runtime's per-execution watchdog at its default. Device-trace
instrumentation slows the
instrumented rank's execution; a collective is a **barrier**; so the one profiled rank becomes the
straggler and every other rank blocks waiting for its completion notification until the runtime's
per-execution watchdog (**30 s by default**) fires — on *all* ranks at once. Observed: 16 ranks of a
16-way shard group all raised `execution timeout (30000 ms) ... waiting for execution completion
notification` within ~130 ms of each other, each on its own graph hash, leaving a 21 KB (empty)
device trace.

The expensive part is not the failed capture — it is the **misattribution**. It was written up as "the
per-op device path *hangs on this graph*," i.e. blamed on the graph, which is immutable and
unfixable, rather than on the capture configuration, which is neither. Downstream, "the graph cannot be
profiled per-op" is what justified reporting a lumped bucket table instead of a ranking (B12), which is
what let an unfalsifiable "we are at the hardware ceiling" verdict stand (A10).

**Prevention.**
- **Raise the per-execution watchdog before any instrumented capture** (the runtime exec-timeout knob,
  and the framework's execute-model timeout). This is the fix, and it is the step that was skipped: no
  launch script in the effort set an exec-timeout knob at all. Instrumentation inflates step time by
  integer factors (C4), so a default watchdog sized for uninstrumented steps *will* fire, and on a
  collective graph it fires on every rank at once.
- **Pinning one rank is the working default** (A7) — the cost of instrumenting the whole shard group is
  a much larger trace plus a per-rank notification buffer, and it is rarely what you want while
  iterating. What a single-rank capture requires is the raised watchdog and the awareness that the
  instrumented rank is now the straggler for every collective in the step; its *absolute* per-step
  numbers carry the inflation of the whole barrier, so only shares travel (C4).
- Escalate to **all ranks in the shard group**, or to **system-trace-only**, when the question is
  cross-rank: per-rank skew, straggler identity, or collective exposure attributed per participant. The
  offline replay path states the same invariant explicitly for that case ("profiler worker count must
  match the shard degree; a mismatch causes collective failures").
- When a capture fails, **diff the capture configuration against a capture that worked** before
  concluding anything about the graph. "This graph can't be profiled" is a claim about an immutable
  object and should be the last hypothesis, not the first. The tell here was already in hand: the same
  rank-selection config had captured successfully at a different operating point, so the failure was
  operating-point dependent — instrumented per-rank step time crossing a fixed watchdog — not a
  property of the graph.

### A5 🟡 Sizing the capture window without regard to the ring buffer or the ingest cost

**Pattern.** Two symmetric failures come from the same unexamined knob. Capture too naively and the
heavy prefill lands first, overflows the device trace ring buffer, and the capture stops *before* the
phase you wanted — leaving the target graph with zero engine/DMA data while collective ops still
appear, which reads as a real-but-odd profile rather than a truncated one. Capture too widely and the
trace becomes multi-gigabyte, takes hours and tens of GB of RAM to ingest, and can silently produce
**empty** tables. The same trap appears as a whole-trace JSON dump that buffers hundreds of GB in RAM
with zero output.

**Prevention.**
- Skip past warm-up/prefill with an explicit iteration delay so the trace begins mid-target-phase with
  an empty buffer, and **scale that delay with batch size** — a value that works at small batch
  overflows at larger batch, because each step dispatches more work.
- Keep the window small (a handful of steps, not tens). **Bound every capture and ingest** (`timeout`,
  scratch data-path); confirm output grows within ~1 min or kill and rethink.
- **Ingest to parquet and query specific tables** (`Instruction`, `ActiveTime`, `DmaPacket`, `CcOp`,
  `OpcodeSummary`) — never serialize the whole trace. If ingestion yields empty tables, the capture
  was too large — re-capture smaller rather than debugging the ingest.
- Sanity-check a fresh capture for the *presence* of the target phase's instructions before analyzing
  it (A6's gate; buffer overflow is one of its most common causes).

**Addendum — when you re-capture smaller, change ONLY the window.** The retry after a failed ingest is
itself an experiment, and one-variable hygiene applies to captures exactly as it applies to A/Bs.
Observed: a re-capture shrank the window *and* changed shard degree *and* changed dtype *and* changed
batch width in one step; it then failed differently (a collective-barrier timeout, A4), and because
nothing was held fixed there was no way to attribute the new failure to the window or to the
configuration — and the successful earlier capture was no longer comparable, destroying the very diff
that would have identified the cause. Hold the served configuration fixed, vary the window alone, and
keep the failing and succeeding captures side by side.

### A6 🔴 Ranking kernel-level levers from a profile that holds the wrong kernels or phase

**Pattern.** Per-kernel DMA/engine ranking begins before confirming the profile actually contains the
kernels meant for optimization. If the capture is the wrong phase (e.g. a prefill graph when decode
was intended), the kernels ranked and the bottleneck class assigned are for the wrong graph entirely,
and a correct fix lands on an irrelevant kernel.

**Prevention.** Before ranking any kernel's DMA/engine time, read the per-instruction
source-location / kernel-name population and confirm the kernels present are the ones you intend to
optimize (decode vs prefill, attention vs MLP, etc.). **Zero instances of the target kernel = wrong
capture.** Kernel analysis inherits every upstream capture error — re-verify the artifact at the
*start* of kernel analysis, not only at the serving layer.

### A7 🟠 Scoping the capture for iteration: one tile, then the pipeline

**Pattern.** A multi-tile, multi-rank trace is captured and serialized wholesale while iterating on a
single kernel, so no instruction is traceable to a source line and every iteration costs a full
capture.

**Prevention.**
- **Profile a single tile first (`num_tiles=1`)** — every instruction is then traceable to a source
  line. Get the single-tile instruction sequence optimal, *then* build the multi-tile pipeline.
- **Pin one rank** and ingest to parquet. This is the default iteration scope even on a sharded graph;
  raise the per-execution watchdog first (A4), and read only shares from it, never absolutes (C4).
  Escalate to the full shard group or to system-trace-only when the question itself is cross-rank.
- **Re-profile after every change.** On a multi-resource asynchronous machine, predictions about which
  fix mattered are wrong more often than on a CPU — measure, don't assume. Confirm the binding
  bottleneck actually *moved*; a local speedup that doesn't change E2E is not a win.

### A8 🟠 Treating a reduced-size-model profile as numerically equivalent to the full model

**Pattern.** A reduced-layer profile is captured because it is cheap, and its per-step timings are
then quoted, scaled, or compared against full-model targets as if the graph were the same. It is not:
changing the layer count changes buffer/bucket geometry, so the compiler emits a **different graph**
that can independently fail to compile and whose per-step behavior is not guaranteed to match.

**Prevention.** Use reduced-size profiles for per-op *structure* and config shakedown, and never for
absolute numbers against an SLA. When a reduced profile must be extended to the full model,
extrapolate with an explicit **linear per-layer model** (fixed per-step overhead + per-layer cost),
state it as a model rather than a measurement, and validate the fixed/per-layer split against at least
two layer counts before trusting it. Derive nothing at all from a graph variant that failed to execute
(see H7).

### A9 🟠 Correlating a trace against artifacts from a different compile

**Pattern.** The profiler requires HBM headroom, so a memory-utilization or KV-budget knob is lowered
to free space. That changes the number of cache blocks → changes the compiled graph's I/O tensor
shapes → produces a **different compiled artifact with a different UUID**. The trace was captured
executing the *profiling* config's artifacts, but the operator then tries to correlate it against
artifacts from the *production* compile cache, and hits a UUID mismatch. Time is then spent searching
compile caches and trying every artifact file — none of which can work, because the trace literally
captured a different graph.

Beyond the wasted time: if the profiling config's graph has a structurally different cache shape, the
profiled per-op times may not represent the production config's behaviour (different tensor sizes in
attention, different DMA burst patterns).

**Prevention.**
- **Accept that the profile represents the config it was captured on.** If you changed a
  memory/capacity knob to fit the profiler, the profile reflects THAT config — do not correlate it
  with another config's artifacts.
- **Use the auto-copied artifacts from the profiling output directory** when opening the trace in a
  viewer; they match by construction. If they do not match (a bug or race), read the expected UUID out
  of the trace session metadata and search for that UUID in the profiling server's own compile cache,
  which may differ from the production cache path.
- **Minimize the graph-shape delta.** Prefer a knob that changes allocation without changing compiled
  shapes over one that changes block count. Note the measurement consequence: a knob that drops the
  concurrency cap below the compiled bucket makes the capture inadmissible as a kernel measurement (C2),
  so free the headroom without leaving the bucket underfilled, or fall back to the system trace.
- **A system trace does not need artifact matching.** For per-request device time, scheduling
  overhead, and collective fractions, the system trace alone suffices and avoids the entire matching
  problem. Reserve the full device profile for per-instruction engine-utilization breakdowns.

### A10 🔴 A failed capture is a configuration bug until proven otherwise

A10 is the chain formed by A3–A6 and C4, worth stating as a chain because it is how an entire
optimization effort reached a confident wrong answer:

> a capture was mis-configured (instrumented against a default per-execution watchdog it was certain
> to exceed) → the failure was attributed to the *graph* rather than the *setup* → the per-op layer
> was declared impossible → a lumped two-bucket split was presented in place of a ranking (B12) → the
> un-decomposed bucket was labelled a hardware ceiling → the target was declared infeasible.

Every individual step looked reasonable and the numbers in it were real.

**Rule.** Treat **"the profiler doesn't work on this graph"** as one of the highest-severity claims in
the effort, not a logistical aside: it silently deletes the only layer that can name a binding op.
Before accepting it, diff the failing capture against any capture that worked, raise the watchdog,
shrink the window, and check the rank scope against the question being asked. If the
per-op path is genuinely lost, apportion by parameter sweep (B13) and compare **marginals to their own
floors** — never let the effort proceed on a bucket total, because a bucket total is exactly the shape
of evidence that cannot be falsified.

### A11 🔴 Arming the capture window around a workload not yet proven to produce valid target-phase steps

**Pattern.** The capture window is bracketed (profiler start → drive requests → profiler stop) around a
driving workload whose shape was never dry-run. The requests reject **instantly** — the request shape
violates a serving constraint (input + output over the maximum sequence length), a chat/formatting template
inflates the token count past that limit, or an instruction-tuned model emits end-of-turn immediately — so the
window opens and closes over **zero** real target-phase steps. The resulting artifact is empty or step-free
and looks exactly like a profiler-tooling failure, so the fix is aimed at the profiler (watchdog, window size,
ring buffer) when the defect is in the workload construction. A warm-server window and device time are spent
producing nothing, and the mis-attribution can escalate into "this graph can't be profiled" (A10).

**Prevention.**
- **Dry-run one request of the exact shape, outside any capture window, and confirm it returns the expected
  number of decode/target-phase steps** before arming the profiler. Never let the first execution of a new
  workload shape also be the profiled window.
- Compute the token budget **including template/formatting overhead** against the serving limit before
  building a batch: assert `input + output ≤ max_sequence_length` with headroom, measured on a **real
  tokenization**, not estimated from raw character or word count.
- Apply a **step-acceptance check to the capture itself**: after `stop`, confirm the trace holds target-phase
  *kernel* instructions (not only collective ops) and a plausible step count. A window that captured zero
  target-phase steps is void and must be re-driven, not analyzed — a truncated/empty capture with surviving
  collective ops reads as a real-but-odd profile (see A5).
- When a capture comes back empty, **rule out the workload before blaming the tooling**: read the driver's
  request success count and the served step count first. An empty trace with **zero successful requests** is a
  workload-construction bug (this class); an empty trace with **successful requests** is a window/tooling bug
  (A5/A10). They have different fixes, and confusing them sends effort to the wrong layer.

---
### A12 🔴 Arming or stopping the profiler while a collective-bearing step is in flight deadlocks every rank

**Pattern.** A live capture is taken by toggling the profiler (start, or stop) through the serving
endpoint while requests are **actively running**. All ranks hang or hit a runtime undefined-state /
collective timeout (a reduce-scatter / all-reduce deadlock, an `nrta status` error), the capture is
lost, and the failure gets misdiagnosed as a model or watchdog problem.

**Mechanism.** Turning the on-device profiler on or off is itself a device operation that must be
sequenced against the ranks' collective schedule. Toggling it in the middle of an in-flight step that
contains a collective interleaves a profiler-control op with the collective's barrier, and the ranks
deadlock — one rank waits on the profiler op while the others wait on the collective. The identical
toggle while the server is **idle** is harmless.

**Rule.**
- Toggle the profiler only while the server is **idle**: arm it before dispatching the batch, use an
  iteration **delay** to skip warmup/prefill and land in decode steady-state, and let the profiler
  **auto-stop** at its max-iterations rather than calling stop mid-flight. Issue an explicit stop only
  after all requests have drained.
- To land the window on the target phase, drive the batch to steady-state decode first, then let the
  delayed arm fire — arming before any request captures only prefill (A11). These are the two halves of
  the same discipline: *what* phase the window lands on (A11) and *when* it is safe to toggle (this).

---

### A12 🔴 Concluding a collective-heavy graph "cannot be per-op device-profiled" after a wide or all-rank armed capture hangs — a MINIMAL single-rank window succeeds
**Pattern.** An armed per-op device capture of a deep, per-layer-collective graph hung the collective
(ranks blocked on a barrier), and this was taken to mean the graph is unprofilable at the per-op level,
so the effort fell back to a coarse system-trace or a lumped bucket. But the hang was governed by the
**instrumentation perturbation**, not the graph: a wide armed window (many profiled steps) and/or
profiling many ranks perturbs the collective enough that a barrier fails to release, and raising the
per-execution watchdog alone does not help when the barrier truly never completes.
**Rule (the working recipe — proven):**
- Profile a **single rank** with the **smallest possible armed window** — often **one profiled step is
  enough** for per-kernel attribution. The minimal window is the decisive lever; it keeps the
  instrumented rank close enough to the others that the collective completes.
- Set the **source-attribution debug environment** at compile so the ingested instructions carry
  source locations (per-kernel attribution needs this), and pass the correlating artifact explicitly at
  ingest.
- Raise the per-execution watchdog too (A4) — necessary, but on a true barrier deadlock it is **not
  sufficient**; the window minimization is what makes the capture complete. Distinguish the two by the
  runtime signature: a collective with a **START notification but no completion** is a deadlock (shrink
  the window), a plain per-execution timeout with completion-eventually is a straggler (raise the
  watchdog).
- "This graph cannot be per-op device-profiled" is the **last** hypothesis, not the first: shrink the
  window, profile one rank, set the debug env, and re-arm before concluding anything about the graph.

## Part B — Reading the profile

Every number in a mis-read profile is real. That is what makes this class silent: the counts are
genuine, the totals are internally consistent, and only the label, the window, or the denominator is
wrong. The high-severity items here all reduce to reading a *summary* — which engine is busiest, a
count-weighted histogram, a delegated aggregate — instead of the *primary computation* that assigns
the bottleneck.

### B1 🔴 Attributing over a whole capture session that contains several executions

**Pattern.** Computing per-class instruction attribution over an entire capture session containing
several distinct executions — a compute-heavy phase plus repeated lighter steps — and labelling the
result as the heavy phase's breakdown.

**Mechanism.** A capture session records **every** execution inside its window, and the instruction
table carries all of them together. Phases have completely different instruction mixes: a large-tile
projection-dominated phase and a small-batch attention-dominated phase share almost no geometry
profile. Aggregating without filtering blends them, so a class that dominates one phase appears to
dominate the other.

The consequences run downstream immediately, because a per-class table is what produces a ranked lever
list. Observed: the top-ranked "prefill" lever was a class with **zero** instances in the prefill
execution — it belonged entirely to the decode steps, and its apparent share was an order of magnitude
above its real one. An implementation effort was then spent looking for a class that was not there.

**Detection.** The session carries an execution-index table with per-execution start/end timestamps
and a per-execution row count. Filter the instruction table by timestamp to a single execution, then
**cross-check two ways**: the filtered window duration should match that execution's recorded
duration, and the filtered row count should be consistent with the execution's own row count. If a
class's share changes when you window, the unwindowed table was wrong.

**Rule.**
1. Read the **execution table first**. State the window boundaries in every table you produce.
2. Attribute **per-phase, never per-session**. A magnitude quoted without its window is unusable.
3. A class's share differing between phases is a **finding**, not noise — often the fastest route to
   naming each phase's binding term.
4. When a phase's floor and its measured time are both known, confirm the windowed instruction work is
   consistent with the floor's inputs. A windowed table that disagrees with the analytic work by more
   than padding explains is mis-windowed.

### B2 🔴 A profiler column's name is not its semantics

**Pattern.** Taking a column whose **name** matches the feature under investigation — a "performance
mode" flag, an "optimization" marker — as evidence about that feature, observing it empty across every
instruction of the class of interest, and concluding the feature was not engaged. That conclusion then
carried a feasibility verdict: with the feature assumed off, the target's required efficiency exceeded
the architectural ceiling and the effort was declared blocked on toolchain work.

**Mechanism.** Profiler schemas are wide, and their fields are **scoped** — to particular engines,
instruction families, or hardware features. A field can be uniformly empty on your instruction class
simply because *it does not apply there*. The inference then runs "the flag is absent, therefore the
feature is off," when the truth is "the flag belongs to a different engine." Nothing warns you: the
column exists on every row, and emptiness looks like a measured negative.

Meanwhile the authoritative evidence is usually in the same table, in the **operand descriptor** — the
operand dtype, and the array geometry the instruction actually occupies. Observed: the operand strings
stated the narrow dtype explicitly on the large majority of instructions of that class; the feature was
engaged the whole time, and the real binding term was array utilization and operand reuse — an entirely
different and far more tractable problem.

**Detection.** Two one-query checks. Is the field **ever** populated for this instruction class? Which
classes **do** populate it? If it is populated only on other classes, it is scoped to them and says
nothing about yours.

**Rule.**
1. Read operand **dtype and array geometry from the operand descriptor**, not from a summary flag. The
   descriptor is the primary record; summary columns are derived and scoped. (Some quantities have no
   flag at all — a vector-engine performance mode may be unflagged, and array efficiency read off
   operand partition counts.)
2. Before any profiler field carries a verdict, establish its definition — from tooling source or
   documentation, or empirically by showing which instruction classes populate it.
3. A field that is **uniformly empty** on your class is evidence of nothing. Treat uniform emptiness as
   a scope question, not a measured negative.
4. This applies with special force to derived "achievable" or "maximum" metrics. A metric whose
   definition you cannot state may not be used to reason about ceilings.
5. Confirming this class of error requires a *different path* to the same quantity — re-reading the
   same column, however carefully and however exactly the number reproduces, cannot detect it.

### B3 🔴 Summed instruction durations are not occupancy

**Pattern.** Summing per-instruction durations per engine and reading the totals as engine busy time,
then computing each class's "share of the step" against them.

**Mechanism.** An instruction's recorded duration spans issue to completion and **overlaps** its
neighbours on a pipelined machine. Summing them routinely produces a total exceeding the step's own
wall time — observed at more than 2× on the binding engine — so every "share" derived from it is a
fraction of a denominator that does not exist. Worse, the error is not uniform across classes: classes
with long pipeline latency and short occupancy are inflated most, which is exactly the distortion that
misranks levers.

Occupancy is a **separate measurement**, and the profiler reports it separately. Using it changes the
qualitative reading, not just the numbers: observed, the engine turned out to be **idle the majority of
the step** while summed durations had suggested saturation — the difference between a throughput
problem and a stall problem, which have disjoint remedies.

**Detection.** Sum the durations and compare against the step's wall time. If the sum exceeds it, the
quantity is instruction-time, not occupancy.

**Rule.**
1. Take occupancy from the profiler's **active-time** measurement, never from summed instruction
   durations.
2. Use summed instruction-time only for **relative** comparisons *within* one engine, and label it as
   instruction-time wherever it appears.
3. Quote engine **busy versus idle against the step's wall time**. That ratio distinguishes a saturated
   engine from a stalled one, and it decides which half of the playbook applies.
4. When busy-time is low on the binding engine *and* memory utilization is also low, the step is
   **stall-bound**: neither a compute nor a bandwidth remedy will move it, and the dependency structure
   is the target.
5. Rank candidate work by **attributed exposed/active time**, never by occurrence count and never by
   summed instruction duration — "weight by cost, not frequency" means exposed time, not the summed
   figure, which inflates exactly the long-latency classes a ranking must not promote.

### B4 🔴 Summing active time across parallel engine instances

**Pattern.** Taking a per-engine active-time table, summing it across the **parallel engine instances**
a logical core contains, and dividing by a **single-instance** wall-clock window. Observed: this
reported the binding engine as 90% busy and "saturated" when it was 45% busy and stalled, and on that
basis the effort formally retracted a correct stall-bound diagnosis and closed off stall reduction as a
path.

**Mechanism.** Logical-core abstractions fuse several physical engine instances, and the profiler
reports active time **per instance** — correctly. Summing across instances produces a numerator
covering `k` engines against a denominator covering one, inflating occupancy by exactly `k`. Nothing in
the table signals this: the instance column is present but easy to aggregate away, the totals are
internally consistent, and the inflated figure lands in the plausible 80–95% range that reads as
"saturated" rather than in a range that would look wrong.

This is a **different mechanism** from B3. There, the durations genuinely overlap in time and the fix
is to use an active-time table. Here the active-time table is already correct and non-overlapping — the
defect is purely the **scope** it was aggregated over. A diagnostic distinguishes them: check the ratio
of summed active time to the union of the same intervals. A ratio near 1.0 means the intervals do not
overlap and you are looking at genuine occupancy; if occupancy still exceeds 100%, the error is
cross-instance aggregation, not overlap.

The consequence is the worst available: occupancy decides **which half of the playbook applies.**
Saturated means throughput work — tiling, dtype, operand reuse. Stalled means dependency and pipeline
work — prefetch depth, buffer counts, drain structure, access patterns. These share almost no remedies,
so the error does not degrade the plan, it inverts it.

**Rule.**
1. Report occupancy **per engine instance**, with the instance count stated beside it.
2. Every occupancy figure carries its **numerator scope and denominator scope** explicitly (`k`
   instances over a `1`-instance window is not a percentage of anything).
3. Cross-check against the per-instance interval **union** (ratio ≈1.0) and against the window bound:
   occupancy above 100% is impossible, and above ~95% on a real kernel should be treated as a
   normalization error until proven otherwise.
4. Generalize to every profiler quantity aggregated over a hardware hierarchy — instances per core,
   cores per device, devices per node. This is the occupancy instance of the same
   value-unit-**scope** discipline that floor constants require (E1): normalize to one scope, and say
   which.

### B5 🟠 Calling a workload bandwidth-bound because "DMA is the busiest engine"

**Pattern.** An occupancy summary shows DMA as the busiest engine at a modest fraction of peak
bandwidth, and a bandwidth lever (coalesce descriptors, grow the free dimension) is chosen. But the
same summary is produced by a **serialization-bound** step, where no single engine saturates and the
real lever is pipelining/overlap and fusion — the opposite fix.

**Prevention.** Compute the **roofline ratio** = `step_time ÷ (HBM_bytes_moved ÷ HBM_peak_BW)` before
choosing a kernel fix.
- ≈1 with one resource near peak ⇒ genuinely bandwidth-bound (increase reuse).
- ≫1 with **no** engine saturated (all partly busy, high busy-union) ⇒ cross-engine serialization (the
  scheduling/`+stalls` term) ⇒ pipeline / overlap / fuse.

Never infer the bottleneck class from "which engine is busiest."

### B6 🟠 Weighing DMA by event count instead of by bytes

**Pattern.** Two forms of the same error. "Sub-knee (small) packets dominate" is flagged as a
coalescing opportunity — but the small packets are a majority *by count* while carrying only a small
fraction of the *bytes moved*, so coalescing is a minor lever, not the wall. Conversely, a
host-transfer suspect is **cleared** by counting events (an unremarkable hundred-odd readbacks) without
ever summing bytes; most were tens of megabytes each, totalling gigabytes and seconds of transfer. The
cheap statistic hid the expensive one in both directions.

**Prevention.**
- **For DMA, readback, and descriptor analysis: sum sizes, don't count events.** Query the
  size/`bytes` field explicitly. Always weigh packet-size / descriptor distributions by **bytes
  moved**.
- Localize DMA by **bytes-per-DMA-millisecond per source line**: low ⇒ latency/fragmentation/
  re-stream-bound (coalescable); near-peak ⇒ genuinely bandwidth-bound (leave it).
- Check the re-stream tax as `total DMA transfer_bytes ÷ true HBM-read bytes > 1` (on-chip reloads,
  per-consumer re-streaming, dequant intermediates), and attack the worst-ratio sources — not the
  count-heavy ones.
- When a suspect is "cleared," name **which statistic** cleared it. Clearing by the wrong statistic is
  indistinguishable from not checking.

### B7 🟠 Reporting a collective or bandwidth figure without stating the denominator

**Pattern (collectives).** A system trace shows `cc_running: count=717, total_ms=110.5` across 3
executions. The operator divides 110.5 / 3 = 37 ms per execution and reports "collectives are only 9%
of the phase — not the bottleneck." But the events include **overlapping** ones (multiple collective
channels running simultaneously) and `total_ms` is a simple **sum** of event durations, double-counting
the overlap. The correct metric is the **per-execution overlap-merged union duration**, which a proper
ingest-and-union analysis put at 102 ms (24.7%) — a 2.7× difference, and it inverted the verdict.

**Pattern (bandwidth).** A bandwidth figure is reported ("51% BW utilization," "366 GB/s achieved")
without stating whether it is computed over the full step, over the DMA-active window only, over a
single burst, or as bytes/wall-clock. The same raw data yields very different percentages depending on
the denominator, and a reader cannot reproduce or compare the figure.

**Prevention.**
- **Never divide `sum(duration_ns)` by execution count to get a per-execution cost.** Use the
  overlap-merged union duration within each execution window. Report collective time as
  "X ms (Y% of execution, overlap-merged)" — never as a bare duration without saying summed or merged.
- **When a quick system-trace estimate disagrees with a rigorous profile analysis by >2×, trust the
  profile analysis.** The system trace is a first approximation; the device profile is ground truth.
- Always state the bandwidth formula — `achieved_bw = bytes_transferred / time_window` — and say what
  `time_window` is (full step, DMA-active-only interval, single packet burst). State both the achieved
  BW and the peak it is divided by, and whether that peak is per-engine or aggregate.
- "DMA is 67% occupied but only 51% BW-efficient" is meaningful only if the reader knows that
  67% = merged-DMA-active-time/step-time and 51% = bytes/(DMA-active-time × peak-BW). State both
  formulas on first use.

### B8 🔴 Deriving a component by subtraction

**Pattern.** A collective-time figure produced as `measured_total − modeled_compute_floor` and reported
as though measured. No collective event in the trace corroborated it, and the conclusion built on it
("the model overestimates communication") had to be withdrawn.

**Prevention.**
- **`total − model` is not a measurement.** It inherits the model's entire error and cannot be
  falsified. Query the component's own rows — collective/`CcOp` events, DMA packets, semaphore waits,
  per-engine active time — or drop the term and label it unverified.
- **Attribution requires overlap, not arithmetic.** Before attributing residual time to a component,
  confirm that component's events actually overlap the execution window in question.
- **Retract rather than hedge.** A conclusion whose evidence cannot be re-derived should be dropped
  from the report, not restated with qualifiers.

### B9 🔴 Naming a dominant cost from an op *count* instead of an exposed *time*

**Pattern.** The per-op timing path fails or is unavailable, so the analysis reaches for a static
artifact that *is* available — a compiler-IR op census — and names the mechanism from it: "the step
contains 354 collectives, therefore the step is collective-bound." A count is a plausible-looking
stand-in carrying none of the inferential weight of a time: it establishes that the ops exist, not that
they are exposed, and not that they dominate. Observed: the count-derived verdict survived into a
written report; the measured exposed collective share was ~24% of the step, and a standalone collective
benchmark at the same payload shape and participant count put it near ~35 ms of a ~146 ms step. The
binding term was something else, and an iteration was spent on the wrong mechanism.

**Prevention.**
- Treat an evidence-class substitution as a change to **what you may conclude**, not merely a change of
  source. If the artifact is a count and the claim needs a time, the claim is unsupported — say the
  ranking does not exist yet (B12) rather than silently downgrading the evidence while keeping the
  conclusion's strength.
- Cross-check any communication-bound claim with the runtime's **standalone collective benchmark** at
  the exact payload shape and participant count, multiplied by the IR count. Minutes, no profile
  needed, decisive in both directions — it has both confirmed and refuted such claims.
- Counts remain useful for *structure* (how many, what shape, where) and for sanity-checking a timed
  profile's op population against the compiled graph. Never for "dominant."

### B10 🟠 Taking computed/aggregated metrics on trust before recomputing them

**Pattern.** Accepting a delegated analysis's *derived* metrics — achieved bandwidth, net-exposed
collective fraction, per-engine active %, MFU — as given. These are exactly where sincere-but-wrong
numbers arise: an occupancy figure quoted as a net-exposed (overlap-merged) figure, an aggregate built
on a mislabeled capture, a per-stream count reported as a per-execution merge.

**Prevention.**
- Distrust should scale with how *derived* a metric is. Raw single-field reads (a duration, a byte
  size, an event count) are usually right; anything requiring interval-overlap-merge,
  cross-engine/cross-rank aggregation, or steady-state/warm-up filtering must be **independently
  recomputed against the raw profile tables** before a decision rests on it — and the query plus its
  raw output shown.
- **Personally verify the claims that gate a decision:** is this the binding op, does the cited profile
  exist, is this device time from the live trace, is this the production shape. A confident,
  well-formatted answer is evidence-shaped, not evidence.
- **Don't full-read huge machine-generated artifacts.** HLO, penguin IR, compile logs, instruction
  dumps and per-op JSON are large; grep/head/sample for the signature you need (an opcode, a `128*96`
  operand shape, a semaphore-wait name, an error code). The profiler's instruction detail *is* the
  feedback signal — query the specific instruction rows.

### B11 🟠 Optimizing the kernel you *believe* owns the cost

**Pattern.** Optimizing a kernel because it is believed to own a cost, without attributing the cost to
its source line.

**Mechanism.** Per-instruction profile tables typically carry instruction class, a source-location
field mapping back to the kernel source line, and per-instruction duration. Grouping by these fields
converts a guess into an attribution, and routinely relocates the blame to a different kernel — or to
framework glue *around* the kernel rather than the kernel itself (a frequent and easily-missed case).

```python
# time by kernel source location
inst.groupby('source_location')['duration_ns'].sum().sort_values(ascending=False)

# transpose-class instructions only, for one kernel file
inst[(inst['instruction_class'] == 'TRANSPOSE') &
     (inst['source_location'].str.contains('<kernel_file>'))]

# which engine binds
inst.groupby('engine')['duration_ns'].sum().sort_values(ascending=False)
```

**Rule.** Trace every performance anomaly to specific source lines before proposing a change, and
explicitly consider that the cost may sit in the surrounding framework code rather than in kernel
source. Note that a meaningful fraction of instructions in a real profile carry **no** source
attribution — see G10, where that residual becomes a hard ceiling on source-level work.

### B12 🔴 Presenting a two-bucket split as a ranked table

**Pattern.** The deliverable ("rank ops by measured exposed time, attack the top one") is satisfied on
its face by a table whose top row lumps everything that is not communication:
`compute + memory (attention over cache, MLP matmuls, weight read) — 178 ms — 76%`. It is formatted
like a ranking and it is genuinely measured, but it is **unactionable**: no op is named, so no kernel
can be targeted and no floor can be compared. The lump also hides the mix — a per-item term and a
batch-invariant term inside one row behave completely differently as the operating point moves, and
collapsing them is what makes a per-item inefficiency look like a fixed hardware cost.

**Prevention.**
- A ranking is per-op or per-kernel. A bucket table with fewer rows than candidate mechanisms is a
  **split**, not a ranking — label it as such and state that the ranking is still missing.
- Minimum acceptable granularity separates terms that scale differently: per-item (cache/attention) vs
  per-step-invariant (weight read, communication).
- Before proposing kernel work, **name the op**. "The compute+memory bucket is large" cannot direct a
  fix, and improving a secondary op inside the bucket moves nothing end-to-end.

### B13 🟠 Stopping when the per-op path breaks, instead of apportioning by parameter sweep

**Pattern.** The per-op capture hangs, overflows, or ingests empty on the target graph — sometimes a
real blocker — and the analysis stops at whole-step totals. But the data needed to split the step is
usually already on disk: a sweep of item count against step time apportions it with **no additional
device time**.

**Prevention.**
- **Fit step time against the swept parameter: the slope is the per-item term, the intercept is the
  invariant term** (weight read + communication + fixed overhead). Report both with a standard error.
  Two mechanisms separated, from measurements you already have.
- **Compare the per-item *marginal* against its own floor, not the whole step against the whole
  floor** — this is the sharper localizer by a wide margin. Observed: the whole step sat ~3–5× above
  its sum-of-floors (arguable, and it was argued away), while the per-item marginal sat ~11× above its
  own memory floor (≈0.72 ms/item against a ≈64 µs/item cache-read floor), which points unambiguously
  at the per-item path.
- Remaining fallbacks before giving up: capture per-op on a **variant graph whose timing path does
  work** (e.g. the higher-precision build) and use it for apportionment only, noting the dtype caveat;
  or shrink the capture until the path survives.
- **Occupancy caveat:** a sweep apportions only if compiled width tracks the item count. At fixed width
  the slope is an artifact (C2).

### B14 🔴 Computing array utilization from the profiler's `free_dimension` column instead of the output-tile shape

**Pattern.** A matmul is judged badly-tiled — "each instruction produces only a 128-wide output, so PE
array utilization η is ~0.33" — from a profiler column named `free_dimension`. A whole retile lever is
proposed on that reading. In fact the hardware instruction already produces the wide output; the column
was misread, and the "fix" would emit **identical** instructions for zero gain.

**Mechanism.** The `free_dimension` column can report the **innermost contiguous stride** of the moving
operand's multi-dimensional SBUF access pattern, not the total output free dimension. A moving operand
stored as strided quadrants (e.g. `[128, 2, 2]`) reports the inner `128` even though the instruction
processes all `512` elements and writes a `[512, …]` output tile. Computing
`η = F/(F+K+P−2)` with `F=128` gives ~0.33; with the true `F=512` it is ~0.67 — the difference between
"badly tiled, retile it" and "already near the architectural optimum."

**Rule.**
- Read the true output free dimension from the **ISA operand (destination) shape** and from
  `raw_flops / (2·P·K)`, not from the `free_dimension` column. Cross-check: `throughput = raw_flops /
  duration` against the η-limited peak `peak · η`.
- Only after the output tile shape confirms a low η is a retile lever admissible (extends B2 "a profiler
  column's name is not its semantics"; this is the specific, high-cost instance for matmul tiling).

---

## Part C — Is this number attributable to a kernel at all?

A kernel number that fails any item in this part is not a kernel number.

### C1 🔴 Deriving kernel-level attribution without a device profile

**Pattern.** Deriving statements like "this kernel achieves X% of peak" from end-to-end client-side
benchmark numbers.

**Mechanism.** An end-to-end latency contains, besides kernel time: host-side tokenization,
detokenization and scheduling; queueing behind other requests; compiled-bucket padding (C2); exposed
communication (D4); and phase interleaving where one phase blocks the other. None of that is
attributable to a kernel, and the sum frequently dominates. Dividing a kernel floor by a client-side
latency therefore produces an "efficiency" that is mostly a measure of everything else.

**Rule.**
1. Kernel-level claims require **device-time measurement from a live execution trace**
   (`nc_exec_running` / the device profile) — not wall-clock, not client-side latency.
2. An isolated replay of a compiled artifact is also not a substitute where it under-counts
   communication (A3); prefer the live trace for anything communication-sensitive. `nki.benchmark` is
   fine for device latency percentiles while iterating a standalone kernel, but cross-check the in-graph
   device trace before trusting E2E impact.
3. With no profile captured, report the number as **end-to-end** and explicitly decline the
   attribution, naming the unmeasured components. That is the honest form.
4. Cross-check any device number against an independent view before building on it — engine phase
   metrics (A2) and `actual_batch / step_time` are both free.

### C2 🔴 Per-step cost tracks the *compiled* bucket, not the actual batch

**Pattern.** Benchmarking a kernel at a batch far below the compiled bucket it executes, then comparing
the result to a per-step floor and concluding the kernel is inefficient.

**Mechanism.** Compiled-graph stacks compile a fixed set of batch and token buckets. A stream of actual
batch `b` executes the graph compiled for the smallest bucket `B ≥ b` and pays close to the **full
`B`-batch step cost**: the padded step reads the full bucket's weight tiles and executes the full tile
grid. Illustratively, holding kernel, hardware and weights fixed and changing *only* the compiled batch
bucket from large to small can move per-output-token latency by several-fold at low actual batch.

**Why it is especially deceptive.** The padded step's profile *shape* — memory-dominated, low arithmetic
intensity, depressed engine occupancy, depressed achieved bandwidth, depressed utilization — is
indistinguishable from a genuine memory-bound kernel defect. Every indicator moves in the direction a
real defect would move it.

**Rule.** Never quote a per-step time, achieved-bandwidth figure, or utilization percentage without
stating **actual batch vs compiled bucket** beside it. If actual < bucket, it is not a kernel
measurement: either compile a bucket matching the measurement point, or measure at a concurrency that
fills the bucket, before attributing anything to the kernel. This constrains how headroom is freed for
the profiler (A9): a capture bought by dropping the concurrency cap below the bucket is not a kernel
measurement, however well its artifacts match.

### C3 🔴 Benchmarking stock defaults as "the kernel's performance"

**Pattern.** Benchmarking "as-is" and treating the result as the kernel's capability.

**Mechanism.** Delivered kernel optimizations are routinely shipped **behind gates, disabled by
default** — deliberately, so a known-good baseline stays reachable and each lever can be A/B'd. The
consequence is counter-intuitive but reliable: **stock defaults are the unoptimized path.** Benchmarking
defaults and then comparing to a hardware floor attributes the shortfall to hardware while validated
wins sit inert in the same source tree.

**Rule.**
1. Before any kernel benchmark, search the source tree for performance/feature gates and list every one
   with its default.
2. Record in the report **which gates were enabled and which defaults were accepted**. A tunable you did
   not set, you chose by default.
3. Label "no gates enabled" as a distinct configuration — not as "the kernel's performance."
4. When stacking multiple gates, **measure them together**: they are frequently non-additive (competing
   for the same scheduling slack) or synergistic (one only exposes the stall another fixes). Never sum
   individual deltas to predict a stacked result.

This is a self-contained discovery action on code already in hand — no prior run, reference number, or
external report required. It is the mirror of E3: failing to look for an existing gate produces false
*pessimism*; assuming one exists produces false *optimism*.

### C4 🟡 Quoting profiled absolute times as if the profiler were free

**Pattern.** Absolute per-step times read off an instrumented capture are quoted against a budget, or
compared with an uninstrumented measurement. Tracing perturbs what it measures: observed ~242 ms per
step under capture against ~98.7 ms for the same configuration unprofiled — a ~2.4× inflation. The
*shares* were stable and correct; the absolutes were not, and only the shares should have travelled.
(This inflation is also why a default per-execution watchdog fires during capture — A4.)

**Rule.** Record the uninstrumented reference for the same configuration beside every profiled absolute
and state the inflation factor. Use profiled data for **attribution** (shares, ratios, rankings) and
uninstrumented measurement for **absolutes** (budgets, SLA comparisons, floors). Never compare a
profiled absolute to a target, and never subtract a profiled number from an unprofiled one.

### C5 🔴 Never comparing numbers that came from different executions

**Pattern.** Subtracting a device time from one capture and a wall-clock from another — arithmetic
dressed as measurement.

**Rule.** Numbers compared must come from the *same* execution. Confirm warmup is excluded before
believing any rate. This is the precondition for C4's inflation factor and for B8's ban on subtraction.

### C6 🟠 Quoting a headline number without verifying its arithmetic identity

**Pattern.** A harness's headline `tokens_per_second` was quoted for two rounds as an output rate.
`num_requests × bucket_size` equalled the reported total token count **exactly** — proving the figure
counted padded bucket elements, not real work. The true rate was lower by roughly the padding ratio. The
tell was in the same four-line JSON that was being cited. Separately, measurements labelled with the
bucket size had real lengths ~25% of that, padded up.

**Rule.**
- **Reconcile any headline throughput/latency number against shapes and iteration counts before quoting
  it:** `iters × tile_elems`, `steps × batch`, `requests × seq_len`, elapsed × rate. An identity that
  closes exactly on a padded dimension means padding is being counted as work.
- **Report effective vs padded utilization separately.** A kernel at 90% of roofline on padded elements
  can be far below it on real elements; the padded figure is not a performance claim.
- **Label every measurement with the real element/token count**, not the bucket or tile it was padded
  into — and re-check that label when the harness has its own default length flag.

### C7 🔴 Attributing kernel performance before confirming which library tree the runtime imported

**Pattern.** Kernel work depends on a patched or locally modified kernel library, and the environment
holds **more than one copy** of it — an installed package and a vendored source tree, or two entries on
the path. The patch lands on one; the runtime imports the other. Kernels then execute in their unpatched
form while the analysis reasons about the patched source, and every per-kernel number is attributed to
code that never ran. No timing profile can reveal this, and the patch command reports success.

**Prevention.**
- Resolve the library's import path **at run time**, inside the same interpreter and environment the
  workload uses, and patch and verify *that* path specifically. Log it beside the profile.
- Verify by **sentinel**: grep the resolved tree for a string introduced by the patch and require a
  non-zero count. A patch that applied cleanly to the wrong tree is indistinguishable from success at
  the command line.
- Where a kernel change should move a *structural* quantity — memory footprint, instruction mix, the
  presence or absence of an op — confirm that quantity moved before trusting any timing delta. An
  unchanged weight footprint under a supposedly halved-precision path means the path is not engaged.
- **Check artifact mtimes.** An mtime is the cheapest provenance check there is — "profiled this
  session" is falsifiable in one `stat`.

### C8 🟠 Reporting a long campaign as one end-of-run document

**Pattern.** A long kernel/perf campaign reported in one large end-of-run document whose central metric
was mislabeled; every finding had to be reconstructed by an independent audit. In the same effort an
automated review loop was wired to re-trigger on a token, the token was emitted, and the loop silently
never fired.

**Rule.**
- **Report per-shape / per-tile as you go**, each with its artifact path, rather than one final document.
  A single report concentrates unverifiable claims; a mislabeled anchor should surface in minutes.
- **Pair a long campaign with an independent reviewer** that inspects the artifacts without the
  implementer's context — it catches mislabeled metrics and false blockers that self-review misses.
- **Check that automation fired** (stage/iteration count, artifact mtimes).

**Addendum — a review reachable only through success reviews nothing.** Wiring the reviewer to run *after*
the work stage means it inspects a finished product, so when that stage runs long, stalls, or dies the
review never executes — precisely in the cases where it was most needed, and silently, because a review
that never ran leaves no missing finding to notice. Make the review runnable on **partial state**: its
input is whatever is on disk now, including nothing, and "no trace exists yet" is a valid and useful
finding. Trigger it independently of the work's outcome (elapsed time, artifact count, checkpoint cadence),
require the work to checkpoint at a stated cadence, treat a missing checkpoint as a reviewable event rather
than silence to wait through, and keep "reviewed, nothing found" distinct from "not reviewed" in whatever
record a later reader consumes.

---

## Part D — What actually binds

### D1 🔴 Writing a kernel for an op that is secondary or already at its floor

**The most expensive kernel is the one that shouldn't have been written.** A correct, validated kernel
for a **secondary op**, or for an op already at its compute/memory roofline floor, moves nothing
end-to-end.

**Pattern.** A binding matmul measured deep in the compute-bound regime (arithmetic intensity far above
the roofline knee, low memory-bandwidth utilization) — i.e. already at the compute floor. Writing a
faster matmul kernel there moves ~0 ms end-to-end; the correct call is *no kernel* — the win is
elsewhere (serving config, dtype/quantization, an existing optimized path).

**Guardrails**
- **Rank ops by measured device time; compute the entitlement floor for the top op** (compute-bound if
  arithmetic intensity ≫ roofline knee, memory-bound otherwise). Only optimize an op with real headroom
  **above** its floor.
- After any new kernel, **re-profile to confirm the binding bottleneck actually moved** (A7).
- If the op is at floor, the lever is elsewhere (dtype/quantization, tiling for reuse, parallelism,
  overlap) — not this kernel.

**Two ways an "already at floor" reading is usually constructed, and how each is wrong.**
- **"At N% of peak while active"** excludes the operator's own launch, tail and wait time — which a
  kernel change can remove. Report active-time efficiency and occupancy **separately**: fraction of peak
  while active, and fraction of wall time active. High first number with low second is a
  launch/scheduling opportunity, not a closed question.
- **"Near its floor in aggregate"** prices only the work *as currently expressed*, so it is blind to
  work a different formulation would not do at all: a materialization that fusion removes, a layout pass
  a different tiling makes unnecessary, a value recomputed rather than kept. A small distance to a floor
  bounds how much faster the present formulation can run; it says nothing about how much less work
  another formulation would perform.
- **Decompose the aggregate before trusting it.** A modest total gap can conceal a large gap in one
  shape, bucket, or dtype variant; inspect the per-variant distribution.

### D2 🔴 The analytic entitlement's binding-op RANKING can invert against the live trace, and it re-ranks with parallelism degree

**Pattern.** An analytic entitlement model ranked **collectives** as the binding decode term
(communication above KV-cache reads above weight reads) and the record was written up as "binding =
collectives." The live device trace at the *actually chosen* parallelism degree disagreed: collectives
were a minority of the step and memory traffic the clear majority — i.e. **memory-bound, not
collective-bound.** The analytic ranking had been computed at a *higher* TP degree, where collectives
genuinely do approach parity; the ranking **flipped** between the two degrees. A kernel written against
the analytic verdict would have targeted a non-binding op and moved nothing.

**Guardrails**
- **The live trace outranks the analytic model on *ranking*, not just on magnitude.** Analytic
  entitlement is for narrowing hypotheses and sizing headroom; which op binds must be confirmed from
  measured per-op device time before a single line of kernel code (D1).
- **Re-confirm the binding op at the EXACT config you will ship** — parallelism degree, batch, context,
  dtype. The collective:memory:compute ratio is a function of all of them, and the ordering inverts
  across the sweep. An entitlement table computed for a neighbouring cell is a different question's
  answer.
- **Name the parallelism degree next to every binding-op claim.** "Binding = collectives" without a TP
  degree is not a finding; it is true at one degree and false at another within one sweep.
- **A ranking near-tie is a measurement request, not a conclusion.** When the top two or three terms are
  within model error of each other (E4), no kernel decision is licensed until the trace separates them.

### D3 🔴 Modelling decode as weight-bound, and optimizing weight loading

**Pattern.** Reasoning about decode as "weight-memory-bound" via `t_step ≈ weight_bytes / BW`, and
therefore aiming optimization at weight-load efficiency. That model is only valid near batch 1.

**Mechanism.** Per step, at batch `B` and average KV length `KV_len`:

```
attention load  = weight_attn_bytes + B × KV_len × kv_bytes_per_token_per_rank
FFN load        = weight_ffn_bytes                       (independent of B)
compute         ∝ B
collectives     ≈ 2 × L × per_op_cost                    (D4)
```

Weight terms are **amortized** across the batch; the KV term **scales** with it. So the binding resource
*changes with batch*:

| Operator | Binds on | Crossover |
|---|---|---|
| Attention | KV read, for all but the smallest batches | where `B × KV_len × kv_bytes/token` exceeds `weight_attn_bytes` — typically an order of **tens** of sequences at multi-thousand-token context |
| FFN | weight read | becomes compute-bound only at large `B` (ridge point ≈ `peak_FLOPs / (2 × BW)`) |
| Attention | essentially never compute-bound at high TP and long context | KV-read slope per sequence exceeds the compute slope |

**Consequences.**
1. A kernel change that improves **weight** loading (better prefetch, larger weight-load tiles, weight
   layout) moves little or nothing at serving batch sizes — it targets an amortized term.
2. The real decode targets are KV-read efficiency (layout, dtype, paging granularity, contiguity of the
   per-step gather) and the communication path.
3. **Batch increases yield sublinear throughput**, because raising `B` raises the KV term
   proportionally. Model the gain as `(B₂/B₁) × (t_step(B₁)/t_step(B₂))`, which is strictly less than
   `B₂/B₁`. Never promise linear scaling from a batch or capacity increase.

**Rule.** Before optimizing a decode kernel, compute the KV-vs-weight crossover batch and state which
side the **production** batch sits on. Name the binding operator *at the production batch*, not at batch
1, and re-check after any batch change — the binding operator can flip.

### D4 🔴 Building a step floor from memory traffic alone

**Pattern.** Leaving communication out of a step-time floor on the assumption that it is hidden.

**Mechanism.** A standard tensor-parallel decoder issues roughly **two collectives per layer** per token
step (after attention output projection and after the FFN down projection):

```
collectives_per_step  ≈ 2 × L
t_collective_per_step ≈ 2 × L × per_op_cost
```

For a deep model that is hundreds of operations per step. At small payloads each is
**latency-dominated**, not bandwidth-dominated, so the cost scales with the **count** and with `TP`, and
barely improves by shrinking messages. Once included, this term is frequently the **largest single
component** of a decode floor — larger than weight read or KV read — and its omission can change a
"distance from floor" figure by a large factor. Doubling `TP` roughly doubles it while, past
`n_kv_heads`, buying no capacity benefit at all (D5).

**Rule.**
1. Include `2 × L × per_op_cost` in **every** step floor.
2. Measure `per_op_cost` for the algorithm actually selected — selection is driven by payload size and
   tensor residency, and choices can differ by ~2×. Confirm which algorithm the runtime picked rather
   than assuming a knob changed it; a knob that selects an already-default algorithm is a no-op, not a
   null result.
3. If communication dominates the floor, the high-value moves are **reducing count or TP degree**, not
   making each operation faster.
4. A communication-dominated step measured with **default (conservative) collective precision is not at
   its floor** — it is at its unoptimized default. Search for precision-reduction and
   algorithm-selection gates before concluding anything about hardware limits (G8).

### D5 🔴 When the binding op is memory-bound, the lever is BYTES — and a sharding choice is often what inflated them

**Pattern.** The binding decode term was cache-read bandwidth, and the cache was inflated by a
**sharding** decision: the tensor-parallel degree exceeded the model's key/value head count, so the
cache could not shard past that count and **replicated** instead (replication factor
≈ `TP / min(TP, n_kv_heads)`). No kernel can read fewer bytes than the layout obliges it to read. The
decisive win came from **halving the cache element width** — a serving-config dtype flag, with no kernel
component at all, on the same op a kernel would have targeted.

**Guardrails**
- **For a memory-bound op, enumerate the bytes before the instructions.** Ask, in order: are these bytes
  *replicated* by the sharding choice, can the *element width* shrink (dtype/quantization), can the
  *layout* improve reuse, can the access be *fused* to avoid a round-trip. Only then consider a better
  kernel.
- **Check head-count divisibility against the parallel degree as a first-class kernel input.** When the
  degree exceeds the KV/attention head count, per-device cache bytes and the per-step read both scale
  with the replication factor. This is a parallelism decision that lands as a kernel-visible cost, and
  it is invisible in a per-op FLOP count.
- **A sharding choice optimal for one phase can be the binding cost of the other.** Faster prefill from
  a higher degree can be paid for by cache replication in decode; state the trade explicitly rather than
  optimizing one phase's kernel against the other's constraint.
- **Log and confirm the layout the runtime actually chose** (a head-conversion / replication notice in
  the engine log is evidence about your byte budget, not noise to filter out).

### D6 🟠 A capacity ceiling masquerading as a kernel problem

**Pattern.** Attributing plateaued decode throughput to a slow kernel when the running batch is sitting
far below the configured maximum with requests queued.

**Mechanism.** An allocator or configuration ceiling caps concurrency below what memory allows. The
kernel is then executing a smaller batch than intended, so per-token cost looks poor — a configuration
finding wearing a kernel finding's clothes.

**Detection.** Compare three numbers from the serving log: allocated cache (in tokens) divided by
per-request context, giving the real concurrency limit; free device memory per rank after weights; and
the configured maximum batch. If allocated capacity is far below what free memory permits, the
deployment is leaving batch unused. Compute achievable batch as
`free_bytes_per_replica / (context_len × kv_bytes_per_token_per_rank × TP)` — and apply the replication
factor for grouped-query attention past `n_kv_heads` (D5), or the estimate will be too high.

**Rule.** Treat "running batch ≪ configured batch with a nonzero waiting queue" as a **capacity**
finding, not a kernel finding. Fix the allocation first — configuration, no code — re-measure, and only
then attribute any residual gap to kernels. Raising capacity does **not** buy proportional throughput
(D3); quantify the expected sublinear gain before committing.

### D7 🔴 Optimizing before separating per-invocation fixed cost from per-unit cost

**Pattern.** A graph's non-arithmetic time is the dominant target: the matrix unit is already near its
achievable efficiency on the operations that matter, while a large remainder sits in idle,
synchronization, and transfer-wait. Two very different explanations fit that shape — a **fixed cost paid
once per invocation** (setup, layout preparation, descriptor construction, launch and teardown
synchronization) or a **per-unit cost** that scales with the work — and they call for opposite
instruments. Fixed cost is amortized by packing more work into each invocation, a configuration change
with no kernel component at all; per-unit cost is what a kernel can attack. Proceeding without
separating them risks the most expensive kernel there is: one written against a cost that would have
disappeared under a larger batch.

**Guardrails**
- **Vary the work per invocation and watch time per unit of work.** Falling per-unit time indicates a
  fixed cost being amortized; flat per-unit time indicates a genuine per-unit cost a kernel may address.
  This one sweep decides whether kernel work is the right instrument at all.
- **Normalize before comparing.** Per-invocation time rises with more work under *both* hypotheses and
  therefore separates nothing; only the per-unit quantity discriminates.
- **Idle, semaphore-wait, and transfer-wait time are not addressable by faster arithmetic.** Attribute
  the remainder to a class — fixed setup, dependency stall, exposed transfer, exposed collective —
  before choosing an instrument. A faster inner loop moves none of them.
- **Rank by time share, not FLOP share, or this stays hidden.** An operation carrying a trivial fraction
  of the arithmetic can hold a large fraction of the time at very low efficiency; a FLOP-weighted view
  conceals it entirely (extends B6 from event counts to FLOP share).

### D8 🟠 Interchanging whole-graph efficiency with a kernel's own efficiency

**Pattern.** Total useful work divided by total elapsed device time yields an efficiency figure, which is
then discussed as though it characterized the dominant kernel. It does not: the denominator also
contains communication, layout operations, other engines, and idle gaps the kernel neither performs nor
controls. The conflation also runs the other way — a kernel's high active-time efficiency cited to
explain why the graph-level figure cannot improve. Two numbers with different denominators used
interchangeably, and the overhead between them — the part actually worth attacking — is exactly what the
conflation conceals.

**Guardrails**
- **Always state the denominator.** Graph efficiency uses total elapsed time; kernel efficiency uses that
  kernel's own active time. Label every efficiency figure with which one it is.
- **The ratio between them is itself the finding.** Graph efficiency far below kernel efficiency
  localizes the opportunity *outside* the kernel and quantifies it.
- **Never justify a graph-level ceiling with a kernel-level efficiency, or the reverse.** They may be
  compared; they may not be substituted.
- **Compute both before deciding whether the next move is a kernel or a scheduling/decomposition
  change.** The gap between them is what tells you which.

### D9 🔴 Reading a large measured-vs-floor gap as a verdict

**Pattern.** Reading a large gap between a measured step and its computed floor as either (a) a kernel
defect — prompting a rewrite of a kernel that was never the problem — or (b) a hardware ceiling —
declaring the target impossible while the machine runs far below its own capability.

**Mechanism.** Both readings skip localization. A gap of several-fold is almost never one cause, and
kernel arithmetic efficiency is usually the **smallest** term in it. Mature kernels are typically already
near their tile-shape ceiling, so the headroom lives in the layers around them.

**Ordered candidate list — verify in this order before blaming a kernel:**

1. **Capacity / configuration** — running batch below configured, queue waiting (D6). Configuration fix,
   no code.
2. **Compiled-shape padding** — actual batch below compiled bucket (C2). Recompile or re-measure.
3. **Disabled optimizations** — default-off gates left off (C3). Enable and re-measure.
4. **Measurement validity** — client-side rather than device time (C1); phase interleaving.
5. **Communication exposure** — counted explicitly, not assumed hidden (D4).
6. **Memory access patterns** — KV gather contiguity, paging granularity, descriptor sizes, layout
   (D3, D5, F1).
7. **Arithmetic efficiency** — last, and usually already near ceiling in a mature kernel.

**Rule.** Report the gap as `measured / floor` **with the floor's definition inline**, then attribute it
across terms 1–7 with evidence for each. Neither "the kernel is slow" nor "the hardware cannot do this"
is a permissible conclusion while terms 1–5 remain unexamined. A ceiling claim additionally requires
operating **near** the floor.

### D10 🟠 Optimizing an engine that is not binding, or work that is already overlapped

**Pattern.** Optimizing whichever engine or operation is most familiar, rather than the one on the
critical path.

**Mechanism.** In transformer prefill, the matrix engine (matmul plus weight-load instructions) is
usually binding, often a clear majority of summed instruction time. Work that does not need to be there
— transposes, casts, scale application, data movement — can frequently be relocated to vector, scalar,
or DMA engines that would otherwise idle, and which execute **concurrently** with matrix compute.
Relocation is therefore a genuine critical-path win, unlike shaving cycles off work that already
overlaps.

**Detection.** Profile at the engine level *first*: per-engine active (non-overlapping) time, and the
dominant opcode per engine. Summed engine time far exceeding step time indicates substantial parallelism
(B3) — meaning some engines' work is already hidden and reducing it will yield nothing.

**Rule.**
1. Establish the binding engine before proposing any change.
2. If matrix-bound: relocate transposes to DMA (F1); reduce weight-load overhead via larger tiles and
   better prefetch overlap.
3. If DMA-bound: attack transfer patterns, descriptor sizes, and gather contiguity.
4. If vector/scalar-bound: examine activation and scale implementations.
5. **Do not optimize an engine whose work already fits inside the binding engine's window.** Verify
   overlap before assuming a reduction helps — this is the single most common source of "correct
   optimization, zero measured gain."

### D11 🟠 Expecting a work-skipping optimization to scale with the FLOPs it removes

**Pattern.** A masking/windowing/sparsity path is known to skip a large fraction of the compute tiles
(e.g. two-thirds of the attention tiles under a sliding window), so a proportional speedup is expected.
The measured layer is only marginally faster than the dense path, and the "win" never materializes.

**Mechanism.** Per-invocation **fixed overhead** — the softmax/exp pipeline over all key positions,
weight/LDWEIGHTS loading, semaphore/scheduling cost — is shared by the skipped and non-skipped paths and
often dominates the layer. Removing matmul tiles shrinks only the compute term; when compute is a
minority of the layer, an N% tile reduction yields far less than N% wall-clock. This is Amdahl's law at
the kernel layer, with fixed overhead as the serial part.

**Rule.**
- Before promising a work-skipping speedup, measure the **fixed-overhead floor** (the part that runs
  regardless of how many tiles are skipped) and bound the achievable win by the compute fraction only
  (extends D7 "separate per-invocation fixed cost from per-unit cost").
- If fixed overhead dominates, the lever is the overhead (fuse/overlap the softmax, weight load,
  scheduling), not more aggressive skipping.

### D12 🟡 Ignoring the cold-start tax where a model interleaves two kernel variants

**Pattern.** A per-layer time is treated as uniform, but the layers immediately following a *different*
kernel variant are measurably slower, and the aggregate is mis-modeled.

**Mechanism.** A model that alternates kernel variants (e.g. two attention types with different head
dims, or a periodic global layer) evicts one variant's weights/NEFF while the other runs; the boundary
layer then pays a **cache-thrash cold-start** to reload what was evicted, seen as large TE gaps at the
start of that layer's matmul stream.

**Rule.**
- Measure the boundary layers as a **distinct bucket**, not folded into the warm-layer average, so the
  cold-start tax is visible and quantified.
- The lever is cross-kernel weight/NEFF **prefetch** (stage the next-variant weights during the current
  variant's compute), a graph/scheduling change — not a change to either kernel's inner loop.

---

## Part E — Is the floor I am dividing by sound?

Parts A–D concern measurements and what may be concluded from them. This part concerns the **inputs to
the floor itself**. Every statement of the form "N× off floor," "requires X% of peak," or "configuration
A beats configuration B" is a ratio whose denominator is assembled from constants, term estimates and
capability assumptions. An error in any of those moves the ratio by the same factor while leaving the
arithmetic visibly correct — so these errors survive every consistency check applied to the result.

### E1 🔴 Hardware constants must be sourced and unit-qualified, never recalled

**Pattern.** Building a roofline or entitlement floor from remembered peak figures — peak arithmetic
throughput, memory bandwidth, on-chip capacity, interconnect bandwidth. At the kernel layer the same
error class lands on tiling: an assumed on-chip buffer/accumulator capacity, partition count, or
per-core HBM figure invalidates the tile sizes, occupancy, and the roofline the whole kernel is designed
around. Observed: a node's **logical** core count asserted from memory was 2× wrong, and every quantity
derived from it (replica count, per-core memory budget, the resulting fit table) was silently invalid.

**Mechanism.** Each of these constants has several published variants differing by **2–8×**:

| Variant axis | Typical spread | How it hides |
|---|---|---|
| dense vs structured-sparse peak | 2× | the larger number is the headline figure |
| per-dtype peak | 2× per halving of width | quoted without naming the dtype |
| per-chip vs per-instance | device count | both are "per accelerator" in prose |
| per-chip vs per **logical** core | the logical-core divisor | a logical-core configuration subdivides a physical chip and silently rescales every per-core number |
| theoretical vs achievable bandwidth | 1.2–1.5× | rarely labelled |

A floor is a workload quantity divided by one of these constants, so one mis-sourced or mis-scoped
constant moves the floor by exactly that factor. Recalled constants additionally **lose their
qualifier**, which is what makes the error undetectable afterwards: the number still looks specific.

**Detection.** For each constant state value + unit + scope + dtype + dense/sparse + source. Then
cross-check one derived quantity two ways: compute the arithmetic-intensity ridge point
(`peak_FLOPs / bandwidth`) from a per-chip pair and again from a per-logical-core pair — they must agree.
Disagreement is a unit error, not an insight.

**Rule.**
1. Every constant entering a floor carries provenance inline:
   `<value> <unit> per <scope>, <dtype>, dense, from <source>`. No unsourced constant enters a floor.
2. **Query the target-info / device-listing tool for every constant you tile against:** on-chip buffer
   and accumulator bytes per core, partition count, engine inventory, per-*logical*-core HBM, and the
   logical-core-config mode (which changes cores/device and memory/core together). Where the tree ships a
   cost model or hardware-constants module, that is the source.
3. **Normalize all constants to one scope before combining them**, and state the scope the floor is
   expressed in. Mixing per-chip bandwidth with per-logical-core compute is the standard error, and the
   logical-core divisor is the standard multiplier that conceals it. Convert explicitly:
   `cores = devices × logical_cores_per_device`, and re-derive per-replica capacity from *that*.
4. **Distinguish logical from physical cores everywhere.** Core-fusing modes change the core count, the
   memory per core, and the collective topology at once; a kernel sized for the wrong one fits in
   arithmetic and OOMs on device.
5. **A wrong constant in a spec/prompt is worse than one in a report** — downstream work treats it as
   ground truth and will not re-derive it. Verify it, or label it unverified.
6. After correcting a constant, **re-derive every dependent number** (tiles, occupancy, roofline, fit)
   rather than patching the conclusion.

### E2 🟠 Choosing a configuration on a total when one guessed term decides it

**Pattern.** Assembling a floor from several terms, assigning plausible values to all of them, and
selecting a configuration on the resulting **total** — when the selection is actually decided by the one
term that was guessed.

**Mechanism.** Floors are sums of heterogeneous terms (compute, weight read, activation/KV read,
communication, host). Candidate configurations differ in only a **subset** of them; the rest is common
and cancels in the comparison. The decision therefore rests entirely on the differing terms — and those
are frequently the least well known: per-operation communication cost at a given payload and rank count,
gather efficiency, achieved overlap. Averaging plausible guesses across all terms yields a total that
*looks* robust while the discriminating term carries all of the uncertainty. The failure is silent
because the total is dominated by well-known terms, so the number looks sound even though the
*difference* between candidates does not.

**Detection.** Compute the total per candidate, then **difference them term by term**. Rank terms by
`|Δ across candidates| × uncertainty(term)`. The top of that ranking is the only measurement that can
change the decision.

**Rule.**
1. Before running any configuration, difference the candidate floors and identify the discriminating
   terms. Measure those first, in sensitivity order, with a micro-benchmark at the **production payload
   and degree** — not by running the whole configuration.
2. A guessed constant may never be the discriminator. If it is, that guess **is** the decision.
3. Terms common to all candidates need only be *consistent*, not accurate — spend no measurement there.
4. Re-rank after each measurement; the discriminator moves as uncertainty collapses.

### E3 🔴 A projection may not contain a capability you have not located in the tree

**Pattern.** Including a capability-dependent saving in a floor or plan — a narrower cache dtype, a fused
path, an alternate transpose route, an overlap mode, a dtype performance mode — before confirming the
capability exists **and is reachable from the configuration you will run**.

**Mechanism.** Capability assumptions enter projections as innocuous multipliers ("halves this term") and
are indistinguishable in the output from measured or derived terms. Unlike an efficiency assumption, a
capability assumption is **binary and often simply false**: the mode may not exist for this operator, may
exist only for another dtype or engine, may sit behind a gate that does not apply to this path, or may
exist and be numerically unusable at production shapes. The projection then has no achievable
configuration behind it — and the work needed to reach it is *unbounded* rather than merely large, which
is the difference between a plan and a wish.

This is the complement of C3 and G8: failing to look for an existing gate produces false *pessimism*;
assuming a gate exists produces false *optimism*. Both are resolved by the same action — locating the
code path.

**Detection.** Annotate every term of the projection with the code path that implements it (module +
symbol, or the flag that selects it). Terms that cannot be annotated are wishes.

**Rule.**
1. Each projection term names its implementing path or gating flag. An un-annotated term is removed from
   the projection, or labelled speculative and **excluded from the verdict**.
2. Confirm the capability at the **production operator, dtype and shape**. Availability elsewhere in the
   library is not availability here — sibling kernels routinely differ on exactly this (F1), and
   DMA/transpose engines routinely support only some element widths (F3).
3. Where the capability's absence is what makes the target unreachable, **implementing it is the work
   item** — state that, rather than projecting as though it existed.

### E4 🟠 Porting a roofline correction factor across regimes

**Pattern.** An analytic model was observed to overestimate one regime and that was generalized to "the
model is pessimistic" — while in the other regime it **underestimated**, implying ~1.5× more throughput
than measured. The optimistic direction was the one the verdict depended on.

**Guardrails**
- **Calibrate per regime and never carry a fudge factor across regimes.** Compute-bound (large-`M`
  prefill-like) and memory/collective-bound (small-`M` decode-like) shapes fail in opposite directions;
  so do small-tile and large-tile variants of the same kernel.
- **Treat an uncalibrated analytic figure as an upper bound on performance**, particularly the one your
  go/no-go rests on.
- **A margin smaller than the model's demonstrated per-regime error is not a margin.** Say which side of
  the target the uncertainty falls on.

### E5 🟠 Leaving a dominant compute-phase term as a scalar "raise efficiency" target

**Pattern.** Producing a plan whose largest term is a compute-phase cost, and representing the work on
that term as a single scalar to be raised — utilization, achieved bandwidth, overlap fraction — without
decomposing it into the operators and kernels that spend it.

**Mechanism.** A scalar efficiency target names no operator, no engine and no instruction class, so it
implies no experiment: it is unactionable and unfalsifiable in the same breath. All the actionable
structure lives in the decomposition — which operator dominates the phase, which engine binds inside it
at the production shape, what fraction of that engine's time is **non-arithmetic** (transposes, casts,
weight-load, scale application), and which of those has an alternate path or gate. A plan that stops at
the scalar also silently assumes the gap is **uniformly distributed** across the phase, which it never
is; the gap is nearly always concentrated in a few operators and a few instruction classes.

**Detection.** Try to write the next action. "Raise the efficiency of phase `P`" ⇒ undecomposed. "Move
operator `O`'s activation transpose off the binding engine" ⇒ decomposed.

**Rule.**
1. Decompose any dominant compute-phase term into **per-operator shares** before planning against it, and
   name the binding engine per operator at the production shape (B11, D3, D10).
2. Express the phase target as a set of per-operator, per-engine actions with expected **exposed-time**
   deltas — never as a single utilization figure.
3. The scalar remains valid as an **acceptance criterion**; it is never a plan.
4. Corollary: if the dominant term is a kernel term, the effort cannot be planned at the serving level
   alone. Kernel-level attribution is a **prerequisite**, not a later phase.

### E6 🔴 "No kernel needed" is itself a claim — record its REAL justification

**Pattern.** The decision not to write a kernel was correct — the binding op was memory-bound and at its
bandwidth floor — but it was **recorded** with the justification "the config sweep met both SLA points."
That premise was false (the throughput figure behind it was an unmeasured extrapolation, and the SLA was
in fact missed). So a right decision was archived behind a wrong reason, and when the premise was
withdrawn the decision record still asserted it. A later reader would have inherited both "no kernel
needed" and "the SLA was met."

**Guardrails**
- **State the no-kernel decision's real basis, in floor terms:** which op binds, what its entitlement
  floor is, what the measured value is, and therefore how much headroom a kernel could recover. "Config
  sweep succeeded" is a *different* claim and does not justify skipping kernel work if the target is
  still missed.
- **Distinguish "no kernel is warranted" (the op is at floor — a physics statement) from "no kernel is
  needed" (the target is met — a status statement).** The first survives the target being missed; the
  second does not. Record the first.
- **When a premise is withdrawn, re-record every decision that cited it**, including decisions that
  remain correct. A correct conclusion resting on a retracted premise is indistinguishable from an
  unjustified one on re-read.
- **Never justify a kernel decision with an instantaneous-peak or extrapolated throughput number.**
  Kernel go/no-go rests on measured per-op device time against a computed floor, from the same execution
  (C1, C5, C6).
- **Exhaust and NAME the config axes swept before the no-kernel record** (cache dtype, block count/size,
  batch and sequence caps, optimization level, quantization family). A config sweep can outweigh any
  available kernel change on a memory-bound op — which is a reason to run it, not a predicted payoff; an
  unnamed sweep is not an exhausted one.

### E7 🟠 An attempted sweep that produced no VALID points has not been run

**Pattern.** A workflow made the configuration sweep a mechanical prerequisite for kernel work. The sweep
stage was entered and consumed substantial device time, but every point failed its acceptance criteria
and no valid measurement was produced. Two opposite errors then become available, and a ledger recording
*attempts* cannot distinguish either from a completed sweep: kernel work opened on the premise that the
config axes are exhausted, when none was ever measured; or kernel work skipped on the premise that config
already carried the target. The axes' *reachable ranges* were never established either — a capacity clamp
had gone unexamined — so even the sweep's design was unvalidated.

**Guardrails**
- **A sweep is complete when it has valid measured points per axis, not when it has attempts.** Zero
  valid points is an unrun sweep; say so, and do not open kernel work on it.
- **Record per-axis validity, not just axis names.** E6 requires the axes be named; naming an axis whose
  every point failed overstates the sweep exactly as an unnamed sweep understates it.
- **Establish each axis's reachable range before sweeping it.** A knob the platform silently clamps has a
  narrower usable range than its flag implies, and points planned outside it yield nothing.
- **Kernel work needs the binding op AND the config baseline established.** A binding-op measurement
  taken under a configuration that never ran as specified is not a baseline any kernel can be judged
  against — D2's "exact shipping config" rule applied to the sweep itself.

### E8 🔴 A precedent magnitude must never order kernel work

**Pattern.** The kernel go/no-go is explicitly a *magnitude* comparison (D1, E6) — measured per-op device
time against a computed entitlement floor, with the recoverable headroom deciding whether a kernel is
warranted. Yet the levers a reader inherits as precedent — a cache-dtype change, a block-count sweep —
arrive as bare payoffs detached from the regime that produced them. From those a reader can construct an
apparently well-grounded ordering of work ("sweep config first, expect a large win, skip the kernel")
entirely out of figures that may come from a different model, a different parallelism degree, and a
different binding operator.

D2 is the counter-proof from within this document: the binding-op ranking inverted between two
parallelism degrees of one configuration sweep. If a *ranking* flips within a single regime change, a
*magnitude* certainly does not survive one.

**Guardrails**
- **A precedent magnitude licenses an experiment, never a decision.** Cite it to justify *running* a
  sweep; never to predict its outcome, rank it against kernel work, or record a no-kernel decision.
- **Re-measure every lever in your own regime before it enters the plan** — same model, parallel degree,
  batch, context, dtype (D2). A lever that paid at one degree can be neutral or negative at another.
- **Enumerate the levers from the config surface, not from this document's examples.** The list here is a
  starting set; the authoritative one is the serving stack's own flags, and it is version-dependent. E6's
  "an unnamed sweep is not an exhausted one" applies to *which* axes exist, not just to naming them.
- **Record what you measure as a class, not as a score.** When a lever pays, the transferable lesson is
  the mechanism it exploited (fewer bytes, less replication, better overlap) — not the number, which the
  next reader cannot use.

### E9 🟠 Reading a measured op time *below* its analytic floor as a physics win instead of a floor defect

**Pattern.** A device measurement comes in **below** the analytic entitlement floor for that op (e.g.
measured matmul time well under the quoted "matmul floor"). The gap is quietly treated as headroom
already captured, or ignored, and the floor keeps being used elsewhere.

**Mechanism.** You cannot beat a correct roofline floor. A measured-below-floor result means the
**floor is wrong**: it was built from wrong dimensions, an inflated FLOP count, padding priced as
compute, or a bandwidth/efficiency constant that does not match this graph. The real floor may be lower
(and the true gap elsewhere larger), or the measured number is being counted differently than the floor
assumed.

**Rule.**
- Treat `measured < floor` as a **reconciliation task**, not a win: re-derive the floor from sourced
  dimensions and the measured hardware-FLOP count before trusting either number (extends E1 "constants
  must be sourced, never recalled," to derived op floors).
- Only once floor and measurement are reconciled on the same definition can any `measured/floor` ratio
  steer the work.

---

## Part F — Layout, engines, and on-chip resources

### F1 🟠 Activation transposes are a hidden tax when the ISA fixes operand layout

**Pattern.** Reading a matrix-engine time breakdown as "compute-bound" without separating genuine matmul
instructions from **transpose-class** instructions issued on the same engine.

**Mechanism.** Tiled matmul ISAs typically require the stationary operand in
`[contraction_dim, free_dim]` order. Activations arrive in `[tokens, hidden]` but are needed as
`[hidden, tokens]`, so the kernel transposes on-chip. If that transpose is executed on the **matrix
engine**, it consumes the binding engine's cycles while carrying **zero useful FLOPs**. It is common for
transpose-class instructions to reach double-digit percentages of matrix-engine time across a deep
model's projections (source activation, intermediate activation, attention output).

The asymmetry worth internalizing: sibling kernels in the same library often differ here. One projection
kernel may already route the transpose through a **DMA engine**, which runs in parallel with matrix
compute; another may still use the matrix-engine path — for the same logical operation.

**Caveat — the DMA path only helps in the regime where it overlaps.** "Runs in parallel with matrix
compute" is a property of the *isolated* kernel, where the DMA engine has slack. In the deployed graph
the DMA engine may already be on the critical path (weights/activations streaming), so a relocated
transpose is **exposed**, not hidden; and the layout the relocation needs can make it a
**partition-strided** DMA (short runs, large inter-partition stride, a fraction of peak bandwidth — F9).
An exposed, strided DMA transpose can cost more wall-clock than the on-chip matrix-engine transpose it
replaced. Treat the move as a *relocation, not an elimination* (G5) and validate it end-to-end in the
deployed regime, not on the isolated kernel.

**Detection.** Break matrix-engine time down by instruction class and attribute transposes to source
lines (B11). Ask, per kernel: is any transpose on the matrix engine, and does a DMA-transpose path
already exist elsewhere in the library?

**Rule.** If transpose-class instructions exceed a few percent of the binding engine's time:
1. Classify each as **activation** or **weight** transpose.
2. Weight transposes should not exist at runtime at all (F2).
3. For activation transposes, look for or add a DMA-engine transpose path — but the "improves the
   critical path" outcome holds **only when**, in the *deployed* regime, (a) the target DMA engine has
   critical-path slack and (b) the resulting access is coalescible, not partition-strided (F9). Absent
   either condition the relocation can regress; if the required layout forces a partition-strided DMA,
   the on-chip transpose may be the better choice.
4. Validate the relocation end-to-end in the exposed (model) regime, not on the isolated kernel where
   the DMA is hidden under compute — it is a relocation, not an elimination (G5).
5. Check whether a sibling kernel already solves it; the fix is often enabling an existing path rather
   than authoring one.

### F2 🟠 Weight layout belongs at load time, never at runtime

**Pattern.** Investigating "weight transpose overhead" in a profile, or adding a runtime transpose, when
the layout mismatch should be resolved once during weight loading.

**Mechanism.** Frameworks commonly store projections as `[out_features, in_features]` while kernels
expect `[in_features, out_features]`. Resolving this in the weight loader (a storage-transposed load)
costs load time once and is invisible at inference. Resolving it at runtime costs the binding engine on
every step.

**Detection.** Compare the compiler IR's weight parameter shapes against the kernel's documented weight
signature. If they already match, there is **no** runtime weight transpose and the profile anomaly is
elsewhere — a cheap check that prevents an entire misdirected investigation.

**Rule.**
1. Verify IR parameter shapes against the kernel's documented layout *before* investigating weight
   transposes.
2. All weight layout adjustment belongs in the loader.
3. Beware the interaction with in-place weight fixups: storage-transposed shards are typically
   **non-contiguous**, which is the precondition for the mutation-loss bug in the framework companion
   doc.

### F3 🟠 Sub-2-byte dtypes carry a transpose throughput penalty

**Pattern.** Assuming a narrow-dtype (e.g. 1-byte quantized) transpose costs the same as, or less than, a
2-byte transpose because it moves fewer bytes.

**Mechanism.** On-chip transposes route through an accumulator whose elements have a minimum width. For
1-byte data the engine must stride the accumulator address (a doubled step) to preserve alignment, so
each instruction covers half the logical elements and the **instruction count roughly doubles** for the
same logical tile. Narrow dtypes therefore make matrix-engine transposes *more* expensive in time, not
less.

Order-of-magnitude intuition: with per-layer transposes over `(H/tile) × (S/tile)` and `(I/tile) ×
(S/tile)` grids across `L` layers, a deep model can issue on the order of `10^5` transpose instructions
per step per core — so a 2× per-instruction penalty is not a rounding error.

**Rule.**
1. When a narrow quantized dtype is active on a matrix-engine transpose path, expect roughly double the
   transpose instruction count versus a 2-byte path, and budget accordingly.
2. This makes the DMA-transpose alternative **more** valuable for narrow dtypes than for wide ones — the
   opposite of the intuition that fewer bytes must be cheaper.
3. Confirm the dtype actually supported by the DMA transpose before planning the migration: DMA
   transpose engines frequently support only 2-byte and 4-byte elements, which can make a "transpose the
   quantized data directly" plan impossible (F5, G6).

### F4 🟡 DMA transpose has hard buffer-alignment requirements

**Pattern.** Adding a DMA-transpose path and allocating its source/destination buffers with the default
alignment, then reading the resulting load-time rejection as a kernel logic bug.

**Mechanism.** DMA transpose engines commonly require source *and* destination offsets to be aligned to a
fixed boundary (e.g. 32 bytes). A buffer the allocator happens to place off-boundary causes the runtime
to reject the compiled artifact at load time with an alignment diagnostic — a failure that looks
structural but is a one-word fix.

```python
buf = allocator(shape, dtype=<2-byte dtype>, align=32, name="xpose_buf")
```

**Rule.** Every buffer handed to a DMA transpose, as source or destination, is allocated with the
required alignment explicitly. This constraint is usually documented only in the kernels that already use
the path — so it is easy to miss when extending the pattern to a new kernel. When an alignment diagnostic
appears, fix the allocation before questioning the algorithm.

### F5 🟠 Changing transpose strategy shifts the on-chip memory budget

**Pattern.** Treating a matrix-engine → DMA transpose migration as performance-only, then reading the
resulting on-chip memory overflow as an unrelated defect.

**Mechanism.** The two paths draw on different resources. The matrix-engine path consumes accumulator
banks (a separate address space, transient). The DMA path consumes **on-chip buffer scratch** that
coexists with weight tiles and outputs, competing for the same limited capacity — and if the transpose
must be staged in a wider dtype, that scratch is larger than the data it produces. The symptom is a
compile-time capacity overflow.

**Rule.** When migrating a transpose path, re-budget on-chip memory as part of the change:
1. **Stream it.** Stage one tile at a time into a small reused scratch buffer, convert, write the narrow
   result to the persistent output, reuse the scratch. This bounds the wide-dtype footprint to a single
   tile instead of the whole activation.
2. **Order allocations.** Declare weight buffers only after transpose scratch is released so the two
   never coexist at peak.
3. **Reclaim what the old path needed.** The matrix-engine path's accumulator reservation and any
   identity tensor it used are no longer required — free them.
4. Report the new peak occupancy with its margin. A change that fits with only a few kilobytes to spare
   is fragile against later shape changes and should be labelled as such.

### F6 🟠 Rewriting the suspicious primitive instead of isolating it

**Pattern.** After a numerical failure appears alongside a new kernel path, rewriting the most suspicious
primitive (transpose mechanism, tiling scheme, accumulation order) without first proving it is wrong.

**Mechanism.** Rewrites are expensive and frequently target a correct component; the actual cause is
usually arithmetic *around* the primitive (scale direction, dtype conversion order, index arithmetic). A
minimal isolation test settles it in a fraction of the time.

**Rule.**
1. Write a standalone kernel exercising **only** the suspected primitive at the exact shapes, dtypes, and
   mode flags used in production.
2. Run it **on device**, not only in a simulator — a simulator's reference can share the same systematic
   error, and functional simulation does not validate layout/transpose/packing/sub-byte paths at all
   (H6).
3. Compare against an independent reference. If it matches, the primitive is exonerated; move
   upstream/downstream.

Primitives worth isolating, with the parameter that usually matters:

| Primitive class | Representative shape | Parameter that decides correctness |
|---|---|---|
| On-chip (matrix-engine) transpose | one square tile, narrow dtype | accumulator step size |
| DMA transpose | tile × wide free dim, 2-byte dtype | access-pattern rank, buffer alignment |
| Quantized matmul performance mode | tile × tile, quantized | the mode flag (e.g. double-row) |
| Scale application | broadcast scale × activation tile | scale direction (quant vs dequant) |

### F7 🟠 Choosing K tiling before checking quantization scale granularity

**Pattern.** Choosing the contraction tiling, then discovering the scale layout forbids it — or projecting
a narrow-dtype gain that the real contraction depth cannot realize.

**Rule.**
- **Check quantization scale granularity before choosing K tiling.** Per-tensor/per-channel scales permit
  a full-depth contraction; a scale that varies *along* K forces K to split and interleave vector work —
  attacking the `K/128` (or `K/512` MX) term and breaking the accumulation chain. This is a model-side
  decision that silently caps matrix-array efficiency.
- **Re-derive any projected gain from a narrow dtype with your actual K.** A 4× figure that comes from
  extending the contraction to 512 is not available to a matmul whose contraction is a head_dim of 64,
  absent head-packing or array tiling. A confidently-wrong peak-number projection is worse than none.

### F8 🟠 Hand-tweaking one tiling config, and not knowing the instruction mix

**Rule.**
- **Tuning is a map-space search** (tile sizes × loop order × parallelization, per memory level) — build a
  small autotune harness rather than hand-tweaking one config; the optimum is often non-monotonic and one
  step from an OOM cliff, so probe the boundary automatically. Sweep **per site**, not globally (G11).
- **Compute the matmul-to-total instruction ratio** from the profile. In heavily quantized kernels the
  matrix unit can be a small minority of instructions (layout/pack/format dominate) — if so, array-
  efficiency work is the wrong target; fusion / vector-perf-mode / native format are.
- **Fuse the elementwise chain** (`activation` / `activation_reduce` / `tensor_scalar`) — often the
  highest win-to-effort lever — but note **fusion can regress** by creating a new synchronization
  boundary; measure per regime (prefill vs decode) and gate it (H6).

### F9 🟠 Assuming a "transpose"-classed instruction runs on the matrix engine, and "moving it off" through HBM

**Pattern.** A profiler shows a large "transpose" instruction bucket on what looks like the compute
engine, so an optimization routes the transpose through an HBM scratch to "get it off the tensor
engine." The result is zero improvement (or a regression) — the transpose was never on the tensor engine.

**Mechanism.** On some targets an SBUF→SBUF DMA-transpose primitive already executes on **DMA engines**,
which overlap matrix compute; the profiler's "TRANSPOSE" instruction class is then DMA time, not
matrix-engine time. Re-routing SBUF→SBUF through HBM adds a round-trip hop on the same DMA engines and
changes nothing about engine contention. (Complements F1 — activation transposes *are* a matrix-engine
tax **when the transpose is on the matrix engine**; verify which engine before acting.)

**Rule.**
- Confirm the executing **engine** of a transpose-classed instruction (from the ISA/engine field) before
  optimizing to relocate it. "Transpose in the profile" ≠ "transpose on the matrix engine."
- Remember an **instruction-duration sum is not wall-clock** in a software-pipelined kernel: transpose
  and matmul instruction sums on different engines overlap, so a large transpose *sum* need not sit on
  the critical path (see B3/B4).

### F10 🟠 Trying to eliminate a layout transpose that the contraction makes mathematically required

**Pattern.** A transpose is assumed to be removable "overhead," and effort goes into deleting it via
operand-role swaps or alternate formulations — each of which turns out worse or impossible.

**Mechanism.** A matmul contracts over the dimension that must sit on the partition axis of **both**
operands. When a producer leaves data with the wrong axis on partitions (e.g. flash-attention's `P` has
query positions on partitions, but `P@V` contracts over key positions), a transpose is **required** to
put the contraction axis on partitions. Operand-role swaps to avoid it typically either shrink the
output free dimension (dropping array utilization, multiplying matmul instructions) or move a reduction
across partitions (making softmax/normalization far more expensive) — a net loss.

**Rule.**
- Before treating a transpose as removable, **prove it is not required by the contraction**: identify
  which axis each operand carries on partitions and which axis is contracted.
- Enumerate the operand-role alternatives and **price each** (array-utilization drop, extra matmul
  instructions, cross-partition reductions) before committing; a "required" transpose is a fixed cost to
  overlap, not to delete.

### F11 🔴 Declaring an on-chip-memory-bound lever infeasible from remembered dimensions

**Pattern.** A pipeline/double-buffering lever is judged infeasible because the required buffers
"don't fit" in on-chip memory, and the lever is abandoned as BLOCKED. A later step, reading the actual
dimensions from the config, finds the buffers **do** fit — the blocking verdict rested on wrong numbers.

**Mechanism.** On-chip (SBUF/PSUM) budgets are tight, and a double-residence pipeline (tile *i* computing
while tile *i+1* loads/norms) needs two live buffer sets plus intermediates and weights. Whether that
fits is decided by the **exact** hidden/intermediate/tile dimensions; a wrong recalled dimension shifts
the total by tens of KB and flips fits↔does-not-fit — abandoning a viable optimization on a
fabricated constraint.

**Rule.**
- Compute the on-chip budget from dimensions **read from the config/source**, itemized per buffer
  (shape × dtype × subtiles), and carry the margin. Never gate a lever on recalled dims (extends E1
  "constants sourced, never recalled," to model dimensions and on-chip budgets).
- Distinguish the sub-cases: full double-residence may not fit while a **progressive/tiled** variant of
  the same overlap (tiling the norm temporary) does — re-price the reduced-footprint version before
  declaring the lever dead.

### F12 🔴 Trusting the functional simulator to clear a PSUM-bank / cross-engine kernel rewrite, and chasing the eviction when the write is the defect

**Pattern.** A transpose-coarsening rewrite (batch N `nc_transpose` into sub-columns of one wide PSUM
tile, then evict once) garbles on device but passes the NKI functional simulator exactly (cos=1.0).
The sim-pass is read as "the index math is right, so the bug must be a missing cross-engine barrier on
the eviction," and successive fixes are aimed at the **eviction** (`tensor_copy` → `tensor_scalar(×1.0)`
to force a TensorE→VectorE dependency, engine alternation, etc.), each re-validated only after a
~15-min full serve+compile.

**Mechanism.** The NKI functional simulator models tensor **index arithmetic** but NOT PSUM **bank
contention**, compiler instruction scheduling, or hardware timing of `nc_transpose` writes to
**sub-columns of a tile wider than 128**. So it passes every variant regardless of whether the compiled
kernel places/orders the sub-column writes correctly — a sim pass is *necessary, not sufficient*, for
any rewrite whose correctness depends on PSUM addressing or cross-engine order. Separately, when several
variants that differ only in the **eviction** all garble identically, the eviction is not the defect:
the controlled comparison is a variant that keeps the wide-PSUM sub-column **write** but evicts with the
*baseline's exact* per-sub-tile narrow read — if that still garbles, the defect is proven to be on the
**write** side (sub-column `nc_transpose` into the wide tile), typically because the wide tile shares a
bank-modulo address scheme with another op in the same complex kernel (e.g. the QK^T matmul) while a
standalone kernel using the identical pattern (e.g. MLA) works precisely because it is a separate HLO
with no bank-address contention.

**Rule.**
- For any kernel rewrite whose correctness rests on PSUM bank placement, sub-column addressing, or
  cross-engine ordering, treat a functional-sim pass as necessary-not-sufficient and require an
  **on-device** correctness check before the mechanism verdict (KERNEL A1/A6 applied to correctness,
  not just perf).
- When ≥2 variants that differ only in eviction all garble, stop iterating the eviction. Run the
  one controlled variant that isolates write vs read (wide-PSUM sub-column write + baseline's narrow
  per-tile eviction). If it garbles, the write is the defect — a compiler/ISA-tier issue, not a
  user-level barrier to insert.
- A standalone kernel "does the same thing and works" is not proof the pattern is portable into a big
  multi-phase kernel: bank-address contention with sibling ops in the same HLO is the differentiator.
  Diff the exact bank/tile/dtype geometry and whether the bank is shared, before adopting the precedent.

---

### F9 🟠 A strided-DMA fix must target the axis that actually carries the stride

**Pattern.** A load/store profiled as a slow, small-granularity DMA is "coalesced" by merging an
already-contiguous inner (free) sub-dimension — while the stride actually lives on the *partition*
axis. The source shape changes; the bytes, descriptor count, and effective bandwidth do not.

**Mechanism.** A tensor stored with the contraction/channel dimension outer, loaded so that dimension
lands on the partition axis, reads one short contiguous run per partition with a large gap (the outer
stride) between partitions. Coalescing the free sub-dimension leaves that partition stride untouched.
A genuine coalesce needs wide contiguous runs of the inner dimension (or a different producer layout),
which may not fit the on-chip budget.

**Detection.** Read BOTH src and dst access strides (outer/partition vs inner/free) for the suspect DMA
and name which axis is strided. After any "coalesce" change, re-read the DMA's bytes / descriptor count
/ effective bandwidth in the profile — if they are unchanged, the fix touched the wrong axis.

**Rule.**
1. Identify the strided axis (partition vs free) from the access pattern before proposing a coalesce.
2. Verify the change moved bytes/bandwidth in the profile, not just the source shape.
3. If the strided axis is intrinsic to the producer's layout, the remedy is upstream (producer layout)
   or a different tiling — not a free-dim merge.

### F10 🟠 A cheap on-chip operand reload can be the deliberate price of keeping the other operand memory-resident

**Pattern.** A 1:1 on-chip stationary-operand reload (a load issued per matmul step) shows large summed
busy-time and is "optimized away" by re-ordering the loop nest so one load amortizes across many steps.
The change regresses by multiples.

**Mechanism.** The original loop order pays the cheap on-chip reload precisely so the *expensive* operand
(large weights) stays streamed/resident from memory in big contiguous runs. Re-ordering to amortize the
on-chip load forces the expensive operand to re-stream from off-chip memory per inner step — trading a
cheap-engine cost for a memory-traffic explosion.

**Detection.** Before re-ordering to cut an on-chip reload, ask what the current order does for the OTHER
operand's memory residency; model the DMA bytes of the proposed order (does the big operand now reload
per step?).

**Rule.**
1. Treat a 1:1 on-chip reload as a possible deliberate trade for memory residency, not free waste.
2. Model the memory-traffic consequence of a loop-reorder for every operand before implementing it.
3. Bracket the reorder both ways and accept only on measured wall-clock; a large summed on-chip
   busy-time is frequently not recoverable without paying more in DMA.

## Part G — Which levers exist, and which layer do they belong to?

A profile finding is not automatically a kernel lever. Before proposing any kernel fix, ask: is this
actually fixable at the kernel layer on this stack today, or does it route to config / topology /
upstream? Mislabeling a config lever as a kernel lever sends the effort down the wrong path — and
mislabeling a kernel lever as unavailable deletes the headroom.

### G1 🟠 Proposing a kernel fix for what is actually a topology/config problem

**Pattern.** The profile shows a large exposed collective cost (e.g. 26% of step time in unoverlapped
all-reduces). The analysis correctly identifies it and proposes "collective↔compute overlap" as a kernel
lever — without checking whether that mechanism is functional on the target stack. Where it is not, the
reachable lever is a **topology change** (reduce TP degree to reduce collective count and latency) — a
config/serving decision, not a kernel one.

**Prevention.**
- Before proposing a kernel fix for exposed collective time, check whether the specific overlap mechanism
  is functional on the target stack, and name which mechanism you checked (G3).
- Distinguish the *theoretical* lever ("overlap would save X ms") from the *reachable* lever (what can be
  done today). Present both, but keep the reachability caveat prominent — not buried in a footnote.
- Latency-bound collectives (small payloads at high shard degree) are a configuration symptom: fewer
  participants (lower TP) reduces both the number of collectives per step AND per-collective latency.

### G2 🔴 Treating "kernel change required" as "not available"

**Pattern.** A profile identifies that the binding cost is inside a kernel (attention running at a wider
dtype than necessary, collectives fully serialized, a normalization not fused with the subsequent
quantize). The operator writes "requires kernel change" in the report and treats this as a blocker —
equivalent to "requires hardware change" — rather than as the next work item.

This happens because the operator views kernel code as immutable infrastructure (like the compiler binary
or the hardware), when in fact NKI kernels can be written, modified and registered as replacements; the
kernel library is typically installed in development mode and editable; custom kernels can be wrapped and
dropped into the model code; and kernel-writing agents exist for this purpose.

**Prevention.**
- **"Requires kernel change" is a TRANSITION, not a STOP.** It means: "I've localized the binding cost to
  a specific kernel operation; now I write/modify/replace that kernel."
- **Enumerate what kernel changes would achieve the savings BEFORE declaring them blocked:** a fused
  kernel (norm + quant + matmul), a GQA-aware attention kernel that avoids the expansion, a
  fused-gather-compute-communicate path to overlap collectives, a patch to the existing library kernel.
- **Delegate with a specific spec:** "Write a kernel that does X (input shapes, output shapes,
  constraints) to replace the current Y which costs Z ms." Not "is it possible to do X?" — that invites a
  theoretical answer rather than code.

### G3 🔴 Accepting a dead end for one mechanism as a dead end for the goal

**Pattern.** A dead-end list says "windowed-einsum / collective-matmul overlap — enabling flags are
backend-specific, not wired to this path. No effect." The operator reads this, writes "collective-compute
overlap: dead end," and a large fully-serialized collective share is declared irreducible.

But the dead-end entry names a **specific mechanism**. It does not say "all overlap is impossible." The
same codebase may contain fused-gather-compute-communicate kernels that overlap collectives with matmul
compute at the kernel level, an explicit collectives API with documented overlap patterns, ring-attention
kernels that overlap K/V communication with score computation, and fine-grained AllGather designed for
intra-kernel overlap. A dead end for mechanism A says nothing about mechanisms B, C, D that achieve the
same effect through a different implementation path.

**Prevention.**
- **When a dead-end names a specific mechanism, list what OTHER mechanisms achieve the same goal**, and
  read the full reference section on the topic before accepting a single mechanism's failure as total.
- **A dead-end entry should name WHAT is dead, not WHAT GOAL is dead.** Write: "windowed-einsum flag:
  dead (not wired)." NOT: "collective-compute overlap: dead." The former is a data point; the latter is a
  premature conclusion.
- **Restate the dependency at tile granularity before closing an overlap question.** "All of X before all
  of Y" seldom implies "no part of Y before all of X." A measured near-zero overlap is a property of the
  current decomposition — whole step produces, whole step consumes — not a law. Ask the kernel path
  directly whether the step can be chunked, pipelined, or fused with its consumer, and at what
  granularity extra launch cost overtakes the overlap gained.
- **Separate "no interface exposed a switch" from "the data dependency forbids it."** Only the second is a
  property of the problem; the first is a property of where you looked.

### G4 🟠 Checking for a gate/flag string instead of reading the mechanism it would implement

**Pattern.** An audit of kernel optimizations proceeds by grepping for environment-variable names in the
codebase. When the grep returns zero hits, the conclusion is "optimization not present, claim
unverifiable." But the *mechanism* those gates would implement is fully visible in the stock code: the
unfused call pattern, the suboptimal constant, the redundant materialization. The audit checked for the
PATCH (a string) instead of the OPPORTUNITY (a code pattern).

**Prevention.**
- A grep for a gate is a **deployment check**, not a technical verification. It answers "is the
  optimization turned on here?" — useful operationally, useless for verifying whether the optimization is
  valid.
- Read kernel source with the question **"what would I change to make this faster?"** — not just "does the
  claimed change exist?" The former produces independent verification; the latter produces
  file-existence checks.
- For every kernel call site in the model code, read the kernel's FULL function signature. Parameters that
  exist but are not used (a fused-RoPE flag, `norm_type`, native batch dimensions, cos/sin caches, scale
  factors, layout hints) are candidate opportunities — the kernel author may already have built the fast
  path and nobody wired it up.
- Patterns worth recognizing on sight in stock code: `num_allocated = min(4, num_available)` where
  `num_available` is much larger; `tensor.repeat_interleave(N, dim=0)` before a kernel whose signature
  already accepts `batch_q=N, batch_kv=1`; a standalone normalization call between two kernels that both
  accept `norm_type` and `norm_weights`; a separate transformation call (RoPE, quantize, transpose) when
  the downstream kernel accepts the transformation's inputs directly.
- Structure the audit as: (1) read the claim's mechanism description, (2) find the corresponding code in
  the stock source, (3) confirm the inefficiency exists and the fix is structurally sound, (4) estimate
  the gain magnitude from the code's data flow. Step 4 is where you discover whether a claimed magnitude
  is plausible.
- A missing gate string means "not deployed here." It does not by itself mean "not achievable."

### G5 🟠 Sizing a lever from data flow and stopping there

**Pattern.** A kernel optimization claim quotes a gain ("−7 ms from fused RoPE," "+3.2% from an extended
weight buffer") and the auditor either accepts it or declares it unverifiable. Both skip the work. The
data-flow properties that bound the gain are usually visible — a separate RoPE pass over Q and K
eliminates work proportional to `L × (Q_size + K_size) × 2`; a `repeat_interleave(G)` on K and V before
the kernel eliminates DMA of `L × 2 × KV_size × (G−1)`; a buffer cap that creates aliasing forces a WAR
barrier whose stall is bounded by the DMA write time for the aliased tile.

**Prevention.**
- For any kernel optimization, identify the **eliminated work** concretely: which DMA transfers, which
  compute operations, which synchronization barriers are removed. Each has a computable cost from the
  hardware constants (E1).
- Before declaring a gain unverifiable, attempt the computation: `eliminated_bytes / bandwidth`,
  `eliminated_flops / peak_flops`, `eliminated_barrier_stalls × stall_duration`. Same ballpark as the
  claim ⇒ plausible. Off by 10× ⇒ the claim needs scrutiny.
- Treat the result as a **plausibility bound, not a measurement.** Expect **relocation rather than
  elimination**: a construct usually satisfies a real constraint, so removing it often moves cost into
  whatever the constraint was protecting (H5, G6). The gain from removing synchronization barriers in
  particular is not derivable from bandwidth/compute reasoning alone — the *presence* of the barrier is
  visible in the code, but its cost must be bounded by the operation it serializes against and then
  measured.

### G6 🔴 Ruling on a precision/layout lever without measuring both SLA legs and the cost it relocates

**Pattern (phase sign).** A narrowing lever (lower-precision weights, a compressed cache) is adopted or
rejected on a single measurement — usually decode throughput, the phase it is expected to help. Its effect
on the other phase carries the opposite sign: quantize/dequantize work and any format-conversion
transposes land in the compute-leaning prefill path, so first-token latency **regresses** while decode
improves. Under a *joint* latency+throughput SLA the lever then gets adopted into a latency miss, or
rejected despite a real throughput win. Observed in one port's reported table: roughly −50% weight memory
and ~+19% decode throughput against a ~+12% first-token-latency regression, from a single flag.

**Pattern (relocation and hard constraints).** Adopting a narrower compute precision for a set of
projections did not simply shrink those matmuls. It introduced a **source-layout transformation** on the
operand path that the wider-precision graph never needed — an operation carrying a trivial share of the
arithmetic, running at very low efficiency, and holding a meaningful share of total time. Separately,
extending the same narrow precision to a cached tensor was rejected outright by a layout operation whose
indirect-transpose path required a **wider source element width** than the narrow format provides.
Neither effect is visible in a per-operator arithmetic count: one is a new operator the precision choice
created, the other a constraint a layout operator imposes on which precisions are reachable at all.

**Prevention.**
- **Measure any precision or layout lever on both SLA legs before ruling on it**, and record the pair. A
  single-phase number cannot rank a lever whose sign is phase-dependent.
- **Judge a precision change on whole-graph measured time, never on the arithmetic it removes.** Enumerate
  the operators that appear or grow — transposes, packing, format conversion, scale application — and
  charge their time to the precision decision that caused them. A workaround operator introduced by a
  precision choice is a first-class cost, not overhead; rank it by measured time beside the operators it
  exists to serve.
- **A/B the precision family as a single variable against an otherwise identical baseline**, per module
  family. Switching a whole family at once confounds the matmul win with the layout tax it imports, and
  the net can fall either way.
- **Check layout operators' element-width and alignment constraints before planning a narrow-precision
  path** (F3, F4). A transpose, gather, or indirect-access path with a minimum source width silently
  determines which precisions are reachable, and it surfaces as a compile/kernel rejection rather than as
  a performance result.
- **Where the lever is per-component** (attention projections vs feed-forward), sweep the components
  separately. The memory win and the latency cost usually do not live in the same component, and a partial
  enablement often beats both extremes.
- Expect a prefill regression to sit in format conversion rather than in the matmul. Those are fusible in
  principle, so a prefill regression is a kernel *finding*, not a fixed tax — localize it before accepting
  it.

### G7 🟠 Declining a precision lever on predicted numerical risk, without a fidelity experiment

**Pattern.** A narrower-precision option for a bandwidth-bound step was declined on the argument that the
step performs an arithmetic reduction, and that the narrow format would lose precision and saturate. The
argument was sound in outline and was never tested. It was also applied to the lever *as a whole*,
although the step comprised operations with very different numerical exposure — pure data movement, which
a narrower format leaves bit-exact, and accumulation, which it does not. The safe component was discarded
along with the unsafe one, and no fidelity cost was ever measured at the operating shape.

**Prevention.**
- **Split a precision lever by numerical exposure before accepting or rejecting it.** Pure movement,
  elementwise transform and accumulation carry different risk; adopt the safe components independently of
  the unsafe ones.
- **A predicted accuracy loss is a hypothesis with a cheap test:** compare against the wider-precision
  reference at production shape and report the measured deviation.
- **Report the fidelity cost beside any accepted narrow-precision win, and the achievable win beside any
  rejection.** A rejection carrying no measured fidelity number is an assumption sitting in a decision's
  chair.

### G8 🟠 Declaring communication irreducible without searching for gates or sweeping the axes

**Pattern (unsearched gates).** Measuring that communication dominates the step budget, computing the
per-operation cost, and declaring it a hardware floor — without searching for the precision-reduction or
algorithm-selection gates the runtime already implements. Accelerator runtimes commonly ship collective
implementations at **multiple precisions**: a default safe path (wide payload) and a reduced-precision
fast path. The fast path halves or quarters the data moved per collective while the numerical impact is
often negligible for inference, since the collective carries activations immediately consumed by a matmul
rather than accumulated across many steps. These paths are gated by environment variables because they
change the compiled graph and must be validated per model. The failure pattern: you correctly profile the
step, correctly identify communication as binding, correctly compute that the matmul floor is far below
the measured step — and then attribute the entire gap to irreducible collective latency, while a flag in
the same source tree would halve the payload.

**Pattern (single-point characterization).** A communication step's cost was measured at the one message
size the current formulation happens to produce, found roughly constant across the parallel degrees tried,
and declared a fixed irreducible tax. Two axes were never measured: how cost varies with **message size**
— the axis a different decomposition or a fusion would actually move — and **how many** such steps the
algorithm issues, which a restructuring can change. A single point along one axis had been generalized
into a property of the problem.

**Prevention.**
- When communication is identified as binding, **immediately search** for precision-reduction and
  algorithm-selection gates. This is a 60-second grep, not an investigation. Targets: collective precision
  flags, DMA/transpose path flags, collective algorithm overrides (ring vs recursive-halving),
  async/pipelined execution flags, and conditional branches in the communication code gated on env vars.
  Check whether the scatter/gather payload is at the model's compute precision or at a wider "safe" one.
- Enable every relevant gate, recompile if needed, and re-measure before concluding anything about
  hardware limits (all four gates of H4 apply).
- **Sweep message size, not only parallel degree.** Latency-dominated and bandwidth-dominated regimes
  respond oppositely, and which regime you occupy decides whether fewer-larger or more-smaller helps.
- **Count the steps as well as timing them.** The governing quantity is cost per step × steps per unit of
  work; a restructuring that changes the count is invisible to a per-step measurement.
- **"Flat across the degrees you happened to try" is not flat.** State the axes and ranges actually
  measured whenever calling a cost fixed.
- **"Irreducible" applies to bytes the algorithm must move, never to bytes the current formulation happens
  to move.** Separate the two before using the word.

### G9 🟠 Building a workaround on an inherited claim that has gone stale

**Pattern.** A "kernel X SIGSEGVs / heap-corrupts / is unsupported" note from an older compiler is taken as
current and a workaround is built on it. Conversely, a genuine win is often a **stale format/compiler
workaround** (an upcast-for-transpose flag, a disabled fusion) that routed around a bug since fixed.

**Rule.** Re-test any inherited "unsupported / crashes / must be worked around" claim on the **current**
compiler before building on it — a direct on-device test takes minutes and has reversed such claims.
Re-test the native path for correctness *and* speed; it is frequently a zero-code flip. This is the same
staleness discipline that I1 applies to the environment: both go stale, and both are cheaper to re-test
than to work around.

### G10 🔴 Sizing a fix by its mechanism instead of by the share of the bottleneck it reaches

**Pattern.** Localizing a bottleneck to a single mechanism, correctly identifying a structural fix for that
mechanism, and then sizing the fix against the **whole** bottleneck — when the fix reaches only one of
several sites the bottleneck is spread across. Observed: a stall term attributed by source site as roughly
*30% site A, 17% site B, 41% unattributable (no source location), 12% other*; the fix addressed site A,
was sized as though it addressed the term, and delivered a **~2% step improvement against a target
needing ~130%**. The per-site breakdown was already in hand when the sizing was written.

**Mechanism.** Two independent errors compound, and both are invited by the same table:
- **Reach.** A structural fix operates on the code path it edits. If the site distribution is 30/17/41/12,
  a fix at the first site has a **hard ceiling of 30%** of that term, no matter how completely it removes
  the mechanism there. Sizing from the mechanism instead of from the reach silently multiplies the estimate
  by the reciprocal of the reachable share.
- **The unattributable residual.** A meaningful fraction of instructions in a real profile carry **no
  source attribution** — they are toolchain-generated. That fraction is invisible to *every* source-level
  edit. Ranking levers over the attributable portion and implicitly assuming the residual "comes along" is
  a projection containing a capability that does not exist in the tree (E3).

Together they produce a well-localized diagnosis attached to a badly-oversized remedy — the most
demoralizing failure available: the diagnosis survives every audit, the fix lands and is verified to have
engaged the mechanism, and the number does not move.

**Rule.**
1. Every lever's size is the **sum of the bottleneck rows it can reach**, never the whole term. State the
   reachable share beside the estimate.
2. Carry the **unattributable share as an explicit hard ceiling** on what source-level work can achieve.
   If the residual exceeds the required improvement, source-level work cannot get there and the plan needs
   a different class of change.
3. When a verified-landed fix moves the mechanism but not the metric, the first hypothesis is a **reach**
   error in the sizing, not a failure of the diagnosis. Re-partition before re-diagnosing.
4. Distinguish this from measurement-scope errors (B1, B4): there the *magnitudes* are wrong; here the
   magnitudes are right and the **mapping from fix to magnitude** is wrong.
5. Prefer a fix whose reach spans the largest share, even if its mechanism is less clean than a narrower
   fix's. Reach beats elegance.

### G11 🟠 Sweeping a shared tiling constant globally and reading one aggregate number as the verdict

**Pattern.** Treating a shared tiling constant as one axis, changing it globally, and reading the aggregate
outcome as that axis's verdict. Observed: reducing a shared tile-width constant so a sharded dimension
split into multiple tiles **helped** one projection (it created the multi-buffering the single-tile
structure had made impossible) and **regressed another by an order of magnitude more** — so the global
change was net negative, and a single-number readout would have closed the axis as unhelpful while the
mechanism it enabled was in fact the right one.

**Mechanism.** A tiling constant interacts with each site's own shape: the size of the dimension it
divides, how many tiles that yields, the resulting per-descriptor byte count against the DMA efficiency
knee, the live on-chip footprint, and how much reuse the loop structure can exploit. Those inputs differ
per site, so the *same* constant sits on a different part of each site's non-monotone response curve. The
extra trap is that a shared constant **looks** like a single knob — one symbol, one edit, one recompile —
so it invites one experiment and one conclusion, when structurally it is as many experiments as there are
sites.

**Detection.** For each site the constant governs, compute what it yields there **before** changing it:
tiles produced, bytes per descriptor, live on-chip footprint. Sites where the count is 1 are structurally
unable to pipeline and want a smaller value; sites already well above 1 are candidates for the opposite.
If the computed effects differ in sign across sites, a global change cannot be evaluated as one
experiment.

**Rule.**
1. **Parameterize per site** before sweeping. If the code exposes one shared constant, add per-site
   constants first; that refactor is a precondition for measuring the axis at all.
2. Sweep and **bracket each site independently**, and record per-site outcomes. Never let one site's
   regression retire a mechanism that another site's improvement validated.
3. A tile count of **1** on any site is a structural finding in its own right: multi-buffering, and
   therefore load/compute overlap, cannot exist there. Report it as a defect rather than as a tuning
   opportunity.
4. Report the outcome per site with its computed inputs, so a net-negative aggregate does not bury a
   positive component — and so the negative component's cause (descriptor size below the knee, footprint
   growth, instruction-count growth) is named rather than guessed.

### G12 🟠 Treating a buffer-depth change as monotone

**Pattern.** Reducing the multi-buffer depth of on-chip weight or activation tiles to relieve capacity
pressure and eliminate spill traffic, treating the reduction as a strict improvement because the capacity
arithmetic clearly favours it.

**Mechanism.** **Buffer depth *is* the pipeline.** Depth is precisely what allows the load of tile `n+1` to
overlap the compute on tile `n`. Reducing it trades overlap for capacity, and the two effects have
**opposite signs** on step time. Which one wins is shape-dependent and is not predictable from the capacity
arithmetic, because that arithmetic contains no term for the overlap being given up. The loss can exceed
the saving outright: observed, halving the depth of two tile classes produced a small but reproducible
**regression**, outside the run-to-run spread of the baseline. The compounding error is evaluating the
change on the wrong quantity — fewer spilled bytes is the *intended mechanism*, not the result.

**Rule.**
1. **Bracket** buffer depth on both sides rather than moving it monotonically. Depth is a non-monotone
   axis, and the optimum is interior.
2. Accept a depth change **only** on a step-time improvement at the production shape, with the run-to-run
   spread quoted and smaller than the effect.
3. Prefer allocation control that **preserves depth while shrinking footprint** — explicit allocation,
   tighter tile shapes, staged reuse — over reducing depth.
4. Never evaluate a capacity-pressure change on the byte counter alone. The byte counter is the mechanism;
   the step time is the verdict.

### G13 🔴 Planning a pipeline/overlap optimization on a compiler primitive that the installed toolchain does not provide

**Pattern.** The plan to hide non-matmul work behind matmul is "use the compiler's multi-buffer /
pipeline / overlap directive" or "signal loop independence so the compiler overlaps iterations." Days go
into the design; then the primitive turns out **absent** in the installed toolchain, or the loop-range
variants are all semantically "fully unrolled" and none license cross-iteration overlap.

**Mechanism.** Overlap primitives (explicit multi-buffer/pipeline directives, independence-carrying loop
ranges) are toolchain-version-specific. A reference/handbook may document one the installed version does
not expose. Separately, a buffer-**address**-separation control (an "interleave degree") is often
mistaken for an instruction-**scheduling** control: it guarantees non-aliasing addresses but does **not**
tell the scheduler to overlap operations across iterations.

**Rule.**
- Confirm the overlap primitive exists in the **installed** toolchain (scan the API surface;
  `hasattr`/`dir` the compiler module) **before** planning around it.
- Do not conflate buffer-address separation (interleave/ring buffers) with instruction-scheduling
  overlap; verify by profile that iterations actually overlap, not just that addresses differ.
- This and G14 are the **intra-kernel, cross-tile analogue** of the companion FRAMEWORK document's I1
  ("architecting around overlap before verifying it is achievable"): I1's mechanism is
  collective-vs-compute overlap and the collective's memory-residency requirement; here it is
  engine-vs-engine overlap across tile iterations and the compiler's scheduling model. Both demand the
  same discipline — a ≤1-hour 2–4-unit profiling probe measuring the active-time *intersection* before
  any multi-week rewrite.

### G14 🟠 Expecting the compiler to overlap iteration i+1's prep with iteration i's compute automatically

**Pattern.** A tile loop shows the compute engine idle most of the time while other engines work, and
the assumption is the compiler will overlap the next tile's load/norm/prep with the current tile's
matmul once buffers are non-aliasing. It does not; the engine stays idle and the wall-clock does not move.

**Mechanism.** With fully-unrolled loops, the scheduler overlaps independent instructions **in the order
they were emitted**, and within one tile the load→norm→transpose→matmul chain is a true dependency chain,
so nothing overlaps. Cross-tile overlap requires (a) **physically interleaving** the next tile's prep
instructions among the current tile's compute instructions in the emitted source, **and** (b)
simultaneous on-chip **residence** of both tiles' buffers (see F11). Address separation alone (G13)
achieves neither.

**Rule.**
- To pipeline across iterations, hand-interleave the emission (prologue prepares tile 0; steady state
  emits compute(*i*) with prep(*i+1*); epilogue finishes the last tile) and budget the double residence
  first (F11).
- Estimate the ceiling honestly: the reducible headroom is `window − (max single-engine busy +
  non-hideable serial work)`. If most of the "compute" window is already max-engine-busy, cross-tile
  overlap buys little and the lever is elsewhere (a different engine's work, or a higher layer).

---

## Part H — Is my experiment capable of detecting what it tests?

An experiment whose observable cannot detect its mechanism produces a confident, precisely-quoted false
negative, and a null result on a correctly-identified bottleneck looks exactly like a genuine
toolchain limitation.

### H1 🔴 Mismatching the observable to the mechanism

**Pattern.** Testing an optimization that works by **overlap** using **instruction counts**; and,
separately, reading a changed compiled-artifact hash as evidence of a changed schedule.

**Mechanism.** Different mechanisms leave different signatures:

| mechanism | signature | wrong observable |
|---|---|---|
| elision / fusion | fewer instructions | time alone (may be unchanged if not binding) |
| overlap / pipelining | lower wall time, higher occupancy, **counts unchanged** | counts |
| retiling | changed geometry histogram, changed counts | wall time alone |
| dtype narrowing | changed operand dtype, changed per-instruction cost | counts |

Testing overlap by counting instructions returns "no change" even when the optimization worked, because
overlap does not remove instructions — it removes *gaps between* them. In the mirror error, a changed
artifact hash proves only that the toolchain **consumed** your edit; any source perturbation changes the
binary.

**Rule.**
1. **State the mechanism first, then choose the observable**: counts for elision and retiling; wall time
   plus occupancy plus stall decomposition for overlap; geometry histogram for tiling; operand descriptors
   for dtype (B2).
2. Report the observable that *should* change **and** one that should not. "Counts identical and wall time
   unchanged" is a far stronger negative than either alone; "counts identical, wall time down" is a
   successful overlap change that a count-only test would have discarded.
3. A changed artifact hash establishes only that the edit reached the toolchain. Never quote it as a
   behavioural result — its usefulness is real but narrow: it rules out "my change never took effect" as
   the explanation for a null result.
4. Before accepting any null kernel result, confirm the observable was capable of showing the effect.

### H2 🔴 Holding the wrong operand invariant in a reuse experiment

**Pattern.** Testing whether consecutive matmuls can amortize their operand-load instruction by
restructuring the loop nest to hold the **moving** operand constant — when the load instruction under test
loads the **stationary** operand — and then reading the null result as proof that the toolchain does not
implement the optimization.

**Mechanism.** Matmul ISAs of this family distinguish a **stationary** operand, loaded into the array and
amortizable across consecutive instructions, from a **moving** operand, streamed through it. The
operand-load instruction targets the stationary one. Holding the moving operand fixed therefore tests
nothing about the amortization — but it produces a perfectly clean null result, and a null result on a
correctly-identified bottleneck is exactly what a genuine toolchain limitation looks like. The conclusion
then hardens into "this requires a compiler change," which reclassifies tractable kernel work as an
external dependency and terminates the search.

**Rule.**
1. Before any reuse or amortization experiment, **write down which operand the instruction under test
   loads**, and design the loop invariant around that operand.
2. A negative result on an operand-reuse optimization is admissible **only** with the invariant explicitly
   stated and shown to be the right one.
3. This is a precondition for any "the toolchain will not do X" claim. Such a claim reclassifies the work
   as an external dependency, so it carries the burden of a correctly-designed experiment.
4. Generalize: for every optimization that depends on a *hardware asymmetry* between operands, roles, or
   axes, state the asymmetry first. The experiment that ignores it looks identical to the one that respects
   it, and only the conclusion differs.

### H3 🟠 Judging a traffic-attributed wait on end-to-end time alone

**Pattern.** Attributing a compute-engine wait class to a specific traffic source — spill/reload traffic, a
particular queue, a particular transfer class — then attempting a remedy and evaluating it only on
end-to-end step time.

**Mechanism.** The attribution and the remedy are **two independent claims**, and a null end-to-end result
cannot distinguish their failure modes. If step time does not improve, either (a) the attribution was wrong
and the traffic was never the cause, or (b) the attribution was right but the remedy failed to reduce the
traffic. These have opposite next actions — re-localize versus try a different remedy — and the outcome
alone does not tell you which. In practice the effort picks one by plausibility, and the real cause goes
untouched while the attribution survives as an unexamined premise.

**Rule.**
1. Every traffic-attributed wait carries a **two-part validation**: the traffic counter moved, **and** the
   wait moved.
2. If the traffic fell and the wait did not, the **attribution is refuted** and re-localizing becomes the
   top task — not a caveat, and not an argument for a bigger version of the same remedy.
3. If the traffic did not fall, the remedy is void and says nothing about the attribution. Report it as
   such rather than as evidence against the lever.
4. Never accept a null end-to-end result as a verdict on the attribution. It is a verdict on the pair.

### H4 🟠 Treating a clean compile at a higher optimization level as validation

**Pattern.** Treating a successful compile at a higher optimization or instruction-scheduling level as
evidence that the level is usable, and by extension as evidence about the mechanism it was supposed to
exercise.

**Mechanism.** Aggressive scheduling levels are the **least-exercised paths** in a toolchain. Three
distinct failures follow, all easy to misread:
- The artifact **compiles cleanly and then fails to load or execute**, with a runtime error that carries no
  reference to the optimization level.
- Compile time inflates by orders of magnitude, which **trips unrelated startup timeouts** — barrier waits,
  readiness probes, health checks. These surface as infrastructure faults and get diagnosed as such, and
  the level itself is never actually evaluated.
- Numerics change while the artifact runs, so a performance result is quoted from a build that no longer
  computes the right answer.

The compile is the first of **four** gates: compile → load → execute → numerically correct.

**Rule.**
1. Validate through **all four gates** before crediting a level with anything. "It compiled" is not "it
   works," and it licenses no claim about the mechanism.
2. Record the **failing gate and its exact runtime error**, and reproduce once on a clean device before
   recording the level as rejected — so a transient device state is not mistaken for a verdict (I2).
3. **Raise startup, barrier, and readiness timeouts before the attempt**, budgeting for compile inflation.
   A timeout is not the flag's verdict, and discovering that after the fact costs the whole attempt.
4. A level that compiles but does not run is **measured-and-rejected, not untried**. But note the limit of
   what that buys: it removes the flag from the lever list without settling the underlying mechanism, which
   still needs a direct experiment (H1, H2).

### H5 🔴 Reporting location as causation

**Pattern.** A named workaround — introduced to satisfy a layout or type constraint — was located in the
profile and assigned a share of the total. That share was then reported as the workaround's cost and used
to explain why a precision path underperformed. No variant was ever run with the workaround removed or
bypassed. The operators it introduced were real and their time was real, but how much of that time removal
would actually recover, and whether removal would relocate the cost rather than eliminate it, stayed
unmeasured.

**Rule.**
- **Attribute cost to a construct only through an ablation:** an otherwise-identical run with it removed,
  bypassed, or gated off.
- **Expect relocation rather than elimination.** A workaround usually satisfies a real constraint, so
  removing it often moves cost into whatever the constraint was protecting. Measure the delta on the
  **total**, not on the operator.
- **Gate such constructs behind a default-off switch when introducing them,** so the ablation is one flag
  away later (H6 pays for itself here).
- Distinct from B8's ban on deriving a component by subtraction: there the component was never measured;
  here it was measured directly and correctly — what was never measured is its *counterfactual*.

### H6 🔴 Validating a kernel anywhere other than on device, at production shape, behind a gate

**Rule.**
- **Functional simulation does NOT validate layout-, transpose-, packing-, or sub-byte-dtype operations.**
  A functional simulator checks arithmetic, not the hardware data path. Anything touching
  transposes/interleave/`fp8`/`mxfp` packing must be **validated on device** — a 1-byte transpose has been
  correct in sim and garbage on device.
- **Validate any new kernel at the production shape/batch**, not a minimal case — tiling and buffering
  behaviour change with real shapes (and can hit the OOM/scratchpad cliff).
- **Gate every optimization, default off, baseline byte-identical when unset.** This lets you answer "is
  this change responsible?" by flipping one variable, survives a long campaign on a shared tree, and makes
  H5's ablation one flag away. Verify the intended fast path was actually taken (assert/log it) — a silent
  fall-through to a working-but-slower path masquerades as a memory error.
- **Correctness bar:** token-identical on real content (not a synthetic degenerate prompt), and **generate
  enough tokens** to exercise persistent-state (KV-cache) writes — short probes miss bugs that appear past
  the first few tokens. Reproduce any divergence twice before diagnosing it as numerical.

### H7 🔴 Treating "compiled + warmed + ready" as executable

**Pattern.** A compiled graph built cleanly, allocated its caches, completed warmup, and the service
reported itself ready. The first real execution then failed on **every rank** with a generic
execution-scheduling error carrying no diagnostic detail. Suspicion fell first on the reduced-precision
feature under test — an expensive wrong turn, since that feature was the session's whole purpose. The
dumped execution/scheduler state was the actual tell: it showed the large-input phase had already completed
and the failure occurred on the *incremental* phase's graph. Re-running the identical configuration with
only the batch-capacity dimension reduced — one variable, nothing else changed — executed correctly. That
established the failure as a property of **one graph variant of one dimension**, not of the feature, the
phase, or the code path.

**Guardrails**
- **Treat the first real execution at production shape as the validation event.** Warmup may exercise a
  different shape than production traffic, so "compiled + warmed + ready" is not evidence of executability.
- **On a generic execution-time failure with no detail, read the dumped execution/scheduler state FIRST**
  to identify which phase and which graph variant failed. That single fact separates a graph-variant defect
  from a feature defect, and it is usually already in the log.
- **Isolate with a single-variable bisect on the suspect dimension before doubting the feature under
  test.** Hold dtype, precision, parallel degree, and everything else fixed.
- **Expect the blocked dimension to be the one you most need.** When a capacity dimension is required to
  reach the target and its graph variant is defective, that defect *becomes* the work — do not quietly
  re-plan around a smaller value and carry the shortfall forward unstated.
- **Derive nothing from a graph variant that failed to execute** — no timing, no occupancy, no verdict.

### H8 🔴 Certifying a quantized kernel by per-layer similarity on a deep model

**Pattern.** A quantized path is validated by comparing each layer's output against the reference,
observing very high per-layer agreement (cosine ≈ 0.9999), and reporting equivalence. Error **compounds
with depth**: a small per-layer perturbation accumulates over many layers into a logit perturbation large
enough to flip the greedy argmax on a minority of tokens, surfacing as occasional single-token corruption
and, rarely, a derail on long open-ended generation — invisible both to the per-layer check and to a short
smoke prompt.

**Prevention.**
- Validate a precision change **end-to-end at depth**, on outputs that expose token-level divergence (long
  greedy generations, not eight-token smokes), and report a **token-level agreement rate** rather than a
  per-layer similarity.
- Model the compounding before trusting a per-layer figure: a per-layer relative error ε over `L` layers is
  a random walk of order `ε·√L` on the logits, so the acceptable per-layer ε shrinks as depth grows. State
  `L` beside any per-layer similarity claim.
- Separate *implementation* correctness from *numerical* acceptability. A path can be correctly implemented
  and still unacceptable at this precision; conversely, a per-token deviation that also occurs in the
  reference deployment of the same quantized checkpoint is a property of the format, not a bug in the port.
  Establish which before spending debug time.

---

### H9 🔴 Accepting a one-shot measured improvement whose size is inside the run-to-run noise of the oracle

**Pattern.** A candidate is measured once, clears the accept bar, and is banked as a win — but the
oracle reads a single-sample statistic (e.g. one execution's max cycle count) on a target whose
run-to-run spread is wider than the accept margin. A second measurement of the *same* binary lands
on the other side of the bar. The believed win was a draw from noise; the tell is that repeated
measurement of an unchanged artifact spans the margin. Trying the smallest, lowest-risk levers first
compounds this: their true effect is the most likely to be smaller than the noise, so none can ever
be confirmed even if real.

**Rule.**
- Estimate the oracle's noise before trusting any margin: measure the unchanged baseline more than
  once and take the spread. If the spread is at or above the accept margin, the margin is invalid.
- When noise is at or above the margin, either reduce it (report a median/min of several runs instead
  of a single sample) or widen the margin above the noise floor — do not iterate at a margin the
  instrument cannot resolve.
- Require a confirm re-measure of every apparent win; accept only if it clears on both. A single-run
  improvement that does not reproduce is not a win.
- Order candidates by expected effect versus the measured noise, largest first. A candidate whose
  expected effect is below the noise is unconfirmable and should be flagged as such, not spent as an
  iteration.

### H10 🔴 Reading a null result from a fix whose optimized path never executed at the measured vector
**Pattern.** A change compiled and was numerically correct, and its measurement came back at the
baseline, so it was recorded as "no effect." But the optimization was gated on a configuration field
(a window size, a mode flag, a shape predicate) that the measured test vector never set, so the
optimized path never executed for that vector. The experiment measured the unchanged path and could
not have detected the change; the flat result was a detection failure, not a true null.
**Rule.**
- A fix is validated only when the MECHANISM moved AND the metric moved (two-part). A flat metric with
  a change believed-applied usually means the optimized path did not activate at the measured vector.
- Before reading a null as "no effect", confirm the changed path actually executed for the measured
  vector: check that its gating condition (config field / flag / shape predicate) is satisfied at that
  vector, or add an assertion/log that the new path ran.
- Prefer a measured vector that exercises the change. If the vector cannot activate it, fix the vector
  (or the gating) before concluding anything — an inactive change measures identically to no change.

## Part I — Environment, toolchain, and infrastructure failures

### I1 🔴 "Won't build / unsupported" is the highest-cost claim — check toolchain identity first

**Pattern.** Work was declared blocked because a *project dev* virtualenv was broken — it carried a foreign-
accelerator framework build and had never been the device toolchain at all, while the sanctioned platform
venv (correct device runtime, compiler, and plugin) was intact and importable. An earlier round of the same
work had declared a different false blocker; when that was disproven, a fresh one was substituted rather
than the job being run.

**Guardrails**
- **Before accepting any build/unsupported blocker, confirm toolchain identity:** which interpreter, which
  compiler version, does the device runtime import, is the device visible and idle. A dev venv is not the
  device toolchain; its breakage says nothing about buildability.
- **A real blocker = exact command + log excerpt + on-device error.** Anything less is a hypothesis. Route
  genuine compile failures to the artifact-debugging path instead of accepting the blocker.
- **Expect blocker substitution.** After disproving one, verify the replacement at the same depth before
  re-planning around it.
- This is G9's stale-claim rule applied to the *environment* rather than the compiler.

### I2 🔴 After a crashed multi-rank job, a device-init failure on the next launch is usually crash residue

**Pattern.** A multi-rank job died mid-execution. Every subsequent launch then failed much *earlier*, during
device initialization, with a memory-initialization/scrub error naming a specific device plus a generic
device-init failure — a signature that reads unmistakably as hardware. It was neither hardware nor the
change under test: it was cleanup residue from the killed job. No process held a device handle, the kernel
log showed no device errors since boot, and once the driver had reclaimed the memory a minimal single-device
operation executed correctly and the full job initialized normally with **no configuration change at all**.

**Guardrails**
- **A device-init failure immediately following a crash is a distinct event from the crash.** Do not merge
  them into one diagnosis, and do not attribute the second to whatever was under test.
- **Confirm no process still holds a device handle, and read the kernel log with timestamps** — device
  errors dated to boot are not evidence about now.
- **Probe with the smallest possible on-device operation on a single device before escalating.** It
  separates a real fault from residue in seconds, and it is the primary evidence for recovery.
- **Allow the driver time to reclaim device memory after an abnormal exit and re-attempt once** before
  diagnosing; an immediate retry can fail for reasons the next one will not.
- **Confirm recovery on a successful minimal operation, not on an absence of errors.**

### I3 🟠 Losing track of long compiles and profiles

**Guardrails**
- A large-model kernel/graph compile can be tens of minutes; launch it with a **known PID + `timeout`**,
  logged, and **verify via `ps` / device status** before claiming it finished or didn't start. One device job
  at a time; kill orphans by **exact PID**.
- Use a **fresh compile-cache dir per concurrent activity**; a stale-cache race produces spurious "kernel
  file does not exist" failures that look like code bugs. Don't wipe a cache another job may be mid-build
  on.
- A cancelled conversational turn does not kill the device job. Verify state before re-launching.

### I4 🔴 A forbidden measurement path with no named replacement becomes a hand-rolled one

**Pattern.** A brief rules out a specific measurement or launch path — often for good reason — and names
nothing to use instead. The capability is still required, so it gets built. The improvised path then
re-derives stack- and model-specific plumbing one failed launch at a time (input-preprocessing mode, dtype
and quantization wiring, cache roots, readiness limits), and the allotment is consumed before a single
device artifact exists. For kernel work the cost is structural rather than merely wasteful: the one hard
input to this entire document is a phase-verified trace of the target graph (A1), and a session spent on a
substitute launcher produces none — so no binding op can be named, no floor compared, and no lever ranked.
The improvised path additionally owns the definition of whatever quantity it reports, unaudited.

**Prevention.**
- Treat "the sanctioned path is excluded and no substitute was named" as a **blocking procedural gap to
  resolve before any device time**, not as licence to write a harness. It is the absent-mandated-capability
  case, and it is reported, not worked around.
- Name the owner of each required capability — launch, capture, ingest, analysis, the measurement the
  verdict rests on — before writing constraints. An unowned capability will be improvised.
- Where a path must genuinely be built, treat it as **code with a correctness and measurement burden**:
  reconcile its metric definitions against the prescribed ones before quoting any number from it, and
  shake it down on a reduced-size configuration rather than on a full-weight launch.
- **A session that produced no trace has not established anything about kernel headroom** (A1). Report
  absence of measurement as such, never as absence of a finding.

### I5 🟠 A destructive cleanup verified only against what it targeted

**Pattern.** A cleanup of caches, traces, or scratch artifacts is scoped to an explicit list and run; the
surrounding tree is then found to have lost more than the command could have reached. Either another actor
is still live in the workspace running its own cleanup, or one step had broader reach than its name
suggested. Verifying only that the intended targets are gone confirms the intent and cannot surface the
discrepancy — so the wider loss is noticed later, charged to the cleanup by default, and the live actor
goes unnoticed while it is still writing. On a shared kernel/compile tree the stakes are higher than the
deleted bytes: a concurrent build reading a half-cleaned cache yields spurious "artifact does not exist"
failures that read as code defects (I3).

**Prevention.**
- After any destructive step, list the **parent scope** and reconcile what disappeared against what the
  command could reach. An unexplained difference is evidence of a concurrent actor; investigate before
  proceeding.
- Confirm no spawned compile, capture, or ingest job is live in the workspace **before** deleting anything
  in it — process table and device state first (I3).
- Prefer explicit target lists to recursive removal, and assert each target exists so a no-op is loud.
- **Positive-check that untargeted dependencies survived** (staged weights, compile caches another job may
  be mid-build on, captures not yet ingested). Absence of an error is not confirmation.
- State which effects you can attribute to your command and which you cannot; unexplained side effects are
  an open item, not a completed step.

### I6 🔴 Coarsening a PSUM→SBUF transpose evict changed K-layout, not just copy count — and dtype was a red herring

**Pattern.** A decode attention K-transpose emits many tiny ops: one `nc_transpose` + one `tensor_copy`
per sub-tile (per `blk_len_i` × `d_tile`). To cut the copy count, the sub-tile transposes were batched
into one wide PSUM tile (`[128, coarsen_factor·128]`) written by several `nc_transpose` into adjacent
sub-columns, then evicted with ONE wide `tensor_copy` to `k_sb`. Across THREE attempts (auto-PSUM →
manual-contiguous-PSUM → wide bf16 copy) the model compiled cleanly every time but produced **garbled,
repetitive decode** (`"France is France is …"`, `"LCBCBC…"`). The second attempt's post-mortem blamed an
implicit **bf16→fp8** down-conversion in the wide copy (PSUM is bf16, `k_sb` was fp8) and cited a working
wide copy elsewhere whose destination was bf16. Attempt three made `k_sb` **bf16** so the copy was
bf16→bf16 — and it **still garbled**. The dtype hypothesis was wrong; the defect was structural.

**Why the signature matters.** A precision fault (wrong dtype/scale) yields *slightly-off but coherent*
text. **Repetition / fixed-token output means attention is reading the wrong K positions** — a layout or
PSUM-bank placement bug. The per-tile→wide change altered *where the transposed K lands*, even when the
`k_sb` column arithmetic looks algebraically identical to the per-tile version: writing N `nc_transpose`
outputs into sub-columns of one PSUM allocation and then reading it back linearly assumes those outputs
are physically contiguous in that bank, and the per-tile original used a **quarter-bank** slot per 128-wide
transpose while the wide tile fills the **whole bank** — so a per-tile `address` interleave stride can
alias once the tile width changes.

**Prevention.**
- **A kernel edit that changes tile geometry / PSUM-bank layout is a CORRECTNESS change, not just a perf
  change — validate it OFF-DEVICE with NKI simulation (cosine vs a per-tile reference) BEFORE spending a
  10–20 min device compile + serve.** Three device round-trips were burned confirming "still garbled"
  that an isolated-kernel sim would have caught in minutes (A1/A6: build the cheap oracle first).
- **Read the failure signature before theorizing a cause.** Repetition ⇒ wrong-K-position (layout);
  slightly-wrong-but-coherent ⇒ precision. Do not attribute a *structural* garble to a *dtype* cast.
- **When a hypothesis (here: the fp8 down-cast) predicts a fix that does not work, the hypothesis is
  refuted — do not carry it forward.** Record the refutation so a fourth attempt does not re-assume it.
- **After a lever fails on the same symptom ~twice, stop patching it incrementally and either root-cause
  it in isolation or pivot to a different lever** (here: native-fp8 matmul to remove the bf16 CAST).
  Preserve the known-good source (revert to the backup) between attempts so the best config stays live.

---

### I6 🔴 An analysis step runs (or silently degrades to) a weaker profile-less mode instead of FAILING when the mandatory profile is absent

**Pattern.** A per-kernel measurement step already leaves a device trace and its correlating
artifact on disk as a side effect. A separate analysis step could run in a full mode (all
profile-consuming specialists) when given an ingested profile, or a weaker "source-only" mode when
not. Because nothing ingested the trace and passed its path (e.g. a `--no-collect` / `no_collect`
flag, or a missing argument), the analysis took the weaker branch and dispatched only the subset of
analysts that need no profile — and the operator read the thin result as "all that could be done."
The profile-consuming specialists (bottleneck/gap/offload/efficiency, and the qor/stalls set) that
own the ACTUAL bottleneck class were never engaged, so the bottleneck was mis-localized. The tell:
the analysis reports a "degraded"/empty-artifact flag while a raw trace plus its correlating file sit
unused next to the measurement output.

**Rule.**
- The device profile is MANDATORY. There is NO degraded / source-only analysis mode. If a profile
  cannot be resolved (auto-ingested from the measurement capture, or supplied as an ingested
  profiler_db), the analysis step **FAILS loudly (non-zero) and the loop STOPS and tells the user** —
  it never proceeds on a profile-less, partial analysis.
- When the profile IS present, analysis is ALWAYS full: every profiler_db region specialist
  dispatches alongside cost+compute (and the qor/stalls specialists when those artifacts exist).
  Region dispatch is not an opt-in flag.
- Make the analysis auto-discover and ingest the trace the measurement step already produced; pass
  the correlating artifact (NEFF) explicitly (it usually lives one directory above the trace) — the
  raw trace alone will not ingest.
- A "that is all the analysis available" result with an empty artifact set, while a capture sits on
  disk, is an orchestration failure — the missing profile must be produced or the run must fail, not
  be quietly narrowed.

### I7 🟡 An emitted op whose identity is reused across emissions is a hard compile error

**Pattern.** A device op (DMA / transpose / compute) emitted inside a loop is given an identity built
from a subset of the enclosing loop indices; the same identity is emitted more than once (across
fold/tile/batch/buffer-parity iterations, or a prologue emission plus an in-loop emission of the "same"
op), and the build fails with a duplicate-identity error.

**Mechanism.** Each emitted op's identity must be unique per emission; a name that omits a distinguishing
index collides the moment the loop issues the op again.

**Detection.** Any emitted-op identity built from fewer than all enclosing loop indices is suspect; a
prologue emission plus an in-loop emission of the same logical op is the classic collision.

**Rule.**
1. Interpolate ALL distinguishing indices into every emitted op's identity (batch, buffer-parity,
   fold/block, tile, sub-tile) when you write the op — not after the compiler rejects it.
2. If a prologue and an in-loop path both emit the op, ensure their identities cannot coincide.

## Part J — Decision trees

### J1 Prefill transpose / layout optimization

```
Transpose-class instructions are a nontrivial share of the binding engine's time?  (F1, B3)
│
├─ YES → Which kind of transpose?
│   │
│   ├─ ACTIVATION (source hidden states, intermediate results)
│   │   ├─ Does a DMA-transpose path already exist in a sibling kernel?
│   │   │   ├─ YES → enable it; validate correctness AND on-chip memory fit (F5)
│   │   │   └─ NO  → implement stream + DMA-transpose + convert (F4, F5)
│   │   └─ Is the dtype narrower than 2 bytes?
│   │       ├─ YES → the matrix-engine penalty is ~doubled, so the migration is worth more —
│   │       │        but first confirm the DMA engine supports the dtype at all (F3)
│   │       └─ NO  → proceed with the standard migration
│   │
│   └─ WEIGHT (projection matrices)
│       └─ Fix at load time via storage-transposed loading. Should never be a runtime cost
│          (F2). Beware non-contiguous shards for in-place fixups.
│
└─ NO → Look to weight-load instructions, DMA patterns, or another engine (D10);
        confirm the candidate is not already overlapped before spending effort.
```

### J2 Decode optimization

```
Decode throughput below expectation?
│
├─ 0. VALIDITY PRE-FLIGHT (configuration and measurement, not kernel — do these first):
│     a. Actual batch ≈ COMPILED bucket? If actual ≪ bucket you are measuring padding,
│        not the kernel (C2).
│     b. Disabled-by-default gates enabled, or their state recorded? Stock defaults are
│        the UNoptimized path (C3).
│     c. Device time from a live trace, or client-side E2E? No kernel attribution from
│        E2E (C1).
│     d. Free engine-metrics read done, and does it agree with the client number (A2)?
│     e. Is the library the runtime imported the one you patched (C7)?
│
├─ 1. Running batch ≈ configured batch?
│     ├─ NO, and a queue is waiting → capacity ceiling (D6). Fix allocation first;
│     │      expect SUBLINEAR gain (D3).
│     └─ YES → continue.
│
├─ 2. Compute the per-operator floor AT THE PRODUCTION BATCH:
│        weights + KV read + compute + (2 × L × per_collective_cost)          (D4, E1)
│     Which term is largest?
│     ├─ KV read (usual past batch ~tens) → enumerate BYTES first: sharding replication,
│     │      element width, layout reuse, fusion (D5). NOT weight loading (D3).
│     ├─ Communication (common at high TP on deep models) → search precision/algorithm
│     │      gates (G8), then reduce count or TP degree (D4, G1).
│     ├─ Weight read (only at very small batch) → prefetch, tiling, layout.
│     └─ Compute (only at large batch) → matmul efficiency, dtype performance modes.
│
├─ 3. Is TP > n_kv_heads?
│     └─ YES → KV is replicated with no capacity benefit while the communication term
│            grows. Reconsider the TP/DP split (D5).
│
├─ 4. Is the remainder idle / semaphore-wait / transfer-wait rather than arithmetic?
│     └─ YES → separate per-invocation FIXED cost from PER-UNIT cost by varying work per
│            invocation and reading time-per-unit (D7). Fixed ⇒ config, not kernel.
│
└─ 5. Only now compare measured vs floor, quoting the floor definition alongside the
       ratio — and branch on the ratio: a large gap is a localization task (D9),
       never a kernel verdict and never a hardware ceiling.
```

---

## Part K — Staged pre-flight checklists

### K1 Before trusting any kernel measurement

| # | Check | Entry |
|---|---|---|
| 1 | Is this **device time** from a live execution trace, not client-side or wall-clock? | C1 |
| 2 | Does the **actual batch** fill the **compiled bucket** it executed? | C2 |
| 3 | Are the tree's **disabled-by-default optimization gates** enabled (or their state recorded)? | C3 |
| 4 | Are **communication-precision gates** (FP8 collectives, DMA path flags) enabled or their defaults recorded? | G8 |
| 5 | Is the **running batch** ≈ the configured batch, with no waiting queue? | D6 |
| 6 | Which **operator/engine binds at the production batch** (not at batch 1)? | D3, D10 |
| 7 | Does the floor include **communication**, not just memory traffic? | D4 |
| 8 | Has the free **engine-metrics** read been taken off the same baseline launch as the capture? | A1, A2 |
| 9 | Is the profile **windowed to the single execution/phase** under study, boundaries stated and cross-checked? | B1 |
| 10 | Does the profile contain the **target phase's kernels at all** (source-location population)? | A6 |
| 11 | Was the capture method **valid for the topology** (live, not isolated replay) and the **per-execution watchdog raised** before instrumenting? | A3, A4 |
| 12 | Are all hardware constants **sourced and unit-qualified**, normalized to one scope (chip vs logical core)? | E1 |
| 13 | Is engine **occupancy** from an active-time measurement (not summed durations), **per instance**, with both scopes stated? | B3, B4 |
| 14 | For every profiler field carrying a claim, is its **definition and scope** established — and is dtype/geometry read from the **operand descriptor**? | B2 |
| 15 | Is every collective/bandwidth figure **overlap-merged** with its denominator stated? | B7 |
| 16 | Is the **library the runtime imported** the one that was patched (resolved path + sentinel)? | C7 |
| 17 | Is the **profiled absolute** accompanied by its uninstrumented reference and inflation factor? | C4 |
| 18 | Do all compared numbers come from the **same execution**? | C5 |
| 19 | Is every **efficiency figure labelled with its denominator** (graph elapsed vs kernel active)? | D8 |
| 20 | Has the headline number been **reconciled against shapes × iterations** (padded vs real)? | C6 |

### K2 Before writing any kernel

- [ ] Read the transfer contract. No magnitude from this document may order or skip kernel work — re-measure the lever in your own regime first (E8).
- [ ] Is this the measured binding op, and is it ABOVE its entitlement floor? (D1)
- [ ] Any "at floor / little left" reading not resting on active-time efficiency alone nor on an ideal-FLOP floor alone: occupancy reported beside efficiency, per-variant distribution inspected, and work-*elimination* options (fusion, avoided materialization, avoided layout pass) named before ruling a kernel out (D1).
- [ ] Binding op confirmed from the LIVE trace at the exact shipping config (parallel degree, batch, context, dtype) — not from an analytic ranking or a neighbouring cell; degree named alongside the claim (D2).
- [ ] Top terms separated by more than model error? A near-tie means measure, not decide (D2, E4).
- [ ] Is the ranking a **ranking** (per-op), not a two-bucket split? (B12)
- [ ] If the per-op path is unavailable: apportioned by parameter sweep, and per-item **marginals compared to their own floors** (B13).
- [ ] If memory-bound: bytes enumerated first — replication from sharding, element width, layout reuse, fusion — before any instruction work (D5).
- [ ] Head-count vs parallel-degree divisibility checked; replication factor known and included in the byte budget (D5).
- [ ] Which resource binds (matrix / vector / scalar / DMA / collective)? Optimize that one, and confirm it is not already overlapped (D10).
- [ ] Bound class fixed by the **roofline ratio**, not by "which engine is busiest" (B5).
- [ ] Config axes swept AND named (cache dtype, blocks, batch/seq caps, optimization level, quantization family) before concluding kernel work is required (E6).
- [ ] That sweep has VALID measured points per axis, not just attempts, and each axis's reachable range was established first (E7).
- [ ] Non-arithmetic time separated into per-invocation fixed cost vs per-unit cost by varying work per invocation (D7).
- [ ] Costs ranked by time share, not FLOP share and not event count (D7, B6).
- [ ] Target constants pulled from the tool — on-chip buffer/accumulator bytes, partition count, per-*logical*-core HBM, core-config mode (E1).
- [ ] Both efficiency figures computed, and the gap between them used to decide kernel vs scheduling/decomposition work (D8).
- [ ] Every lever sized by the **share of the bottleneck it reaches**, with the unattributable share carried as an explicit ceiling (G10).
- [ ] Quantization scale granularity checked before choosing K tiling; any narrow-dtype gain re-derived at the real contraction depth (F7).

### K3 If deciding NOT to write a kernel

- [ ] Recorded in floor terms: binding op, its floor, the measured value, the recoverable headroom — not "the target was met" (E6).
- [ ] Justification contains no instantaneous-peak or extrapolated throughput number (E6, C1).
- [ ] If any cited premise is later withdrawn, this decision re-recorded even though it still stands (E6).
- [ ] No serializing dependency closed as inherent without restating it at TILE granularity and asking whether chunking/pipelining/fusion with the consumer changes it (G3).
- [ ] No dead end recorded against a GOAL where the evidence covers only one MECHANISM (G3).
- [ ] No communication or movement cost called irreducible from a single message size: size swept, step count counted, gates searched, and algorithm-required bytes separated from formulation-produced bytes (G8).
- [ ] No lever closed on a predicted numerical-risk argument: precision levers split by numerical exposure (pure movement vs elementwise vs accumulation) and fidelity measured at production shape before rejection (G7).
- [ ] "Requires kernel change" not recorded as a blocker where kernel authoring is in scope (G2).
- [ ] A trace was actually produced and read — absence of a finding distinguished from absence of a measurement (A1).

### K4 While iterating

- [ ] Profile `num_tiles=1`, one rank pinned, per-execution watchdog raised; query parquet fields (never a whole-trace JSON dump); bound with `timeout` (A4, A5, A7).
- [ ] Compile with a known PID + timeout + fresh cache dir; verify via `ps`; one device job at a time (I3).
- [ ] Re-profile after every change; confirm the binding bottleneck actually moved (A7, D1).
- [ ] Read HLO/IR/logs by grep/sample, never whole (B10).
- [ ] Report each shape/tile result with its artifact path as you go, not in one final document (C8).
- [ ] Recompute any *derived* delegated metric against the raw tables before a decision rests on it (B10).
- [ ] Shared tiling constants parameterized and bracketed **per site**, with tiles/bytes-per-descriptor/footprint computed first; a tile count of 1 reported as a structural defect (G11).
- [ ] Buffer-depth changes bracketed both ways and accepted on step time at production shape, with the run-to-run spread quoted (G12).
- [ ] When a capture is re-tried smaller, ONLY the window changed (A5).

### K5 Before trusting a change

- [ ] Mechanism stated first, then the observable chosen to match it; one observable that should change and one that should not, both reported (H1).
- [ ] For a reuse/amortization experiment, the invariant is on the operand the instruction actually loads (H2).
- [ ] Every traffic-attributed wait validated in two parts — the traffic counter moved AND the wait moved (H3).
- [ ] Any scheduling/optimization-level flag passed all four gates — compile, load, execute, numerically correct — with startup/barrier timeouts raised beforehand (H4).
- [ ] Cost attributed to a workaround or construct ONLY by ablation, with the delta read on the total and relocation expected rather than elimination assumed (H5).
- [ ] Validated ON DEVICE at production shape (functional sim ≠ layout/transpose/sub-byte validation) (H6).
- [ ] Executed at production shape — not merely compiled + warmed + reported ready; first real execution treated as the validation event (H7).
- [ ] Any generic execution-schedule failure localized to a phase + graph variant from the dumped execution state, and bisected on one dimension before doubting the feature under test (H7).
- [ ] Any device-init failure following a crash re-tested after driver reclaim, with a minimal single-device op, before being called a hardware fault (I2).
- [ ] Gated, default off, baseline byte-identical; intended fast path asserted/logged (H6).
- [ ] Token-identical on real content; enough tokens to exercise KV-cache writes; divergence reproduced twice (H6).
- [ ] Any precision change measured on BOTH SLA legs, judged on whole-graph time, with operators it *introduced* (transpose/pack/convert/scale) charged to it; layout operators' element-width constraints checked first (G6).
- [ ] Any quantized path validated end-to-end at depth with a token-level agreement rate, and `L` stated beside any per-layer similarity (H8).
- [ ] No component derived by subtraction; residuals corroborated by their own overlapping events (B8).
- [ ] Analytic comparison calibrated for THIS regime; no factor ported across regimes (E4).
- [ ] Artifact mtimes checked; nothing claimed "this session" that predates it (C7).
- [ ] Any structural quantity the change should move (footprint, op population, instruction mix) confirmed to have moved before trusting a timing delta (C7).

### K6 Traps

- [ ] Re-tested any stale "unsupported / SIGSEGV / must be worked around" claim on the current compiler; re-tested the native path where an existing workaround may now be obsolete (G9).
- [ ] Any "won't build" blocker checked against toolchain identity (right venv/compiler/runtime, device idle) with an exact command + log excerpt; substituted second blockers verified too (I1).
- [ ] Instruction mix known (matmul-to-total ratio) so the right engine is being optimized (F8).
- [ ] Elementwise chain fusion measured per regime and gated — fusion can regress by creating a new synchronization boundary (F8).
- [ ] DMA-transpose buffers allocated with explicit alignment before questioning the algorithm (F4).
- [ ] Transpose-path migration re-budgeted against on-chip capacity, with the old path's reservations reclaimed (F5).
- [ ] Suspected primitive isolated on device at production shapes before any rewrite (F6).
- [ ] Trace correlated only against the artifacts from the compile that produced it (A9).
- [ ] Reduced-scale profiles not used for absolute numbers against an SLA; extrapolation stated as a validated linear per-layer model (A8).
- [ ] Every required capability (launch, capture, ingest, analysis, the verdict measurement) mapped to a named owner; any excluded path with no named substitute resolved as a procedural gap rather than hand-built (I4).
- [ ] No destructive cleanup run without first confirming no live compile/capture job, and its parent scope reconciled afterwards against what the command could reach (I5).
- [ ] Review runnable on partial state and triggered independently of the work's outcome; "not reviewed" kept distinct from "nothing found" (C8).

---

## Part L — Summary table

| Entry | Mistake pattern | Prevention |
|---|---|---|
| A1 | The kernel stage silently never happens because the capture was scheduled behind the sweep | Capture FIRST on an already-warm server, ahead of anything else costing device time; state the dependency in the plan; distinguish absence of a finding from absence of a measurement |
| A2 | Spend device time on per-phase capability the serving log already prints | Grep phase/running/waiting/cache-usage lines off the same launch; build and validate a serving model from them; never subtract across captures |
| A3 | Use isolated replay for a multi-shard graph, or forget the profiler's own memory pool | Plan the live capture path; budget profiler resources into the first compile; a tiny trace dir is a failed capture |
| A4 | Instrument against a default per-execution watchdog, then blame the graph | Raise the watchdog before instrumenting; pin one rank by default and read only shares; escalate to all ranks only for cross-rank questions; diff against a capture that worked |
| A5 | Size the capture window without regard to the ring buffer or ingest cost | Iteration delay scaled with batch; small window; parquet + targeted tables; on retry change ONLY the window |
| A6 | Rank levers from a profile that holds the wrong kernels or phase | Read the source-location population first; zero instances of the target kernel = wrong capture |
| A7 | Iterate on multi-tile multi-rank traces | `num_tiles=1` and one pinned rank first; re-profile after every change and confirm the bottleneck moved |
| A8 | Treat a reduced-layer profile as numerically equivalent | Structure and shakedown only; explicit linear per-layer model validated at two layer counts |
| A9 | Correlate a trace against artifacts from a different compile | The profile represents the config it was captured on; use the auto-copied artifacts; a system trace needs no matching |
| A10 | Accept "the profiler doesn't work on this graph" | Treat it as one of the highest-severity claims; diff configs, check rank selection, raise the watchdog, shrink the window |
| A11 | Arm the capture window around a workload never proven to produce valid steps | Dry-run one request of the exact shape outside the window; assert input+output ≤ max seq length on a real tokenization; step-accept the trace (target-phase kernel instructions present); an empty trace with zero successful requests is a workload bug, not a tooling bug |
| B1 | Attribute per-class shares over a whole session containing several executions | Read the execution table first; window by timestamp; cross-check duration and row count; state boundaries everywhere |
| B2 | Read a column whose name matches the feature as evidence about the feature | Read dtype/geometry from the operand descriptor; establish any field's definition and scope; uniform emptiness is a scope question |
| B3 | Sum per-instruction durations and call it occupancy | Durations overlap and exceed wall time; take occupancy from active time; quote busy-vs-idle to separate saturated from stalled |
| B4 | Sum active time across parallel engine instances over a single-instance window | Report per instance with the count; carry both scopes; cross-check the interval union (≈1.0) and the window bound |
| B5 | Call the workload bandwidth-bound because DMA is busiest | Compute the roofline ratio; ≫1 with nothing saturated is serialization, and the fix is the opposite |
| B6 | Weigh DMA/readbacks by event count | Sum bytes; localize by bytes-per-DMA-ms per source; check the re-stream ratio; name which statistic cleared a suspect |
| B7 | Divide summed collective duration by execution count; quote bandwidth without a denominator | Overlap-merged union per execution window; state every formula and its time window and peak scope |
| B8 | Derive a component as `total − model` | Query the component's own rows, or drop the term; attribution requires overlap; retract rather than hedge |
| B9 | Name a dominant cost from an op count | A count establishes existence, not exposure; cross-check with a standalone collective benchmark; counts are for structure only |
| B10 | Trust derived aggregates from a delegated analysis | Distrust in proportion to derivation; recompute against raw tables and show the query; sample huge artifacts, never full-read |
| B11 | Optimize the kernel you believe owns the cost | Group by source location; suspect framework glue; note the unattributable residual |
| B12 | Present a two-bucket split as a ranked table | A ranking is per-op; separate per-item from per-step-invariant terms; name the op before proposing work |
| B13 | Stop at whole-step totals when the per-op path breaks | Fit step time vs the swept parameter (slope = per-item, intercept = invariant); compare marginals to their own floors |
| C1 | Derive kernel efficiency from client-side latency | Device time from a live trace only; otherwise report end-to-end and decline the attribution |
| C2 | Benchmark below the compiled bucket and call it kernel cost | State actual batch vs compiled bucket beside every per-step number; padding mimics a memory-bound defect exactly |
| C3 | Benchmark stock defaults as "the kernel's performance" | Enumerate gates and log accepted defaults; measure stacked gates together, never summed |
| C4 | Quote profiled absolutes against a budget | Record the uninstrumented reference and inflation factor; shares travel, absolutes do not |
| C5 | Compare numbers from different captures | Same execution only; confirm warmup excluded |
| C6 | Quote a headline rate without checking its identity | Reconcile against shapes × iterations; separate padded from real element counts; label with real token counts |
| C7 | Attribute performance before confirming which library tree ran | Resolve the import path at run time; verify by patch sentinel; confirm a structural quantity moved; check mtimes |
| C8 | Report a long campaign in one end-of-run document | Per-shape results with artifact paths as you go; independent reviewer; verify automation actually fired |
| D1 | Write a kernel for a secondary op or one already at floor | Rank by measured device time and compute the floor; but neither active-time efficiency nor an ideal-FLOP floor closes the question |
| D2 | Take the analytic entitlement's binding-op ranking as the answer | The live trace outranks the model on ranking; re-confirm at the exact shipping config; name the degree; a near-tie is a measurement request |
| D3 | Model decode as weight-bound and optimize weight loading | Compute the KV-vs-weight crossover; name the binding operator at the production batch; expect sublinear batch scaling |
| D4 | Build a step floor from memory traffic alone | Include `2 × L` collectives at the selected algorithm's measured cost; reduce count or degree, not per-op time |
| D5 | Attack instructions when the binding op is memory-bound | Enumerate bytes first: replication from sharding, element width, layout reuse, fusion; check head-count divisibility |
| D6 | Attribute a plateau to the kernel while batch sits below configured | Capacity finding; fix allocation, re-measure, expect sublinear gain |
| D7 | Optimize before separating per-invocation fixed cost from per-unit cost | Vary work per invocation and read time-per-unit; idle/wait time is not addressable by faster arithmetic; rank by time share |
| D8 | Interchange whole-graph efficiency with a kernel's own efficiency | Label every figure with its denominator; the gap between them is the finding and decides kernel vs scheduling work |
| D9 | Read a large measured-vs-floor gap as a kernel defect or a hardware ceiling | Walk the ordered localization list 1–7; arithmetic efficiency is usually the smallest term |
| D10 | Optimize a non-binding engine, or work already overlapped | Establish the binding engine first; relocate non-arithmetic work to idle engines; verify overlap before assuming a reduction helps |
| E1 | Build a floor from recalled peak/bandwidth/capacity constants | value+unit+scope+dtype+dense/sparse+source; query the target-info tool; normalize to one scope; logical vs physical cores; re-derive dependents |
| E2 | Choose a configuration on a total when one guessed term decides it | Difference the candidate floors; rank by abs-delta × uncertainty; micro-benchmark the discriminator at production payload |
| E3 | Put a capability-dependent saving in a projection before locating the capability | Annotate every term with its implementing path or gate; confirm at production operator/dtype/shape; un-annotated terms leave the verdict |
| E4 | Port a roofline correction factor across regimes | Calibrate per regime; treat uncalibrated figures as upper bounds; a margin below the model's error is not a margin |
| E5 | Leave a dominant compute-phase term as a scalar "raise efficiency" target | Decompose to per-operator shares and binding engine at production shape; express as per-engine actions with exposed-time deltas |
| E6 | Record a no-kernel decision as "the target was met" | Record in floor terms: binding op, floor, measured, recoverable headroom; distinguish warranted from needed; re-record on premise withdrawal; name the swept axes |
| E7 | Treat an attempted sweep as a completed one | Valid measured points per axis, not attempts; record per-axis validity; establish each axis's reachable range first |
| E8 | Let a precedent magnitude order kernel work | A precedent licenses an experiment, never a decision; re-measure every lever in your own regime; record mechanisms, not scores |
| F1 | Read matrix-engine time as "compute-bound" without separating transpose-class instructions | Break down by instruction class; relocate activation transposes to a DMA engine; check sibling kernels first |
| F2 | Investigate a runtime weight transpose that should not exist | Verify IR weight shapes against the kernel signature; fix layout in the loader; beware non-contiguous shards |
| F3 | Assume a narrower dtype transposes more cheaply | Narrow dtypes ~double transpose instruction count; verify DMA dtype support before migrating |
| F4 | Read a DMA-transpose alignment rejection as a logic bug | Allocate DMA-transpose buffers with the required alignment explicitly |
| F5 | Treat a transpose-path migration as performance-only | Re-budget on-chip memory: stream tiles, order allocations, reclaim the old path's reservations, report peak with margin |
| F6 | Rewrite the suspicious primitive after a numerical failure | Isolate it on device at production shapes against an independent reference first |
| F7 | Choose K tiling before checking scale granularity; project a narrow-dtype gain at the wrong depth | Scales varying along K force a split; re-derive any narrow-dtype gain at the real contraction depth |
| F8 | Hand-tweak one tiling config; optimize the wrong engine | Autotune the map space and probe the OOM boundary; compute the matmul-to-total ratio; fuse the elementwise chain but gate it |
| G1 | Propose a kernel fix for a topology/config problem | Check the mechanism's availability and name it; present theoretical vs reachable lever; latency-bound collectives are a config symptom |
| G2 | Treat "requires kernel change" as a blocker | It is a transition; enumerate the kernel changes that would achieve the saving; delegate with a concrete spec, not a question |
| G3 | Accept a dead end for one mechanism as a dead end for the goal | Name WHAT is dead, not WHAT GOAL; list alternative mechanisms; restate the dependency at tile granularity |
| G4 | Grep for a gate string instead of reading the mechanism | A gate grep is a deployment check; read the stock pattern and the full kernel signature; unused parameters are candidate opportunities |
| G5 | Accept or dismiss a quoted gain without sizing the eliminated work | Compute eliminated bytes/FLOPs/barriers as a plausibility bound — then measure; expect relocation, not elimination |
| G6 | Rule on a precision/layout lever from one phase and the arithmetic it removes | Measure both SLA legs; judge on whole-graph time; charge introduced operators to the decision; check element-width constraints; sweep per component |
| G7 | Decline a precision lever on predicted numerical risk | Split the lever by numerical exposure; test the prediction at production shape; report the fidelity number either way |
| G8 | Declare communication irreducible | Grep for precision/algorithm gates first; sweep message size and count steps; separate algorithm-required from formulation-produced bytes |
| G9 | Build a workaround on a stale "unsupported" claim | Re-test on the current compiler; also re-test whether an existing workaround is now obsolete |
| G10 | Size a fix against the whole bottleneck when it reaches one site | Partition by site plus an explicit unattributable row; size as the sum of reachable rows; a landed fix that moves nothing is a REACH error |
| G11 | Change a shared tiling constant globally and read one aggregate number | Parameterize per site, bracket each independently, report per-site outcomes with computed inputs; a tile count of 1 is a defect |
| G12 | Cut buffer depth to relieve capacity pressure as a strict win | Depth is the pipeline; bracket both ways; accept only on step time with the spread quoted; prefer footprint cuts that preserve depth |
| H1 | Test overlap by counting instructions; read a changed artifact hash as behaviour | State the mechanism, then the observable; report one that should change and one that should not |
| H2 | Hold the wrong operand invariant in a reuse experiment | Name the operand the load instruction takes and build the invariant on it; required before any "the toolchain won't do X" claim |
| H3 | Judge a traffic-attributed wait on end-to-end time alone | Two-part validation: the traffic moved AND the wait moved; traffic down with a flat wait refutes the attribution |
| H4 | Treat a clean compile at a higher optimization level as validation | Four gates: compile, load, execute, numerically correct; raise timeouts beforehand; record the failing gate and its error |
| H5 | Report location as causation | Attribute only by ablation; read the delta on the total; expect relocation; gate the construct default-off when introducing it |
| H6 | Validate in simulation, at a minimal shape, ungated | On device at production shape; gate default-off with a byte-identical baseline; assert the fast path; token-identical on real content past the first tokens |
| H7 | Treat "compiled + warmed + ready" as executable | The first real execution is the validation event; read the dumped scheduler state; single-variable bisect; derive nothing from a variant that failed |
| H8 | Certify a quantized kernel by per-layer cosine similarity | Validate end-to-end at depth with a token-level agreement rate; state `L`; separate implementation correctness from numerical acceptability |
| I1 | Accept a "won't build / unsupported" blocker | Confirm toolchain identity first; a real blocker is command + log + on-device error; expect blocker substitution |
| I2 | Read a post-crash device-init failure as hardware | It is a distinct event, usually residue; check handles and timestamped kernel logs; probe minimally; allow driver reclaim and retry once |
| I3 | Lose track of long compiles and profiles | Known PID + timeout + logs; verify via `ps`; one device job at a time; fresh compile-cache dir per activity |
| I4 | A forbidden measurement path with no named replacement becomes a hand-rolled one | Resolve the unowned capability before any device time; name an owner per capability; if built, reconcile its metric definitions and shake it down at reduced scale; no trace ⇒ nothing established about kernel headroom |
| I5 | Verify a destructive cleanup only against its targets | Reconcile the parent scope against what the command could reach; confirm no live compile/capture job first; positive-check untargeted dependencies |

---

## Cross-cutting takeaways

**1. Read the primary computation, not a summary.** The high-severity items in Parts A–B all reduce to
reading a *summary* — which engine is busiest, a count-weighted histogram, a name-matched column, a
delegated aggregate — instead of the primary computation that assigns the bottleneck. Before ranking or
fixing a kernel: confirm the profile holds the target kernels via the source-location population (A6);
window to the execution under study (B1); compute the roofline ratio to fix the bound class (B5); localize
DMA by bytes-per-ms per source with byte-weighted distributions (B6); take occupancy from active time,
per instance (B3, B4); and recompute any derived aggregate yourself (B10). A correct kernel fix for a
mis-diagnosed bottleneck is still wasted effort.

**2. Route the finding to the right layer.** A profile finding is not automatically a kernel lever. Exposed
collective time at high shard degree, a batch below the configured maximum, a batch below the compiled
bucket, and a fixed per-invocation setup cost are all real findings whose remedies live in config,
topology, or upstream (D6, D7, G1). Equally, "requires a kernel change" is not a blocker where kernel
authoring is in scope (G2). Both directions of mislabeling are expensive; the first sends the effort down
the wrong path, the second declares the headroom inaccessible.

**3. The trace must exist and be valid for the topology.** Parts A1–A9 all sit upstream of any analysis —
the capture that was never scheduled, the replay method that undercounts multi-shard collectives, the
reduced-size graph that isn't the graph you ship, the window that overflowed the ring buffer or the
ingester, the instrumented capture that tripped a watchdog it was certain to exceed. None announce themselves; each yields either
no artifact or a plausible-looking partial one.

**4. The code you analyzed must be the code that ran, and the win must hold on both legs.** A kernel
library resolved from a second copy on the path executes unpatched while the analysis reads the patch, and
the profile looks entirely normal (C7). A narrowing lever measured only on the phase it was expected to
help scores as a clean win while regressing the other SLA leg (G6). A quantized kernel certified at
cos ≈ 0.9999 per layer passes review and still flips tokens once depth multiplies the error (H8). So before
a kernel result travels: print the resolved import path and a patch sentinel into the run log; confirm a
**structural** quantity moved and not merely a timing; measure the lever on both SLA legs and report the
pair; and state the layer count next to any per-layer accuracy figure.

**5. A magnitude never orders the work.** Floors, rankings and payoffs are all regime-specific. The
binding-op ranking itself inverts with parallelism degree (D2), analytic error changes sign between regimes
(E4), a lever that paid at one degree can be negative at another (E8), and a shared tiling constant sits on
a different part of each site's curve (G11). Re-measure in the regime you will ship, record mechanisms
rather than scores, and size every lever by the share of the bottleneck it can actually reach (G10).

**6. Where kernel headroom actually is — with its precondition.** A recurring transferable finding: for
memory-bound decode with the matrix engine largely idle and collectives already hidden, kernel/matmul
optimization has little end-to-end headroom — the multiplier is more aggregate memory bandwidth or fewer
weight bytes. When the binding cost localizes to a **hardcoded tiling constant inside a maintained kernel
library** with no exposed override, that value is a *floor at the integration layer*: quantify the
achievable ceiling so the verdict is falsifiable, then route a shape-aware-tiling request upstream rather
than source-patching a shared kernel whose edit reverts on every version bump. Conversely, a genuine win is
often a **stale format/compiler workaround** that routed around a bug since fixed (G9).

**Precondition on the above.** It is conditioned on the step *actually being* memory-bound, and
"memory-bound" is a claim requiring a floor, not a label earned by a flat curve. Verify it as a ratio:
compute the per-item marginal and divide by its own bandwidth floor. Observed misapplication — a decode
step was declared memory-bound and its kernel headroom written off, while the per-item marginal sat ~11×
above its own cache-read floor; the precondition was never checked, so the conclusion pointed the effort at
hardware when the headroom was in the kernel. A term at ≤2–3× its floor is where this applies; a term at
~10× its floor is a kernel finding, whatever the curve looks like.

---

## Appendix — provenance map

Every entry in the three source documents, and where it landed. `→ §Xn` means the material (or part of
it) was contradicted by another source and is recorded in `KERNEL_MERGE_CONFLICTS.md` instead of, or in
addition to, being merged here.

**`KERNEL_OPT_LESSONS_gaal.md`** (numbered items)

| Source | Merged into |
|---|---|
| 1 wrong kernels/phase in the profile | A6 |
| 2 "DMA is busiest" ⇒ bandwidth-bound | B5 |
| 3 packet distribution by count | B6 |
| 4 trusting derived metrics | B10 |
| 5 serving layer starves the kernel layer | A1 |
| 6 capture method invalid for the shard degree | A3 |
| 7 reduced-size profile treated as equivalent | A8 |
| 8 capture window / ring buffer / ingest | A5 |
| 9 kernel fix for a topology problem | G1 |
| 10 achieved bandwidth without a denominator | B7 |
| 11 rank-0-only capture in a collective world | A4 |
| 12 dominant cost from an op count | B9 |
| 13 two-bucket split presented as a ranking | B12 |
| 14 stopping when the per-op path breaks | B13 |
| 15 profiled absolutes quoted as free | C4 |
| 16 wrong library tree imported | C7 |
| 17 precision lever evaluated on one phase | G6 |
| 18 quantized kernel certified per-layer | H8 |
| 19 optimization opportunities in stock source | G4, G5 |
| 20 gate string instead of mechanism | G4 |
| 21 gains from visible data-flow properties | G5 |
| 22 profiling GMU vs production NEFF UUIDs | A9 |
| 23 "kernel change required" as "not available" | G2 |
| 24 dead-end mechanism vs dead-end goal | G3 |
| 25 collective duration divided across executions | B7 |
| cross-cutting + companion notes | A10, Cross-cutting takeaways 1–4, 6 |

**`KERNEL_OPTIM_lucy.md`** (Lesson n)

| Source | Merged into |
|---|---|
| 1 activation transposes on the matrix engine | F1 |
| 2 weight layout at load time | F2 |
| 3 sub-2-byte transpose penalty | F3 |
| 4 identify the binding engine | D10 |
| 5 isolate the primitive | F6 |
| 6 DMA transpose alignment | F4 |
| 7 transpose strategy shifts on-chip budget | F5 |
| 8 attribute to kernel source lines | B11 |
| 9 decode is KV-bandwidth-bound early | D3 |
| 10 communication as a first-class floor term | D4 |
| 11 read serving engine metrics first | A2 |
| 12 capacity ceiling as a kernel problem | D6 |
| 13 compiled bucket vs actual batch | C2 |
| 14 disabled-by-default gates | C3 |
| 15 no attribution without a device profile | C1 |
| 16 measured-vs-floor gap is a localization task | D9 |
| 17 communication-cost reduction gates | G8, D4 |
| 18 hardware constants sourced and unit-qualified | E1 |
| 19 measure the discriminating term | E2 |
| 20 projections may not contain unlocated capabilities | E3 |
| 21 "raise efficiency" is not a plan | E5 |
| 22 window the profile to one execution | B1 |
| 23 a column's name is not its semantics | B2 |
| 24 summed durations are not occupancy | B3 |
| 25 operand-reuse invariant | H2 |
| 26 match the observable to the mechanism | H1 |
| 27 buffer depth is non-monotone | G12 |
| 28 traffic-attributed waits need both to move | H3 |
| 29 four gates for a scheduling flag | H4 |
| 30 occupancy across parallel engine instances | B4 |
| 31 size a fix by its reach | G10 |
| 32 sweep shared tiling constants per site | G11 |
| Pre-Flight Checklist | Part K, checklist K1 |
| Decision trees (prefill, decode) | J1, J2 |
| Summary table | Part L |

**`KERNEL_OPTIMIZATION_LESSONS.md`** (Kn)

| Source | Merged into |
|---|---|
| K1 confirm the op is not at its floor | D1 |
| K2 device time, never a proxy | C1, C5 |
| K3 one tile / one rank; query fields | A5, A7 |
| K4 validate on device, production shape, gated | H6 |
| K5 sample huge artifacts | B10 |
| K6 track long compiles | I3 |
| K7 re-derive inherited claims; dtype/shape traps | G9, F7 |
| K8 bound the search; instruction mix | F8 |
| K9 constants from the tool | E1 |
| K10 verify a benchmark's arithmetic identity | C6 |
| K11 count what costs time | B6, B3 |
| K12 never derive by subtraction | B8 |
| K13 roofline calibration is regime-specific | E4 |
| K14 "won't build" and toolchain identity | I1 |
| K15 incremental evidence; audit gating claims | C8, B10 |
| K16 the analytic ranking can invert | D2 |
| K17 memory-bound ⇒ bytes; sharding replication | D5 |
| K18 "no kernel needed" is a claim | E6 |
| K19 a precedent magnitude must not order work | E8 |
| K20 compiling ≠ executability | H7 |
| K21 post-crash device-init failure | I2 |
| K22 a sweep with no valid points | E7 |
| K23 fixed per-invocation vs per-unit cost | D7 |
| K24 precision relocates cost into layout ops | G6 |
| K25 "at N% of peak" / "near floor" are weak | D1 |
| K26 unoverlapped is a decomposition property | G3 |
| K27 location is not causation | H5 |
| K28 a lever declined on predicted risk | G7 |
| K29 single message size cannot show irreducibility | G8 |
| K30 graph efficiency vs kernel efficiency | D8 |
| Transfer contract | "How to read this document" |
| One-screen pre-flight checklist | Part K, checklists K2–K6 |

### Conflict resolutions applied

Ten contradictions between the sources were held in `KERNEL_MERGE_CONFLICTS.md` rather than merged. All
are now resolved and integrated. Two were decided in favour of one source, changing guidance (a third,
**S1**, was added by a later lessons-merge and likewise changed guidance):

| # | Question | Resolution | Entries changed |
|---|---|---|---|
| X2 | May a single rank be device-instrumented in a collective world? | **Yes — pinning one rank is the working default.** The observed 16-rank deadlock is retained as the mechanism, but its cause is the **default per-execution watchdog**, not the rank count: instrumentation inflates step time by integer factors, and on a barrier that fires everywhere at once. A4 is therefore about raising the watchdog and about the misattribution to the graph, not about a prohibition on subsets. Escalation to all ranks is reserved for genuinely cross-rank questions (per-rank skew, straggler identity, per-participant collective exposure). Because the instrumented rank is the straggler for every collective, only **shares** travel from such a capture, never absolutes (C4). | A4 (retitled, prevention rewritten), A7, A10, K1 #11, K4, Part L rows A4/A7, cross-cutting takeaway 3 |
| X3 | Capture the profile first, or read the free serving metrics first? | **Capture first.** The profile is the first device action after a single baseline launch, ahead of anything else that consumes device time. The free per-phase log read does not compete for that budget and so is not an ordering question at all: take both off the same warm server, and use the log to validate the capture rather than to defer it. A2 is therefore about not *paying* device time for a signal already printed — including the "subtract across two captures" version — rather than about precedence. | A1 (prevention), A2 (retitled, rule rewritten), K1 #8, Part L rows A1/A2 |
| S1 | Does relocating an activation transpose to a DMA engine improve the critical path directly? | **Only conditionally.** "Runs in parallel with matrix compute" is an isolated-kernel property (DMA has slack). In the deployed graph the DMA engine may already be on the critical path, so the relocated transpose is exposed, and the required layout can make it a partition-strided DMA that costs more than the on-chip transpose it replaced. The relocation improves the critical path only when the target DMA has slack AND the access is coalescible; otherwise the on-chip transpose may win. It is a relocation, not an elimination (G5), and must be validated end-to-end in the deployed regime. | F1 (mechanism caveat added; rule step 3 conditioned; steps 4–5 added), with F9/F10 |

The remaining eight (X1 collective-overlap dead end; X4 stock source as sufficient evidence; X5 buffer/tile
caps; X6 does "at floor" close the kernel question; X7 profiler headroom vs compiled bucket; X8 ranking by
summed instruction time; X9 where an exposed-collective finding routes; X10 bottleneck class from a
reduced-scale profile) were resolved as **scope distinctions rather than contradictions** — each side holds
over a different range, and both statements were already carried in the merged text. Their inline dispute
markers are removed, and where the two sides meet the boundary is now stated in the entry itself:

- **X1 / X9** — D4 records the measured dead ends (compiler-level collective/matmul fusion; naive
  whole-step in-kernel collective fusion at ~0% overlap, net slower). G3 keeps the search open at tile
  granularity and for distinct mechanisms. The boundary is granularity and mechanism identity: a dead end
  is recorded against what was measured, never against the goal. G1 routes the finding without de-ranking
  it below a gate search (G8).
- **X4** — G4 keeps "read stock source for opportunities, not patch strings" and the recognizable stock
  patterns. G5 downgrades the data-flow arithmetic from certification to a **plausibility bound**, with
  relocation expected rather than elimination, and defers to E3 (confirm the capability at production
  operator/dtype/shape) and H5 (attribute only by ablation).
- **X5** — G4/G5 keep an artificial cap as a recognizable red flag; G12 governs how it is *evaluated* —
  bracketed both ways, accepted on step time at production shape with the run-to-run spread quoted, and
  swept per site (G11).
- **X6** — D1 now carries both the stopping rule and the two ways an "at floor" reading is mis-constructed
  (active-time efficiency excluding launch/tail/wait; an ideal-FLOP floor blind to work another
  formulation would not do). G2 keeps "requires a kernel change" as a transition. Floor proximity bounds
  the present formulation; it does not close the question.
- **X7** — C2 and A9 now cross-reference: freeing profiler headroom must not drop the concurrency cap
  below the compiled bucket, or the capture is inadmissible as a kernel measurement; the system trace is
  the fallback.
- **X8** — B3 states the rule plainly: rank by exposed/active time. "Weight by cost, not frequency" (B6)
  survives as the ban on event counts, not as a licence to rank by summed instruction duration.
- **X10** — A8 keeps reduced-scale profiles for structure and shakedown only, with C2 supplying the
  mechanism by which class inference from an underfilled shape fails.
