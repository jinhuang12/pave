# Toolchain evidence pitfalls — the Neuron compiler and runtime

Use compiler and runtime evidence for the claim its channel can support.
For verdict instruments, read `references/measurement-pitfalls.md`.
These observations describe the cited pins. Apply a remedy when the current
path has the same failure mechanism; a past fix alone does not establish that.

Read it before you credit a compiler flag, cost a compile, attribute a compile
or serve failure, or localize a device wedge.

Citation classes used below:
- A construct named without a line number (a flag, an env knob, an assertion
  text, a log line, a file name) is matched by name in the installed
  toolchain: the compiler binaries and the runtime library in the venv on the
  leased host. Line numbers move on a pin bump; match the construct.
- A rule marked `(campaign history)` was measured on a prior port campaign on
  the same hardware class, and re-checked against Neuron SDK 2.32 and
  vllm-neuron 0.24. The learning id is provenance, not a file you must have —
  the rule stands on the mechanism it names.
- Nothing here comes from vendor documentation. Where the docs and the
  toolchain disagree, the toolchain governs, and one whole error-code family
  in this file has no public documentation at all.

## Re-derive every second-hand claim at the pin

**Trap:** Secondary sources about this stack are wrong at any given pin, in both
directions. A release note can describe a commit that was reverted and
reapplied with a changed parameter. A headline win can already be present at
your pin. A version number implies neither that a defect is fixed nor that a
fix is blocked on an upgrade. Upstream release lines are rooted at their own
orphan commits, so no merge base exists between two of them and a version lift
has no diff to read. A novelty census keyed on a channel's name reports a
settled quantity as new and burns a scarce device grant re-measuring it. And a
compile that a memory guard killed still holds most of the answers you are
about to pay for again.

**Rule:** Trace every second-hand claim to the pinned artifact before you spend
on it: re-run the original discriminator against the installed distribution,
check for a merge base before you plan a version lift, key a novelty census on
the quantity a candidate channel would read rather than on the channel's name,
and mine the logs you already hold before you request a device. Score a probe
that crashed before the stage under test as void, never as negative. Expect
line numbers to drift and match the construct.

**Evidence:** L-080, L-124, L-169, L-213, L-379 (campaign history).

## A flag's behaviour lives in the installed binary, not in its name or its docs

**Trap:** The Neuron compiler driver is a thin Python wrapper over C++ binaries
that parse their own command lines. Their defaults, aliases, hidden options,
and coupled side effects are printed nowhere the driver or the Python package
can show you, and the machine-readable flag list omits whole families of
option by name class. A flag that reads like a switch can be an integer, can
already be on, can be an echo of a default rather than something you passed,
and can change a second thing you did not plan for.

**Rule:** Before a flag experiment, establish its accepted syntax, default,
and coupled effects at the installed pin. Reuse evidence for that binary and
path. If syntax is unresolved, inspect `--help-hidden` (including stderr);
the machine-readable list omits some options. If you infer the type from bare
argument parse errors, include known-value, known-boolean, and bogus controls.
Use pinned source or a controlled probe to settle effects that help text does
not establish. State the affected graph classes and cache keys using "Credit
a flag" below and the cache rule in `references/measurement-pitfalls.md`.

**Why:** an option's name does not establish its type or effects; a repeated
syntax probe adds no evidence once the installed interface is established.

**Evidence:** L-002, L-003, L-005, L-008, L-025, L-044, L-056, L-066, L-079,
L-085, L-086, L-087, L-113 (campaign history). Public-documentation gap:
L-101 — two documentation sweeps with firing instruments found zero hits for
an internal compiler error code, and the code string was absent from the
package that emits it, so never plan a step whose evidence is "the docs say".

## Credit a flag only after you prove delivery and that the pass ran

**Trap:** A compile runs more than one binary, and the first invocation line in
the log is not the backend. The driver-level argument file carries no backend
flag. `ps` joins the argument list with spaces and destroys the quoting that
groups several backend options into one element. The compiler prints its own
effective defaults in the same line shape as a flag you passed. And a single
global injection site can apply your flag to every graph class, which
confounds the comparison you thought was scoped to one.

**Rule:** Select the backend invocation line by binary name, recover its
argument list from the process's own command line or the preserved argument
file, and confirm from the same log that the pass you targeted actually ran
before you read its marker. Then confirm the injection site scopes the flag to
the graph class you claim. A marker count of zero can mean the pass never
executed.

