#!/usr/bin/env python3
"""P/D artifact integrity, gate arithmetic, and incumbent compare-and-swap.

Hashes detect stale or changed inputs. They do not replace independent review
of the benchmark implementation, source provenance, or raw profiler evidence.
"""
from __future__ import annotations

import contextlib
import fcntl
from pathlib import Path

from serving_contract import Arm, ContractError, GateFailure, ServingSpec, SHA, contained, digest, file_digest, read_json, write_json
from serving_metrics import compare_windows, count, number


def validate_component(raw: dict) -> None:
    if count(raw.get('tests_run'), 'component tests_run') < 1:
        raise ContractError('5.1a: no component tests executed')
    if count(raw.get('failures'), 'component failures'):
        raise GateFailure('5.1a', '5.1a: component correctness failed')
    if not raw.get('artifacts'):
        raise ContractError('5.1a: missing primary test artifacts')


def correctness_score(raw: dict, spec: ServingSpec) -> float:
    p = spec.policy
    if raw.get('num_questions') != p.correctness_questions or raw.get('max_tokens') != p.correctness_max_tokens:
        raise ContractError('5.1b: correctness workload mismatch')
    answers, labels = raw.get('answers'), raw.get('labels')
    if (not isinstance(answers, list) or not isinstance(labels, list)
            or len(answers) != p.correctness_questions or len(labels) != len(answers)):
        raise ContractError('5.1b: incomplete correctness results')
    if not isinstance(raw.get('suite_sha256'), str) or not SHA.fullmatch(raw['suite_sha256']):
        raise ContractError('5.1b: missing suite identity')
    from run_vllm_bench_latency_sweep import _get_answer_value
    if any(not isinstance(a, str) for a in answers) or any(type(x) is not int for x in labels):
        raise ContractError('5.1b: malformed answer or label')
    return sum(_get_answer_value(answer) == label for answer, label in zip(answers, labels)) / len(labels)


def validate_correctness(raw: dict, golden: dict, spec: ServingSpec) -> dict:
    if raw.get('suite_sha256') != golden.get('suite_sha256') or raw.get('labels') != golden.get('labels'):
        raise ContractError('5.1b: golden suite differs from the candidate suite')
    b, c = correctness_score(golden, spec), correctness_score(raw, spec)
    if b <= 0:
        raise ContractError('5.1b: incumbent correctness is zero; no usable reference')
    passed = c >= b - spec.policy.correctness_tolerance_pp / 100 - 1e-12
    if not passed:
        raise GateFailure('5.1b', '5.1b: full-model accuracy regressed beyond the frozen tolerance')
    return {'baseline_accuracy': b, 'candidate_accuracy': c,
            'tolerance_pp': spec.policy.correctness_tolerance_pp}


def validate_mechanism(raw: dict, spec: ServingSpec, baseline_arm: Arm, candidate_arm: Arm) -> None:
    if raw.get('baseline_arm_sha256') != baseline_arm.checksum or raw.get('candidate_arm_sha256') != candidate_arm.checksum:
        raise ContractError('5.2: mechanism comparator does not match the current arms')
    rows = raw.get('buckets')
    if not isinstance(rows, list) or not rows or not raw.get('artifacts'):
        raise ContractError('5.2: mechanism boundary evidence is missing')
    if len({row.get('id') for row in rows}) != len(rows):
        raise ContractError('5.2: duplicate mechanism bucket')
    positive = False
    for row in rows:
        if row.get('scope') != 'production_boundary' or not row.get('boundary'):
            raise ContractError('5.2: proxy timing is not mechanism evidence')
        b, c = row.get('baseline_s'), row.get('candidate_s')
        if not isinstance(b, list) or not isinstance(c, list) or len(b) < 3 or len(b) != len(c):
            raise ContractError('5.2: need at least three paired boundary observations')
        b = [number(v, 'baseline boundary', positive=True) for v in b]
        c = [number(v, 'candidate boundary', positive=True) for v in c]
        wins = sum(x > y for x, y in zip(b, c))
        positive |= sum(c) < sum(b) and wins > len(b) / 2
        if sum(c) > sum(b) and not row.get('regression_explanation'):
            raise ContractError('5.2: negative bucket has no explanation')
    if not positive:
        raise GateFailure('5.2', '5.2: no reproducible positive production-boundary delta')


