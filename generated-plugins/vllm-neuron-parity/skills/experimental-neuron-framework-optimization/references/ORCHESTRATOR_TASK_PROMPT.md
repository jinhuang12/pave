# Model optimization task prompt

Fill the placeholders from the request or managed runtime. Do not ask for values already
available from the environment, model configuration, hardware topology, or planner.

| Input | Value |
|---|---|
| Project root | `{{PROJECT_ROOT}}` |
| Model | `{{MODEL_NAME}}` |
| Input/output tokens | `{{INPUT_LEN}}` / `{{OUTPUT_LEN}}` |
| Latency target | `{{TTFT_TARGET_MS}}` |
| Aggregate throughput target | `{{OTPS_TARGET}}` |
| SLA relationship | `{{SLA_MODE}}`: joint or independent legs |
| Hardware and logical cores | observed hardware topology |
| Frozen baseline | exact model/checkpoint, source revision and supplied config |
| Allowed changes | optimization/qualification-only mode, allowed axes, source-write permission |
| Correctness | criterion, reference identity/method/precision, corpus, input/output lengths |
| Deadline | absolute workload deadline and closeout reserve |

Run the optimization agent at the top level. Use `neuron-campaign` for initialization,
context, task preparation, specialist submissions, audit, recovery, and finalization.
Read `campaign.md` for the JSON contracts. If the CLI is missing, report the installation
blocker. Do not select another controller or drive a separate kernel workflow.

Change to `{{PROJECT_ROOT}}`, export that same `NEURON_PROJECT_ROOT`, and pass the root to
every specialist. Keep state and artifacts under it. Use the managed reservation and
visible devices for grants, and derive logical core count from topology rather than device
file count. Preserving one project root keeps plan, entitlement, profile, and analysis in
one evidence trail.

The orchestrator delegates workload work. Obtain context, prepare a legal action, and
invoke exactly the granted specialist with its task. The specialist owns implementation,
serving, capture, and measurement; the orchestrator inspects and submits its result without
adding stronger facts. A failed/blocked result retains the actual command and error.
Use the campaign formatter and local submission validation for the mechanical envelope.
Track each native child/session ID, expected output and submission receipt through `progress`
and `context`. Resume existing work; do not end the parent turn while a child runs.

Start with planning and entitlement for a single-node colocated configuration. Verify
TP/DP capacity, operating-point feasibility, and the scope of the analytical floor. If the
cell cannot compile or admit the requested workload, return to the planner with that
constraint instead of inventing an unrelated sweep.

For a joint SLA, both objectives use one serving configuration; require the same observation
window when the request requires simultaneous satisfaction. For independent per-leg
configurations, initialize separate latency and throughput campaigns through the same
controller. Certify each leg's actual scope and configuration independently.

Profile through the profiling specialist. Keep the live device capture, phase, window,
source revision, and observed serving configuration. Run matching equivalence at the
requested context length. Benchmark stdout and serving logs do not certify a device
mechanism. Device time and end-to-end time are different quantities.
Resolve weight/compute/KV precision from separate raw observations and probe the profiler
interface cheaply before capture. Preserve unresolved `auto`; do not infer FP8 or patch
serving code. Reuse bounded exact references for the frozen criterion; capture logits only
when required and rerun target correctness for changed execution identity.

Use profile analysis to localize the binding operation and state a falsifiable hypothesis.
Sweep cheap serving parameters before compiler/kernel work. Record actual varied axes and
profile attempt IDs in `config_sweep`; a narrative claim that a sweep ran is insufficient.
Preserve the best verified configuration after each experiment.
Rank one-factor control/treatment/drift candidates using this run's evidence and allowed
axes. Label capacity changes and price the complete chain, including correctness/submission
and closeout reserve, against the workload deadline before starting. Establish both baseline
legs early, retain current profiles by identity/configuration/leg, and choose a cheaper legal
candidate when a chain cannot fit.

Kernel work uses the same campaign: triage, applicable specialist analyses, implementation,
then full-model remeasurement and equivalence. Derive the entire kernel dispatch shape
from the compiler fingerprint. An accepted kernel improvement needs passing correctness,
a candidate win beyond the noise margin, and an independent confirmation. Keep compiler
and measurement helpers subordinate to those prepared tasks.

Re-measure every requested metric after an accepted change. A binding-mechanism improvement
with a flat headline metric requires new attribution. A change that pushes another joint
objective out of range is a regression. Restore the verified configuration through the
owning specialist and retain the evidence.

Persist completed measurements before cleanup. Reconcile child/output/receipt state, then
use `report` and `finalize` to close the allocated run with observed scoped gains,
correctness state, unmet target and resume action. Lifecycle completion does not imply MET:
certification still requires strict audit, measured objectives and matching correctness.
A large floor gap is not a hardware ceiling. Do not add a separate writer execution timeout.

Generate the tracker from admitted evidence through `report`. Report exact configurations,
commands, admitted artifact paths, scope, and any
unverified claims. Final output cites the campaign's audited verdict.
