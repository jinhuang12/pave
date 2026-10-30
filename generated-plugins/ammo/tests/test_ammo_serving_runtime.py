"""CPU tests for ownership and the upstream benchmark adapter, not inference."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from serving_contract import ContractError
from serving_benchmark_adapter import preserve_request_ids, verify_source


@pytest.fixture
def reservation(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('pd_test_reservation', SCRIPTS/'gpu_reservation.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'STATE_DIR', tmp_path/'claims')
    monkeypatch.setenv('AMMO_RUNTIME_PROFILE', 'vllm-cuda')
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '0,1')
    # The pool is every host GPU (conventions section 5), not the caller's CVD.
    monkeypatch.setattr(module, '_discover_session_gpus', lambda: [0, 1])
    module.read_state()
    return module


def expired_claim(reservation):
    reservation.write_reservation([0], 'campaign:pd:test', lease_hours=1, require_cleanup=True)
    state = reservation.read_state()
    state['gpus']['0']['lease_expires'] = '2000-01-01T00:00:00Z'
    reservation._write_state(state)


def test_expired_serving_claim_is_not_reclaimed(reservation):
    expired_claim(reservation)
    assert reservation.check_and_reclaim_expired() == []
    with pytest.raises(reservation.ReservationError):
        reservation.write_reservation([0], 'other')
    assert reservation.reserve(1, 'other') == [1]


def test_auto_release_does_not_clear_serving_claim(reservation):
    expired_claim(reservation)
    assert reservation.reserve(1, 'campaign:pd:test', auto_release=True) == [1]
    assert reservation.read_state()['gpus']['0']['release_policy'] == 'confirmed_cleanup'


def test_parent_cleanup_cannot_clear_durable_child(reservation):
    expired_claim(reservation)
    assert reservation.release_by_session('campaign', include_children=True) == []
    assert reservation.release_by_session('campaign', include_children=True, cleanup_confirmed=True) == []
    assert reservation.release_by_session('campaign:pd:test') == []
    assert reservation.release_by_session('other', cleanup_confirmed=True) == []
    assert reservation.release_by_session('campaign:pd:test', cleanup_confirmed=True) == [0]


def test_legacy_expiry_unchanged(reservation):
    reservation.write_reservation([0], 'legacy', lease_hours=-1)
    assert reservation.check_and_reclaim_expired() == [0]


@dataclass
class Request:
    prompt: str
    extra_headers: dict | None = None
    request_id: str | None = None


def fake_benchmark():
    """This fake checks wrapper behavior, never claims runtime compatibility."""
    sent = []
    async def request(request_func_input, session=None, pbar=None):
        sent.append(request_func_input)
        await asyncio.sleep(0.002 if request_func_input.prompt == 'slow' else 0)
        return SimpleNamespace(error='failed' if request_func_input.prompt == 'fail' else '',
                               start_time=time.perf_counter(), latency=0.01)
    def calculate_metrics(outputs, **kwargs):
        return len(outputs)
    async def get_request(items):
        for item in items:
            yield item, 1.0
    serve = SimpleNamespace(ASYNC_REQUEST_FUNCS={'openai':request},
                            calculate_metrics=calculate_metrics, get_request=get_request)
    async def benchmark():
        await serve.ASYNC_REQUEST_FUNCS['openai'](Request('warmup'))
        tasks = []
        async for item, _ in serve.get_request([Request(p) for p in ['slow', 'fast', 'fail']]):
            tasks.append(asyncio.create_task(serve.ASYNC_REQUEST_FUNCS['openai'](item)))
        out = await asyncio.gather(*tasks)
        serve.calculate_metrics(outputs=out)
        return {'errors':[x.error for x in out]}
    serve.benchmark=benchmark
    return serve, sent


def test_benchmark_keeps_upstream_order_failures_and_excludes_warmup():
    serve, sent = fake_benchmark()
    original = serve.benchmark
    with preserve_request_ids(serve, 'run:workload:'):
        result=asyncio.run(serve.benchmark())
    assert serve.benchmark is original
    assert result['request_ids'] == ['run:workload:0','run:workload:1','run:workload:2']
    assert result['errors'] == ['', '', 'failed']
    assert result['latencies'] == [0.01]*3
    assert len(result['queue_times']) == 3
    assert all(q >= 0 for q in result['queue_times'])
    assert result['queue_times'][0] > 0.001
    assert [r.request_id for r in sent] == ['run:workload:warmup:0', *[f'run:workload:{i}' for i in range(3)]]


@pytest.mark.parametrize('prefix', ['', 'x\n', 'x\r', 'x y', 'é', 'x'*257])
def test_benchmark_rejects_bad_prefix(prefix):
    serve, _ = fake_benchmark()
    with pytest.raises(ContractError), preserve_request_ids(serve, prefix):
        pass


def test_benchmark_rejects_static_id_and_restores_patches():
    serve, _ = fake_benchmark()
    original=serve.ASYNC_REQUEST_FUNCS['openai']
    with pytest.raises(ContractError), preserve_request_ids(serve, 'run:'):
        asyncio.run(serve.ASYNC_REQUEST_FUNCS['openai'](Request('x', {'x-request-id':'fixed'})))
    assert serve.ASYNC_REQUEST_FUNCS['openai'] is original


def test_unobserved_request_output_is_rejected():
    serve, _ = fake_benchmark()
    with pytest.raises(ContractError), preserve_request_ids(serve, 'run:'):
        serve.calculate_metrics(outputs=[SimpleNamespace(error='')])


def test_benchmark_does_not_invent_ids_when_metrics_population_is_missing():
    serve, _ = fake_benchmark()
    async def bad():
        return {'errors':['']}
    serve.benchmark=bad
    with pytest.raises(ContractError), preserve_request_ids(serve, 'run:'):
        asyncio.run(serve.benchmark())


def test_source_check_rejects_wrong_import(tmp_path):
    with pytest.raises(ContractError):
        verify_source(SimpleNamespace(__file__=__file__), tmp_path)


def test_native_upstream_request_id_cannot_override_the_measured_id():
    serve, _ = fake_benchmark()
    with pytest.raises(ContractError), preserve_request_ids(serve, 'run:'):
        asyncio.run(serve.ASYNC_REQUEST_FUNCS['openai'](Request('x', request_id='old-run')))


def test_warmup_result_cannot_be_promoted_to_measured_request():
    serve, _ = fake_benchmark()
    with pytest.raises(ContractError), preserve_request_ids(serve, 'run:'):
        out = asyncio.run(serve.ASYNC_REQUEST_FUNCS['openai'](Request('warmup')))
        serve.calculate_metrics(outputs=[out])


def test_concurrent_warmup_requests_have_unique_ids():
    serve, sent = fake_benchmark()
    async def warmups():
        await asyncio.gather(*(serve.ASYNC_REQUEST_FUNCS['openai'](Request('slow')) for _ in range(4)))
    with preserve_request_ids(serve, 'run:'):
        asyncio.run(warmups())
    assert len({item.request_id for item in sent}) == 4
