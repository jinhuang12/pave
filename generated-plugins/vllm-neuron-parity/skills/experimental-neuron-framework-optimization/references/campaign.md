# Campaign operation

The user interacts with the native optimization agent in Kiro or Claude Code. The agent
reasons with the user and invokes specialists through the client's native delegation tool.
`neuron-campaign` is a deterministic local controller; it does not call an LLM, host an
agent, or monitor a subagent process.

## Initialize

Use one absolute project root for the campaign and all specialist artifacts. Create a
version-1 CampaignSpec like this. Replace the example core count with the observed
hardware topology; never infer logical core count solely from the number of device files:

```json
{
  "contract_version": "1",
  "campaign_id": "optimization-run",
  "project_root": "/absolute/project/root",
  "ask_type": "joint",
  "model": {"name": "<requested-model>"},
  "hardware": {"name": "<observed-hardware>", "cores_per_node": 64},
  "objectives": [
    {"leg": "latency", "metric": "ttft_ms", "operator": "<=", "target": 400, "unit": "ms"},
    {"leg": "throughput", "metric": "out_tok_s", "operator": ">=", "target": 5000, "unit": "tokens/s"}
  ],
  "constraints": {"serving": "colocated", "max_nodes": 1},
  "same_window_required": true
}
```

Run `neuron-campaign init --spec <file>`. Campaign data is stored under
`<project_root>/agent_artifacts/autobridge/<campaign_id>/`; an interrupted client session can
reopen it without conversational memory.

Include `baseline_identity` (`model`, `config`, optional `source_revision`) separately
from `optimization` (`mode`, `allowed_axes`, `source_write_allowed`). Use the supplied
baseline fields, not an inferred model recipe. `qualification_only` authorizes no changes;
`optimization` permits only the named axes and source writes. Checkpoint and declared
precision stay immutable.

Include `correctness_contract`: `criterion`, `reference_method`, `corpus_digest`,
`input_tokens`, `output_tokens`, `requires_logits`, and `reference_identity` with
`checkpoint`, `tokenized_prompts_sha256`, `code_revision`, `runtime`, `precision`.
Resolve these before execution. Carry the absolute workload deadline in
`budget.deadline_at` (ISO timestamp with timezone) and `budget.closeout_reserve_seconds`.
With a deadline every ActionRequest needs `inputs.estimated_chain_seconds`, covering
planning, cold/warm compilation, all arms, benchmarks, correctness and submission.

## Handoff Loop

1. Run `neuron-campaign context --campaign <id> --project-root <root>`.
2. Choose one returned `legal_actions[]` entry. Its capability, exact NAD agent,
   required inputs, resource class, and output contract are authoritative.
3. Write an ActionRequest containing `contract_version`, `campaign_id`, current
   `state_version`, exact `action_id`, capability inputs, and a unique `idempotency_key`.
   Set `client` to the native runtime in use (`kiro` or `claude`).
   Hardware work also supplies `resources.devices` and a required
   `external_reservation_id`. Source-writing work supplies an existing isolated
   `resources.workspace` under the project root.
4. Run `neuron-campaign prepare --request <file> --project-root <root>`.
5. Invoke exactly the returned `grant.agent_id` through native client delegation, passing
   the returned task and grant verbatim. The controller has not launched or watched it.
6. Record the native child with `progress` below. Require producer facts following
   `task.output_contract`: `status`, `observed_config`, `payload`, and any `artifacts`,
   `metrics`, `claims`, `next_actions`, `failure`.
7. Run `neuron-campaign format-submission --campaign <id> --project-root <root>
   --task <task-id> --facts <facts.json> --idempotency-key <key>`. Save
   `result.submission` as JSON; inspect `result.valid` and the complete `result.errors`.
   The formatter fills identity/request echoes and paths, never observations. Run
   `neuron-campaign validate-submission --submission <file> --project-root <root>`,
   then `neuron-campaign submit --submission <file> --project-root <root>`. Do not repair or
   strengthen a specialist's facts in transit. A rejection leaves the attempt open for a
   corrected submission or cancellation.
8. Explain the machine outcome and repeat from fresh context.

Read the returned contract before delegation. `next_actions` contains capability-name
strings, not action objects. Artifact/metric evidence classes come from `evidence_classes`;
planner and entitlement results require an `ANALYTIC` artifact. On rejection, return the
exact error and contract to the producing specialist. The parent must not rewrite its
observations, classifications or claims to get a submission admitted.
Mechanical envelope correction alone needs no hardware rerun.

Bind the native child/session/job identifier immediately after delegation:

```json
{
  "campaign_id": "<id>",
  "task_id": "<prepared-task-id>",
  "idempotency_key": "<unique-update-key>",
  "child_id": "<native-child-id>",
  "expected_output_path": "<exact-task.expected_output_path>",
  "state": "running"
}
```

Send it with `neuron-campaign progress --request <file> --project-root <root>`.
States are `running`, `end_turn`, `completed`, `failed`, `unknown`; `end_turn` does not
mean completion. Optional `artifacts` preserve completed arms; optional `cleanup_error`
records cleanup failure separately. Reconcile native child state, output availability and
submission receipt from context before closeout. Resume the child or submit its existing
output; do not launch duplicates or end the parent turn while a child is running.
For the server, use `safe_serve.py status --runfile <path> --json`: `state` is `live`,
`terminal`, or `unverified` (exit 1); the cleanup object is separate. This process receipt
cannot establish scientific correctness or gains.

