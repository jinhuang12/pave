#!/usr/bin/env python3
"""Typed, frozen inputs for AMMO's opt-in P/D measurement binding.

This is not a router, KV transport, or cluster scheduler. Commands come from
an approved deployment recipe. Runtime and serving topology are independent.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

SCHEMA = 'ammo_serving/2'
IDENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
SHA = re.compile(r'^[a-f0-9]{64}$')
COMMIT = re.compile(r'^[a-f0-9]{40}$')
RESERVED_ENV = {
    'CUDA_VISIBLE_DEVICES', 'NEURON_VISIBLE_DEVICES', 'NEURON_RT_VISIBLE_CORES', 'NEURON_LOGICAL_NC_CONFIG',
    'AMMO_RUNTIME_PROFILE', 'AMMO_ACCELERATOR_KIND', 'AMMO_SERVING_OUTPUT',
    'AMMO_SERVING_CONTEXT', 'AMMO_SERVING_RUN_ID', 'AMMO_SERVING_ROLE',
}
Metric = Literal['ttft_ms', 'tpot_ms', 'itl_ms', 'e2e_ms', 'goodput_rps']
# Runtime profiles by accelerator kind. The vllm-* and pumice-* profiles differ
# in the engine, not in device ownership, so each pair shares one rule set.
CUDA_PROFILES = ('vllm-cuda', 'pumice-cuda')
NEURON_PROFILES = ('vllm-neuron', 'pumice-neuron')


class ContractError(ValueError):
    """An input or an artifact cannot establish a valid comparison."""


class GateFailure(ContractError):
    """Valid primary evidence disproves a candidate. Not an infrastructure error."""

    def __init__(self, gate: str, message: str):
        super().__init__(message)
        self.gate = gate


def digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ContractError(f'duplicate JSON key: {key}')
            result[key] = value
        return result
    def invalid(value: str):
        raise ContractError(f'non-finite JSON value: {value}')
    try:
        value = json.loads(path.read_text(), parse_constant=invalid, object_pairs_hook=unique)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f'cannot read {path}: {exc}') from exc
    if not isinstance(value, dict):
        raise ContractError(f'{path}: expected an object')
    return value


def write_json(path: Path, value: object) -> None:
    """Atomically write a derived file. Primary slots use exclusive creation."""
    import os
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def contained(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ContractError('artifact references must be nonempty relative paths')
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()) or candidate == root.resolve():
        raise ContractError('artifact path escapes its evidence directory')
    return candidate


def identifier(value: str) -> str:
    if not IDENT.fullmatch(value):
        raise ValueError('expected a bounded alphanumeric identifier with - or _')
    return value


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', validate_default=True, allow_inf_nan=False)


class Command(Closed):
    argv: list[StrictStr] = Field(min_length=1)
    timeout_s: StrictFloat = Field(default=3600, gt=0, le=86400)

    @field_validator('argv')
    @classmethod
    def valid_argv(cls, values):
        if any(not value or '\x00' in value for value in values):
            raise ValueError('empty or NUL command argument')
        # Commands are executed as argv, never through shell=True.
        return values


class Worker(Closed):
    name: StrictStr
    role: Literal['prefill', 'decode', 'router']
    resource_ids: list[Annotated[StrictInt, Field(ge=0)]] = Field(default_factory=list)
    command: Command
    ready_url: StrictStr
    env: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    execution_ranks: StrictInt = Field(default=1, ge=1)
    execution_identity: dict[StrictStr, StrictStr] = Field(min_length=1)
    _name = field_validator('name')(identifier)

    @field_validator('execution_identity')
    @classmethod
    def complete_execution_identity(cls, value):
        required = {'engine', 'runtime', 'compiler', 'compile_config', 'cache_policy', 'graph_mode', 'transport'}
        if not required <= set(value) or any(not item.strip() for item in value.values()):
            raise ValueError('execution identity must cover engine, runtime, compiler, compile config, cache policy, graph mode and transport')
        return value

    @field_validator('ready_url')
    @classmethod
    def local_endpoint(cls, value):
        u = urlsplit(value)
        # Local management is intentional. Reject external hosts rather than
        # pretending a session lock provides multi-node ownership.
        if (u.scheme != 'http' or u.hostname not in {'127.0.0.1', '::1'}
                or (u.port is None or u.port == 0) or u.username or u.password or u.query or u.fragment):
            raise ValueError('managed-local health URL must use literal loopback and a port')
        return value

    @field_validator('env')
    @classmethod
    def no_resource_override(cls, value):
        if RESERVED_ENV.intersection(value) or any(k.startswith('AMMO_') for k in value):
            raise ValueError('worker environment cannot override ownership or harness identity')
        if any('\x00' in k + v or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k)
               for k, v in value.items()):
            raise ValueError('invalid environment key or value')
        return value

    @model_validator(mode='after')
    def resources(self):
        if len(set(self.resource_ids)) != len(self.resource_ids):
            raise ValueError('duplicate worker resource ID')
        if (self.role == 'router') != (len(self.resource_ids) == 0):
            raise ValueError('router is CPU-only; engine workers must own resources')
        return self


class Workload(Closed):
    id: StrictStr
    request_rate: StrictFloat = Field(gt=0)
    num_prompts: StrictInt = Field(ge=2)
    # Complete argv for an existing load generator. It writes one vLLM-style
    # detailed result file. No command-line flags are guessed by this module.
    command: Command
    # Prompt corpus, arrival trace, generation config, tokenizer identity, and
    # cache policy are covered by immutable local file bindings.
    files: dict[StrictStr, StrictStr] = Field(min_length=1)
    output_tokens: StrictInt = Field(ge=2)
    input_tokens: StrictInt | None = Field(default=None, ge=1)
    _id = field_validator('id')(identifier)

    @field_validator('files')
    @classmethod
    def valid_files(cls, values):
        for name, checksum in values.items():
            if not Path(name).is_absolute() or not SHA.fullmatch(checksum):
                raise ValueError('workload files require absolute paths and SHA256 digests')
        return values

    @model_validator(mode='after')
    def placeholders(self):
        text = '\n'.join(self.command.argv)
        for required in ('{result_dir}', '{result_filename}', '{seed}', '{endpoint}', '{request_rate}', '{num_prompts}', '{request_id_prefix}'):
            if required not in text:
                raise ValueError(f'benchmark command must bind {required}')
        # Self-paced/concurrency-limited traffic can conceal overload. A pinned
        # custom adapter may produce the same schema, but must retain arrivals.
        if any(
            arg.split('=')[0] in {'--profile', '--max-concurrency', '--ramp-up-strategy'}
            for arg in self.command.argv
        ):
            raise ValueError('official sweep must be profiler-free fixed offered load')
        return self


class Policy(Closed):
    objective: Metric
    percentile: StrictFloat = Field(default=99, gt=0, le=100)
    min_improvement_pct: StrictFloat = Field(default=0.5, gt=0, lt=100)
    regression_tolerance_pct: StrictFloat = Field(default=0.5, ge=0, lt=100)
    alpha: StrictFloat = Field(default=0.05, gt=0, lt=0.5)
    repetitions: StrictInt = Field(default=10, ge=6, le=1000)
    # A limit is applied to the same percentile in every window, never to a
    # pooled distribution which could hide a regressed workload.
    slo_ms: dict[Literal['ttft_ms', 'tpot_ms', 'itl_ms', 'e2e_ms'], StrictFloat] = Field(min_length=1)
    max_error_rate: StrictFloat = Field(default=0, ge=0, lt=1)
    min_duration_s: StrictFloat = Field(default=30, gt=0)
    max_client_queue_ms: StrictFloat = Field(default=1, ge=0)
    # Goodput counts complete requests meeting ALL individual SLO predicates.
    # ITL uses maximum recorded streaming interval for per-request compliance.
    correctness_questions: StrictInt = Field(default=1319, ge=1, le=1319)
    correctness_tolerance_pp: StrictFloat = Field(default=1, ge=0, le=100)
    correctness_max_tokens: StrictInt = Field(default=1024, ge=1)

    @field_validator('slo_ms')
    @classmethod
    def finite_limits(cls, values):
        if any(isinstance(v, bool) or not math.isfinite(v) or v <= 0 for v in values.values()):
            raise ValueError('SLO limits must be finite and positive')
        return values


class ServingSpec(Closed):
    schema_version: Literal['ammo_serving/2'] = SCHEMA
    mode: Literal['pd'] = 'pd'
    manager: Literal['local'] = 'local'
    runtime_profile: Literal['vllm-cuda', 'vllm-neuron', 'pumice-cuda', 'pumice-neuron']
    lnc: StrictInt | None = None
    model_id: StrictStr = Field(min_length=1)
    model_revision: StrictStr
    dtype: StrictStr = Field(min_length=1)
    endpoint: StrictStr
    workers: list[Worker] = Field(min_length=3)
    workloads: list[Workload] = Field(min_length=1)
    policy: Policy
    seed: StrictInt = Field(default=0, ge=0)
    startup_timeout_s: StrictFloat = Field(default=1800, gt=0, le=86400)
    max_run_s: StrictFloat = Field(default=14400, gt=0, le=172800)
    # Scripts for exact runtime/connector revisions are part of the frozen
    # measurement contract, not agent-written verdict files.
    adapters: dict[Literal['component', 'mechanism', 'activation', 'timing_proof'], Command]
    environment: dict[StrictStr, StrictStr] = Field(default_factory=lambda: {
        'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8',
    })
    adapter_files: dict[StrictStr, StrictStr] = Field(min_length=1)

    _environment = field_validator('environment')(Worker.no_resource_override.__func__)
    _endpoint = field_validator('endpoint')(Worker.local_endpoint.__func__)
    _adapter_files = field_validator('adapter_files')(Workload.valid_files.__func__)

    @field_validator('model_revision')
    @classmethod
    def pinned_revision(cls, value):
        if not COMMIT.fullmatch(value):
            raise ValueError('model revision must be a resolved 40-character commit')
        return value

    @model_validator(mode='after')
    def coherent(self):
        names = [w.name for w in self.workers]
        ids = [w.id for w in self.workloads]
        if len(names) != len(set(names)) or len(ids) != len(set(ids)):
            raise ValueError('duplicate worker or workload identity')
        roles = [w.role for w in self.workers]
        if any(roles.count(role) != 1 for role in ('prefill', 'decode', 'router')):
            raise ValueError('local binding supports exactly one P replica, one D replica, and one router')
        resources = [r for w in self.workers for r in w.resource_ids]
        if len(resources) != len(set(resources)):
            raise ValueError('P and D resource assignments overlap')
        urls = [urlsplit(w.ready_url) for w in self.workers]
        ports = [u.port for u in urls]
        if len(ports) != len(set(ports)):
            raise ValueError('workers must have distinct local listener ports')
        router = next(w for w in self.workers if w.role == 'router')
        if urlsplit(router.ready_url).netloc != urlsplit(self.endpoint).netloc:
            raise ValueError('client endpoint must point to the managed router')
        if set(self.adapters) != {'component', 'mechanism', 'activation', 'timing_proof'}:
            raise ValueError('component, mechanism, activation and passive timing-proof adapters are required')
        if self.runtime_profile in CUDA_PROFILES and self.lnc is not None:
            raise ValueError('LNC is only valid for the Neuron runtime')
        if self.runtime_profile in NEURON_PROFILES:
            if self.lnc not in (1, 2):
                raise ValueError('Neuron serving requires the server-assigned LNC (1 or 2)')
            for worker in self.workers:
                r = sorted(worker.resource_ids)
                if r and r != list(range(r[0], r[-1] + 1)):
                    raise ValueError('each Neuron role requires a contiguous logical core range')
        return self

    @property
    def checksum(self) -> str:
        return digest(self.model_dump(mode='json'))

    def verify_files(self) -> None:
        for bindings in [self.adapter_files, *(w.files for w in self.workloads)]:
            for name, expected in bindings.items():
                if file_digest(Path(name)) != expected:
                    raise ContractError(f'frozen input changed: {name}')


class Source(Closed):
    path: StrictStr
    commit: StrictStr

    @field_validator('path')
    @classmethod
    def absolute(cls, value):
        if not Path(value).is_absolute():
            raise ValueError('source worktree must be absolute')
        return value

    @field_validator('commit')
    @classmethod
    def pinned(cls, value):
        if not COMMIT.fullmatch(value):
            raise ValueError('worker source must be a pinned commit')
        return value


class Envelope(Closed):
    """Production-observable predicate, never a benchmark workload-ID switch."""
    roles: list[Literal['prefill', 'decode']] = Field(min_length=1)
    min_input_tokens: StrictInt = Field(default=1, ge=1)
    max_input_tokens: StrictInt = Field(ge=1)
    predicate_symbol: StrictStr = Field(min_length=1)
    fallback_identity_sha256: StrictStr

    @model_validator(mode='after')
    def valid(self):
        if len(set(self.roles)) != len(self.roles) or self.min_input_tokens > self.max_input_tokens:
            raise ValueError('invalid enablement predicate')
        if not SHA.fullmatch(self.fallback_identity_sha256):
            raise ValueError('envelope must pin the incumbent fallback implementation')
        return self

    def active(self, workload: Workload, role: str) -> bool:
        if workload.input_tokens is None:
            raise ContractError('gated workloads must declare exact input token counts')
        return role in self.roles and self.min_input_tokens <= workload.input_tokens <= self.max_input_tokens


class Arm(Closed):
    id: StrictStr
    sources: dict[StrictStr, Source]
    # Candidate-specific environment values must be declared before testing.
    # Topology, dtype, routing, and workload remain in ServingSpec.
    mechanism_env: dict[StrictStr, dict[StrictStr, StrictStr]] = Field(default_factory=dict)
    envelope: Envelope | None = None
    enablement: StrictStr = Field(default='all frozen workloads', min_length=1)
    _id = field_validator('id')(identifier)

    @model_validator(mode='after')
    def valid_env(self):
        for value in self.mechanism_env.values():
            Worker.no_resource_override(value)
            if set(value) & {'PATH', 'PYTHONPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'NEURON_LOGICAL_NC_CONFIG'}:
                raise ValueError('mechanism flags cannot replace source/runtime/loader identity')
        return self

    @property
    def checksum(self) -> str:
        return digest(self.model_dump(mode='json'))

    def validate_contract(self, spec: ServingSpec) -> None:
        """Validate the envelope without requiring the original worktree to exist."""
        names = {w.name for w in spec.workers}
        if set(self.sources) != names or not set(self.mechanism_env) <= names:
            raise ContractError('arm must bind every worker, and no unknown worker')
        if self.envelope:
            enabled = [any(self.envelope.active(w, role) for role in self.envelope.roles) for w in spec.workloads]
            if not any(enabled) or all(enabled):
                raise ContractError('a gated arm requires positive and negative frozen workload coverage')
    def verify(self, spec: ServingSpec) -> None:
        import subprocess
        self.validate_contract(spec)
        for name, source in self.sources.items():
            path = Path(source.path)
            try:
                head = subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True, timeout=10).strip()
                dirty = subprocess.check_output(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=all'], text=True, timeout=10).strip()
            except (OSError, subprocess.SubprocessError) as exc:
                raise ContractError(f'{name}: cannot verify source') from exc
            if head != source.commit or dirty:
                raise ContractError(f'{name}: source commit mismatch or dirty worktree')


def load_spec(target: Path) -> ServingSpec:
    obj = read_json(target)
    spec = ServingSpec.model_validate(obj.get('serving', obj))
    outer = obj.get('target')
    if outer and (outer.get('model_id') != spec.model_id or outer.get('dtype') != spec.dtype):
        raise ContractError('serving model/dtype differ from the campaign target')
    return spec


def reject_legacy_target(target: dict, consumer: str) -> None:
    """Never let an unsupported serving target fall through a batch-only gate."""
    if 'serving' in target or str(target.get('schema_version', '')).startswith('ammo_serving/'):
        raise ContractError(
            f'{consumer} is batch-only. Use run_serving_sweep.py for experimental '
            'P/D measurements; serving evidence must not enter a batch-latency consumer.')