def validate_context(context: dict, spec: ServingSpec, arm: Arm) -> None:
    if (not isinstance(context.get('run_id'), str) or not context['run_id']
            or context.get('contract_sha256') != spec.checksum
            or context.get('arm_sha256') != arm.checksum
            or context.get('model_revision') != spec.model_revision):
        raise ContractError('deployment context does not identify the exact contract and arm')
    workers = context.get('workers')
    if not isinstance(workers, dict) or set(workers) != set(arm.sources):
        raise ContractError('deployment context has incomplete worker coverage')
    for worker in spec.workers:
        entry = workers[worker.name]
        if (entry.get('source') != arm.sources[worker.name].model_dump()
                or entry.get('role') != worker.role
                or entry.get('resource_ids') != worker.resource_ids
                or count(entry.get('pid'), 'worker PID') < 1):
            raise ContractError('deployment context has the wrong worker assignment')


def validate_activation(raw: dict, context: dict, spec: ServingSpec, arm: Arm) -> None:
    validate_context(context, spec, arm)
    if raw.get('run_id') != context.get('run_id') or raw.get('model_revision') != spec.model_revision:
        raise ContractError('5.3a: activation run/model mismatch')
    observed = raw.get('workers', {})
    if set(observed) != {w.name for w in spec.workers}:
        raise ContractError('5.3a: activation must identify every worker')
    inactive = []
    for name, source in arm.sources.items():
        entry = observed[name]
        expected = context['workers'][name]
        if (entry.get('pid') != expected['pid'] or entry.get('source_commit') != source.commit
                or entry.get('resource_ids') != expected['resource_ids']):
            raise ContractError(f'5.3a: wrong worker identity for {name}')
        imported = entry.get('imported_source')
        if not isinstance(imported, str) or not Path(imported).resolve().is_relative_to(Path(source.path).resolve()):
            raise ContractError(f'5.3a: {name} imported source outside its assigned worktree')
        if type(entry.get('activation_observed')) is not bool:
            raise ContractError(f'5.3a: {name} activation observation missing or malformed')
        if entry['activation_observed'] is False:
            inactive.append(name)
    handoff = raw.get('handoff', {})
    request_ids = handoff.get('request_ids')
    if (not isinstance(request_ids, list) or not request_ids
            or any(not isinstance(x, str) or not x for x in request_ids)
            or len(set(request_ids)) != len(request_ids)):
        raise ContractError('5.3a: missing unique request-correlated handoff evidence')
    if count(handoff.get('fallback_requests'), 'handoff fallback_requests') != 0:
        raise ContractError('5.3a: recompute/fallback observed instead of the declared handoff')
    number(handoff.get('sent_bytes'), 'handoff sent_bytes', positive=True)
    number(handoff.get('received_bytes'), 'handoff received_bytes', positive=True)
    if count(handoff.get('completed_requests'), 'handoff completed_requests') != len(request_ids):
        raise ContractError('5.3a: request completion does not reconcile with handoff evidence')
    if not raw.get('artifacts'):
        raise ContractError('5.3a: missing primary trace/counter artifacts')
    if inactive:
        raise GateFailure('5.3a', '5.3a: production mechanism inactive on ' + ', '.join(inactive))


def verify_adapter_artifacts(raw: dict, directory: Path) -> None:
    records = raw.get('artifacts')
    if (not isinstance(records, list) or not records
            or any(not isinstance(x, str) for x in records) or len(records) != len(set(records))):
        raise ContractError('adapter must name unique primary artifact paths')
    for record in records:
        path = contained(directory, record)
        if not path.is_file() or path.stat().st_size == 0 or path.name == 'result.json':
            raise ContractError('adapter primary artifact missing, empty, or self-referential')


