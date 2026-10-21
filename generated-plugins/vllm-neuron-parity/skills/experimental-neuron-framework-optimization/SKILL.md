---
name: experimental-neuron-framework-optimization
description: |
  End-to-end workflow for optimizing a model on AWS Neuron (Trainium/vLLM-Neuron) to meet
  a joint latency + throughput SLA. Delegates planning, entitlement, profiling, analysis,
  implementation and compiler debugging through the campaign controller, behind
  two decision gates:
  Gate A (classify the ask — latency-optimized prefill, throughput-optimized decode, or a
  joint SLA — and reconcile it into a single-node DP×TP config before profiling) and Gate B
  (delegate to the right agent and verify every delegated claim against primary evidence).
  Use when the user says "optimize this model", "hit this latency/throughput SLA",
  "make this model faster on trn2", "AutoBridge", "integrate and optimize", or gives a
  target like "~25ms E2E and 20-40K tok/s".
argument-hint: "<model / bundle path> <SLA: latency + throughput target>"
---

# AutoBridge: Model optimization

## One campaign controller

`neuron-campaign` is the only controller for AutoBridge. Read
[the campaign command contract](references/campaign.md) for initialization, task preparation,
submission, pause/resume, audit, and finalization. The optimization agent stays at the top
level and delegates each prepared task to exactly the agent named in its grant.

If the CLI is unavailable, report an installation blocker. Existing campaign databases
resume through that CLI. State from retired controllers stays historical: create a new
campaign ID and re-establish evidence through specialist submissions. Never select a
controller based on installed commands, filenames, or whether the task concerns a kernel.

All supported campaigns optimize a model on one colocated node. Kernel work is a step
inside the same campaign. The staging, measurement, compiler-parsing, and analysis scripts
under `scripts/` are utilities used by the specialists; they do not own campaign state.

Read `$AUTOBRIDGE_CONTEXT` first when supplied and carry its checkpoint, interpreter,
repository, project root and objective into the campaign and specialist tasks. Use the
managed project root, reservation, and hardware when supplied. Derive missing
concurrency through the planner and hardware core counts through the observed topology.
Do not ask the user to repeat runtime inputs. Apply the remaining workload budget without
adding a separate writer execution timeout.

Freeze `baseline_identity` independently from `optimization.allowed_axes` and
`source_write_allowed`; distinguish `optimization` from `qualification_only` mode.
Do not infer permission from a promising gain. An explicitly permitted treatment may change
its axis after the baseline, while checkpoint and declared precision constraints remain fixed.
Preserve `correctness_contract` (criterion, precision-aware reference method/identity,
tokenized corpus, input/output lengths) and `budget.deadline_at` plus closeout reserve in
every grant. These campaign constraints take precedence over generic examples below.

Keep the mainline shared run specification for profiling and equivalence. Read the owning
specialist's worked examples when relevant; its domain methods remain in its own skill.
Use this skill's [investigation playbook](references/INVESTIGATION_OPTIMIZATION_PLAYBOOK.md),
[framework lessons](references/FRAMEWORK_MODEL_OPTIMIZATION_LESSONS.md), and
[kernel lessons](references/KERNEL_OPTIMIZATION_LESSONS.md) to check inherited claims.

## SCOPE (hard constraint — read before Gate A)

**Single-node, colocated serving ONLY.** Every config you plan, profile, or recommend must be
ONE replica serving BOTH prefill and decode, entirely within ONE node
(`TP × DP ≤ cores_per_node`; trn2.48xlarge = 64 logical cores at LNC2). DP replicas are
independent single-node replicas on that same node — never a second machine.

The following are **out of scope** and must never be proposed, modeled, benchmarked, or
offered as a way to close an SLA gap — not even when the analytic model favors them, and not
even when no colocated config meets the target:

- disaggregated inference (DI) / split prefill + decode pools / prefill-decode phase splitting
- 1P1D, 1P2D, or any xPyD prefill-decode topology
- KV-cache transfer or handoff between instances (NIXL, `kv-transfer-config`, EFA KV paths)
- any multi-node, multi-instance, cross-node, or multi-machine serving arrangement

