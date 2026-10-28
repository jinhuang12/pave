#!/usr/bin/env python3
"""Run AMMO's opt-in P/D baseline, candidate, or integration measurement.

The frozen target supplies commands for the deployed router, workers, load
generator, and runtime-specific evidence collectors. AMMO invents no serving
flags and does not provide a second implementation of the KV transfer path.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time
import uuid
from pathlib import Path

import httpx

from deployment_ops import Deployment, reserve_deployment, run_command
from serving_contract import Arm, ContractError, GateFailure, ServingSpec, digest, load_spec, read_json, write_json
from serving_evidence import (correctness_score, incumbent_path, promote, seal,
    validate_activation, validate_component, validate_correctness, validate_mechanism,
    verify_adapter_artifacts, verify_result, verify_seal)
from serving_metrics import compare_windows, summarize_window
from serving_proof import snapshot_processes, validate_timing_proof, same_processes


def collect_correctness(deployment: Deployment, spec: ServingSpec) -> dict:
    # Reuse the exact bundled prompt construction and scoring from the existing
    # batch runner, including its numeric-answer extraction behavior.
    from run_vllm_bench_latency_sweep import _build_gsm8k_prompts
    prompts, labels = _build_gsm8k_prompts(spec.policy.correctness_questions)
    answers = []
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        for index, prompt in enumerate(prompts):
            deployment.check_alive()
            timeout = min(600, deployment.deadline - time.monotonic())
            if timeout <= 0:
                raise ContractError('correctness exceeded the deployment deadline')
            response = client.post(spec.endpoint.rstrip('/') + '/v1/completions',
                json={'model': spec.model_id, 'prompt': prompt, 'temperature': 0,
                      'seed': spec.seed, 'max_tokens': spec.policy.correctness_max_tokens,
                      'logprobs': 5, 'stream': False},
                headers={'X-Request-Id': f'{deployment.run_id}-correctness-{index}'},
                timeout=timeout)
            response.raise_for_status()
            body = response.json()
            choices = body.get('choices')
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0].get('text'), str):
                raise ContractError('correctness endpoint returned an invalid completion')
            answers.append(choices[0]['text'])
    deployment.check_alive()
    return {'num_questions': len(prompts), 'max_tokens': spec.policy.correctness_max_tokens,
            'suite_sha256': digest({'prompts': prompts, 'labels': labels}),
            'answers': answers, 'labels': labels, 'run_id': deployment.run_id}


def run_adapter(name: str, deployment: Deployment, result_dir: Path, spec: ServingSpec,
                baseline_arm: Arm | None = None, *, extra_context: dict | None = None) -> dict:
    result_dir.mkdir(parents=True, exist_ok=False)
    context = read_json(deployment.context_path)
    context['baseline_arm'] = baseline_arm.model_dump() if baseline_arm else None
    context['candidate_arm'] = deployment.arm.model_dump()
    context['endpoint'] = spec.endpoint
    context['serving_spec'] = spec.model_dump()
    if extra_context:
        context.update(extra_context)
    context['enablement'] = deployment.arm.enablement
    write_json(result_dir / 'context.json', context)
    env = deployment.environment()
    env['AMMO_SERVING_CONTEXT'] = str(result_dir / 'context.json')
    env['AMMO_SERVING_OUTPUT'] = str(result_dir / 'result.json')
    run_command(spec.adapters[name], cwd=Path(next(iter(deployment.arm.sources.values())).path),
                env=env, log=result_dir / 'command.log',
                values={**deployment.values(), 'run_dir': str(result_dir)}, deadline=deployment.deadline)
    deployment.check_alive()
    result = read_json(result_dir / 'result.json')
    if result.get('run_id') != deployment.run_id:
        raise ContractError(f'{name}: stale collector output or missing run identity')
    verify_adapter_artifacts(result, result_dir)
    return result


def timing_proof(deployment, spec, workload, directory, phase, raw=None):
    processes = deployment.snapshot_processes()
    extra = {'workload_id': workload.id, 'phase': phase, 'profiled': False,
             'processes': processes, 'workload': workload.model_dump(),
             'raw_result': str(directory.parent / 'raw.json') if raw is not None else None}
    proof = run_adapter('timing_proof', deployment, directory, spec, extra_context=extra)
    validate_timing_proof(proof, read_json(directory / 'context.json'), spec,
                          deployment.arm, workload, phase, raw, directory=directory, live=True)
    return proof


def timing_window(deployment: Deployment, spec: ServingSpec, workload, repetition: int,
                  result_dir: Path) -> dict:
    result_dir.mkdir(parents=True, exist_ok=False)
    env = deployment.environment()
    # The load generator is CPU-only and cannot inherit candidate mechanism
    # overrides. The same command, prompt files, and seed serve both arms.
    values = {**deployment.values(), 'result_dir': str(result_dir),
              'result_filename': 'raw.json', 'request_rate': str(workload.request_rate),
              'num_prompts': str(workload.num_prompts), 'seed': str(spec.seed + repetition),
              'workload_id': workload.id, 'request_id_prefix': f'{deployment.run_id}:{workload.id}:'}
    deployment.check_alive()
    before = timing_proof(deployment, spec, workload, result_dir / 'proof_before', 'before')
    run_command(workload.command, cwd=result_dir, env=env,
                log=result_dir / 'load.log', values=values, deadline=deployment.deadline)
    deployment.check_alive()
    spec.verify_files()
    deployment.arm.verify(spec)
    result = read_json(result_dir / 'raw.json')
    # Validate immediately; partial or failed infrastructure cannot create a
    # success-shaped summary and later be silently omitted from the sweep.
    summarize_window(result, workload, spec.policy)
    after = timing_proof(deployment, spec, workload, result_dir / 'proof_after', 'after', result)
    same_processes(before, after)
    write_json(result_dir / 'invocation.json', {
        'workload_id': workload.id, 'request_rate': workload.request_rate,
        'num_prompts': workload.num_prompts, 'seed': spec.seed + repetition,
        'command': workload.command.model_dump(), 'profiled': False,
        'run_id': deployment.run_id, 'arm_sha256': deployment.arm.checksum,
        'contract_sha256': spec.checksum,
    })
    return result


def execute(spec: ServingSpec, arm: Arm, output: Path, kind: str,
            *, incumbent: Path | None = None, reserve=reserve_deployment,
            deployment_class=Deployment, correctness=collect_correctness, composition: dict | None = None) -> dict:
    """Dependency injection is for CPU tests, not a production CLI bypass."""
    if kind not in {'baseline', 'candidate', 'integration'}:
        raise ContractError('unknown measurement kind')
    if (kind == 'baseline') != (incumbent is None):
        raise ContractError('candidate and integration require an incumbent; baseline cannot replace one')
    spec.verify_files()
    arm.verify(spec)
    parent = verify_seal(incumbent, spec) if incumbent else None
    base_arm = Arm.model_validate(read_json(incumbent / 'arm.json')) if incumbent else None
    if base_arm:
        base_arm.verify(spec)
    if composition is not None:
        selected = composition.get('selected_results')
        if (kind != 'integration' or set(composition) != {'selected_results'}
                or not isinstance(selected, dict) or not selected):
            raise ContractError('composition must name selected candidate seals for integration')
        from serving_contract import SHA, identifier
        for op_id, checksum in selected.items():
            identifier(op_id)
            if not isinstance(checksum, str) or not SHA.fullmatch(checksum):
                raise ContractError('integration composition contains an invalid seal digest')
    output.mkdir(parents=True, exist_ok=False)
    if composition is not None:
        write_json(output / 'composition.json', composition)
    write_json(output / 'target.json', spec.model_dump(mode='json'))
    write_json(output / 'arm.json', arm.model_dump(mode='json'))
    deadline = time.monotonic() + spec.max_run_s
    owner = f"{os.environ.get('AMMO_SESSION_ID', 'session')}:pd:{uuid.uuid4().hex}"
    events = []
    try:
        with reserve(spec, owner):
            with deployment_class(spec, arm, output / 'gate_deployment', deadline=deadline) as deployment:
                if base_arm:
                    component = run_adapter('component', deployment, output / 'component', spec, base_arm)
                    validate_component(component)
                    events.append('5.1a')
                correct = correctness(deployment, spec)
                write_json(output / 'correctness.json', correct)
                if incumbent:
                    validate_correctness(correct, read_json(incumbent / 'correctness.json'), spec)
                    events.append('5.1b')
                    # The collector reads the incumbent's sealed ledger as its baseline.
                    # It runs in the arm worktree, so the path is absolute.
                    mechanism = run_adapter('mechanism', deployment, output / 'mechanism', spec, base_arm,
                        extra_context={'incumbent': str(incumbent.resolve()),
                                       'incumbent_seal_sha256': digest(parent)})
                    validate_mechanism(mechanism, spec, base_arm, arm)
                    events.append('5.2')
                else:
                    if correctness_score(correct, spec) <= 0:
                        raise ContractError('baseline GSM8K accuracy is zero')
                    repeat = correctness(deployment, spec)
                    write_json(output / 'correctness_repeat.json', repeat)
                activation = run_adapter('activation', deployment, output / 'activation', spec, base_arm)
                validate_activation(activation, read_json(output / 'activation/context.json'), spec, arm)
                if base_arm:
                    events.append('5.3a')
            # Profiling cannot bleed into authoritative timing. Every measured
            # arm uses a newly started, profiler-free production deployment.
            windows = []
            for repetition in range(spec.policy.repetitions):
                order = [('baseline', base_arm), ('candidate', arm)] if base_arm else [('baseline', arm)]
                if base_arm and repetition % 2:
                    order.reverse()  # AB / BA order, with the same workload seeds.
                paired = {w.id: {'workload_id': w.id, 'repetition': repetition} for w in spec.workloads}
                for label, current in order:
                    root = output / 'timing' / str(repetition) / label
                    with deployment_class(spec, current, root / 'deployment', deadline=deadline) as deployment:
                        for workload in spec.workloads:
                            location = root / workload.id
                            timing_window(deployment, spec, workload, repetition, location)
                            paired[workload.id][label if base_arm else 'raw'] = str((location / 'raw.json').relative_to(output))
                windows.extend(paired.values())
            write_json(output / ('paired_windows.json' if base_arm else 'baseline_windows.json'), {'windows': windows})
            arm.verify(spec)
            if base_arm:
                base_arm.verify(spec)
                if digest(verify_seal(incumbent, spec)) != digest(parent):
                    raise ContractError('incumbent evidence changed during measurement')
                events.append('5.3b')
            write_json(output / 'gate_events.json', {'events': events})
            seal(output, spec, arm, kind=kind, parent=digest(parent) if parent else None)
            result = verify_result(output, spec, incumbent)
            # Summary is a cache. Verification always recomputes it from raw
            # results; a manually edited summary cannot turn a failure into PASS.
            write_json(output / 'summary.json', result)
            return result
    except GateFailure as exc:
        # A gate failure can be sealed only after all owned workers have stopped
        # and the reservation context has exited. Cleanup failures override it.
        if incumbent is None:
            write_json(output / 'failure.json', {'status': 'BLOCKED', 'error': str(exc),
                                                'completed_gates': events})
            (output / 'seal.json').unlink(missing_ok=True)
            raise
        write_json(output / 'failure.json', {'status': 'FAIL', 'failed_gate': exc.gate,
                                            'error': str(exc), 'completed_gates': events})
        write_json(output / 'gate_events.json', {'events': events})
        seal(output, spec, arm, kind='candidate_failure', parent=digest(parent))
        try:
            result = verify_result(output, spec, incumbent)
        except BaseException:
            (output / 'seal.json').unlink(missing_ok=True)
            raise
        write_json(output / 'summary.json', result)
        return result
    except BaseException as exc:
        write_json(output / 'failure.json', {'status': 'BLOCKED', 'error': str(exc),
                                            'completed_gates': events})
        # A partial run never retains a valid-looking seal.
        (output / 'seal.json').unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-dir', required=True)
    parser.add_argument('--kind', choices=['baseline', 'candidate', 'integration', 'verify', 'promote'], required=True)
    parser.add_argument('--composition', help='Frozen JSON selected_results map for integration')
    parser.add_argument('--arm', help='JSON binding each worker to its exact source commit')
    parser.add_argument('--round', type=int, default=1)
    parser.add_argument('--op-id', default='baseline')
    parser.add_argument('--result', help='Existing evidence directory for verify/promote')
    parser.add_argument('--against', help='Exact historical incumbent directory, for verify only')
    parser.add_argument('--expected-incumbent', help='CAS digest from incumbent.json; required for promotion after baseline')
    args = parser.parse_args(argv)
    if args.composition and args.kind != 'integration':
        parser.error('--composition is only allowed for integration')
    if args.against and args.kind != 'verify':
        parser.error('--against is allowed only for historical verification')
    from serving_contract import identifier
    identifier(args.op_id)
    if args.round < 1:
        parser.error('--round must be positive')
    root = Path(args.artifact_dir).resolve()
    spec = load_spec(root / 'target.json')
    pointer = root / 'serving' / 'incumbent.json'
    incumbent = incumbent_path(pointer, spec) if pointer.exists() else None
    result_dir = Path(args.result).resolve() if args.result else root / 'serving' / 'rounds' / str(args.round) / args.kind / args.op_id
    if args.kind == 'promote':
        if not args.result:
            parser.error('--result is required for promotion')
        result = promote(pointer, result_dir, spec, expected=args.expected_incumbent)
    elif args.kind == 'verify':
        if not args.result:
            parser.error('--result is required for verification')
        against = Path(args.against).resolve() if args.against else incumbent
        result = verify_result(result_dir, spec, against)
    else:
        if not args.arm:
            parser.error('--arm is required for measurement')
        if args.kind == 'baseline' and incumbent:
            raise ContractError('baseline already exists; do not reset the incumbent')
        if args.kind != 'baseline' and incumbent is None:
            raise ContractError('capture and promote the initial baseline first')
        arm = Arm.model_validate(read_json(Path(args.arm)))
        result = execute(spec, arm, result_dir, args.kind, incumbent=incumbent,
            composition=read_json(Path(args.composition)) if args.composition else None)
    import json
    print(json.dumps(result, indent=2, allow_nan=False))
    return 1 if result.get('status') in {'FAIL', 'BLOCKED'} else 0


if __name__ == '__main__':
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        sys.exit(main())
    except (ContractError, ValueError, OSError, httpx.HTTPError) as exc:
        print(f'Serving measurement blocked: {exc}', file=sys.stderr)
        sys.exit(2)
