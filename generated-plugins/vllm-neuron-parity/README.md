# vllm-neuron-parity

A Codex runtime binding for the vLLM-Neuron parity plugin. It brings the
vLLM-Neuron platform plugin (fork
`jinhuang12/vllm-neuron`) to feature parity with upstream GPU vLLM —
find the gaps, cost them, and close the approved ones with
evidence-backed pull requests, measured against a GPU baseline.

This README is a rendered view of the shipped package, never an
authority. The packaged v0 graph (`workflow.pave.yaml`), the Codex lead skill
(`skills/vllm-neuron-parity/SKILL.md`), and the native agent contracts stay
the source of truth; every section below links what it renders.

The original Claude lead source is retained under `claude/`, and the original
role sources remain in `agents/*.md`. They are provenance records. The Codex
runtime does not translate them at run time.

## 1. What it does, and for whom

For the maintainer of a vLLM-Neuron fork who needs upstream parity
closed methodically, not ad hoc. End to end: the workflow scans the
upstream delta per requested target, costs each closing route, ranks a
backlog, and asks you to pick campaigns (gate 1). Each approved
campaign is designed with pre-registered acceptance comparators
(gate 2), implemented in CPU-verifiable increments, brought up on
leased Neuron hardware, measured against the GPU baseline, adjudicated,
adversarially reviewed, and closed as an evidence-backed PR to the
fork, a no-benefit closure, or a blocked record (gate 3). Every closure
updates the committed cross-run scorecard, backlog, debt ledger, and
failure fingerprints.

Baseline and target pins are invocation-time inputs, so the workflow
survives re-baselines. PR merge stays human; fork sync stays yours.

### Installation

The plugin is the directory containing this README.

1. Add this package to a configured local Codex marketplace, then install it:

```bash
codex plugin add vllm-neuron-parity@<marketplace>
```

2. Install the six custom agents into the target project:

```bash
python3 /path/to/vllm-neuron-parity/codex/install_agents.py --project /path/to/target
```

3. Trust the target project. Accept the Codex trust prompt, or add this to
   `~/.codex/config.toml`:

```toml
[projects."/absolute/path/to/target"]
trust_level = "trusted"
```

4. Keep subagents enabled in the target project's `.codex/config.toml`:

```toml
[agents]
enabled = true
max_concurrent_threads_per_session = 6
```

5. Verify the installed agents, then restart Codex in the target project:

```bash
python3 /path/to/vllm-neuron-parity/codex/install_agents.py --project /path/to/target --check
```

6. Open `/hooks`, review the six
   registered controls, and trust them when their paths match this package.

Requirements are declared in the lead skill's `metadata.compatibility` field: trusted
Codex hooks, the installed `vllm-neuron-parity:*` custom agents, bash, and
Python 3.
Graph validation (`scripts/validate_pave.py`) additionally needs
`pyyaml` and `jsonschema` and fails closed without them. Run-state
validation (`scripts/validate_run_state.py`) uses `jsonschema` when present.
Without it, the dependency-free validator checks every schema keyword this
package declares.