The parallelism-planner enforces this mechanically: `plan()` / `build_bundle` raise on any
`serving` other than `"colocated"`, and `entitlement_formulas._axis_link_bw` raises on a
collective degree wider than one node. **If the applicable target(s) — the latency point
(Type 1), the throughput point (Type 2), or both (Type 3), see Gate A — cannot be met by a
single colocated single-node config, the correct output is a documented miss** — best
achievable numbers, the binding operator, the remaining headroom, and what would be required.
Reaching for a phase split or a second node is a scope violation, not a solution.

## Gate A — Target reconciliation (BEFORE profiling; before ever saying "infeasible")

**Step A0 — Classify the ask.** Before reconciling anything, decide which of THREE ask types
the user gave you. The ask type determines what "met" means and which operating point(s) you
must reconcile. Restate it back explicitly (e.g. "this is a latency-only ask on prefill").

| # | Ask type | What the user gave | What "met" means | What you reconcile |
|---|---|---|---|---|
| 1 | **Latency-optimized (prefill)** | a TTFT / single-request latency target only (e.g. "TTFT < 400 ms"), or "make prefill as fast as possible" | the latency operating point holds | the **latency operating point** only |
| 2 | **Throughput-optimized (decode)** | an aggregate decode throughput target only (e.g. "> 5000 tok/s"), or "maximize decode tok/s" | the throughput operating point holds | the **throughput operating point** only |
| 3 | **Joint SLA** | BOTH a latency target AND a throughput target, required together / in the same run | both operating points hold on ONE config | **both** operating points as a pair |

The two operating points, in all three cases, are:

- **Latency operating point** = min-latency single request → an **interior-optimum TP**
  (via the parallelism-planner; for collective-bound / short-sequence / small-activation
  models the latency bottoms out at an interior TP and **regresses** beyond it — never
  default to max-TP).
- **Throughput operating point** = aggregate at concurrency → **DP = (available logical
  cores / TP)** independent replicas, plus a `max_num_seqs` packing sweep.

**Step A1 — Reconcile according to the ask type:**

- **Type 1 (latency-optimized, prefill).** Find the single-node config that minimizes /
  meets the latency operating point: sweep TP for the interior optimum, then choose the
  smallest TP (leaving the most cores for other work / DP headroom) that still meets the
  latency target. DP is free to fill the remaining cores but is not what the verdict rests
  on. Report the achieved TTFT and its binding operator. Do NOT overspend TP past the
  interior optimum — beyond it latency regresses.
- **Type 2 (throughput-optimized, decode).** Find the single-node config that maximizes /
  meets the aggregate decode throughput: pick the TP that maximizes per-node aggregate
  `DP=(cores/TP) × per-replica tok/s` (smaller TP usually wins until per-replica decode
  becomes weight-HBM/KV-HBM bound), then run the `max_num_seqs` packing sweep. The verdict
  rests on a **measured concurrent N-replica** aggregate. A `dp_projected` per-replica
  measurement × N is diagnostic and cannot certify the SLA (E5). Report achieved
  aggregate tok/s and its binding operator.
- **Type 3 (joint SLA).** Restate the target as `(latency_point, throughput_point)` and
  identify the single `DP=N × TP=(interior optimum)` config that satisfies **both**. **Do
  not declare a joint SLA infeasible until you have checked it against this pair.** Worked
  example this gate catches: "22 ms latency AND 45k tok/s" on a 32-logical-core trn2 is met
  by DP=2 × TP=16 — NOT infeasible, even though no single instant shows both numbers.

**In every case the config is ONE colocated single-node `DP × TP` recipe.** For the joint
ask, the "pair" is two operating points of that ONE config — it is NOT a licence to serve the
two phases on different configs. If the latency point wants one TP and the throughput point
another, that is a genuine conflict to resolve on the in-scope axes below — NOT a reason to
split prefill and decode into separate pools or add a node (see SCOPE above). Resolve it with:
the explicitly allowed compiler/scheduler, batch, block sizing, or context-bucketing axes,
or source work on the binding operator when source writes are authorized.

## Gate B — Delegation + verification (BEFORE writing any code, and BEFORE trusting any result)

If a specialized agent exists for the sub-task, delegate to it:

| Sub-task | Agent |
|---|---|
| parallelism / DP×TP planning | `neuron-parallelism-planner-agent` |
| hardware entitlement / binding-operator localization | `neuron-hardware-entitlement-agent` |
| kernels | `neuron-nki-agent` |
| model porting / framework code | `neuron-framework-autoport-vllm-agent` |
| profiling (capture NEFF/NTFF/system trace) | `neuron-framework-profiling-vllm-neuron-agent` |
| profile analysis (bottleneck localization) | `neuron-framework-profile-analysis-vllm-neuron-agent` |
| compile-error / artifact debugging | `experimental-neuron-perf-compiler-artifact-debugging-agent` |

