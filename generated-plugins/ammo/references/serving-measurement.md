# Managed P/D measurement

`serving-campaign.md` owns integration with the existing AMMO stages. This file
owns the local deployment, collector, and measurement contract. It does not
create a router, transfer protocol, or autoscaler.

## Supported execution boundary

The explicit `ammo_serving/2` manifest names one prefill replica, one decode
replica, and one CPU router. Each replica can use multiple assigned resources.
All are local foreground process groups. Readiness endpoints must use literal
loopback addresses and separate ports. A listener already in use is rejected;
AMMO never adopts or terminates a peer deployment.

The campaign reservation (`references/gpu-pool.md`) covers the whole experiment,
including both roles and all baseline/candidate windows. `deployment_ops.py`
reserves it, starts every worker on this host, and tears them down. Resources
must be disjoint and inside the host pool. Neuron role ranges must be contiguous.
Neuron LNC must match the campaign value, remains frozen, and is included in
timing proof.
Startup failure tears down already-started groups. Teardown includes children
whose launcher exited. Uncertain teardown raises `CleanupError` and does not
release the claim. Serving claims use `release_policy: confirmed_cleanup`; expiry,
release of the parent owner, and ordinary auto-release cannot reclaim them. Release
requires the exact owner and confirmed cleanup. Operator recovery must first
verify that the owned processes have stopped. This is a local reservation-store
guarantee, not distributed fencing or protection against deleting the store.
Remote hosts, SSH launchers, daemonized workers,
and cross-node performance are outside the supported boundary.

A fixed resource split and existing transfer path are part of the target. A
candidate cannot win by adding hardware, changing the workload, silently changing
precision, or switching the approved transport. The acceptance unit is the whole
deployment; the optimization boundary can be a role, kernel, phase, or transfer.

## Frozen inputs

The closed manifest includes model ID and exact revision, dtype, role assignments,
worker commands, adapter commands, workload files, policy, and explicit process
environment. Every worker has an `execution_identity` with nonempty `engine`,
`runtime`, `compiler`, `compile_config`, `cache_policy`, `graph_mode`, and
`transport` values. Use exact production identities and explain how the collector
observes them. A label copied from a manifest is not independent runtime proof.

An arm names a clean source worktree and exact commit for every worker, plus its
declared mechanism environment. Both arms use the frozen deployment environment.
Ambient Python, loader, compiler, cache, and unrelated secret variables are not
inherited. Only the trusted campaign identity and managed runtime/resource values
are injected. Keep required production variables explicit. Mechanism overrides
cannot change ownership, LNC, PATH, Python import paths, or loader injection.

The workload and adapter files have absolute paths and SHA256 digests. Keep them
outside candidate worktrees when possible. The runner checks their bytes before
and after official timing. The deployment owner supplies the exact commands for
the installed runtime; the binding does not invent vLLM or Neuron CLI flags.

Benchmark commands must bind these runner substitutions:

```text
{result_dir} {result_filename} {seed} {endpoint}
{request_rate} {num_prompts} {request_id_prefix}
```

Other supported substitutions are `{model}`, `{model_revision}`, `{dtype}`,
`{source}`, `{run_dir}`, `{run_id}`, and `{workload_id}` where relevant. Commands
are literal argv, without shell expansion. The load generator must retain each
actual dispatched request ID. IDs use the supplied deployment/workload prefix.
Do not synthesize IDs from a summary after the request was sent. A pinned wrapper
around an existing benchmark can preserve its actual request objects and results.

`serving_benchmark_adapter.py` supplies that wrapper for the pinned vLLM v0.24.0
benchmark at commit `ee0da84ab9e04ac7610e28580af62c365e898389`. It checks the
imported benchmark source and request-helper blob, rejects tracked source changes,
uses the native `RequestFuncInput.request_id` field, and retains IDs in the actual
measured output order. It also retains the actual request latencies and measures
client queue time from the upstream arrival yield to the request helper start,
on the same `perf_counter` clock. It does not infer queue times from zeros or
claim to measure lateness before the upstream generator yields.
Failed requests remain present; readiness/warmup requests
do not enter the measured population. It delegates traffic and SSE parsing to
vLLM. Only the `openai` completions backend is supported. This benchmark pin does
not change the serving workers or claim that their runtime is qualified.

