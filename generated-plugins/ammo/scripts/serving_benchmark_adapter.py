#!/usr/bin/env python3
"""Preserve actual request IDs in the pinned vLLM serving benchmark.

This is a measurement adapter, not a second traffic generator. It delegates
arrivals, HTTP/SSE handling, token counting and metrics to upstream vLLM. The
only addition is one unique X-Request-Id on each request, retained in upstream
output order, plus request latencies and observed client-queue durations.
A different upstream implementation is rejected before traffic.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import inspect
import math
import time
from pathlib import Path
import sys

from serving_contract import ContractError

VLLM_COMMIT = 'ee0da84ab9e04ac7610e28580af62c365e898389'
SERVE_BLOB = '4d6fdbe22afa6544cd8ae764d26c7e5724463293'
REQUEST_BLOB = 'db58f422b808c5da81d036556119c30249d5958a'
# Installed-wheel releases qualified for preserve_request_ids, keyed by the
# exact vllm.__version__. Values are the git-blob SHA-1s of
# (vllm/benchmarks/serve.py, vllm/benchmarks/lib/endpoint_request_func.py),
# measured in the image that ships the wheel. 0.30.0: pumice-cuda-base:local
# sha256:b6334f8fe2c086f0f4adb0739cc31f92915d2a6ec587ca5eef6b9386327efe63.
WHEEL_BLOBS = {
    '0.30.0': ('0292ff939a553fcc78f9048e6e22d95d46633a12',
               '54c6ce63b0fa5b961fe4887aeff4d9ee5666edb2'),
}


def git_blob(path: Path) -> str:
    """The `git hash-object` identity of one file."""
    raw = path.read_bytes()
    return hashlib.sha1(f'blob {len(raw)}\0'.encode() + raw).hexdigest()


def verify_source(module, source: Path) -> None:
    """Check imported code, not a version string or a declared installation."""
    import subprocess
    path = Path(module.__file__).resolve()
    if not path.is_relative_to(source.resolve()):
        raise ContractError('benchmark import is outside the pinned vLLM source')
    try:
        head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'],
                                       text=True, timeout=10).strip()
        subprocess.run(['git', '-C', str(source), 'diff', '--quiet', 'HEAD'],
                       check=True, timeout=10)
    except subprocess.SubprocessError as exc:
        raise ContractError('benchmark source is changed or cannot be verified') from exc
    if head != VLLM_COMMIT:
        raise ContractError('unsupported benchmark revision; qualify a new adapter first')
    expected_path = source.resolve() / 'vllm/benchmarks/serve.py'
    if path != expected_path:
        raise ContractError('benchmark import does not resolve to the pinned module')
    for module_path, expected in (
        (path, SERVE_BLOB),
        (source / 'vllm/benchmarks/lib/endpoint_request_func.py', REQUEST_BLOB),
    ):
        if git_blob(module_path) != expected:
            raise ContractError('pinned benchmark or request implementation was changed')


def verify_wheel(serve, request, vllm, site_packages: Path) -> None:
    """Check the imported installed wheel: location, exact release, and code."""
    root = site_packages.resolve()
    serve_path = root / 'vllm/benchmarks/serve.py'
    request_path = root / 'vllm/benchmarks/lib/endpoint_request_func.py'
    for module, path in ((serve, serve_path), (request, request_path),
                         (vllm, root / 'vllm/__init__.py')):
        if Path(module.__file__).resolve() != path:
            raise ContractError('benchmark import is outside the pinned vLLM wheel')
    pinned = WHEEL_BLOBS.get(getattr(vllm, '__version__', None))
    if pinned is None:
        raise ContractError('unsupported benchmark wheel version; qualify a new adapter first')
    for path, blob in zip((serve_path, request_path), pinned):
        if git_blob(path) != blob:
            raise ContractError('pinned benchmark or request implementation was changed')


@contextmanager
def preserve_request_ids(serve, prefix: str):
    """Wrap upstream functions, preserving their behavior and ordered outputs."""
    if not prefix or len(prefix) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in prefix):
        raise ContractError('request prefix must be nonempty bounded printable ASCII')
    request = serve.ASYNC_REQUEST_FUNCS['openai']
    calculate = serve.calculate_metrics
    benchmark = serve.benchmark
    get_request = serve.get_request
    request_signature = inspect.signature(request)
    metric_signature = inspect.signature(calculate)
    if ('request_func_input' not in request_signature.parameters
            or 'outputs' not in metric_signature.parameters):
        raise ContractError('upstream benchmark interface changed')
    # Keep objects alive, including warmups, so Python object IDs cannot recycle.
    observed = {}
    ordered = []
    sequence = 0
    warmup_sequence = 0
    arrivals = {}
    measured = []

    async def tagged_arrivals(*args, **kwargs):
        nonlocal sequence
        async for item, rate in get_request(*args, **kwargs):
            rid = f'{prefix}{sequence}'
            sequence += 1
            # Preserve the upstream traffic generator and its yielded order.
            # The clock matches RequestFuncOutput.start_time in the pinned helper.
            arrivals[rid] = time.perf_counter()
            yield replace(item, request_id=rid), rate

    async def tagged_request(*args, **kwargs):
        nonlocal warmup_sequence
        bound = request_signature.bind(*args, **kwargs)
        item = bound.arguments['request_func_input']
        headers = dict(item.extra_headers or {})
        if any(key.lower() == 'x-request-id' for key in headers):
            raise ContractError('a static request ID would hide per-request transfer identity')
        rid = item.request_id
        if rid is not None and rid not in arrivals:
            raise ContractError('the upstream request carries an unobserved arrival ID')
        if rid is None:
            # Readiness and warmup calls are outside get_request and never enter
            # the measured population. Give them a distinct, non-measured ID.
            rid = f'{prefix}warmup:{warmup_sequence}'
            warmup_sequence += 1
        bound.arguments['request_func_input'] = replace(item, extra_headers=headers, request_id=rid)

        output = await request(*bound.args, **bound.kwargs)
        if id(output) in observed:
            raise ContractError('upstream recycled a request result object')
        observed[id(output)] = (output, rid)
        return output

    def ordered_metrics(*args, **kwargs):
        bound = metric_signature.bind(*args, **kwargs)
        outputs = bound.arguments['outputs']
        ids = []
        records = []
        for output in outputs:
            entry = observed.get(id(output))
            if entry is None or entry[0] is not output:
                raise ContractError('unobserved request; only the pinned openai backend is supported')
            rid = entry[1]
            if rid not in arrivals:
                raise ContractError('a warmup or unknown arrival entered the measured population')
            start = output.start_time
            latency = output.latency
            if (not isinstance(start, (float, int)) or not math.isfinite(start)
                    or start < arrivals[rid] or not isinstance(latency, (float, int))
                    or not math.isfinite(latency) or latency < 0):
                raise ContractError('upstream request timestamps are invalid or missing')
            ids.append(rid)
            records.append((latency, start - arrivals[rid]))
        if len(set(ids)) != len(ids):
            raise ContractError('duplicate output in the measured request population')
        ordered[:] = ids
        measured[:] = records
        return calculate(*args, **kwargs)

    async def tagged_benchmark(*args, **kwargs):
        ordered.clear()
        measured.clear()
        result = await benchmark(*args, **kwargs)
        if not ordered or len(ordered) != len(result.get('errors', [])):
            raise ContractError('upstream did not retain the complete measured request population')
        result['request_ids'] = list(ordered)
        result['latencies'] = [row[0] for row in measured]
        result['queue_times'] = [row[1] for row in measured]
        result['queue_time_semantics'] = 'upstream arrival yield to request-helper start; perf_counter seconds'
        return result

    serve.ASYNC_REQUEST_FUNCS['openai'] = tagged_request
    serve.calculate_metrics = ordered_metrics
    serve.benchmark = tagged_benchmark
    serve.get_request = tagged_arrivals
    try:
        yield
    finally:
        serve.ASYNC_REQUEST_FUNCS['openai'] = request
        serve.calculate_metrics = calculate
        serve.benchmark = benchmark
        serve.get_request = get_request


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    pinned = parser.add_mutually_exclusive_group(required=True)
    pinned.add_argument('--vllm-source', type=Path, help='git checkout at the pinned commit')
    pinned.add_argument('--vllm-wheel', type=Path,
                        help='site-packages root of an installed, pinned vLLM release')
    parser.add_argument('--request-id-prefix', required=True)
    parser.add_argument('vllm_args', nargs=argparse.REMAINDER,
                        help='-- followed by documented vllm bench serve arguments')
    args = parser.parse_args(argv)
    forwarded = args.vllm_args[1:] if args.vllm_args[:1] == ['--'] else args.vllm_args
    if forwarded[:2] != ['bench', 'serve']:
        parser.error('expected -- bench serve followed by the frozen benchmark arguments')
    import vllm.benchmarks.serve as serve
    from vllm.entrypoints.cli.main import main as vllm_main
    if args.vllm_wheel is not None:
        import vllm
        import vllm.benchmarks.lib.endpoint_request_func as request
        verify_wheel(serve, request, vllm, args.vllm_wheel)
    else:
        verify_source(serve, args.vllm_source)
    original = sys.argv
    try:
        sys.argv = ['vllm', *forwarded]
        with preserve_request_ids(serve, args.request_id_prefix):
            return vllm_main()
    finally:
        sys.argv = original


if __name__ == '__main__':
    try:
        main()
    except (ContractError, OSError, ValueError) as exc:
        print(f'Serving benchmark blocked: {exc}', file=sys.stderr)
        sys.exit(2)