If you are about to hand-write a **load balancer, timing/benchmark harness, data-parallel
serving strategy, or parallelism scheme** — STOP. It is almost certainly an existing
capability or a supported recipe (see the parallelism-planner's DP-replica serving
recipe). Hand-rolling these is the single biggest time sink and the most common source of
off-agent thrash.

**Every prohibition must name its sanctioned replacement (B12).** When the requester
forbids a specific tool/harness/agent ("do not use X"), that is an *incomplete* instruction
until a named substitute is confirmed to exist and be reachable ("use Y, not X"). A
prohibition with no located replacement is a **procedural gap to resolve with the requester
before any device time** — never a licence to improvise one (an improvised harness silently
owns the definition of whatever quantity the verdict rests on). Map each capability the task
needs — launch, capture, ingest, analysis, and the measurement the verdict rests on — to a
named owner before writing code against it.

**Delegation does NOT mean unverified acceptance.** Run this checklist against every
delegated claim before building on it — not just the ones that "feel" suspicious. A
plausible, well-formatted, specific answer is not evidence (see the profile-analysis
skill's `fabricated-analysis-case-study.md`: a detailed table and confident verdict were
entirely fabricated with no artifact behind them). Also treat pre-existing report files in
the workspace with the same suspicion as live subagent output — a confident report dated
"today" is exactly what gets taken as ground truth and skipped.

1. **Does the claimed artifact exist on disk?** `find`/`ls` for the exact path. Missing
   file = disqualifying.
2. **Does the artifact contain what's claimed?** Grep/query the raw artifact yourself; do
   not trust a subagent's summary of its own evidence.
3. **Does the code path support the claim?** For a "missing handler for Y" style claim,
   read the real source; the true mechanism is often more specific (or the claim is false).
4. **Is the claim scoped correctly?** "Bug X blocks all paths" needs each path actually
   tried, not inferred from one failure.
5. **Distrust *computed/aggregated* numbers hardest.** Raw-field reads (a `duration_ns`, a
   byte `size`, an event count) are usually right. Anything requiring interval-overlap-
   merge, cross-stream/cross-rank aggregation, or warmup filtering must be independently
   recomputed — that's a distinct failure mode from fabrication (the number is real, the
   computation is wrong). Real incident: a subagent reported a collective fraction of
   21%/65-ops; the correct single-rank overlap-merge gave 43.5%/130-ops.

A delegated conclusion that says "unfixable except by X" where X is a much larger effort
(new kernels, SDK upgrade, compiler ticket) is exactly the high-cost claim to re-verify
against artifacts before acting — not the kind to accept and build on. A clear "I tried X,
got error Y, could not determine Z" from a subagent is correct and should be trusted as-is.

---

## Prior observations (check applicability against this run)

These observations motivate cheap falsifiers, not blanket exclusions or permissions.
Do not reopen a candidate contradicted by this run without new evidence:
- **Replicated-TP / RS+AG→AllReduce fusion** — cuts collective count, not bytes/time on
  the SP data dependency; easily breaks correctness. Not a latency win.
- **Windowed-einsum / `AwsNeuronCollectiveMatmul` overlap** — enabling flags are
  XLA-GPU-backend-only, not wired to the trn2 vllm-neuron path. No effect.
- **`-O3` / `--model-type=transformer` at TP>1** — noise on collective-heavy graphs.
- **vLLM async scheduling** — a generative-decode feature; N/A to prefill-only pooling.
- **Two TP replicas on non-zero-based / non-contiguous device windows** — the device
  backend requires device index == local rank, so each replica's `NEURON_VISIBLE_DEVICES`
  window must be zero-based in its own process. A second replica on a contiguous `16-31`
  block works; `0-3` + `12-15` in one launch fails. See the parallelism-planner's
  `tp-feasibility-and-kv-replication.md` §6.
- **Disaggregated inference / prefill-decode phase splitting / any multi-node topology** —
  OUT OF SCOPE, not a dead-end to re-test but a boundary not to cross. Do not propose it,
  size it, or benchmark it, however attractive the analytic model makes it look. See SCOPE
  at the top of this skill; the planner raises rather than modeling it.

