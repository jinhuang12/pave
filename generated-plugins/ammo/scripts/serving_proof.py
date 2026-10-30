#!/usr/bin/env python3
"""Passive proof for the exact processes and requests in each clean load window.

The frozen collector reads runtime-owned receipts/counters. It must not start a
profiler. This validator binds those observations; hashes alone cannot prove a
collector's interpretation. Independent AMMO audits still inspect raw evidence.
"""
from __future__ import annotations

import os
from pathlib import Path

import psutil

from serving_contract import Arm, ContractError, ServingSpec, digest, file_digest, contained
from serving_metrics import count, number


def snapshot_processes(deployment) -> dict:
    """Read only the process groups launched by this deployment, not peer jobs."""
    result = {}
    for worker in deployment.spec.workers:
        leader = deployment.processes[worker.name]
        members = {}
        for proc in psutil.process_iter(attrs=['pid', 'status']):
            try:
                if os.getpgid(proc.pid) != leader.pid or proc.status() in {'zombie', 'dead'}:
                    continue
                members[str(proc.pid)] = {
                    'pid': proc.pid, 'start_time': proc.create_time(),
                    'environment_sha256': digest(proc.environ()),
                    'executable_sha256': file_digest(Path(proc.exe())),
                }
            except (psutil.NoSuchProcess, ProcessLookupError):
                continue
            except (psutil.AccessDenied, PermissionError, OSError) as exc:
                raise ContractError(f'cannot inspect owned worker {worker.name}') from exc
        if str(leader.pid) not in members:
            raise ContractError(f'{worker.name}: launcher vanished before timing proof')
        # A different inherited loader/compiler environment is not a valid arm.
        if members[str(leader.pid)]['environment_sha256'] != digest(deployment.environment(worker)):
            raise ContractError(f'{worker.name}: launch environment differs from the frozen contract')
        result[worker.name] = members
    return result


def validate_timing_proof(proof: dict, context: dict, spec: ServingSpec, arm: Arm,
                          workload, phase: str, raw: dict | None = None, *, directory: Path, live: bool = False) -> None:
    from serving_evidence import validate_context
    validate_context(context, spec, arm)
    if (proof.get('schema_version') != 'ammo_timing_proof/1'
            or proof.get('run_id') != context['run_id']
            or proof.get('workload_id') != workload.id
            or proof.get('phase') != phase or proof.get('profiled') is not False
            or proof.get('contract_sha256') != spec.checksum
            or proof.get('arm_sha256') != arm.checksum):
        raise ContractError('timing proof is missing, profiled, or bound to another window')
    workers = proof.get('workers')
    if not isinstance(workers, dict) or set(workers) != set(arm.sources):
        raise ContractError('timing proof omits a deployed role')
    identities = context.get('processes', {})
    for worker in spec.workers:
        observed = workers[worker.name]
        ranks = observed.get('ranks')
        if (not isinstance(ranks, list) or len(ranks) != worker.execution_ranks
                or {r.get('rank') for r in ranks} != set(range(worker.execution_ranks))):
            raise ContractError(f'{worker.name}: missing/duplicate execution ranks')
        for rank in ranks:
            pid = count(rank.get('pid'), 'execution rank PID')
            source = arm.sources[worker.name]
            process = identities.get(worker.name, {}).get(str(pid))
            if not process or any(rank.get(key) != process.get(key) for key in (
                    'pid', 'start_time', 'environment_sha256', 'executable_sha256')):
                raise ContractError(f'{worker.name}: proof is not from the actual owned timing process')
            imported = rank.get('imported_source')
            if (not isinstance(imported, str)
                    or not Path(imported).resolve().is_relative_to(Path(source.path).resolve())
                    or rank.get('source_commit') != source.commit):
                raise ContractError(f'{worker.name}: timed rank imported the wrong source')
            artifact = rank.get('imported_artifact')
            if (artifact not in proof.get('artifacts', [])
                    or rank.get('imported_sha256') != file_digest(contained(directory, artifact))):
                raise ContractError(f'{worker.name}: loaded source snapshot is missing or changed')
            if live and rank.get('imported_sha256') != file_digest(Path(imported)):
                raise ContractError(f'{worker.name}: loaded module identity changed')
            if (rank.get('runtime_profile') != spec.runtime_profile
                    or rank.get('model_revision') != spec.model_revision
                    or rank.get('dtype') != spec.dtype
                    or rank.get('lnc') != spec.lnc
                    or rank.get('execution_identity') != worker.execution_identity):
                raise ContractError(f'{worker.name}: runtime/model/compile/cache identity mismatch')
            if phase == 'after':
                active = (arm.envelope.active(workload, worker.role)
                          if arm.envelope and worker.role in arm.envelope.roles else True)
                if rank.get('mechanism_active') is not active:
                    raise ContractError(f'{worker.name}: fast path/fallback differs from its declared envelope')
                if arm.envelope and not active and worker.role in arm.envelope.roles:
                    if rank.get('fallback_identity_sha256') != arm.envelope.fallback_identity_sha256:
                        raise ContractError('inactive envelope did not execute the pinned incumbent fallback')
    if phase == 'after':
        if raw is None:
            raise ContractError('timing proof needs the measured request outcomes')
        ids = raw.get('request_ids')
        if (not isinstance(ids, list) or len(ids) != workload.num_prompts
                or any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids)):
            raise ContractError('load generator must preserve unique request IDs')
        prefix = f"{context['run_id']}:{workload.id}:"
        if any(not rid.startswith(prefix) or rid == prefix for rid in ids):
            raise ContractError('request IDs must be unique to the actual deployment and workload window')
        success = {rid for rid, error in zip(ids, raw['errors']) if not error}
        handoff = proof.get('handoff', {})
        observed = handoff.get('request_ids')
        if (not isinstance(observed, list) or len(set(observed)) != len(observed)
                or set(observed) != success
                or count(handoff.get('completed_requests'), 'completed handoffs') != len(success)
                or count(handoff.get('fallback_requests'), 'recompute fallbacks') != 0):
            raise ContractError('handoff proof does not cover exactly the successful measured requests')
        number(handoff.get('sent_bytes'), 'sent bytes', positive=True)
        number(handoff.get('received_bytes'), 'received bytes', positive=True)
    if not proof.get('artifacts'):
        raise ContractError('timing proof has no raw runtime observations')


def same_processes(before: dict, after: dict) -> None:
    """Readiness from an old worker cannot validate a restarted worker's timing."""
    def identities(proof):
        return {name: sorted((r['rank'], r['pid'], r['start_time'], r['environment_sha256'],
                              r['imported_sha256'], r['executable_sha256']) for r in obj['ranks'])
                for name, obj in proof['workers'].items()}
    if identities(before) != identities(after):
        raise ContractError('execution ranks changed during the official timing window')