**Why:** a flag credited from the driver's echo is a claim about the
compiler's defaults, and a pass marker read without a run check is a claim
about a pass that may never have started.

**Evidence:** L-043, L-057, L-114, L-203, L-311 (campaign history).

## Cost a compile from the structure the changed pass processes

**Trap:** At the cited pin, expensive compiles depended on custom-call
composition, shared-device-memory tensors, repeated kernel call sites,
collective partitions, retained access-pattern caches, and expansion passes.
Raw graph bytes or instruction counts did not capture those costs. The
observed parallelism knobs controlled different work: one limited threads;
another replicated front-end memory per worker.

**Rule:** Rank compile buckets by the structures the expensive stage consumes.
Pilot a flag or fix on the cheapest representative class against a recorded
control wall. Instruction removal is a useful candidate when it reduces that
stage's work; a smaller count alone does not predict a faster compile. Before
changing parallelism for memory, establish which workers, allocations, and
stages the knob controls at the pin, then measure peak memory and wall time.

**Why:** cost follows the work the compiler performs, so size and knob names
alone can select the wrong pilot or reject a useful one.

**Evidence:** L-031, L-034, L-035, L-036, L-070, L-075, L-078, L-094, L-099,
L-100, L-103, and knob behaviour L-005 (campaign history).

## Price device-memory overflow from the graph's own deduplicated resident I/O

**Trap:** The backend's device-memory assertion is a cumulative-counter
breach: it names the operator at which a running total passed the limit, not
the allocation that made the total large. The driver re-reports the same
failure minutes later under its own error code, so a scan for one of the two
reads clear while the other fires. Declared graph input and output bytes are
not resident bytes — when legs of different geometry alias one physical
buffer, each view becomes its own declared parameter and the declared total is
a multiple of residency. Operand-slot suffixes on a buffer name are slots of
one tensor, not independent workspaces you can halve.

**Rule:** Price an overflow from the graph's own deduplicated resident I/O per
core: harvest the tensor-declaration lines, deduplicate by backing buffer
keeping the maximum size, rank, and compare that against the per-core memory
constant. Scan for both the backend assertion and the driver error code before
you report a clear, and only after no compiler process is still alive. An
entry-level input-output alias cannot cover a value the graph uses mid-graph;
price that value as an inter-partition intermediate.

**Why:** the assertion is a position in a sum, so a fix aimed at the operator
it names removes memory the graph was not spending.

**Evidence:** L-011, L-042, L-059, L-060, L-091 (campaign history).

## A compiler-internal name or id is an anonymous, version-minted lead

**Trap:** The compiler mints internal integer ids per compile and ships no
named roster for them. It names queues and subgraphs after the structures it
emits rather than after mechanisms. Some internal-error citations compose their
file and line at run time, so the line number is absent from the binary's own
string table and a numerically adjacent line can belong to an unrelated
assertion. A string table for a symbolize function is a spelling vocabulary,
not the set of values the compiler can encode: membership clears nothing and
absence identifies nothing. Ids and hashes are reused across artifacts, so a
join on one resolves silently wrong. And a value that reads wrong can be
legitimate: a compiler-emitted affine index table holds negative bases by
design, so a negative component is not by itself a defect — the runtime keeps
its own ungated negative-offset test that logs at error level and fails the
load, which is the check that decides.

**Rule:** Join compiler artifacts by instruction name plus shape or parameter
number, never by an internal id, a content hash, or a per-core file name.
Treat every internal id, queue name, subgraph label, and error-message line
number as a lead, and settle it with a single-variable A/B compile at your own
pin. Re-derive any internal roster at the version you run, and treat a
matching count between two independent sources as coincidence until you have
verified both.

**Why:** these names exist for the compiler's own bookkeeping, so they carry no
promise of stability, uniqueness, or meaning across versions.