## Diagnostic leads (require current evidence and authorization)

- **`[NCC_IXRO002] Undefined SB Memloc dot.*_deconcat_*` at LNC=2** on an all-PyTorch
  graph (`kernel_call_count: 0`) is a known one-flag fix: sweep
  `--internal-hlo2tensorizer-options=--modular-flow-mac-threshold=<N>` above the graph's
  MAC count. Do NOT reach for `--logical-nc-config 1` (TP=1-only; blocks TP>1 collectives).
- **README/design-note claims that a fused op "heap-corrupts"/"SIGSEGVs" on an older
  compiler** — re-test on the *current* compiler before building a fallback workaround on
  the claim. These go stale; a direct on-device test takes minutes and has reversed such
  claims before.

---

## Campaign progression

These are the domain obligations for the actions returned by `neuron-campaign context`.
The controller prepares grants and admits submissions; do not mark stages complete in a
separate file or bypass a missing action by running a specialist yourself.

### Planning and entitlement

Start with the requested model, actual input/output lengths, hardware, project root, and
latency, throughput, or joint objectives. The planner returns candidate IDs with TP/DP
that fit the node. Preserve its exclusions and context buckets. The entitlement agent
supplies per-operator floors, the binding resource, scope, and reconciliation residual.
Respect mainline's distinction between certified engine floors and scoped component
estimates; a scoped estimate is not a global hardware ceiling.

Profile the currently entitled candidate with its exact TP/DP. Change the plan and obtain
new entitlement before changing that serving cell. An infeasible analytical floor calls
for another in-scope configuration or an interim analytical miss; it never certifies an
exhausted model optimization.

### Profiling and correctness

The profiling specialist owns serving, benchmark, capture, and trace ingestion. Preserve
requested configuration and observed configuration, reservation/device identity, phase,
window, and native artifacts. Resolve weight, compute and KV precision separately:
checkpoint metadata and serve inputs are premises; raw runtime configuration proves active
precision. `auto` never proves FP8, and a user-required precision mismatch remains blocked.
Run the profiling skill's cheap version-checked interface probe before loading/capturing;
do not patch serving source to adapt the profiler or invent model-specific bucket values.
Each accepted full-model profile needs a non-empty live NTFF
or ingested parquet capture. A serve log, benchmark summary, empty directory, isolated NEFF
replay, or reduced-model trace cannot replace the full-model capture.

A reduced-model structural probe can cheaply expose compiler rewrites or host-side work;
submit it as reduced/diagnostic evidence. Restore the full model and remeasure before
certification. Verify warmup, rank, and phase from the trace. Device execution time is a
lower bound for an end-to-end objective; account separately for host overhead.

If compilation/startup fails, preserve the exact command and full error log in a
blocked/failed submission. That recorded attempt can be the source of `compiler_debug`
before any successful profile exists. Pass its failure object unchanged and use the
returned `source_context`. Replanning is also available after a recorded execution failure.
The compiler specialist's diagnosis guides the next action; it does not certify performance.
Profiling specialists must return for a new grant before source or checkpoint mutations.
Separate the observed error from hypotheses about its cause, and stop repeating a
configuration hypothesis after its proposed falsifier has already contradicted it.

Run equivalence against the selected profile's shared run specification, configuration,
and frozen criterion/corpus/context lengths. Check capture capabilities before a logit-based
method; exact greedy-token matching does not require huge logits or a new distributional
criterion. Reuse only exact reference artifacts keyed by checkpoint, prompt token IDs,
lengths, reference code/runtime, precision and method. Target correctness is rerun after
relevant execution changes. A short-context probe never certifies a longer context.
Classify failures as mismatch, missing evidence or unsupported tooling. Follow the
controller's diagnostic/authorized repair route; when mutation is forbidden, report the
defect and required permission instead of repeating the same check.

### Analysis and configuration sweeps

The profile-analysis agent reports queries, measured bottlenecks, and conditions that
would falsify each explanation. Inspect its evidence. Mechanism claims need the device
capture; computed aggregates need their actual derivation and observation window.

