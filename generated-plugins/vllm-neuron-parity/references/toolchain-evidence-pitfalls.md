# Toolchain evidence pitfalls — the Neuron compiler and runtime

How to read what the Neuron compiler and runtime say about themselves. Read
this before you credit a compiler flag, cost a compile, attribute a compile or
serve failure, or localize a device wedge; for verdict check tools, read
`references/measurement-pitfalls.md`. Constructs are named, never line-numbered:
match a flag, knob, assertion text, or log line by name in the installed
toolchain on the leased host. "L-<id> (campaign history)" names a learning
measured on a prior campaign on this hardware class and re-checked at Neuron
SDK 2.32 and vllm-neuron 0.24; the rule stands on the mechanism it names.
Nothing here comes from vendor documentation: where the docs and the toolchain
disagree, the toolchain governs, and one whole error-code family has no public
documentation at all. Apply a remedy only when the current path has the same
failure mechanism; a past fix alone does not establish that.

## Re-derive every second-hand claim at the pin

**Trap:** Release notes, version numbers, and prior scans are wrong at any
given pin in both directions: a note can describe a reverted commit, a win can
already be present, release lines share no merge base, and a novelty scan keyed
on a channel's name re-measures a decided quantity on scarce hardware.

**Rule:** Trace every second-hand claim to the pinned artifact before you spend
on it: re-run the original discriminator against the installed distribution,
check for a merge base before you plan a version lift, key a novelty scan on
the quantity a candidate channel would read rather than on the channel's name,
and mine the logs you already hold before you request a device. Score a probe
that crashed before the stage under test as void, never as negative. Expect
line numbers to drift and match the construct.

**Evidence:** L-080, L-124, L-169, L-213, L-379 (campaign history).

## A flag's behaviour lives in the installed binary, not in its name or its docs

**Trap:** The compiler driver is a thin wrapper over C++ binaries that parse
their own command lines; defaults, aliases, hidden options, and coupled side
effects are printed nowhere the driver shows you, and a flag that reads like a
switch can be an integer, already on, or an echo of a default.

**Rule:** Before a flag experiment, establish its accepted syntax, default,
and coupled effects at the installed pin. Reuse evidence for that binary and
path. If syntax is unresolved, inspect `--help-hidden` (including stderr);
the machine-readable list omits some options. If you infer the type from bare
argument parse errors, include known-value, known-boolean, and bogus controls.
Use pinned source or a controlled probe to decide effects that help text does
not establish. State the affected graph classes and cache keys using "Credit
a flag" below and the cache rule in `references/measurement-pitfalls.md`.

**Evidence:** L-002, L-003, L-005, L-008, L-025, L-044, L-056, L-066, L-079,
L-085, L-086, L-087, L-113; documentation gap L-101 — two sweeps with firing
controls found zero hits for an internal error code, so never plan a step
whose evidence is "the docs say" (campaign history).

## Credit a flag only after you prove delivery and that the pass ran

**Trap:** A compile runs more than one binary and the first invocation line is
not the backend; `ps` destroys the quoting that groups backend options; the
compiler echoes its defaults in the same shape as a flag you passed; and one
global injection site can apply your flag to every graph class.

**Rule:** Select the backend invocation line by binary name, recover its
argument list from the process's own command line or the preserved argument
file, and confirm from the same log that the pass you targeted actually ran
before you read its marker. Then confirm the injection site scopes the flag to
the graph class you claim. A marker count of zero can mean the pass never
executed.

**Evidence:** L-043, L-057, L-114, L-203, L-311 (campaign history).

## Cost a compile from the structure the changed pass processes

**Trap:** Expensive compiles depended on custom-call composition, shared
device-memory tensors, repeated kernel call sites, collective partitions, and
expansion passes; graph bytes and instruction counts did not capture that, the
front end was about 5% of a good compile, and two "parallelism" knobs
controlled different work (threads versus per-worker memory).

