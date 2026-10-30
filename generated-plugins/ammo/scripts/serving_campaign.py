#!/usr/bin/env python3
"""Serving evidence binding for the existing AMMO state machine.

This module does not advance stages, create agents, or grant audits. The shared
state engine keeps those responsibilities. It supplies the domain-specific
measurements that batch-latency gates cannot interpret.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Literal

from pydantic import Field, StrictBool, StrictFloat, StrictStr

from serving_contract import (
    Arm, Closed, ContractError, ServingSpec, SHA, contained, digest, file_digest,
    load_spec, read_json, write_json,
)
from serving_evidence import incumbent_path, verify_result, verify_seal
from serving_metrics import improvement, number, summarize_window

STAGES = ('1_baseline', '2_bottleneck_mining', '3_debate', '4_5_parallel_tracks',
          '6_integration', '7_campaign_eval', '7b_report')
TERMINAL = {'PASS', 'GATED_PASS', 'FAIL'}
SHIP = {'single_pass', 'combined', 'gated_pass'}


class FileRef(Closed):
    path: StrictStr = Field(min_length=1)
    sha256: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')


class EvidenceRef(Closed):
    """A directory reference binds its complete seal, not a mutable summary."""
    path: StrictStr = Field(min_length=1)
    sha256: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')


class Opportunity(Closed):
    id: StrictStr = Field(min_length=1)
    role: Literal['prefill', 'decode', 'handoff', 'router']
    scope: Literal['bound', 'production_boundary', 'clean_e2e']
    upper_bound_improvement_pct: StrictFloat | None = Field(default=None, ge=0)
    evidence: list[FileRef] = Field(min_length=1)
    derivation: StrictStr = Field(min_length=1)


class Mining(Closed):
    schema_version: Literal['ammo_serving_mining/1'] = 'ammo_serving_mining/1'
    contract_sha256: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    incumbent_sha256: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    objective: StrictStr
    coverage_complete: StrictBool
    opportunities: list[Opportunity] = Field(min_length=1)
    # Under-load capacity/queueing claims require a measured serving boundary.
    # No serial Amdahl formula is supplied by this binding.
    workload_ids: list[StrictStr] = Field(min_length=1)


class FailureClosure(Closed):
    schema_version: Literal['ammo_serving_failure_closure/1'] = 'ammo_serving_failure_closure/1'
    op_id: StrictStr
    result_sha256: StrictStr = Field(pattern=r'^[a-f0-9]{64}$')
    attempted_repairs: list[FileRef] = Field(min_length=1)
    fallback_disposition: StrictStr = Field(min_length=1)
    unresolved_actions: list[StrictStr]
    exhausted: Literal[True]
    # The independent Stage-45 audit still reviews this authored disposition.


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def binding(spec: ServingSpec) -> dict:
    return {'schema_version': 'ammo_serving_campaign/1',
            'contract_sha256': spec.checksum, 'objective': spec.policy.objective,
            'min_improvement_pct': spec.policy.min_improvement_pct}


def file_ref(root: Path, path: Path) -> dict:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ContractError('campaign evidence must be a file inside its artifact tree')
    return {'path': str(path.relative_to(root.resolve())), 'sha256': file_digest(path)}


def evidence_ref(root: Path, path: Path, spec: ServingSpec) -> dict:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ContractError('serving result must stay inside its campaign artifact tree')
    return {'path': str(path.relative_to(root.resolve())), 'sha256': digest(verify_seal(path, spec))}


def resolve_file(root: Path, value: dict) -> Path:
    ref = FileRef.model_validate(value)
    path = contained(root, ref.path)
    if not path.is_file() or file_digest(path) != ref.sha256:
        raise ContractError(f'changed or missing campaign artifact: {ref.path}')
    return path


def resolve_result(root: Path, value: dict, spec: ServingSpec) -> Path:
    ref = EvidenceRef.model_validate(value)
    path = contained(root, ref.path)
    if digest(verify_seal(path, spec)) != ref.sha256:
        raise ContractError(f'changed or missing serving result: {ref.path}')
    return path


def current_round(state: dict) -> dict:
    c = state['campaign']
    return c['rounds'][c['current_round'] - 1]


def round_baseline(state: dict, root: Path, spec: ServingSpec, index: int | None = None) -> Path:
    """Verify the baseline chain from Round 1; never accept a stale predecessor."""
    rounds = state['campaign']['rounds']
    index = state['campaign']['current_round'] - 1 if index is None else index
    if index < 0 or index >= len(rounds):
        raise ContractError('serving round is out of range')
    first = (rounds[0].get('serving') or {}).get('baseline')
    if not first:
        raise ContractError('serving baseline has not been captured and synchronized')
    path = resolve_result(root, first, spec)
    if verify_result(path, spec)['status'] != 'BASELINE':
        raise ContractError('Round 1 must bind a verified initial baseline')
    previous_ref = first
    for n in range(1, index + 1):
        previous = rounds[n - 1]
        if previous.get('status') == 'SHIPPED':
            ref = (previous.get('serving') or {}).get('integration')
            if not ref:
                raise ContractError('a shipped round has no integrated serving result')
            new = resolve_result(root, ref, spec)
            result = verify_result(new, spec, path)
            if result['status'] not in {'PASS', 'GATED_PASS'}:
                raise ContractError('promoted serving integration does not pass')
            if read_json(new / 'seal.json').get('kind') != 'integration':
                raise ContractError('a candidate result cannot replace integration validation')
            path, previous_ref = new, ref
        if (rounds[n].get('serving') or {}).get('baseline') != previous_ref:
            raise ContractError('new serving round does not use the exact incumbent')
    return path


def mining_result(value: dict, root: Path, spec: ServingSpec, baseline: Path) -> dict:
    path = resolve_file(root, value)
    mined = Mining.model_validate(read_json(path))
    if (mined.contract_sha256 != spec.checksum
            or mined.incumbent_sha256 != digest(verify_seal(baseline, spec))
            or mined.objective != spec.policy.objective):
        raise ContractError('serving mining has a stale objective or incumbent')
    if len(set(mined.workload_ids)) != len(mined.workload_ids) or set(mined.workload_ids) != {w.id for w in spec.workloads}:
        raise ContractError('serving mining must cover all frozen workloads')
    ids = [r.id for r in mined.opportunities]
    if len(set(ids)) != len(ids):
        raise ContractError('duplicate serving opportunity')
    for row in mined.opportunities:
        for evidence in row.evidence:
            resolve_file(root, evidence.model_dump())
    roles = {row.role for row in mined.opportunities}
    values = [row.upper_bound_improvement_pct for row in mined.opportunities]
    known = (mined.coverage_complete and roles == {'prefill', 'decode', 'handoff', 'router'}
             and all(value is not None for value in values))
    top = max(values) if known else None
    decision = ('UNRESOLVED' if top is None else
                'STOP' if top < spec.policy.min_improvement_pct else 'CONTINUE')
    return {'decision': decision, 'top_addressable_objective_pct': top,
            'objective': spec.policy.objective, 'opportunities': [r.model_dump() for r in mined.opportunities]}


def failure_closed(track: dict, op_id: str, root: Path) -> None:
    ref = track.get('serving_failure_closure')
    if not ref:
        raise ContractError(f'{op_id}: record repairs and fallback disposition before terminal FAIL')
    raw = FailureClosure.model_validate(read_json(resolve_file(root, ref)))
    if (raw.op_id != op_id or raw.result_sha256 != track['serving_result']['sha256']
            or raw.unresolved_actions):
        raise ContractError(f'{op_id}: failure closure is stale or has unresolved actions')
    for attempted in raw.attempted_repairs:
        resolve_file(root, attempted.model_dump())


def track_result(state: dict, root: Path, op_id: str, *, require_terminal: bool = True,
                 monitor: bool = True) -> dict:
    spec = load_spec(root / 'target.json')
    rnd = current_round(state)
    tracks = (rnd.get('parallel_tracks') or {}).get('tracks') or {}
    if op_id not in tracks:
        raise ContractError('serving result is not assigned to a selected implementation track')
    track = tracks[op_id]
    ref = track.get('serving_result')
    if not ref:
        raise ContractError(f'{op_id}: no sealed serving result')
    result_dir = resolve_result(root, ref, spec)
    result = verify_result(result_dir, spec, round_baseline(state, root, spec))
    if read_json(result_dir / 'seal.json').get('kind') not in {'candidate', 'candidate_failure'}:
        raise ContractError('track result must be candidate evidence')
    if require_terminal:
        if track.get('status') != result['status'] or track.get('verdict') != result['status']:
            raise ContractError(f'{op_id}: state verdict differs from recomputed serving evidence')
        if result['status'] == 'FAIL':
            failure_closed(track, op_id, root)
    if monitor:
        from verify_validation_gates import _verify_monitor_evidence
        _verify_monitor_evidence(root, state['campaign']['current_round'], op_id, track)
    return result


def objective_values(directory: Path, spec: ServingSpec) -> dict[str, float]:
    """Read already verified clean measurements; never multiply round deltas."""
    baseline = read_json(directory / 'seal.json')['kind'] == 'baseline'
    windows = read_json(directory / ('baseline_windows.json' if baseline else 'paired_windows.json'))['windows']
    values = {w.id: [] for w in spec.workloads}
    by_id = {w.id: w for w in spec.workloads}
    for item in windows:
        raw = read_json(contained(directory, item['raw' if baseline else 'candidate']))
        value = summarize_window(raw, by_id[item['workload_id']], spec.policy)[spec.policy.objective]
        values[item['workload_id']].append(value)
    return {k: mean(v) for k, v in values.items()}


def accounting(state: dict, root: Path, spec: ServingSpec, integrated: Path) -> dict:
    original = round_baseline(state, root, spec, 0)
    old, new = objective_values(original, spec), objective_values(integrated, spec)
    return {'objective': spec.policy.objective, 'method': 'direct original-to-current ratio per frozen workload',
            'original': (state['campaign']['rounds'][0]['serving']['baseline']),
            'current': evidence_ref(root, integrated, spec),
            'workloads': {key: {'original': old[key], 'current': new[key],
                               'improvement_pct': improvement(old[key], new[key], spec.policy.objective)} for key in old}}


def integration_result(state: dict, root: Path, spec: ServingSpec, *, require_promoted: bool = False) -> dict:
    rnd = current_round(state)
    ref = (rnd.get('serving') or {}).get('integration')
    if not ref:
        raise ContractError('no sealed serving integration result')
    directory = resolve_result(root, ref, spec)
    result = verify_result(directory, spec, round_baseline(state, root, spec))
    record = read_json(directory / 'seal.json')
    if record.get('kind') != 'integration' or result['status'] not in {'PASS', 'GATED_PASS'}:
        raise ContractError('serving integration did not pass the complete deployment gates')
    # Composition is a frozen input recorded by the runner, never a report added
    # after timing. It names the exact validated track evidence chosen for merge.
    if 'composition.json' not in record['files']:
        raise ContractError('integration lacks its sealed candidate composition')
    composition = read_json(directory / 'composition.json')
    selected = composition.get('selected_results')
    if not isinstance(selected, dict) or not selected:
        raise ContractError('empty integration composition')
    if set(selected) != set((rnd.get('integration') or {}).get('selected_candidates') or []):
        raise ContractError('integrated candidates differ from the selected composition')
    for op_id, checksum in selected.items():
        track = (rnd['parallel_tracks']['tracks']).get(op_id)
        if not track or track.get('status') not in {'PASS', 'GATED_PASS'} or checksum != track['serving_result']['sha256']:
            raise ContractError('integration includes an unvalidated or failed track')
        track_result(state, root, op_id)
    if require_promoted:
        promoted = incumbent_path(root / 'serving' / 'incumbent.json', spec)
        if promoted != directory.resolve():
            raise ContractError('campaign SHIP requires the exact integrated serving incumbent')
        if set(rnd.get('shipped') or []) != set(selected):
            raise ContractError('SHIP list differs from the measured composition')
    return {**result, 'accounting': accounting(state, root, spec, directory)}


def validate_candidate_scores(state: dict, root: Path, spec: ServingSpec) -> None:
    rnd = current_round(state)
    for candidate in (rnd.get('debate') or {}).get('selected_candidates') or []:
        score = candidate.get('score_breakdown') or {}
        if score.get('expected_e2e_pct') is not None or candidate.get('projected_e2e_improvement_pct') is not None:
            raise ContractError('serving candidate must use objective units, not batch E2E magnitude')
        expected = number(score.get('expected_objective_pct'), 'expected objective improvement')
        if expected < spec.policy.min_improvement_pct:
            raise ContractError('serving projection is below the frozen objective improvement floor')
        if spec.policy.objective != 'goodput_rps' and expected > 100:
            raise ContractError('a latency-reduction projection cannot exceed 100 percent')
        feasibility = number(score.get('feasibility'), 'feasibility')
        weighted = number(score.get('weighted_total'), 'weighted objective EV')
        if feasibility > 10 or abs(weighted - feasibility / 10 * expected) > max(.05, weighted * .01):
            raise ContractError('serving candidate EV does not match its objective projection')
        if score.get('evidence_scope') not in {'bound', 'production_boundary', 'clean_e2e'}:
            raise ContractError('serving proxy magnitude cannot rank a candidate')
        if not candidate.get('category') or candidate.get('selection_mode') not in {'ordinary', 'long_shot', 'contingent_host_spike'}:
            raise ContractError('serving candidate needs its category and selection mode')
        if candidate.get('selection_mode') == 'long_shot' and (
                feasibility < 3 or not candidate.get('stage_4_validation_obligations')
                or candidate['stage_4_validation_obligations'][0] != 'production_boundary_spike'):
            raise ContractError('serving long_shot must retain the P-score and first-boundary-spike contract')
        if candidate.get('selection_mode') == 'contingent_host_spike':
            raise ContractError('serial host-slice spike is batch-only; use long_shot with an actual serving boundary')
        refs = candidate.get('serving_projection_evidence')
        if not isinstance(refs, list) or not refs:
            raise ContractError('serving candidate needs hash-bound projection evidence')
        for ref in refs:
            resolve_file(root, ref)


def violation(state: dict, root: Path, prev: dict | None = None) -> str | None:
    """Validate serving-specific invariants, then let ordinary audits still run."""
    try:
        target = read_json(root / 'target.json')
        enabled = 'serving' in target
        if not enabled:
            if state.get('serving') or (prev and prev.get('serving')):
                raise ContractError('cannot drop the frozen serving target')
            return None
        spec = load_spec(root / 'target.json')
        if state.get('serving') != binding(spec) or (prev and prev.get('serving') != state.get('serving')):
            raise ContractError('serving state binding differs from the frozen target')
        if state.get('target', {}).get('model_id') != spec.model_id or state.get('target', {}).get('dtype') != spec.dtype:
            raise ContractError('state model/dtype differ from the serving target')
        runtime = state.get('runtime') or {}
        if runtime.get('runtime_profile') != spec.runtime_profile:
            raise ContractError('campaign runtime differs from serving evidence')
        if runtime.get('lnc') != spec.lnc:
            raise ContractError('campaign LNC differs from the frozen serving logical core configuration')
        assigned = set(runtime.get('resource_ids') or [])
        required = {resource for worker in spec.workers for resource in worker.resource_ids}
        if not required <= assigned:
            raise ContractError('serving workers exceed the server-assigned campaign resource pool')
        if prev:
            for old, new in zip(prev['campaign']['rounds'], state['campaign']['rounds']):
                a, b = old.get('serving') or {}, new.get('serving') or {}
                if a.get('baseline') and a['baseline'] != b.get('baseline'):
                    raise ContractError('cannot replace a recorded serving comparator')
                if old.get('status') == 'SHIPPED' and (old.get('status') != new.get('status')
                        or a.get('integration') != b.get('integration')):
                    raise ContractError('cannot rewrite serving SHIP history')
        # Existing default placeholders are allowed, but serving objective values
        # never populate the legacy batch-latency accounting fields.
        if state['campaign'].get('cumulative_speedup_vs_round1') not in (None, 0, 1, 1.0):
            raise ContractError('serving objective gains cannot populate batch E2E accounting')
        for recorded in state['campaign']['rounds']:
            for key in ('cumulative_speedup_vs_round1', 'combined_e2e_speedup_x'):
                value = recorded.get(key)
                if value not in (None, 0, 1, 1.0):
                    raise ContractError('serving objective gains cannot populate batch E2E accounting')
            if (recorded.get('integration') or {}).get('combined_e2e_speedup_x') not in (None, 0, 1, 1.0):
                raise ContractError('serving integration gains cannot populate batch E2E accounting')
        rnd = current_round(state)
        data = rnd.get('serving') or {}
        stage = state['campaign']['current_stage']
        level = STAGES.index(stage)
        if (rnd.get('baseline') or {}).get('completed_at') or level >= 1 or data.get('baseline'):
            baseline = round_baseline(state, root, spec)
        else:
            return None
        if level >= 1 and not (rnd.get('baseline') or {}).get('completed_at'):
            raise ContractError('baseline evidence must be synchronized before mining')
        mining = None
        if data.get('mining'):
            mining = mining_result(data['mining'], root, spec, baseline)
            stored = (rnd.get('bottleneck_mining') or {}).get('top_addressable_objective_pct')
            if stored != mining['top_addressable_objective_pct']:
                raise ContractError('stored serving opportunity differs from raw mining evidence')
        if level >= 2:
            if not mining or not (rnd.get('bottleneck_mining') or {}).get('completed_at'):
                raise ContractError('serving Stage 3+ requires current-incumbent mining')
        if prev and prev['campaign']['current_round'] == state['campaign']['current_round']:
            old = current_round(prev).get('serving') or {}
            if old.get('baseline') and old['baseline'] != data.get('baseline'):
                raise ContractError('cannot change an active round\'s comparator')
        validate_candidate_scores(state, root, spec)
        for op_id, track in (rnd.get('parallel_tracks') or {}).get('tracks', {}).items():
            if track.get('diluted') is True or track.get('e2e_speedup') is not None:
                raise ContractError('serving tracks cannot claim diluted batch E2E wins')
            if track.get('status') in TERMINAL:
                track_result(state, root, op_id)
        integ = rnd.get('integration') or {}
        if integ.get('status') in SHIP or rnd.get('status') == 'SHIPPED':
            verified = integration_result(state, root, spec, require_promoted=rnd.get('status') == 'SHIPPED')
            if data.get('accounting') != verified['accounting']:
                raise ContractError('serving cumulative accounting differs from direct original-to-current measurements')
        if integ.get('status') in {'exhausted', 'failed'}:
            tracks = (rnd.get('parallel_tracks') or {}).get('tracks') or {}
            if not tracks or any(t.get('status') != 'FAIL' for t in tracks.values()):
                raise ContractError('serving EXHAUSTED requires all selected tracks to have verified terminal failure')
        if state['campaign'].get('status') in {'campaign_complete', 'campaign_exhausted'}:
            if not mining or mining['decision'] != 'STOP':
                raise ContractError('serving termination requires complete current-incumbent bounds below its objective floor')
        return None
    except (KeyError, IndexError, TypeError, ValueError, OSError) as exc:
        return f'Serving gate: {exc}'


def synchronize(state: dict, root: Path, kind: str, path: Path, op_id: str | None = None) -> dict:
    """Derive state from evidence. Caller holds the normal state lock and gates."""
    spec = load_spec(root / 'target.json')
    if state.get('serving') != binding(spec):
        raise ContractError('scaffold a serving campaign before synchronizing evidence')
    rnd = current_round(state)
    data = rnd.setdefault('serving', {})
    stage = state['campaign']['current_stage']
    allowed = {'baseline': {'1_baseline'}, 'mining': {'2_bottleneck_mining'},
               'track': {'4_5_parallel_tracks', '6_integration'},
               'failure-closure': {'4_5_parallel_tracks'}, 'integration': {'6_integration'}}
    if stage not in allowed.get(kind, set()):
        raise ContractError('serving evidence cannot be synchronized outside its existing AMMO stage')
    if kind == 'track' and stage == '6_integration':
        old = (rnd['parallel_tracks']['tracks'].get(op_id) or {}).get('serving_result')
        if old != evidence_ref(root, path, spec):
            raise ContractError('cannot replace a validated track during integration')
    if kind == 'baseline':
        if state['campaign']['current_round'] != 1:
            raise ContractError('later rounds inherit integration evidence; do not recapture the baseline')
        if verify_result(path, spec)['status'] != 'BASELINE':
            raise ContractError('initial serving result is not a baseline')
        ref = evidence_ref(root, path, spec)
        if data.get('baseline') not in (None, ref):
            raise ContractError('initial baseline has already been frozen')
        data['baseline'] = ref
        rnd['baseline']['completed_at'] = stamp()
    elif kind == 'mining':
        ref = file_ref(root, path)
        result = mining_result(ref, root, spec, round_baseline(state, root, spec))
        data['mining'] = ref
        rnd['bottleneck_mining'].update(completed_at=stamp(), **{
            k: result[k] for k in ('top_addressable_objective_pct', 'objective')})
    elif kind == 'track':
        tracks = rnd['parallel_tracks']['tracks']
        if not op_id or op_id not in tracks:
            raise ContractError('synchronization requires an existing selected track')
        tracks[op_id]['serving_result'] = evidence_ref(root, path, spec)
        result = track_result(state, root, op_id, require_terminal=False, monitor=False)
        if result['status'] == 'FAIL':
            # No automated failure verdict until the normal repair/fallback work
            # is documented and independently audited. Keep a nonterminal status.
            failure_closed(tracks[op_id], op_id, root)
        tracks[op_id].update(status=result['status'], verdict=result['status'])
        done = result.get('completed_gates', ['5.1a', '5.1b', '5.2', '5.3a', '5.3b'])
        for key in ('5.1a', '5.2'):
            tracks[op_id]['gate_' + key.replace('.', '_')] = 'PASS' if key in done else 'FAIL' if key == result.get('failed_gate') else None
        tracks[op_id]['correctness'] = (True if '5.1b' in done else
                                        False if result.get('failed_gate') == '5.1b' else None)
    elif kind == 'failure-closure':
        if not op_id or op_id not in rnd['parallel_tracks']['tracks']:
            raise ContractError('failure closure requires a selected track')
        rnd['parallel_tracks']['tracks'][op_id]['serving_failure_closure'] = file_ref(root, path)
    elif kind == 'integration':
        data['integration'] = evidence_ref(root, path, spec)
        result = integration_result(state, root, spec)
        data['accounting'] = result['accounting']
        rnd['integration'].update(completed_at=stamp(), status='gated_pass' if result['status'] == 'GATED_PASS' else 'combined')
    else:
        raise ContractError('unknown serving synchronization kind')
    return state


def inherit_incumbent(previous: dict, new: dict) -> None:
    data = previous.get('serving') or {}
    ref = data.get('integration') if previous.get('status') == 'SHIPPED' else data.get('baseline')
    if not ref:
        raise ContractError('cannot create next serving round without a verified incumbent')
    new['serving'] = {'baseline': dict(ref)}
    source = previous['integration'] if previous.get('status') == 'SHIPPED' else previous['baseline']
    new['baseline']['completed_at'] = source.get('completed_at') or stamp()
    if previous.get('status') != 'SHIPPED' and previous.get('mining_invalidated') is not True:
        if data.get('mining'):
            new['serving']['mining'] = dict(data['mining'])
            new['bottleneck_mining'] = dict(previous['bottleneck_mining'])


def guidance(state: dict) -> str:
    stage = state['campaign']['current_stage']
    tasks = {
        '1_baseline': 'Capture and promote the managed serving baseline, then serving-sync baseline. Run T_AUDIT_S1.',
        '2_bottleneck_mining': 'Mine prefill, decode, handoff, and router evidence against the current serving incumbent. Use objective bounds; unknown is not zero. Run T_AUDIT_S2.',
        '3_debate': 'Keep the full mandatory debate and current candidate rules. Record expected_objective_pct and hash-bound serving_projection_evidence. Do not use batch E2E magnitude.',
        '4_5_parallel_tracks': 'Keep isolated implementer/monitor pairs. Use run_serving_sweep.py. Resolve BLOCKED evidence; record repairs before FAIL. Run T_AUDIT_S45 after all tracks are terminal.',
        '6_integration': 'Measure the exact selected composition with --composition. Synchronize and promote only the passing integration, then run T_AUDIT_S67. A candidate result is not integration evidence.',
        '7_campaign_eval': 'Keep the existing continue/stop stage. After SHIP mine the promoted incumbent in the next round. Stop only on complete objective bounds below serving.min_improvement_pct.',
        '7b_report': 'Use serving evidence for objective and direct original-to-current accounting. Keep the independent report writer and fact-checker; no batch speedup claims.',
    }
    return f"Serving objective: {state['serving']['objective']}. {tasks[stage]} See references/serving-campaign.md."


def validation_report(state: dict, root: Path, op_id: str | None = None):
    """Return the existing verifier's report type, not a parallel verdict format."""
    from verify_validation_gates import VerificationReport, GateResult, finalize_report_status
    report = VerificationReport(artifact_dir=str(root))
    tracks = (current_round(state).get('parallel_tracks') or {}).get('tracks') or {}
    ids = [op_id] if op_id else list(tracks)
    if not ids:
        raise ContractError('serving campaign has no selected tracks to verify')
    for selected in ids:
        try:
            result = track_result(state, root, selected)
            report.track_outcomes[selected] = result['status']
            report.gates.append(GateResult(name='serving_evidence', status='PASS',
                message='Recomputed the serving verdict and checked the paired monitor evidence',
                evidence=[tracks[selected]['serving_result']['path']], track_id=selected))
        except (ValueError, OSError, KeyError) as exc:
            report.track_outcomes[selected] = 'GPU_BLOCKED'
            report.gates.append(GateResult(name='serving_evidence', status='FAIL', message=str(exc),
                                           evidence=[], track_id=selected))
            report.blockers.append(f'{selected}: {exc}')
    # A per-track verifier is useful during implementation. For the whole cohort,
    # also retain the state engine's cohort identities and serving invariants.
    if not op_id:
        reason = violation(state, root)
        if reason:
            report.blockers.append(reason)
            report.gates.append(GateResult(name='serving_campaign', status='FAIL', message=reason, evidence=[]))
        from verify_validation_gates import _selected_track_ids
        selected = _selected_track_ids(state)
        if not selected or set(selected) != set(tracks):
            report.blockers.append('selected candidate/track cohort mismatch')
    finalize_report_status(report)
    return report