Establish both requested baseline legs early, then submit `candidate_experiments` through
profile analysis: each names a legal capability and complete inputs, one allowed axis,
this run's evidence attempt IDs, exposed cost, predicted benefit, evidence quality, full
`estimated_chain_seconds`, a cheap falsifier, and control/treatment/drift configurations.
Drift returns to the control; label replica/admission changes as `change_scope=capacity`.
Price planning, cold/warm compilation, all benchmark arms, required correctness and
submission together, with closeout reserved separately. Use the controller's feasible
ranked queue and choose a cheaper legal candidate if the chain will not fit the workload
deadline. Do not reopen failed/contradicted candidates without new evidence.

Preserve raw requested/observed config and canonical effective config with runtime-proven
defaults. Select evidence by source identity, configuration and leg; a newer decode capture
alone does not invalidate prefill. Use bounded profile attempts and preserve the best verified
configuration. Read
[the sweep acceleration reference](references/SWEEP_ACCELERATION.md) for minimizing compile
and startup overhead. If an operating point cannot actually be admitted or compiled,
return to planning/entitlement with that evidence.

The analysis records `config_sweep.axes` and `config_sweep.profile_attempt_ids`. These must
name actual variation across at least two accepted current full-model profiles, including
the profile being analyzed. The controller refuses kernel work until that evidence exists.
This requirement does not force kernel work after the requested SLA is already met.

### Kernel and framework improvements

Delegate framework changes to autoport and kernel analysis/implementation to the granted
NKI specialists. Before kernel work, use `parse_fxgraph_config.py` to derive the target's
complete compiler-observed launch arguments. Supply the fingerprint artifact and shape in
`target`; an incomplete shape or different dispatch branch is rejected.

Specialists may use `stage_kernel_tree.py`, `measure.py`, `analyze_local.py`,
`kernel_loop_scaffold.py`, and the existing profiler/cost utilities. Staging comes from the
installed runtime, with edits confined to the granted workspace. Keep measurement helpers
and references from the installed test support; do not substitute prebuilt model kernels.
The [stub synthesis procedure](scripts/kernel_stub_synthesis.md) explains capture-derived
shape selection. It supplies a measurement input, not a separate optimization workflow.

Return specialist results through `neuron-campaign submit`. A successful implementation
needs passing correctness and both candidate and confirmation measurements beating the
baseline beyond the noise margin. A no-win attempt is partial/failed evidence, not a
fabricated improvement. A source change then requires full-model profiling and matching
equivalence before any new SLA verdict. Recheck every requested objective after each win.

### Evidence, reporting, and completion

Every claimed value cites an admitted artifact and names its configuration, scope,
statistic, and measurement window. Hashes prove artifact identity, not measurement truth.
Concurrent full-model measurements certify node aggregates. DP projections and inverse
median-ITL proxies remain diagnostic. A full-window rate is different from a decode-only
rate and must not be silently substituted.

Keep before/after compiler, model, TP/DP, KV, sequence limits, request lengths, concurrency
and timing scope with each comparison. If several settings change, attribute the gain to
that configuration bundle. Preserve successful local improvements and rejected experiments
even when the complete objective remains unmet.

For an objective within N% of a roofline, latency uses `actual/floor <= 1 + N/100`;
throughput uses `actual/floor >= 1 - N/100`. Record raw measurements and the entitlement
denominator. Joint objectives use one configuration and one window when requested. For
independent per-leg configurations, use separate single-leg campaigns through the same
controller and report their independent scopes explicitly.

Track the native child/session ID, expected output path, completed arm artifacts and
submission receipt through `progress` and `context`. Resume existing work after interruption;
a child `end_turn` is not completion. Do not end the parent turn while a child is running.
Persist completed arms before cleanup; cleanup errors neither erase results nor make them pass.

Use `audit --strict` to assess certification and `report` to generate the tracker from
admitted evidence. After reconciling children and receipts, `finalize` closes the allocated
run with an immutable receipt even if its scientific verdict is a miss or blocker.
Only matching measured performance and correctness can certify MET. Report observed scoped
gains independently, including baseline/best, configuration changes, correctness, unmet target
and resume action. Never turn a large floor gap or untried layer into a hardware ceiling.

Keep the tracker and investigation evidence as work proceeds. Report the recommended
serving configuration, accepted measurements, exact commands and artifacts, unresolved
limitations, and campaign verdict. The playbook's narrative artifacts explain the work;
only the campaign database records authoritative task state and accepted results.