For a blocked/failed execution, submit diagnostic logs and the exact failure object.
`context` then permits compiler diagnosis and replanning even before the first successful
profile. Prepare `compiler_debug` with `source_attempt_id` and `failure` copied from that
recorded result. The task's `source_context` carries the failure, configuration and admitted
artifact identities. Changed evidence or a failure from a superseded serving cell is refused.
A diagnosis is not a successful profile; follow it with a new granted attempt.
Classify equivalence failures as mismatch, missing evidence or unsupported tooling.
Follow the returned diagnostic/authorized repair action or precise permission handoff;
invalidate dependent certification after repair and remeasure.

The executable capability registry covers the existing planner, entitlement, profiling,
profile-analysis, autoport, equivalence, compiler-debugging, NKI coordinator, NKI profile
analysis, NKI leaf specialists, and NKI writer agents. A configuration sweep is a sequence
of controller-approved profile attempts. Its analysis cites `config_sweep.axes` and
`config_sweep.profile_attempt_ids`; every named axis must vary across at least two current
full-model profiles. Without that evidence, kernel work is not a legal next action. Kernel iteration uses the same
attempt lifecycle; `target.dispatch_fingerprint` cites compiler-derived JSON under the
project root and `target.shape` supplies every observed launch argument. No separate
loop controller is involved; accepted implementation work must pass correctness plus a confirmed
noise-margin win and then return to full-model profiling.

## Evidence

Artifacts must exist under the project root when submitted. The controller resolves paths,
hashes files or non-empty directories, and verifies those digests on audit and resume.
Profile results must match every requested configuration field and the granted devices.
Preserve raw requested/observed config. `payload.effective_config` carries canonical axes
and witnessed defaults, backed by `runtime_config` artifacts with this `attempt_id` and
`effective_config`. Weight, compute and KV precision are separate; `auto` or a capability
banner cannot supply an FP8 denominator. Select current profiles by source/configuration/leg.
Only full-model `PRIMARY_MEASURED` profile metrics can set `supports_sla=true`; projected,
analytic, diagnostic, reduced, and isolated evidence cannot certify an SLA. Joint legs must
use one config, and must share a window when `same_window_required=true`.

A file digest establishes artifact identity; a `PRIMARY_MEASURED` label does not establish
measurement truth. Check native reports against raw results. Aggregate throughput requires
a concurrent measurement with the requested metric and window. A concurrency/median-ITL
proxy or DP projection cannot certify it. A full-window token rate must not be substituted
for a decode-only metric.

Before `MET`, submit equivalence against each selected profile attempt. Its observed
configuration and workload must match the profile. Passing short-context tests cannot
certify longer requested contexts. A later plan, entitlement, port, or kernel change
requires fresh profile evidence.

For "within N% of roofline," latency uses `actual/floor <= 1 + N/100`; throughput uses
`actual/floor >= 1 - N/100`. Record the raw quantities and denominator for each ratio.

Use the existing `scripts/safe_serve.py` launch/teardown lifecycle and retain each attempt's
endpoint, process identity, configuration, commands, and logs. Broad process-name kills
can terminate the agent or another attempt. Apply the remaining workload budget to the
campaign; do not introduce a separate writer execution timeout.

## User Control and Recovery

Use these commands rather than editing state:

```text
neuron-campaign status   --campaign <id> --project-root <root>
neuron-campaign report   --campaign <id> --project-root <root>
neuron-campaign pause    --campaign <id> --project-root <root> --mode after-task|now --idempotency-key <key>
neuron-campaign cancel   --campaign <id> --project-root <root> --task <task-id> --idempotency-key <key>
neuron-campaign resume   --campaign <id> --project-root <root> --idempotency-key <key>
neuron-campaign audit    --campaign <id> --project-root <root> --strict
neuron-campaign replay   --campaign <id> --project-root <root>
neuron-campaign finalize --campaign <id> --project-root <root> --idempotency-key <key>
```

`pause --mode after-task` admits the active specialist result and then pauses.
`pause --mode now` preserves a bound child for reconciliation; an unbound active task is
cancelled in campaign state. The native client separately stops the actual process when
needed. Resume verifies the event chain and admitted artifact digests.
Finalization requires reconciling the active attempt and submitting or cancelling it.
The native parent must independently ensure its child has stopped. A strict measured MET
still requires passing audit and matching correctness. NOT_YET, ANALYTIC_MISS and BLOCKED
may close their allocated run without becoming MET; their receipts retain observed gains,
correctness, unmet objectives and a resume action. Use `report` to generate the tracker from
admitted evidence. Preserve completed arms when a later task or cleanup fails.

Planner candidates carry `id`, positive integer `tp` and `dp`; their product must fit
`hardware.cores_per_node`. Profiling uses the currently entitled candidate and matching
TP/DP. A different serving cell requires a new plan and entitlement.

If a retired controller left state for this campaign ID, preserve it as history and use a
new campaign ID. Reuse raw files only through fresh specialist submissions and current
admission checks. The controller never resumes or converts a legacy verdict.