Campaign stages additionally delegate to six Neuron skills that are NOT
shipped in this plugin and must be resolvable in the session that runs a
campaign: `vllm-neuron-feature-port`, `neuron-framework-equivalence`,
`neuron-nki-profile-querying`,
`experimental-neuron-framework-profiling-vllm-neuron`,
`experimental-neuron-framework-profile-analysis-vllm-neuron`, and
`experimental-neuron-autoport-compiler-debugging-vllm-neuron` (today:
the NeuronAgenticDevelopment workspace's `skills/` tree). The delta scan
through gate 1 needs none of them; a campaign whose delegate skill is
unavailable pauses and reports rather than substituting.

Start a run by invoking `$vllm-neuron-parity:vllm-neuron-parity` with the fork
path, the upstream pin, and the GPU baseline pin. Note (disclosed in
the skill description): the stop guard hook blocks at most one stop in
three while a run is active.

## 2. Workflow summary and visual

At a glance — plain-language stage groupings (not graph node ids);
loops and recovery routes omitted here, faithful diagrams below:

```mermaid
flowchart LR
  A[Intake and pin freeze] --> B[Upstream delta scan]
  B --> C[Costing and ranked backlog]
  C --> D{{Gate 1: pick campaigns}}
  D --> E[Campaign design]
  E --> F{{Gate 2: approve design}}
  F --> G[CPU implementation loop]
  G --> H[Neuron hardware bring-up]
  H --> I[Measure vs GPU baseline]
  I --> J[Adjudicate and review]
  J --> K{{Gate 3: PR / close}}
  K --> L[Scorecard update]
  class A,B,C cInv
  class E,G,H cImpl
  class I cMeas
  class J cAdj
  class D,F,K,L cGate
  classDef cInv fill:#cfe2ff,stroke:#1971c2,color:#000
  classDef cImpl fill:#ffd8a8,stroke:#e8590c,color:#000
  classDef cMeas fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef cAdj fill:#e5dbff,stroke:#7048e8,color:#000
  classDef cGate fill:#e9ecef,stroke:#495057,color:#000
```

Stage boxes are tinted by the agent that dominates the stage (color
key below).

The full graph has 31 nodes and 89 edges, so it is rendered as six
stage sub-diagrams. Rectangles are graph nodes labeled with node ids;
hexagons are user gates presented by a node (gates 1 and 3 — gate 2 is
a check on the `design_sound` edge, so it appears as edge text in
§2.2); stadiums are run endpoints; a rounded node names the
sub-diagram an edge continues in. Every edge carries its outcome
label. Source: `workflow.pave.yaml`.

Color code — node fill = the agent seat that runs the node, as bound
in the §4 table. That is usually the graph's first-listed role, with
two deliberate exceptions: `rederive_approach` carries the dedicated
rederiver seat (an agent binding, not a graph role), and
`verify_run_preconditions` is tinted for its investigating seat rather
than the lead that merely freezes state:

| Color | Agent / meaning |
|---|---|
| blue | investigator (intake, delta scan, costing, design screen) |
| orange | implementer (design drafting, increments, hardware, PR) |
| green | measurer (procedures, baseline, runs, stabilize) |
| purple | adjudicator (verdicts, run-closure verification) |
| pink | adversarial-reviewer (the five review nodes) |
| red | rederiver (approach re-derivation — the recovery seat) |
| gray | lead/user gates, stage-level pointers, and run endpoints |

Dashed red edges are repair and recovery routing — backward re-entry
loops and every route into or out of `rederive_approach`, plus the
`recover_leased_host` loop. Solid edges are the forward path. A
rounded pointer naming a single node carries that node's seat color; a
pointer naming a whole stage stays gray. Styling is presentation only;
the graph stays the authority for nodes and edges.

### 2.1 Intake, delta scan, costing, gate 1

```mermaid
flowchart TD
  vrp[verify_run_preconditions] -->|preconditions_met, per requested target| ttd[trace_target_delta]
  vrp -->|inputs_missing| paused([run_paused])
  ttd -->|target_traced| adr[assemble_delta_report]
  ttd -->|source_unreachable| adr
  adr -->|reports_insufficient, per deficient target| ttd
  adr -->|delta_mapped| cost[cost_routes_and_rank_backlog]
  adr -->|sources_unreachable| paused
  cost -->|backlog_ranked| rrv[review_route_verdicts]
  cost -->|evidence_gap| adr
  cost -->|costing_stalled| paused
  rrv -->|verdicts_sound| akc{{assemble_kickoff_contracts}}
  rrv -->|material_findings| cost
  akc -->|contracts_ready, per approved campaign| design(2.2 design)
  akc -->|no_campaign_approved| aborted([run_aborted])
  class vrp,ttd,adr,cost cInv
  class rrv cRev
  class akc,design cGate
  class paused,aborted cEnd
  classDef cInv fill:#cfe2ff,stroke:#1971c2,color:#000
  classDef cRev fill:#fcc2d7,stroke:#d6336c,color:#000
  classDef cGate fill:#e9ecef,stroke:#495057,color:#000
  classDef cEnd fill:#dee2e6,stroke:#868e96,color:#000
  linkStyle 4,8,11 stroke:#c92a2a,stroke-dasharray:4
```

Dashed: the three repair re-entry loops (re-trace deficient targets,
re-assemble on evidence gap, re-cost on material findings).

### 2.2 Campaign design, gate 2

```mermaid
flowchart TD
  spp[screen_pin_and_progress] -->|screen_passed| dip[draft_increment_plan]
  spp -->|pin_infeasible| adrec[assemble_design_record]
  spp -->|progress_exhausted| rede(2.6 rederive_approach)
  dip -->|plan_drafted, upgrade route| arm[assemble_regression_matrix]
  dip -->|plan_drafted, non-upgrade route| pra[preregister_acceptance]
  dip -->|scope_exceeded| rede
  arm -->|matrix_assembled| pra
  arm -->|matrix_blocked| rede
  arm -->|scope_exceeded| rede
  pra -->|acceptance_preregistered| adrec
  pra -->|criteria_unadjudicable| rede
  adrec -->|record_ready| rcd[review_campaign_design]
  adrec -->|infeasibility_recorded| close(2.6 close_campaign)
  adrec -->|record_incomplete| spp
  adrec -->|record_incomplete| dip
  adrec -->|record_incomplete| arm
  adrec -->|record_incomplete| pra
  adrec -->|record_incomplete, repairs exhausted| rede
  rcd -->|design_sound + user gate 2| impl(2.3 implementation)
  rcd -->|material_findings| spp
  class spp cInv
  class dip,arm,pra,adrec cImpl
  class rcd cRev
  class rede cRec
  class close,impl cGate
  classDef cInv fill:#cfe2ff,stroke:#1971c2,color:#000
  classDef cImpl fill:#ffd8a8,stroke:#e8590c,color:#000
  classDef cRev fill:#fcc2d7,stroke:#d6336c,color:#000
  classDef cRec fill:#ffc9c9,stroke:#c92a2a,color:#000
  classDef cGate fill:#e9ecef,stroke:#495057,color:#000
  linkStyle 2,5,7,8,10,13,14,15,16,17,19 stroke:#c92a2a,stroke-dasharray:4
```

Dashed: the record_incomplete repair fan, review findings re-entry,
and every breaker route into re-derivation.

### 2.3 CPU implementation loop

```mermaid
flowchart TD
  sni[scope_next_increment] -->|increment_selected| ri[realize_increment]
  ri -->|increment_passed| sni
  ri -->|increment_stuck| sni
  ri -->|evidence_contradicts_design| design(2.2 screen_pin_and_progress)
  sni -->|plan_satisfied| rc[record_changeset]
  sni -->|plan_unrealizable_as_designed| design
  sni -->|plan_exceeds_node| design
  sni -->|no_new_route| rede(2.6 rederive_approach)
  rc -->|changeset_recorded| rimp[review_implementation]
  rc -->|coverage_gap_found| sni
  rimp -->|ready_for_hardware| hw(2.4 hardware)
  rimp -->|material_findings| sni
  class sni,ri,rc cImpl
  class rimp cRev
  class design cInv
  class rede cRec
  class hw cGate
  classDef cInv fill:#cfe2ff,stroke:#1971c2,color:#000
  classDef cImpl fill:#ffd8a8,stroke:#e8590c,color:#000
  classDef cRev fill:#fcc2d7,stroke:#d6336c,color:#000
  classDef cRec fill:#ffc9c9,stroke:#c92a2a,color:#000
  classDef cGate fill:#e9ecef,stroke:#495057,color:#000
  linkStyle 2,3,5,6,7,9,11 stroke:#c92a2a,stroke-dasharray:4
```

Dashed: stuck/repair loops and design re-entry; the increment_passed
loop stays solid — it is the stage's normal forward cycle. The design
pointer is blue because its target (screen_pin_and_progress) is
investigator-run.

### 2.4 Neuron hardware bring-up

```mermaid
flowchart TD
  ahl[acquire_hardware_lease] -->|lease_held| rcv[replicate_campaign_venv]
  ahl -->|no_host_available| close(2.6 close_campaign)
  rcv -->|venv_ready| eal[execute_attempt_loop]
  rcv -->|replication_failed| rede(2.6 rederive_approach)
  rcv -->|host_faulted| rlh[recover_leased_host]
  eal -->|candidate_serving| meas(2.5 measurement)
  eal -->|breaker_tripped| rede
  eal -->|host_faulted| rlh
  rlh -->|host_restored| rcv
  rlh -->|host_unrecoverable| ahl
  class ahl,rcv,eal,rlh cImpl
  class rede cRec
  class close,meas cGate
  classDef cImpl fill:#ffd8a8,stroke:#e8590c,color:#000
  classDef cRec fill:#ffc9c9,stroke:#c92a2a,color:#000
  classDef cGate fill:#e9ecef,stroke:#495057,color:#000
  linkStyle 3,4,6,7,8,9 stroke:#c92a2a,stroke-dasharray:4
```

Dashed: the host-fault/recovery loop and both breaker routes.
recover_leased_host stays implementer-orange — it is a recovery
procedure but an implementer-run node; the recovery LOOP is what the
dashed edges mark.

### 2.5 Measurement vs GPU baseline

```mermaid
flowchart TD
  rmp[realize_measurement_procedures] -->|procedures_ready| cbr[capture_baseline_reference]
  rmp -->|procedure_unrealizable| rede(2.6 rederive_approach)
  cbr -->|reference_captured| rcm[run_candidate_measurements]
  cbr -->|baseline_unusable| rede
  rcm -->|runs_complete| spe[stabilize_and_package_evidence]
  rcm -->|procedure_defect_found| rmp
  rcm -->|reference_defect_found| cbr
  rcm -->|serving_exhausted| rede
  spe -->|bundles_stable| adj(2.6 adjudicate_results)
  spe -->|collection_defect_found| rcm
  spe -->|declared_measurement_unproducible| rede
  class rmp,cbr,rcm,spe cMeas
  class rede cRec
  class adj cAdj
  classDef cMeas fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef cRec fill:#ffc9c9,stroke:#c92a2a,color:#000
  classDef cAdj fill:#e5dbff,stroke:#7048e8,color:#000
  linkStyle 1,3,5,6,7,9,10 stroke:#c92a2a,stroke-dasharray:4
```

Dashed: the three defect re-entry loops and the four budget/breaker
routes into re-derivation.

### 2.6 Adjudication, PR, closure, re-derivation, gate 3

```mermaid
flowchart TD
  adj[adjudicate_results] -->|verdict_recorded| rmv[review_measurement_verdict]
  adj -->|evidence_unstable| meas(2.5 run_candidate_measurements)
  adj -->|no_progress| rede[rederive_approach]
  rmv -->|pass_confirmed| ppr[prepare_pr]
  rmv -->|correctness_shortfall_confirmed| impl(2.3 scope_next_increment)
  rmv -->|regression_confirmed| impl
  rmv -->|no_benefit_confirmed| cc{{close_campaign}}
  rmv -->|material_findings| adj
  ppr -->|pr_package_ready| rpe[review_pr_evidence]
  ppr -->|evidence_gap_found| meas
  ppr -->|no_progress| rede
  rpe -->|pr_ready| cc
  rpe -->|material_findings| ppr
  cc -->|closure_recorded| vrc[verify_run_closure]
  cc -->|closure_declined| rede
  rede -->|revised_approach| design(2.2 screen_pin_and_progress)
  rede -->|close_out_recommended| cc
  vrc -->|closure_unverified, per discrepant campaign| cc
  vrc -->|run_closed_complete| done([run_complete])
  vrc -->|campaigns_remaining| joinp([await_remaining_closures])
  vrc -->|resumable_stop| paused([run_paused])
  vrc -->|no_route_remains| blocked([run_blocked])
  class adj,vrc cAdj
  class rmv,rpe cRev
  class ppr,impl cImpl
  class rede cRec
  class cc cGate
  class meas cMeas
  class design cInv
  class done,joinp,paused,blocked cEnd
  classDef cInv fill:#cfe2ff,stroke:#1971c2,color:#000
  classDef cImpl fill:#ffd8a8,stroke:#e8590c,color:#000
  classDef cMeas fill:#d3f9d8,stroke:#2f9e44,color:#000
  classDef cAdj fill:#e5dbff,stroke:#7048e8,color:#000
  classDef cRev fill:#fcc2d7,stroke:#d6336c,color:#000
  classDef cRec fill:#ffc9c9,stroke:#c92a2a,color:#000
  classDef cGate fill:#e9ecef,stroke:#495057,color:#000
  classDef cEnd fill:#dee2e6,stroke:#868e96,color:#000
  linkStyle 1,2,4,5,7,9,10,12,14,15,16,17 stroke:#c92a2a,stroke-dasharray:4
```

Dashed: verdict/PR findings loops, both no_progress breakers, and the
re-derivation node's own routes (its revised_approach return to design
is the recovery loop closing). The meas/impl/design pointers carry
their target nodes' agent colors.