def seal(directory: Path, spec: ServingSpec, arm: Arm, *, kind: str, parent: str | None) -> dict:
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ContractError('evidence may not contain symlinks')
        if path.is_file() and path != directory / 'seal.json' and path != directory / 'summary.json':
            files[str(path.relative_to(directory))] = file_digest(path)
    result = {'schema_version': 'ammo_serving_evidence/1', 'kind': kind,
              'contract_sha256': spec.checksum, 'arm_sha256': arm.checksum,
              'parent_incumbent_sha256': parent, 'files': files}
    write_json(directory / 'seal.json', result)
    return result


def verify_seal(directory: Path, spec: ServingSpec) -> dict:
    record = read_json(directory / 'seal.json')
    if record.get('schema_version') != 'ammo_serving_evidence/1' or record.get('contract_sha256') != spec.checksum:
        raise ContractError('evidence schema or frozen target does not match')
    files = record.get('files')
    if not isinstance(files, dict) or not files:
        raise ContractError('empty evidence manifest')
    for name, expected in files.items():
        path = contained(directory, name)
        if (not isinstance(expected, str) or not SHA.fullmatch(expected)
                or not path.is_file() or file_digest(path) != expected):
            raise ContractError(f'changed or missing primary artifact: {name}')
    required_files = ('arm.json', 'target.json', 'gate_events.json') if record.get('kind') == 'candidate_failure' else (
        'arm.json', 'target.json', 'correctness.json', 'activation/result.json', 'activation/context.json')
    for required in required_files:
        if required not in files:
            raise ContractError(f'unsealed required artifact: {required}')
    return record