Invoke its documented CLI as literal argv: `python serving_benchmark_adapter.py
--vllm-source /absolute/pinned/vllm --request-id-prefix {request_id_prefix}
-- bench serve`, followed by the deployed benchmark's documented, frozen flags.
Pin the wrapper and workload files in the manifest. Run `--help` in the target
environment when constructing the command. Do not assume another release shares
this internal interface. A runtime collector still must correlate these dispatched
IDs with actual handoff observations; the benchmark does not invent that proof.

A Pumice CUDA engine venv (`pumice-cuda`) has no vLLM git checkout. It has the
installed vLLM wheel at the pinned version (`pumice.config.REQUIRED_VLLM_VERSION`).
For that runtime, use the wheel form in place of `--vllm-source`. The engine venv
is `target.json` `engine.venv`, and the adapter is the plugin copy:

```text
<engine venv>/bin/python <plugin root>/scripts/serving_benchmark_adapter.py
  --vllm-wheel <engine venv>/lib/python3.12/site-packages
  --request-id-prefix {request_id_prefix} -- bench serve ...
```

The wheel form checks that the imported benchmark module is under that
site-packages root, that `vllm.__version__` is in the adapter's pinned version
table, and that the git blobs of `vllm/benchmarks/serve.py` and
`vllm/benchmarks/lib/endpoint_request_func.py` equal the pinned pair for that
version. The request ID, latency, and queue-time rules above are the same in
both forms. The plugin ships the adapter next to `scripts/serving_contract.py`,
which the adapter imports. Pin both plugin copies in the manifest `files`.

## Collector protocol

Adapters run in CPU-only control processes. They inspect owned workers or read
approved runtime-owned observations. `AMMO_SERVING_CONTEXT` names their input
JSON; `AMMO_SERVING_OUTPUT` names the required output JSON. Context binds the
live run, worker groups, source arms, resources, and frozen spec digest. The
`mechanism` context also names the incumbent: `incumbent` (its absolute result
directory) and `incumbent_seal_sha256` (`digest()` of its parsed `seal.json`). A
collector may read its baseline from that sealed incumbent. Verification
rejects a mechanism result whose sealed context `incumbent_seal_sha256` differs
from the seal's `parent_incumbent_sha256`.

Every collector result echoes `run_id` and lists nonempty, unique primary
artifact paths relative to its output directory. The runner seals all referenced
files. A result cannot reference itself as its only evidence. The frozen adapter
source, primary artifacts, and semantic interpretation still need independent
review.

| Adapter | Required result |
|---|---|
| `component` | Positive `tests_run`, numeric `failures`, and primary correctness-test output. |
| `mechanism` | Exact source-arm digests; named production-boundary buckets; at least three paired baseline/candidate durations per bucket; reproducible positive impact in a bucket; explanations for negative buckets. |
| `activation` | Exact model revision; every role's PID, source commit, imported source path, resource IDs, and mechanism signature; request-correlated handoff IDs, sent/received bytes, completed count, and no recompute fallback; primary traces/counters. |
| `timing_proof` | Passive, profiler-free proof from the actual fresh timing ranks before and after each workload window, as described below. |

Do not profile inside `timing_proof`. The separate activation/profile deployment
may use a profiler. Profiler timings never enter the official serving result.
A component or mechanism adapter must measure the actual production boundary,
not a faster proxy. A missing/malformed adapter result is blocked, not a failed
optimization or a passing gate.

## Passive timing proof

The runner inspects only owned process groups. For each fresh timing deployment
it snapshots PID, process start time, environment digest, and executable digest.
Each worker's `execution_ranks` specifies the expected rank count. The collector
must return one row per actual rank, without gaps or duplicates.

An `ammo_timing_proof/1` record binds phase (`before` or `after`), run ID, workload
ID, contract digest, arm digest, and `profiled: false`. Each rank binds the actual
process identity, imported source path and commit, a sealed copy of the imported
module, its digest, runtime profile, model revision, dtype, LNC, and complete
execution identity. The live check hashes the reported loaded module path too.
Rank identity cannot change between the before and after proof.

The after-proof must cover exactly the successful benchmark request IDs. It
records transfer bytes, completion count, and zero recompute fallbacks. IDs must
carry this fresh deployment's workload prefix, so a previous profile's request
IDs cannot validate a new timing run. Benchmark failures stay in the metric
population and cannot be silently omitted to improve goodput.

For a typed gated arm, the after-proof also checks actual mechanism activation
inside the envelope and the pinned incumbent fallback outside it. Positive and
negative frozen workloads are mandatory. Never substitute a workload label for
the production predicate.

This protocol verifies identity, freshness, and arithmetic. It does not magically
prove that an arbitrary adapter obtained its values from the runtime. A qualified
production collector and independent evidence review remain necessary.