## 3. File structure

The shipped package — a native Codex plugin:

```
vllm-neuron-parity/
  .codex-plugin/plugin.json            # Codex plugin manifest
  .claude-plugin/plugin.json           # retained source-runtime manifest
  hooks/
    hooks.json                         # hook registration (removed from the
                                       #   shipped source by user decision;
                                       #   installed copies may retain it)
    pre_tool_use_router.py             # active-run scope adapter for P1-P3
    dispatch_advisory.py               # advisory-only re-entry dispatch check
                                       #   (PreToolUse Agent|Task, lead-gated)
  codex/
    install_agents.py                  # explicit safe custom-agent installer
    init_evolution_workspace.py        # durable project-local lineage initializer
    agents/                            # complete native role contracts
      vllm_neuron_parity_investigator.toml
      vllm_neuron_parity_implementer.toml
      vllm_neuron_parity_measurer.toml
      vllm_neuron_parity_adjudicator.toml
      vllm_neuron_parity_adversarial_reviewer.toml
      vllm_neuron_parity_rederiver.toml
  skills/vllm-neuron-parity/           # standard Codex skill location
    SKILL.md                            # native Codex lead workflow skill
    hooks/
      protected-branch-guard.sh        # blocks pushes to protected branches
      compile-cache-guard.sh           # blocks Neuron compile-cache clears
      venv-opt-guard.sh                # blocks venv cloning / /opt writes
      state-staleness-reminder.sh      # re-presents run position periodically (lead-session-gated)
      stop-guard.sh                    # blocks at most 1 stop in 3 while a run is active (lead-session-gated)
  agents/*.md                          # preserved original role contracts
  claude/skills/vllm-neuron-parity/    # retained original lead source
  workflow.pave.yaml                   # immutable packaged graph seed (approved v0)
  workflow-manifest.yaml               # immutable packaged v0 lineage seed
  history/                             # immutable packaged pre-freeze placeholder
  references/
    artifact-layout.md                 # single authority for artifact shapes
    measurement-pitfalls.md            # known measurement-tool traps
    patch-mechanism-inventory.md       # how the plugin patches vLLM
    collision-ranking.md               # file surfaces where ports collide
    pave.schema.json                   # PAVE graph schema (validator input)
  schemas/run-state.schema.json        # single authority for run-state shape
  scripts/
    validate_run_state.py              # run-state checker
    validate_pave.py                   # graph checker
    freeze_revision.py                 # evolving-tier freeze tool
  tests/
    test_run_state_schema.py           # schema accept/reject tests
    test_workflow_pave.py              # shipped-graph validity test
  README.md                            # this file — rendered view, never authority
  VERSION                              # package changelog
```