def verify_result(directory: Path, spec: ServingSpec, incumbent: Path | None = None) -> dict:
    record = verify_seal(directory, spec)
    if record.get('kind') == 'candidate_failure':
        return verify_failure(directory, spec, incumbent)
    arm = Arm.model_validate(read_json(directory / 'arm.json'))
    arm.validate_contract(spec)
    if arm.checksum != record.get('arm_sha256'):
        raise ContractError('arm identity differs from sealed evidence')
    # A sealed copy of the complete target, not a caller-supplied hash alone.
    if ServingSpec.model_validate(read_json(directory / 'target.json')).checksum != spec.checksum:
        raise ContractError('sealed target differs from frozen contract')
    context = _sealed_json(directory, record, 'gate_deployment/context.json')
    validate_context(context, spec, arm)
    activation = _adapter_result(directory, record, 'activation', spec, arm, context)
    validate_activation(activation, context, spec, arm)
    correct = read_json(directory / 'correctness.json')
    if correct.get('run_id') != context['run_id']:
        raise ContractError('correctness belongs to another deployment run')
    if record.get('kind') == 'baseline':
        if 'correctness_repeat.json' not in record['files'] or 'baseline_windows.json' not in record['files']:
            raise ContractError('incomplete baseline capture')
        score = correctness_score(correct, spec)
        if score <= 0:
            raise ContractError('baseline has no correct answers')
        repeat = read_json(directory / 'correctness_repeat.json')
        correctness_score(repeat, spec)
        if (repeat.get('suite_sha256') != correct.get('suite_sha256')
                or repeat.get('labels') != correct.get('labels')
                or repeat.get('run_id') != context['run_id']):
            raise ContractError('baseline repeat suite changed')
        from serving_metrics import summarize_window
        windows = read_json(directory / 'baseline_windows.json').get('windows', [])
        expected = {(w.id, i) for w in spec.workloads for i in range(spec.policy.repetitions)}
        if {(x.get('workload_id'), x.get('repetition')) for x in windows} != expected or len(windows) != len(expected):
            raise ContractError('baseline workload coverage is incomplete')
        by_id = {w.id: w for w in spec.workloads}
        for window in windows:
            raw = _window_result(directory, record, window['raw'], spec, arm,
                                 by_id[window['workload_id']], window['repetition'])
            summary = summarize_window(raw, by_id[window['workload_id']], spec.policy)
            if not summary['constraints_pass']:
                raise ContractError('baseline is not feasible under frozen SLOs')
        return {'status': 'BASELINE', 'accuracy': score,
                'deterministic': correct['answers'] == repeat['answers']}
    if record.get('kind') not in {'candidate', 'integration'} or incumbent is None:
        raise ContractError('candidate evidence requires its exact incumbent')
    parent_seal = verify_seal(incumbent, spec)
    if digest(parent_seal) != record.get('parent_incumbent_sha256'):
        raise ContractError('candidate was not compared against this incumbent')
    base_arm = Arm.model_validate(read_json(incumbent / 'arm.json'))
    validate_correctness(correct, read_json(incumbent / 'correctness.json'), spec)
    for key in ('component/result.json', 'mechanism/result.json', 'paired_windows.json'):
        if key not in record['files']:
            raise ContractError(f'unsealed required candidate artifact: {key}')
    component = _adapter_result(directory, record, 'component', spec, arm, context, base_arm)
    mechanism = _mechanism_result(directory, record, spec, arm, context, base_arm)
    validate_component(component)
    validate_mechanism(mechanism, spec, base_arm, arm)
    events = _sealed_json(directory, record, 'gate_events.json')['events']
    if events != ['5.1a', '5.1b', '5.2', '5.3a', '5.3b']:
        raise ContractError('gate execution order is missing or invalid')
    pairs = []
    by_id = {w.id: w for w in spec.workloads}
    used_raw = set()
    for item in read_json(directory / 'paired_windows.json')['windows']:
        if item.get('workload_id') not in by_id:
            raise ContractError('unrecognized workload in paired results')
        workload = by_id[item['workload_id']]
        entry = dict(item)
        for label, current in (('baseline', base_arm), ('candidate', arm)):
            path = item[label]
            if path in used_raw:
                raise ContractError('one raw result was reused across measurement windows')
            used_raw.add(path)
            entry[label] = _window_result(directory, record, path, spec, current,
                                          workload, item['repetition'])
        pairs.append(entry)
    result = compare_windows(spec, pairs)
    if arm.envelope and result['status'] == 'PASS':
        by_id = {w.id: w for w in spec.workloads}
        active_win = any(row['status'] == 'PASS' and any(
            arm.envelope.active(by_id[row['workload_id']], role) for role in arm.envelope.roles)
            for row in result['rows'])
        if not active_win:
            result.update(status='FAIL', reason='no significant gain inside the enabled envelope')
            return result
        result['status'] = 'GATED_PASS'
        result['envelope'] = arm.envelope.model_dump()
    return result


def _adapter_result(directory, record, name, spec, arm, gate_context, base_arm=None):
    context = _sealed_json(directory, record, f'{name}/context.json')
    validate_context(context, spec, arm)
    if context['run_id'] != gate_context['run_id']:
        raise ContractError('adapter belongs to another gate deployment')
    if (context.get('candidate_arm') != arm.model_dump()
            or (base_arm is not None and context.get('baseline_arm') != base_arm.model_dump())):
        raise ContractError('adapter context is bound to different source arms')
    raw = _sealed_json(directory, record, f'{name}/result.json')
    if raw.get('run_id') != context['run_id']:
        raise ContractError('adapter result has stale run identity')
    verify_adapter_artifacts(raw, directory / name)
    for path in raw['artifacts']:
        relative = str(contained(directory / name, path).relative_to(directory.resolve()))
        if relative not in record['files']:
            raise ContractError('adapter primary artifact is not sealed')
    return raw


def _mechanism_result(directory, record, spec, arm, gate_context, base_arm):
    raw = _adapter_result(directory, record, 'mechanism', spec, arm, gate_context, base_arm)
    # The collector reads its baseline from the incumbent named in this context.
    context = _sealed_json(directory, record, 'mechanism/context.json')
    if context.get('incumbent_seal_sha256') != record.get('parent_incumbent_sha256'):
        raise ContractError('5.2: mechanism context is not bound to the sealed incumbent')
    return raw