**Rule:** Rank compile buckets by the structures the expensive stage consumes.
Pilot a flag or fix on the cheapest representative class against a recorded
control wall. Instruction removal is a useful candidate when it reduces that
stage's work; a smaller count alone does not predict a faster compile. Before
changing parallelism for memory, establish which workers, allocations, and
stages the knob controls at the pin, then measure peak memory and wall time.

**Evidence:** L-031, L-034, L-035, L-036, L-070, L-075, L-078, L-094, L-099,
L-100, L-103, knob behaviour L-005 (campaign history).

## Price device-memory overflow from the graph's own deduplicated resident I/O

**Trap:** The backend's device-memory assertion names the operator at which a
running total passed the limit, not the allocation that made it large; the
driver re-reports the same failure minutes later under its own code; declared
I/O bytes over-count residency when several views alias one buffer.

**Rule:** Price an overflow from the graph's own deduplicated resident I/O per
core: harvest the tensor-declaration lines, deduplicate by backing buffer
keeping the maximum size, rank, and compare that against the per-core memory
constant. Scan for both the backend assertion and the driver error code before
you report a clear, and only after no compiler process is still alive. An
entry-level input-output alias cannot cover a value the graph uses mid-graph;
price that value as an inter-partition intermediate.

**Evidence:** L-011, L-042, L-059, L-060, L-091 (campaign history).

## A compiler-internal name or id is an anonymous, version-minted lead

**Trap:** The compiler mints integer ids per compile with no roster, names
queues and subgraphs after emitted structures, composes some error line numbers
at run time, and reuses ids and hashes across artifacts, so a join on one
resolves silently wrong. A value that reads wrong can be legitimate: affine
index tables hold negative bases by design, and the runtime's own load-time
check is what decides.

**Rule:** Join compiler artifacts by instruction name plus shape or parameter
number, never by an internal id, a content hash, or a per-core file name.
Treat every internal id, queue name, subgraph label, and error-message line
number as a lead, and decide it with a single-variable A/B compile at your own
pin. Re-derive any internal roster at the version you run, and treat a
matching count between two independent sources as coincidence until you have
verified both.

**Evidence:** L-001, L-009, L-013, L-014, L-022, L-039, L-051, L-081, L-089,
L-090, L-092 (campaign history). Count semantics for the same channels:
`references/measurement-pitfalls.md`, "Record the emitter, the stage, and the
divisor with every count".

## Each compiler artifact owns a disjoint field set and its own survival rule

**Trap:** Evidence is scattered over the argument file, the compile log, the
per-subgraph dumps, the per-job metric file, the compiled artifact, and the
per-rank cache directories, each with fields the others lack and its own
survival: the metric file is written only on an orderly exit, dump flags change
the artifact hash, and under expert parallelism each rank has its own hash.

**Rule:** Read each quantity from the artifact that owns it, and record which
artifact you read it from. Collect the free per-subgraph censuses on every
compile — they cost nothing and they answer the variants-versus-call-sites
question later. Validate any index that maps a compiled member to a name with
a residency fit before you read a name through it. When you need per-job
timing from a compile that was killed, engineer a smaller case that fails by
its own exception instead.

**Evidence:** L-020, L-033, L-041, L-053, L-063, L-084, L-102, L-108, L-112,
L-181 (campaign history).

## Under two logical cores per device the per-core artifacts are legitimately asymmetric

**Trap:** This hardware class runs above one logical core, so the compiler
emits one module per physical core; an unroll pass can emit asymmetric output
from identical input, and a kernel with fewer emitted functions than the core
count is silently erased on the extra cores while the compile still passes.

**Rule:** Divide every per-core count by the physical-cores-per-rank factor,
and record the divisor with the count, before you call a subset structural.
Assert each kernel's emitted-function count is at least the logical-core
configuration value, so a silent erase fails the compile report instead of
passing. Compare the pre-unroll intermediate representation before you
attribute per-core asymmetry to your own code: if no instruction differs in
opcode, name, loop nest, engine, or dependency, the divergence is
compiler-generated.