**Evidence:** L-001, L-009, L-013, L-014, L-022, L-039, L-051, L-081, L-089,
L-090, L-092 (campaign history). Count semantics for the same channels are one rule in
`references/measurement-pitfalls.md` ("Record the emitter, the stage, and the
divisor with every count").

## Each compiler artifact owns a disjoint field set and its own survival rule

**Trap:** The compiler scatters its evidence: the driver argument file, the
compile log, the per-subgraph intermediate-representation dumps and kernel
censuses in the scratch tree, the per-job metric file, the per-engine members
inside the compiled artifact, and the per-rank cache directories. Each holds
fields the others do not, and each survives differently. The per-job metric
file is written only on an orderly Python exit, so a compile that a memory
guard kills leaves none of it. A recompile with dump flags produces a
different artifact hash. Under expert parallelism each rank gets its own
artifact hash, so a kernel-to-graph membership join is valid only for the
directory it was taken in.

**Rule:** Read each quantity from the artifact that owns it, and record which
artifact you read it from. Collect the free per-subgraph censuses on every
compile — they cost nothing and they answer the variants-versus-call-sites
question later. Validate any index that maps a compiled member to a name with
a residency fit before you read a name through it. When you need per-job
timing from a compile that was killed, engineer a smaller case that fails by
its own exception instead.

**Why:** an artifact set with per-file survival rules turns a missing field
into a false negative, and the field you want may be in the one file the
failure destroyed.

**Evidence:** L-020, L-033, L-041, L-053, L-063, L-084, L-102, L-108, L-112,
L-181 (campaign history).

## Under two logical cores per device the per-core artifacts are legitimately asymmetric

**Trap:** This hardware class runs with a logical-core configuration above one,
so the compiler emits one module per physical core and the runtime keys some
messages to the logical core and others to every physical core. An unroll pass
can emit wildly asymmetric output on the two cores from byte-identical input.
A kernel with fewer emitted functions than the core count is erased on the
extra cores while its consumers survive — and the compile still passes.

**Rule:** Divide every per-core count by the physical-cores-per-rank factor,
and record the divisor with the count, before you call a subset structural.
Assert each kernel's emitted-function count is at least the logical-core
configuration value, so a silent erase fails the compile report instead of
passing. Compare the pre-unroll intermediate representation before you
attribute per-core asymmetry to your own code: if no instruction differs in
opcode, name, loop nest, engine, or dependency, the divergence is
compiler-generated.

**Why:** the per-core split is the toolchain's own partitioning, so a per-core
count read as a whole-graph count is off by the core factor and an asymmetry
read as a defect is the partitioner working.

**Evidence:** L-050, L-054, L-109, L-265 (campaign history).

## A kernel is opaque to every graph-level channel

**Trap:** A NKI kernel enters the graph as an opaque custom call. Its
intermediate representation stays anonymous, its body is cloned per call site,
its indirect descriptors are excluded from the whole-graph counters that a
tensorizer produces, its higher-order-operator registration can bypass a
graph-level validator, and its operations can clear tracing and still abort a
late backend stage. A vendor kernel can force a non-configurable
out-of-bounds mode. The kernels-disabled flag is not a free A/B: it re-keys
the cache because the graph traces differently, and at least two paths raise
an error naming the flag instead of falling back.

**Rule:** When a candidate operation's lowering is unproven for the target
compiler, dtype, shape, and kernel context, use a representative micro-kernel
that reaches the backend stage in question. Reuse matching compile evidence;
tracing alone does not establish backend support. Count kernel-internal
quantities in the per-kernel representation, per the count rule in
`references/measurement-pitfalls.md`. Before you read a
kernels-off run as a clean fallback comparison, confirm that the specific
operation's gate consults the flag, and record that the toggle re-keyed the
cache. Treat a raise that names the flag as designed behaviour, not a new
defect. When a vendor kernel forces an out-of-bounds mode and its environment
is read-only, sanitize at your own producer boundary: clamp each sentinel to a
safe in-bounds index AND mask its contribution to exactly zero, because a bare
clamp turns a crash into silent numerical corruption.

**Why:** the custom call is a boundary the graph-level tools cannot see
through, so every graph-level number about a kernel is a number about the call
site.

**Evidence:** L-098, L-193, L-209, L-217, L-225, L-233, L-237, L-386
(campaign history).

## Set deadlines from the waits they cover

**Trap:** The runtime's execution watchdog is a shared deadline: it fires tens
of seconds after the first device-timestamped error and names the stage where
the watchdog ran, not the stage that failed. The compilation file lock bounds
only a rank waiting on another rank's compile. The collective barrier is the
only in-repo bound that spans the first rank's compile. The engine-ready
timeout wraps the whole start. And the host status code can depend on where
the framework read back rather than on the device fault, so one wedge surfaces
as two different statuses.

**Rule:** Map each startup and execution bound to the exact wait it covers,
including when each clock starts. Set an enclosing deadline to cover the work
and cleanup it must await, using the measured wall and the allowed budget.
Order bounds only where those waits are nested; independent waits need no
fixed ordering. A terminal timeout or unrecoverable-execution-unit status can
be a downstream consequence: order the device-timestamped errors, take the
earliest, and walk the barrier or semaphore chain back to the first unmet
threshold. Compare the deterministic device error chain before you conclude
that two legs failed differently. Never reset a shared device as a first move.

**Why:** each bound is a deadline on a different wait, so the bound that fires
tells you which waiter gave up, never which component failed.

**Evidence:** L-262, L-264, L-274, L-325, L-366, L-367 (campaign history).

## A runtime knob is delivered only when the runtime's own render changes

**Trap:** Runtime knobs are consumed by the runtime library when it translates
the compiled artifact, so some of them rewrite the emitted instruction rather
than only reporting. None of them re-keys the compile cache, so an A/B needs
one serve on the same artifacts and nothing in the cache proves the knob
arrived. One is an undocumented dump-on-any-error wildcard whose guard sites
cover only two call paths. And raising a log level can change the observed
terminal failure mode of the same leg.

**Rule:** Prove each knob's delivery from the runtime's own echo or rendered
budget, at the counts you expect — a per-process echo counts ranks, a rendered
per-core budget counts physical cores. An unchanged default render is a
delivery failure, not a result; a missing name literal in a static read is
unresolved, not negative. Read a knob's real scope from the guard sites in the
pinned runtime, not from its name. Treat any knob that rewrites emitted
instructions, or that changes the terminal failure mode, as a variable of the
experiment and not an observation aid — and keep those unexported on shared
hardware, because one of them makes the runtime report success on data it
discarded.

**Why:** the knob is read by a translation step, so the only witness that it
was delivered is that step's own output.

**Evidence:** L-249, L-257, L-263, L-266 (campaign history).

## Live wedge state is per-core and reachable only through the driver

**Trap:** The runtime's in-process debug stream is bound to the calling
process's own core and offers no memory read. A core dump's summary sections
are drained by the time you read them while the per-core sections still hold
live state. The notification stream is a drain of already-completed
instructions, and the instruction index it reports is unresolvable at this
pin. A single-core probe can abort inside the runtime's replica-group table as
a probe artifact and say nothing about the graph. The bound-check mechanisms
differ per chip generation.

**Rule:** Read wedge state through the driver-level debugger and through the
per-core sections of every per-rank dump file, and check that your census
touched every per-core section of every rank file before you call the reading
complete. Decode engine pointers with the fixed instruction-size and
engine-numbering constants, which are the only stable decode key. Treat a
single-core probe abort, a notification-stream position, and an unresolved
instruction index as probe artifacts, never as facts about your graph. Keep
any engine-register form out of a read-only wedge probe: it executes on the
target engine.

**Why:** the live state is per-core and the convenient channels are
process-scoped or already drained, so the reachable evidence and the
interesting evidence are in different places.

**Evidence:** L-242, L-245, L-251, L-254, L-271, L-275, L-278, L-308
(campaign history).

## The pipeline aborts at its first unmet precondition — a cleared wall buys the next stage only

**Trap:** Trace, lowering, backend, artifact, load, and each warmup stage are a
chain of independent preconditions with no rollback. Clearing one abort proves
the graph now reaches the next stage. A replacement operation can hit the same
hardware wall at a different tile shape. One broken shape assumption fires at
several branch-dependent sites. A later warmup stage never runs if an earlier
one dies, so its readings are void even though its graphs traced. And a
segfault in a shared interpreter silently skips every later leg in the same
process.

**Rule:** Budget every repair loop for the next wall, and say so in the plan.
Enumerate and fix all sites that share one broken assumption in a single
change. Run each lowering leg in its own process, and give your outcome
vocabulary a value for "the process died" and "the probe errored" that is
distinct from pass and fail. Gate downstream evidence on the downstream
stage's own non-zero completion counter, never on an upstream success. Do not
hunt a defect that predates a fix with a pre-fix versus post-fix count diff:
an invariant count is invisible to a delta search, so use a single-variable
A/B instead.

**Why:** each stage checks its own preconditions only, so "it compiled" is a
statement about the stage that ran, and the cost of the next wall is a
schedule item, not a surprise.

**Evidence:** L-024, L-046, L-128, L-309, L-323 (campaign history).