## Measurement order and outcomes

Initial baseline capture runs the existing GSM8K prompt/scoring path twice through
the router, captures activation, and measures repeated clean load windows. A
zero-accuracy reference or a baseline that violates frozen SLOs is rejected.

Candidate and integration runs keep the five gates in order:

```text
5.1a component correctness
  -> 5.1b full-model GSM8K accuracy against incumbent golden answers
  -> 5.2 impact at the declared production boundary
  -> 5.3a mechanism and P/D activation
  -> 5.3b repeated clean serving windows
```

Greedy generation uses the frozen question count, output limit, and accuracy
policy. Accuracy uses the existing aggregate comparator, not a newly invented
universal exact-token gate. Valid early correctness, mechanism, or observed activation failures seal a reconstructible
failure and skip later timing. Invalid evidence and infrastructure failures leave
a blocked partial run with no valid seal.

The profile/gate deployment stops before official timing. Every timing window
starts fresh role processes. Paired baseline/candidate windows alternate order,
use the same workload and seed, and collect passive proof. Restarting alone does
not prove statistical independence or matched cache residency. The test owner
must justify the warmup, cache, load-generator capacity, and observation windows.

## Raw metrics and statistics

Detailed benchmark output must provide aligned per-request `request_ids`,
`input_lens`, `output_lens`, `ttfts`, `itls`, `latencies`, `queue_times`, and
`errors`, plus `duration` and `completed`. Optional `failed` must reconcile.
Timings are seconds. Exact output length is required; when `input_tokens` is set,
input lengths must match too. No reported summary can replace these arrays.

TTFT and request latency include observed client queue time. TPOT excludes the
first token. `itl_ms` means observed streaming intervals; chunked output does not
provide exact per-token timestamps. Window percentiles pool the recorded
intervals. Per-request goodput requires every SLO predicate, using the largest
recorded interval for that request. Errors remain in the denominator.

The statistical unit is a complete paired load window. A one-sided exact sign
test checks relative improvement above the frozen floor. Bonferroni correction
covers the frozen workload family. Candidate windows must satisfy all SLOs and
the regression limit. Insufficient evidence cannot PASS. Fixed-load goodput is
not maximum sustainable capacity; a capacity-search objective is not implemented.

## Runner commands

Run these from the root of the Pumice checkout:

```bash
scripts/ammo run_serving_sweep.py \
  --artifact-dir "$ARTIFACT_DIR" --kind baseline --arm "$BASELINE_ARM"

scripts/ammo run_serving_sweep.py \
  --artifact-dir "$ARTIFACT_DIR" --kind promote --result "$BASELINE_RESULT"

scripts/ammo run_serving_sweep.py \
  --artifact-dir "$ARTIFACT_DIR" --kind candidate --arm "$CANDIDATE_ARM" \
  --round 1 --op-id OP-001

scripts/ammo run_serving_sweep.py \
  --artifact-dir "$ARTIFACT_DIR" --kind integration --arm "$INTEGRATED_ARM" \
  --composition "$COMPOSITION_JSON" --round 1 --op-id combined

scripts/ammo run_serving_sweep.py \
  --artifact-dir "$ARTIFACT_DIR" --kind verify \
  --result "$INTEGRATION_RESULT" --against "$EXACT_PREVIOUS_RESULT"

scripts/ammo run_serving_sweep.py \
  --artifact-dir "$ARTIFACT_DIR" --kind promote --result "$INTEGRATION_RESULT" \
  --expected-incumbent "$EXPECTED_EVIDENCE_SHA256"
```

Results use unique paths under `serving/rounds/{round}/{kind}/{op_id}`. Existing
paths are not overwritten. Verification uses the exact historical comparator;
promotion uses compare-and-swap against the current pointer. Only an initial
baseline or passing integration can promote. The original baseline pointer
remains separate. No runner command merges code or grants an AMMO audit/SHIP.

## Qualification before production use

The plugin tests include CPU contract fixtures, not a qualified production
CUDA/NIXL or Neuron adapter package. Those fixtures must never serve as production
collector inputs. To qualify one pinned deployment, supply real commands and
collectors, independently inspect their observations, then exercise a full real
baseline/candidate/integration campaign with startup failure, wrong-source,
missing-handoff, fallback, cancellation, and regression tests. Do not claim
production P/D support until that evidence and independent review exist. Multi-node use requires a
separate distributed ownership/fencing implementation and cross-node validation.
