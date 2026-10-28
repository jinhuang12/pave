# Serving campaign binding

This file owns serving-specific evidence routing into the existing AMMO campaign.
`serving-measurement.md` owns the deployment and measurement interface. Existing
stage, role, debate, repair, audit, and integration authorities still apply.
Serving support does not grant an audit, substitute the lead for a technical
reviewer, or merge code automatically.

## Scope

An explicit `--serving-manifest` selects this binding. Existing targets without
`serving` retain their batch-latency behavior. The current implementation supports
one local prefill replica, one local decode replica, and one CPU router. A replica
can own multiple resources. CUDA and Neuron remain accelerator bindings, not new
campaign types. No cross-node ownership or performance claim is supported.

Use exactly one frozen objective: client TTFT, TPOT, streaming interval, request
latency, or request goodput at specified offered loads. These are NOT maximum
sustainable throughput. Never write serving values into batch `expected_e2e_pct`,
`e2e_speedup`, `combined_e2e_speedup_x`, or cumulative batch-latency fields. A
serving result cannot use the batch diluted-pass exception.

## Scaffold

Prepare a versioned `ammo_serving/2` manifest (`schemas/serving.schema.json`)
using the deployment owner's pinned launch recipe and collectors. The user, as
the deployment owner, supplies the manifest, its router and collector commands,
and their files. The plugin does not prepare a deployment. It must name the exact model revision, resources,
per-role engine/runtime/compiler/cache/transport identity, workload files,
request rates, sample counts, SLOs, and complete benchmark argv. Commands are
literal argument lists. They are not generated from undocumented runtime flags.

From the root of the Pumice checkout:

```bash
scripts/ammo new_target.py --serving-manifest "$SERVING_MANIFEST"
```

The scaffold records the manifest path and SHA256 in `target.json`, and the
campaign layer derives `AMMO_SERVING_SETUP`, `AMMO_SERVING_MANIFEST`, and
`AMMO_SERVING_MANIFEST_SHA256` from them (`references/campaign-env.md`). It
checks the campaign runtime, resources, and Neuron LNC. It refuses explicit batch,
parallelism, or gate flags alongside a serving manifest. Per-role settings come
from the manifest, not the flat legacy TP field. It emits a closed serving state
binding and no batch benchmark/workload block.

Keep the manifest, adapters, model, workload, policies, and resource budget fixed.
Adding a candidate's declared mechanism flag is allowed only in its arm. Changing
a production assumption requires a new approved target, not a silent edit.

## Evidence and state

The existing `ammo_state.py` remains the only state engine. Its normal lock,
schema validation, legal transitions, cohort checks, monitor checks, and audit
gates also apply to serving state. Add evidence with:

```bash
scripts/ammo ammo_state.py serving-sync \
  --state "$ARTIFACT_DIR/state.json" --kind baseline --from "$BASELINE_RESULT"
```

Supported kinds are `baseline`, `mining`, `track`, `failure-closure`, and
`integration`. Track and failure-closure require `--op-id`. Each kind is limited
to its existing AMMO stage. A failed synchronization leaves `state.json`
unchanged. This command records evidence. It does not advance a stage, create an
agent, write an audit pass, retire a teammate, or authorize SHIP.

Directory references bind the complete evidence seal. File references bind a
relative artifact path and SHA256. Verification recomputes gate arithmetic from
raw sealed data. `summary.json` is only a cache. All campaign references remain
inside the campaign artifact tree. A checksum binds identity; it does not prove
that a collector interpreted the runtime correctly. Auditors must inspect the
raw observations and the collector implementation.

## The existing seven stages

| Stage | Serving evidence and unchanged responsibilities |
|---|---|
| 1 | Capture the clean serving baseline and golden answers. Promote the initial measurement pointer, then synchronize baseline. Keep separate role/transfer attribution. Run `T_AUDIT_S1`. |
| 2 | Mine prefill, decode, transfer, and router evidence against the exact current incumbent. Synchronize typed mining. Run `T_AUDIT_S2`. |
| 3 | Keep the mandatory independent proposals and adversarial debate. Use objective-unit magnitude and hash-bound projection evidence. Keep the existing two-to-three selected candidate cohort and mechanism eligibility rules. |
| 4–5 | Keep one isolated implementation track and its paired transcript monitor per candidate. Run all five gates through the serving runner. Resolve missing evidence as blocked. Close required repairs before terminal FAIL. Synchronize each terminal result. Run `T_AUDIT_S45`. |
| 6 | Select the exact compatible passing composition. Resolve overlap using the existing resolver and independent merge review. Measure that composition as an integration result. Synchronize and promote it. Record SHIP only through the existing state transition and audit contract. |
| 7 | Use fresh current-incumbent objective opportunity for continue/stop. Run `T_AUDIT_S67`. After a material SHIP, create the next round and mine the promoted result. |
| 7b | A fresh report writer uses raw serving evidence and direct original-to-current objective accounting. A fresh fact-checker still must accept the report. |

