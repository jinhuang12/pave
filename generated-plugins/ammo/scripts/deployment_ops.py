#!/usr/bin/env python3
"""Own a local P/D deployment and its complete resource reservation.

Only direct foreground processes are supported. An SSH or daemonizing launcher
would defeat process ownership and is deliberately outside this binding.
"""
from __future__ import annotations

import contextlib
import os
import signal
import socket
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from serving_contract import CUDA_PROFILES, NEURON_PROFILES, Arm, Command, ContractError, ServingSpec, digest


def format_command(command: Command, values: dict[str, str]) -> list[str]:
    argv = []
    for arg in command.argv:
        for key, value in values.items():
            arg = arg.replace('{' + key + '}', value)
        argv.append(arg)
    return argv


class CleanupError(ContractError):
    """Cleanup is uncertain. Keep the reservation for operator investigation."""


def spawn_owned(argv, **kwargs) -> subprocess.Popen:
    """Capture identity while the child is still owned, before any wait/reap."""
    import psutil
    if kwargs.get('start_new_session') is not True:
        raise ContractError('owned workers require their own foreground process group')
    proc = subprocess.Popen(argv, **kwargs)
    proc.ammo_create_time = psutil.Process(proc.pid).create_time()
    return proc


def _check_group_owner(proc: subprocess.Popen) -> None:
    import psutil
    expected = getattr(proc, 'ammo_create_time', None)
    if expected is None:
        raise CleanupError('missing launch identity; refusing an untracked process group')
    try:
        if psutil.Process(proc.pid).create_time() != expected:
            raise CleanupError('process ID was reused; refusing to signal a different owner')
    except psutil.NoSuchProcess:
        # An exited leader may leave its original group children. If its PID
        # reappears, the check above refuses the replacement before signaling.
        pass


def _group_has_live_members(pgid: int) -> bool:
    import psutil
    for proc in psutil.process_iter(attrs=['pid', 'status']):
        try:
            if os.getpgid(proc.pid) == pgid and proc.info['status'] not in {'zombie', 'dead'}:
                return True
        except (ProcessLookupError, psutil.NoSuchProcess):
            continue
        except (PermissionError, psutil.AccessDenied) as exc:
            raise CleanupError('cannot inspect owned process-group cleanup') from exc
    return False


def stop_group(proc: subprocess.Popen, grace_s: float = 5) -> None:
    # Signal only this foreground child's group, even if its leader exited.
    # Reap the leader while waiting; orphan zombies hold no accelerator work.
    for sig, grace in ((signal.SIGTERM, grace_s), (signal.SIGKILL, 10)):
        _check_group_owner(proc)
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            raise CleanupError('cannot signal owned process group') from exc
        deadline = time.monotonic() + grace
        while True:
            proc.poll()
            if not _group_has_live_members(proc.pid):
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired as exc:
                    raise CleanupError('launcher has not exited after group cleanup') from exc
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
    raise CleanupError('live processes remain after owned-group shutdown')