Revision machinery: the shipped `workflow.pave.yaml` is the immutable approved
`v0` seed. Before the first real execution, the Codex lead initializes
`<project>/.vllm-neuron-parity/evolution/` and freezes `v1` there per the
evolution contract in the native `SKILL.md`. Frozen revisions and the active
manifest stay in that durable project-local directory, so reinstalling the
plugin does not erase lineage. Package versions in
`VERSION` are separate — they track what a user of the plugin would
notice changed.

## 4. Specialized agents

Source: the agent contracts under `codex/agents/` and the dispatch table in the
native `SKILL.md`.

| Agent | Color (§2) | Role | Model | Key constraint (what it cannot do) |
|---|---|---|---|---|
| (lead = SKILL.md itself) | gray | routing, gates, state writes | session | sole writer of run state and cross-run artifacts; never measures or adjudicates |
| investigator | blue | intake, delta scan, costing, design screen | gpt-5.6-sol | read-only on the fork; cannot approve its own verdicts |
| implementer | orange | design drafting, increments, hardware attempts, PR package | gpt-5.6-sol (xhigh on the attempt loop) | never edits comparators; cannot merge PRs; hardware writes confined to lease/venv/worktree scope |
| measurer | green | procedures, baseline capture, runs, stabilize | gpt-5.6-sol (gpt-5.6-terra at stabilize) | executes what the design record froze — never chooses or alters a comparator; no verdicts |
| adjudicator | purple | verdicts, run-closure verification | gpt-5.6-sol | never produces the evidence it judges (measurer_not_adjudicator check) |
| adversarial-reviewer | pink | all five review nodes | gpt-5.6-sol | reviews only; fresh seat per gate round; cannot repair what it reviews |
| rederiver | red | approach re-derivation after breakers | gpt-5.6-sol, xhigh | read-only inputs; its output re-enters design, it never implements. On an intermittent spawn failure the lead retries identically, never downgrades the model, and pauses for the operator after three identical failures — an undispatchable seat would dead-end all sixteen recovery routes |