def _window_result(directory, record, name, spec, arm, workload, repetition):
    raw = _sealed_json(directory, record, name)
    path = Path(name)
    # Receipts and deployment identities must be read from the same window.
    if path.name != 'raw.json' or path.parent.name != workload.id:
        raise ContractError('timing result has the wrong workload path')
    receipt = _sealed_json(directory, record, str(path.with_name('invocation.json')))
    context = _sealed_json(directory, record, str(path.parent.parent / 'deployment/context.json'))
    validate_context(context, spec, arm)
    expected = {'workload_id': workload.id, 'request_rate': workload.request_rate,
                'num_prompts': workload.num_prompts, 'seed': spec.seed + repetition,
                'command': workload.command.model_dump(), 'profiled': False,
                'run_id': context['run_id'], 'arm_sha256': arm.checksum,
                'contract_sha256': spec.checksum}
    if receipt != expected or receipt.get('profiled') is not False:
        raise ContractError('timing receipt does not match the frozen workload and source arm')
    from serving_proof import validate_timing_proof, same_processes
    proofs = []
    for phase in ('before', 'after'):
        prefix = path.parent / ('proof_' + phase)
        proof_context = _sealed_json(directory, record, str(prefix / 'context.json'))
        proof = _sealed_json(directory, record, str(prefix / 'result.json'))
        if proof_context.get('run_id') != context['run_id']:
            raise ContractError('passive proof belongs to another timing deployment')
        verify_adapter_artifacts(proof, directory / prefix)
        for artifact in proof['artifacts']:
            relative = str(contained(directory / prefix, artifact).relative_to(directory.resolve()))
            if relative not in record['files']:
                raise ContractError('timing-proof primary artifact is not sealed')
        validate_timing_proof(proof, proof_context, spec, arm, workload, phase,
                              raw if phase == 'after' else None, directory=directory / prefix)
        proofs.append(proof)
    same_processes(*proofs)
    return raw


def _sealed_json(directory: Path, record: dict, name: str) -> dict:
    if name not in record['files']:
        raise ContractError(f'artifact is not bound by the run seal: {name}')
    return read_json(contained(directory, name))


@contextlib.contextmanager
def pointer_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def incumbent_path(pointer: Path, spec: ServingSpec) -> Path:
    value = read_json(pointer)
    if value.get('contract_sha256') != spec.checksum:
        raise ContractError('incumbent belongs to a different frozen contract')
    path = contained(pointer.parent, value['path'])
    record = verify_seal(path, spec)
    if digest(record) != value.get('evidence_sha256'):
        raise ContractError('incumbent evidence changed')
    return path


def promote(pointer: Path, directory: Path, spec: ServingSpec, *, expected: str | None) -> dict:
    """Promote only an exact passing integrated result, with stale-writer protection."""
    with pointer_lock(pointer):
        previous = incumbent_path(pointer, spec) if pointer.exists() else None
        old = digest(read_json(previous / 'seal.json')) if previous else None
        if old != expected:
            raise ContractError('incumbent changed; revalidate against the current incumbent')
        record = verify_seal(directory, spec)
        required_kind = 'integration' if previous else 'baseline'
        if record.get('kind') != required_kind:
            raise ContractError(f'promotion requires {required_kind} evidence')
        result = verify_result(directory, spec, previous)
        if result['status'] not in {'BASELINE', 'PASS', 'GATED_PASS'}:
            raise ContractError('nonpassing evidence cannot become the incumbent')
        # Capture current source identity again. A file edited after validation
        # cannot be promoted merely because an earlier test passed.
        Arm.model_validate(read_json(directory / 'arm.json')).verify(spec)
        spec.verify_files()
        path = directory.resolve()
        if not path.is_relative_to(pointer.parent.resolve()):
            raise ContractError('incumbent evidence must stay inside the serving artifact tree')
        entry = {'contract_sha256': spec.checksum, 'evidence_sha256': digest(record),
                 'path': str(path.relative_to(pointer.parent.resolve())),
                 'previous_evidence_sha256': old}
        original = pointer.with_name('original.json')
        if previous is None:
            if original.exists():
                raise ContractError('original incumbent already exists; cannot reset campaign history')
            write_json(original, entry)
        write_json(pointer, entry)
        return entry