**Evidence:** L-050, L-054, L-109, L-265 (campaign history).

## A kernel is opaque to every graph-level channel

**Trap:** A NKI kernel enters the graph as an opaque custom call: anonymous in
the intermediate representation, cloned per call site, excluded from
whole-graph counters, able to clear tracing and still abort a late backend
stage. The kernels-disabled flag is not a free A/B: it re-keys the cache and
some paths raise instead of falling back.

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

**Evidence:** L-098, L-193, L-209, L-217, L-225, L-233, L-237, L-386
(campaign history).

## Set deadlines from the waits they cover

**Trap:** Each runtime bound is a deadline on a different wait: the execution
watchdog fires tens of seconds after the first device error and names the
stage where it ran, the compile file lock bounds only a rank waiting on another
rank, the engine-ready timeout wraps the whole start. The bound that fires
names the waiter that gave up, never the component that failed.

**Rule:** Map each startup and execution bound to the exact wait it covers,
including when each clock starts. Set an enclosing deadline to cover the work
and cleanup it must await, using the measured wall and the allowed budget.
Order bounds only where those waits are nested; independent waits need no
fixed ordering. A terminal timeout or unrecoverable-execution-unit status can
be a downstream consequence: order the device-timestamped errors, take the
earliest, and walk the barrier or semaphore chain back to the first unmet
threshold. Compare the deterministic device error chain before you conclude
that two legs failed differently. Never reset a shared device as a first move.

**Evidence:** L-262, L-264, L-274, L-325, L-366, L-367 (campaign history).

## A runtime knob is delivered only when the runtime's own render changes

**Trap:** Runtime knobs are read when the runtime translates the compiled
artifact, so some rewrite emitted instructions, none re-keys the compile
cache, and raising a log level can change the terminal failure mode of the
same leg.

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

**Evidence:** L-249, L-257, L-263, L-266 (campaign history).

## Live wedge state is per-core and reachable only through the driver

**Trap:** The in-process debug stream is bound to the caller's own core, a
core dump's summary sections are drained by the time you read them while the
per-core sections still hold live state, and the notification stream reports
completed instructions at an index unresolvable at this pin.

**Rule:** Read wedge state through the driver-level debugger and through the
per-core sections of every per-rank dump file, and check that your count
touched every per-core section of every rank file before you call the reading
complete. Decode engine pointers with the fixed instruction-size and
engine-numbering constants, which are the only stable decode key. Treat a
single-core probe abort, a notification-stream position, and an unresolved
instruction index as probe artifacts, never as facts about your graph. Keep
any engine-register form out of a read-only wedge probe: it executes on the
target engine.

**Evidence:** L-242, L-245, L-251, L-254, L-271, L-275, L-278, L-308
(campaign history).

## The pipeline aborts at its first unmet precondition — a cleared wall buys the next stage only

**Trap:** Trace, lowering, backend, artifact, load, and each warmup stage are
independent preconditions with no rollback: clearing one abort only proves the
graph reaches the next stage, one broken shape assumption fires at several
sites, and a segfault in a shared interpreter silently skips every later leg.

**Rule:** Budget every repair loop for the next wall, and say so in the plan.
Enumerate and fix all sites that share one broken assumption in a single
change. Run each lowering leg in its own process, and give your outcome
vocabulary a value for "the process died" and "the probe errored" that is
distinct from pass and fail. Gate downstream evidence on the downstream
stage's own non-zero completion counter, never on an upstream success. Do not
hunt a defect that predates a fix with a pre-fix versus post-fix count diff:
an invariant count is invisible to a delta search, so use a single-variable
A/B instead.

**Evidence:** L-024, L-046, L-128, L-309, L-323 (campaign history).