The lead pauses if a `vllm-neuron-parity:*` agent type is unavailable —
it never substitutes an ordinary worker.

Dispatch mechanics: each node instance's primary seat runs as one retained
Codex custom-agent thread, started with `spawn_agent`, continued with
`followup_task`, awaited with `wait_agent`, and interrupted if still running
when the node closes.
Doer threads continue through repair rounds; reviewer threads stay fresh per
gate round. One-shot sub-agents remain for approved internal fan-out.
Continuity changes no authority: the lead stays the single state writer;
agents never traverse edges or present gates; forbidden effects inherit into
every spawn; a peer message never grants a permission escalation.

## 5. Hooks and enforcement

Source: the native `SKILL.md` (prohibitions P1–P13 and transition guards), the
active-run adapter and the re-entry dispatch advisory under `hooks/`, and the five policy scripts under
`skills/vllm-neuron-parity/hooks/`. Rungs,
weakest to strongest: prose < reinjection < reviewed < mechanical
< blocking hook.

| Rule | Rung | Why that rung |
|---|---|---|
| Never mutate protected branches (release-0.24.0.1.1.0, release-0.21.0.1.0.0, main, mainline — exact names) | BLOCKING hook | likely, costly, irreversible, precisely detectable. Disclosed residual: the no-refspec `git push` arm resolves the current branch in the payload's cwd and fails open — a push that changes directory (`cd … && git push`, `git -C … push`) or a non-default `push.default` can evade it; explicit-refspec and mutation forms are exact matches, and contract text plus the next review gate back the hook |
| Never clear the shared Neuron compile cache | BLOCKING hook + delegate wrapper | documented remedies include cache-clearing, so delegates will try it; hours of recompile for every tenant |
| No `cp -a` venv cloning; no pip writes into /opt | BLOCKING hook | dead-end pressure makes the shortcut likely; /opt damage breaks co-tenants |
| Zero NxDI imports in ported code | MECHANICAL scan (diff-scoped) | exact over added/modified lines; runs before the review gate |
| GPU baseline read-only; no autonomous reboot; durable-host-state scoping | prose + reinjection + mechanical identity/skew probes | a hook cannot see remote SSH side effects; capture refuses on contradiction |
| Benchmark skill's provisioning STOP gate never removed | prose + reinjection | text edit, cheap to catch at review, reversible |
| PRs only to the fork; merge stays human | MECHANICAL (PR URL verified on the fork) + capability absence | merge authority is never granted — stronger than any check |
| No identical hardware retry | MECHANICAL fingerprint gate | fingerprint equality is exact; pre-attempt precondition |
| Comparators frozen before measurement | MECHANICAL timestamp check | registration-timestamp arithmetic is exact |
| Lead single-writer for run state, cross-run artifacts, leases | STRUCTURE + schema validation | ownership-by-structure beats detection; budgets are derived from files, never stored |
| Measured revision = git-issued id, never a branch name | MECHANICAL check | exact string-shape + agreement test |
| Two-tier repair budgets and breakers (measure three/nine; hardware ten + one recovery) | BLOCKING routing preconditions | counts derived from event files; runaway loops are the costliest failure |
| Lead-alignment hook pair (staleness reminder + stop guard) | reinjection | long-horizon, session-crossing workflow — the pair's target case; the stop guard **blocks at most one stop in three** while a run is active, disclosed in the skill description. Both hooks gate on the lead session id in `<run-state>.lead-session` and stay silent in every other session; without the sidecar they fail open |
| Re-entry dispatch advisory (`hooks/dispatch_advisory.py`) | reinjection (advisory `additionalContext`, never blocks) | edge-triggered: fires only when a dispatch names an instrumented design node that already completed a traversal this run, and asks whether the graph's cheaper re-entry instrument settles it without a seat; lead-session-gated, throttled per node via its own counter file |
| New kernel-class functionality must be NKI, never torch fallback (kernel-substrate rule) | MECHANICAL (every increment must declare kernel-class or not — no silent omission; a declared-NKI increment with zero NKI usage in its diff is an exact contradiction caught at the changeset scan) + REVIEWED (both gates challenge the classification itself) | "what is kernel-class" is judgment no scan decides, and absence-of-torch scans false-fire on kernels' legitimate torch boundaries — so the mechanical half checks presence against the doer's own declaration, and review owns only the classification |