def run_command(command: Command, *, cwd: Path, env: dict[str, str], log: Path,
                values: dict[str, str], deadline: float) -> None:
    argv = format_command(command, values)
    remaining = min(command.timeout_s, deadline - time.monotonic())
    if remaining <= 0:
        raise ContractError('deployment run deadline exceeded')
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('xb') as stream:
        proc = spawn_owned(argv, cwd=cwd, env=env, stdout=stream,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = proc.wait(timeout=remaining)
            if rc:
                raise ContractError(f'command failed with exit {rc}; see {log.name}')
        finally:
            stop_group(proc, grace_s=0.1)


@contextlib.contextmanager
def reserve_deployment(spec: ServingSpec, owner: str):
    """Use the host-wide lease table (gpu_reservation.py); never auto-release peers."""
    import gpu_reservation
    profile = os.environ.get('AMMO_RUNTIME_PROFILE') or 'pumice-cuda'
    if profile != spec.runtime_profile:
        raise ContractError('serving runtime differs from the campaign runtime '
                            '(AMMO_RUNTIME_PROFILE, from target.json runtime.profile)')
    if spec.runtime_profile in NEURON_PROFILES:
        assigned_lnc = os.environ.get('NEURON_LOGICAL_NC_CONFIG', os.environ.get('AMMO_NEURON_LNC'))
        if assigned_lnc != str(spec.lnc):
            raise ContractError('serving LNC differs from the server-assigned logical core configuration')
    if not os.environ.get('AMMO_SESSION_ID'):
        raise ContractError('managed serving requires an AMMO campaign id (AMMO_SESSION_ID); '
                            'run inside a campaign so the .ammo/active marker supplies it')
    ids = sorted(r for w in spec.workers for r in w.resource_ids)
    # The host pool seen through the caller's CUDA_VISIBLE_DEVICES.
    pool = gpu_reservation._discover_session_resources()
    if not set(ids) <= set(pool):
        raise ContractError('deployment requests resources outside its session: '
                            f'{sorted(set(ids) - set(pool))} are not in the visible pool {pool}')
    # A unique owner and a bounded run avoid releasing sibling reservations.
    # The diagnostic expiry outlives the deadline. Reclamation nevertheless
    # requires confirmed cleanup, including after a killed orchestrator.
    gpu_reservation.write_reservation(ids, owner, 'AMMO P/D deployment',
                                     lease_hours=(spec.max_run_s + 120) / 3600, require_cleanup=True)
    cleanup_confirmed = True
    try:
        yield
    except CleanupError:
        cleanup_confirmed = False
        raise
    finally:
        if cleanup_confirmed:
            gpu_reservation.release_by_session(owner, include_children=False, cleanup_confirmed=True)



class Deployment:
    def __init__(self, spec: ServingSpec, arm: Arm, output: Path, *, deadline: float):
        self.spec, self.arm, self.output, self.deadline = spec, arm, output, deadline
        self.processes: dict[str, subprocess.Popen] = {}
        self.logs = []
        self.run_id = uuid.uuid4().hex
        self.context_path = output / 'context.json'

    def environment(self, worker=None) -> dict[str, str]:
        # Do not inherit ambient CUDA/Neuron/compiler/Python flags from a track.
        # The complete production environment is explicit in the frozen spec.
        env = dict(self.spec.environment)
        if os.environ.get('AMMO_SESSION_ID'):
            env['AMMO_SESSION_ID'] = os.environ['AMMO_SESSION_ID']
        env.update({'AMMO_SERVING_RUN_ID': self.run_id,
                    'AMMO_SERVING_CONTEXT': str(self.context_path),
                    'AMMO_RUNTIME_PROFILE': self.spec.runtime_profile})
        if self.spec.runtime_profile in NEURON_PROFILES:
            env['NEURON_LOGICAL_NC_CONFIG'] = str(self.spec.lnc)
        if worker is None or worker.role == 'router':
            env.update({'CUDA_VISIBLE_DEVICES': '', 'NEURON_VISIBLE_DEVICES': '-1',
                        'NEURON_RT_VISIBLE_CORES': '-1'})
        else:
            ids = sorted(worker.resource_ids)
            if self.spec.runtime_profile in CUDA_PROFILES:
                env['CUDA_VISIBLE_DEVICES'] = ','.join(map(str, ids))
                env.pop('NEURON_VISIBLE_DEVICES', None)
                env.pop('NEURON_RT_VISIBLE_CORES', None)
            else:
                env['CUDA_VISIBLE_DEVICES'] = ''
                env['NEURON_VISIBLE_DEVICES'] = str(ids[0]) if len(ids) == 1 else f'{ids[0]}-{ids[-1]}'
                # The pinned Neuron plugin owns worker rank-local assignment.
                env.pop('NEURON_RT_VISIBLE_CORES', None)
        if worker is not None:
            env.update(worker.env)
            env.update(self.arm.mechanism_env.get(worker.name, {}))
            env['AMMO_SERVING_ROLE'] = worker.role
        return env

    def values(self, worker=None) -> dict[str, str]:
        return {'endpoint': self.spec.endpoint, 'model': self.spec.model_id,
                'model_revision': self.spec.model_revision, 'dtype': self.spec.dtype,
                'source': self.arm.sources[worker.name].path if worker else '',
                'run_dir': str(self.output), 'run_id': self.run_id}

    def snapshot_processes(self):
        from serving_proof import snapshot_processes
        return snapshot_processes(self)

    def check_alive(self):
        if time.monotonic() >= self.deadline:
            raise ContractError('deployment run deadline exceeded')
        for name, proc in self.processes.items():
            if proc.poll() is not None:
                raise ContractError(f'{name}: managed foreground process exited')

    def __enter__(self):
        from serving_contract import write_json
        self.arm.verify(self.spec)
        self.spec.verify_files()
        self.output.mkdir(parents=True, exist_ok=False)
        try:
            with httpx.Client(timeout=1, trust_env=False, follow_redirects=False) as client:
                for w in sorted(self.spec.workers, key=lambda w: w.role == 'router'):
                    url = urlsplit(w.ready_url)
                    # Do not adopt another session's already-live endpoint.
                    with socket.socket(socket.AF_INET6 if url.hostname == '::1' else socket.AF_INET) as probe:
                        probe.settimeout(0.5)
                        if probe.connect_ex((url.hostname, url.port)) == 0:
                            raise ContractError(f'{w.name}: listener port already in use')
                    log = (self.output / f'{w.name}.log').open('xb')
                    self.logs.append(log)
                    proc = spawn_owned(format_command(w.command, self.values(w)),
                        cwd=self.arm.sources[w.name].path, env=self.environment(w),
                        stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                    self.processes[w.name] = proc
                    ready_deadline = min(self.deadline, time.monotonic() + self.spec.startup_timeout_s)
                    while time.monotonic() < ready_deadline:
                        self.check_alive()
                        try:
                            if client.get(w.ready_url).status_code == 200:
                                break
                        except httpx.HTTPError:
                            pass
                        time.sleep(0.1)
                    else:
                        raise ContractError(f'{w.name}: readiness deadline exceeded')
            write_json(self.context_path, {
                'schema_version': 'ammo_serving_context/1', 'run_id': self.run_id,
                'contract_sha256': self.spec.checksum, 'arm_sha256': self.arm.checksum,
                'model_revision': self.spec.model_revision,
                'workers': {w.name: {'role': w.role, 'pid': self.processes[w.name].pid,
                    'resource_ids': w.resource_ids, 'source': self.arm.sources[w.name].model_dump(),
                    'environment_sha256': digest(self.environment(w)),
                    'execution_ranks': w.execution_ranks}
                    for w in self.spec.workers},
            })
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        errors = []
        for proc in reversed(list(self.processes.values())):
            try:
                stop_group(proc, grace_s=0.2)
            except Exception as exc:
                errors.append(str(exc))
        for log in self.logs:
            log.close()
        if errors:
            from serving_contract import write_json
            write_json(self.output / 'cleanup_failure.json', {
                'run_id': self.run_id, 'errors': errors,
                'owned_groups': {name: {'pid': proc.pid, 'start_time': getattr(proc, 'ammo_create_time', None)}
                                 for name, proc in self.processes.items()},
                'reservation_released': False,
                'warning': 'Local reservation still has an expiry; operator recovery is required.',
            })
            raise CleanupError('could not confirm deployment cleanup: ' + '; '.join(errors))
        self.processes.clear()