A single passing track must also supply an explicit integration measurement in
this serving binding. The batch single-track copy shortcut is not implemented
for serving evidence. The extra deployment measurement is deliberate: a fresh
process composition needs its own proof. Never relabel a candidate seal as an
integration seal.

## Mining and debate

The `ammo_serving_mining/1` record contains `contract_sha256`,
`incumbent_sha256`, `objective`, every frozen `workload_id`, and a list of
opportunities. Each opportunity names its role, evidence scope, hash-bound
primary files, derivation, and nullable `upper_bound_improvement_pct`.

`coverage_complete` requires all four roles and known bounds before it can
support STOP. Partial coverage or unknown bounds produces UNRESOLVED, not zero
remaining opportunity. Independent Stage-2 review must verify the derivation and
coverage. A self-written coverage flag is not sufficient evidence.

For candidates, use `score_breakdown.expected_objective_pct` and
`serving_projection_evidence`. The expected-value arithmetic remains
`feasibility / 10 * expected_objective_pct`. A proxy cannot supply magnitude.
Do not use a serial request-time share as a throughput or queueing projection.
Use an actual serving-boundary experiment or a justified physical bound in the
chosen objective. The existing long-shot mode retains its minimum feasibility
and first-boundary-spike obligations. The batch-only contingent host-slice mode
must not be used for serving claims.

## Failures, gating, and integration

A valid correctness, mechanism, or observed activation failure stops expensive measurement and seals
a `candidate_failure` record. Verification independently reconstructs the first
failed gate. Missing files, malformed results, process loss, and uncertain
cleanup remain BLOCKED and cannot become a terminal success or ordinary failure.

Before a candidate becomes terminal FAIL, write an
`ammo_serving_failure_closure/1` record. Bind the exact failed result, attempted
repair artifacts, exhausted fallback disposition, and zero unresolved actions.
Synchronize the closure first, then the failed result. The paired monitor and
Stage-45 auditor still review whether those repairs were applicable and complete.
The record does not perform the repairs or approve them.

`GATED_PASS` requires a typed arm envelope: targeted roles, exact input-token
range, production predicate symbol, and incumbent fallback identity. Frozen
workloads must exercise both sides. Timing proof must observe fast-path use
inside and the pinned fallback outside. At least one passing workload must be
inside the enabled range. Labels and free-text enablement claims cannot grant a
GATED_PASS. Predicates outside this supported range model need a new reviewed
binding, not a fabricated shape classification.

Before integration, freeze `composition.json` with a `selected_results` map from
candidate IDs to their exact result-seal hashes. Pass it with `--composition` to
the integration run. The composition must match selected, validated, monitored
tracks. The independent merge review must still confirm that the integrated code
is the claimed composition; a JSON map cannot prove patch semantics.

`promote` advances only the measurement incumbent with compare-and-swap. It does
not merge code or grant SHIP. Campaign SHIP requires the same promoted integration
result and matching shipped candidate IDs. The round stores per-workload direct
original-to-current objective changes, not multiplied round percentages.

The current automated EXHAUSTED path requires verified failure and repair closure
for every selected track. When a passing track cannot be composed or integrated,
continue the existing resolver/repair work. Do not invent an exhaustion verdict
to bypass unresolved integration evidence.

## Reporting and recovery

Use the normal explicit report entry point:

```bash
scripts/ammo generate_validation_report.py \
  --artifact-dir "$ARTIFACT_DIR" --track OP-001
```

The report generator and `verify_validation_gates.py` detect the serving target.
They reconstruct serving results rather than reading batch latency slots. The
normal track reconciliation route performs the same serving synchronization and
state checks. Batch enrichment/ingestion routes reject serving targets.

On resume, read the serving binding, current round, exact baseline reference,
selected track seals, and current incumbent pointer. Do not recapture or reset a
recorded comparator. A new round inherits the validated integration or unchanged
baseline as appropriate. Failed work retains its evidence for later mechanism
pivots. No recovered state grants an audit automatically.

## Qualification limits

CPU fixture campaigns test software contracts, not inference performance or
agent judgment. No production CUDA/NIXL or Neuron collector has been qualified
in this change. No distributed lease/fencing binding or independently approved
hardware run is included. These limits remain release blockers for a complete
production P/D feature. Do not turn these limits into successful campaign
evidence.