def write_track_report(state: dict, root: Path, op_id: str, output: Path | None = None) -> dict:
    spec = load_spec(root / 'target.json')
    result = track_result(state, root, op_id)
    directory = root / 'rounds' / str(state['campaign']['current_round']) / 'tracks' / op_id
    directory.mkdir(parents=True, exist_ok=True)
    output = output or directory / 'validation_results.md'
    lines = [f'# Serving validation: {op_id}', '', f'Verdict: **{result["status"]}**.',
             f'Objective: `{spec.policy.objective}` at percentile {spec.policy.percentile:g}.',
             'Evidence scope: fixed offered load, not maximum sustainable capacity.', '',
             'The metrics below come from sealed request-level results. They are not kernel speedups.', '']
    if result.get('rows'):
        lines += ['| Workload | Incumbent | Candidate | Improvement (%) | Verdict |',
                  '|---|---:|---:|---:|---|']
        for row in result['rows']:
            lines.append(f'| {row["workload_id"]} | {row["baseline"]:.6g} | {row["candidate"]:.6g} | {row["improvement_pct"]:.6g} | {row["status"]} |')
    else:
        lines += [f'First failed gate: `{result.get("failed_gate")}`.', str(result.get('reason', ''))]
    ref = current_round(state)['parallel_tracks']['tracks'][op_id]['serving_result']
    lines += ['', f'Raw evidence: `{ref["path"]}`.', f'Seal SHA256: `{ref["sha256"]}`.',
              '', 'This generated report does not grant an independent audit or deployment approval.']
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('\n'.join(lines) + '\n')
    write_json(directory / 'validation_summary.json', result)
    return result