def opportunity_decision(mining: dict, spec: ServingSpec, incumbent_sha256: str) -> str:
    """Missing/unknown evidence is unresolved, never zero opportunity."""
    if (mining.get('contract_sha256') != spec.checksum
            or mining.get('incumbent_sha256') != incumbent_sha256
            or mining.get('objective') != spec.policy.objective):
        raise ContractError('mining objective or incumbent is stale')
    if mining.get('coverage_complete') is not True:
        return 'UNRESOLVED'
    rows = mining.get('opportunities')
    if not isinstance(rows, list) or not rows:
        return 'UNRESOLVED'
    values = []
    for row in rows:
        value = row.get('upper_bound_improvement_pct')
        if value is None or not row.get('evidence'):
            return 'UNRESOLVED'
        values.append(number(value, 'objective opportunity bound'))
    return 'STOP' if max(values) < spec.policy.min_improvement_pct else 'CONTINUE'


def verify_failure(directory: Path, spec: ServingSpec, incumbent: Path | None) -> dict:
    """Reconstruct an early candidate rejection. Corrupt evidence remains BLOCKED."""
    record = verify_seal(directory, spec)
    if record.get('kind') != 'candidate_failure' or incumbent is None:
        raise ContractError('a candidate failure needs its exact incumbent')
    parent = verify_seal(incumbent, spec)
    if digest(parent) != record.get('parent_incumbent_sha256'):
        raise ContractError('failure was measured against a different incumbent')
    arm = Arm.model_validate(_sealed_json(directory, record, 'arm.json'))
    arm.validate_contract(spec)
    if arm.checksum != record.get('arm_sha256') or _sealed_json(directory, record, 'target.json') != spec.model_dump():
        raise ContractError('failure target or arm identity differs')
    base_arm = Arm.model_validate(read_json(incumbent / 'arm.json'))
    failure = _sealed_json(directory, record, 'failure.json')
    gate = failure.get('failed_gate')
    before = {'5.1a': [], '5.1b': ['5.1a'], '5.2': ['5.1a', '5.1b'],
              '5.3a': ['5.1a', '5.1b', '5.2']}
    if gate not in before or failure.get('status') != 'FAIL':
        raise ContractError('unsupported or unclassified failure')
    if _sealed_json(directory, record, 'gate_events.json').get('events') != before[gate]:
        raise ContractError('failure gate ordering differs from primary evidence')
    context = _sealed_json(directory, record, 'gate_deployment/context.json')
    validate_context(context, spec, arm)
    try:
        validate_component(_adapter_result(directory, record, 'component', spec, arm, context, base_arm))
        if gate == '5.1a':
            raise ContractError('recorded correctness failure actually passes')
        correct = _sealed_json(directory, record, 'correctness.json')
        if correct.get('run_id') != context['run_id']:
            raise ContractError('correctness results came from another deployment')
        validate_correctness(correct, read_json(incumbent / 'correctness.json'), spec)
        if gate == '5.1b':
            raise ContractError('recorded accuracy failure actually passes')
        validate_mechanism(_mechanism_result(directory, record, spec, arm, context, base_arm),
                           spec, base_arm, arm)
        if gate == '5.2':
            raise ContractError('recorded mechanism failure actually passes')
        activation = _adapter_result(directory, record, 'activation', spec, arm, context, base_arm)
        validate_activation(activation, context, spec, arm)
    except GateFailure as exc:
        if exc.gate != gate:
            raise ContractError('the first failed gate differs from the recorded failure') from exc
        return {'status': 'FAIL', 'failed_gate': gate, 'reason': str(exc),
                'completed_gates': before[gate]}
    raise ContractError('recorded production activation failure actually passes')