`SKILL.md` carries 13 run-wide prohibitions and 6 transition guards in
total; this table shows the strongest rows, and every rule not shown
sits at a weaker rung with its rationale in the skill and agent
contracts. `hooks/hooks.json` registers the six controls at plugin scope (the three
P1-P3 guards, the lead-alignment pair, and the re-entry dispatch advisory);
the registration file was removed from the shipped source by user decision,
so a fresh install ships the scripts unregistered until re-registered.
P1-P3 fail open unless `.vllm-neuron-parity-run` points to active nonterminal
state, so they do not block unrelated Codex work.
Nothing registers silently.

## 6. Appendix — the shipped authorities

- `workflow.pave.yaml` — the immutable approved v0 seed (31 nodes, 89 edges,
  12 checks, 24 evidence definitions, 5 endpoints; validates clean
  with `scripts/validate_pave.py`). A project's ACTIVE revision lives in its
  evolution root and may carry ledgered in-place amendments
  (`binding-revisions.yaml`); read live counts from the validator, not from here.
- `skills/vllm-neuron-parity/SKILL.md` — the Codex lead: routing, gates,
  state writes, recovery loop, evolution contract (nine rules, including the
  usage ledger and the binding-revision lane ledgered in the evolution root's
  `binding-revisions.yaml`).
- `codex/agents/vllm_neuron_parity_*.toml` — the six complete native
  custom-agent contracts installed by `codex/install_agents.py`.
- `schemas/run-state.schema.json` — the run-state shape authority;
  check an instance with `scripts/validate_run_state.py`.
- `references/artifact-layout.md` — artifact tree, write ownership,
  precedence, supersession rules for a live run.
- `references/measurement-pitfalls.md` — measurement-tool traps the
  measurer must pre-empt.
- `references/patch-mechanism-inventory.md` — how the vllm-neuron
  plugin patches vLLM, and where a ported change lands.
- `references/collision-ranking.md` — which fork files concurrent
  campaigns collide on, and the serialization rules.
