#!/usr/bin/env python3
"""Recompute serving metrics from per-request data, not reported summaries.

Inference timing comes from the load generator. Profiles are attribution only.
Tests in this module do not measure GPU or serving performance.
"""
from __future__ import annotations

import math
from statistics import mean
from typing import Any

from serving_contract import ContractError, Policy, ServingSpec, Workload


def number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ContractError(f'{name}: expected a number')
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        raise ContractError(f'{name}: invalid finite nonnegative value')
    return float(value)


def count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f'{name}: expected a nonnegative integer')
    return value


def percentile(values: list[float], p: float) -> float:
    """Linear interpolation, matching NumPy's default percentile definition."""
    if not values:
        raise ContractError('cannot compute a percentile from an empty sample')
    data = sorted(values)
    rank = (len(data) - 1) * p / 100
    lo = int(rank)
    hi = min(lo + 1, len(data) - 1)
    return data[lo] + (data[hi] - data[lo]) * (rank - lo)


def summarize_window(raw: dict, workload: Workload, policy: Policy) -> dict:
    duration = number(raw.get('duration'), 'duration', positive=True)
    if duration < policy.min_duration_s:
        raise ContractError('benchmark window shorter than the frozen duration floor')
    required = ('ttfts', 'itls', 'latencies', 'queue_times', 'input_lens', 'output_lens', 'errors')
    for key in required:
        if not isinstance(raw.get(key), list) or len(raw[key]) != workload.num_prompts:
            raise ContractError(f'{key}: missing or incomplete per-request evidence')
    completed = count(raw.get('completed'), 'completed')
    errors = raw['errors']
    if any(not isinstance(error, str) for error in errors):
        raise ContractError('errors must be explicit strings for every request')
    success = [i for i, error in enumerate(errors) if not error]
    if completed != len(success) or not success:
        raise ContractError('completed count disagrees with request outcomes or is zero')
    if 'failed' in raw and count(raw['failed'], 'failed') != len(errors) - completed:
        raise ContractError('failed count disagrees with request outcomes')
    metrics = {k: [] for k in ('ttft_ms', 'tpot_ms', 'itl_ms', 'e2e_ms')}
    good = 0
    inputs, outputs = [], []
    for i in range(workload.num_prompts):
        q = number(raw['queue_times'][i], 'client queue') * 1000
        if q > policy.max_client_queue_ms:
            raise ContractError('load generator queue exceeds the frozen limit')
        inputs.append(count(raw['input_lens'][i], 'input length'))
        outputs.append(count(raw['output_lens'][i], 'output length'))
        # Failed requests stay in the denominator. Their missing token timings
        # are not converted to zero-latency successful requests.
        if errors[i]:
            continue
        if (inputs[-1] == 0 or outputs[-1] != workload.output_tokens
                or workload.input_tokens is not None and inputs[-1] != workload.input_tokens):
            raise ContractError('successful request has unexpected token counts')
        ttft = number(raw['ttfts'][i], 'TTFT', positive=True) * 1000
        latency = number(raw['latencies'][i], 'latency', positive=True) * 1000
        if latency > duration * 1000 + 1:
            raise ContractError('request latency exceeds its measurement window')
        if latency < ttft:
            raise ContractError('request completed before its first token')
        if not isinstance(raw['itls'][i], list) or not raw['itls'][i]:
            raise ContractError('multi-token response lacks streaming interval evidence')
        intervals = [number(x, 'streaming interval') * 1000 for x in raw['itls'][i]]
        if sum(intervals) > latency - ttft + 1:
            raise ContractError('streaming intervals exceed the post-first-token duration')
        # vLLM may report chunks rather than one timestamp per token. Do not
        # manufacture token timestamps or require len(itls)==output_tokens-1.
        tpot = (latency - ttft) / (outputs[-1] - 1)
        request = {'ttft_ms': ttft + q, 'tpot_ms': tpot,
                   'itl_ms': max(intervals), 'e2e_ms': latency + q}
        for key in ('ttft_ms', 'tpot_ms', 'e2e_ms'):
            metrics[key].append(request[key])
        metrics['itl_ms'].extend(intervals)
        good += all(request[key] <= limit for key, limit in policy.slo_ms.items())
    result = {key: percentile(values, policy.percentile) for key, values in metrics.items()}
    result.update({
        'goodput_rps': good / duration,
        'completed_rps': completed / duration,
        'output_tokens_per_s': sum(outputs[i] for i in success) / duration,
        'error_rate': 1 - completed / workload.num_prompts,
        'completed': completed, 'offered': workload.num_prompts,
        'duration_s': duration, 'input_lens': inputs, 'output_lens': outputs,
        'stream_interval_semantics': 'client-observed streaming chunks; not inferred token timestamps',
    })
    result['constraints_pass'] = (
        result['error_rate'] <= policy.max_error_rate + 1e-12
        and all(result[key] <= limit for key, limit in policy.slo_ms.items())
    )
    return result


def improvement(baseline: float, candidate: float, objective: str) -> float:
    if baseline <= 0 or not all(math.isfinite(v) for v in (baseline, candidate)):
        raise ContractError('objective needs a finite, positive incumbent measurement')
    return 100 * (candidate / baseline - 1 if objective == 'goodput_rps' else 1 - candidate / baseline)


def sign_pvalue(gains: list[float], floor: float) -> float:
    """One-sided exact paired sign test for median gain > the configured floor.

    One pair is a complete independent load window, not a token or request.
    Ties count against a claim. Bonferroni correction is applied by the caller.
    """
    n = len(gains)
    k = sum(value > floor for value in gains)
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def compare_windows(spec: ServingSpec, windows: list[dict]) -> dict:
    """Judge every frozen workload, including failed and neutral cells."""
    expected = {(w.id, n) for w in spec.workloads for n in range(spec.policy.repetitions)}
    got = [(w.get('workload_id'), w.get('repetition')) for w in windows]
    if len(got) != len(set(got)) or set(got) != expected:
        raise ContractError('missing, extra, or duplicate workload/repetition pairs')
    rows = []
    alpha = spec.policy.alpha / len(spec.workloads)
    for workload in spec.workloads:
        pairs = sorted((w for w in windows if w['workload_id'] == workload.id), key=lambda w: w['repetition'])
        baselines, candidates, gains = [], [], []
        for pair in pairs:
            base = summarize_window(pair['baseline'], workload, spec.policy)
            opt = summarize_window(pair['candidate'], workload, spec.policy)
            if base['input_lens'] != opt['input_lens']:
                raise ContractError('paired requests have different input lengths')
            # A failed baseline is not a valid incumbent workload. Establish
            # an SLO-feasible incumbent before claiming improvement against it.
            if not base['constraints_pass']:
                raise ContractError('incumbent violates frozen serving constraints')
            baselines.append(base)
            candidates.append(opt)
            gains.append(improvement(base[spec.policy.objective], opt[spec.policy.objective], spec.policy.objective))
        b = mean(x[spec.policy.objective] for x in baselines)
        c = mean(x[spec.policy.objective] for x in candidates)
        delta = improvement(b, c, spec.policy.objective)
        p = sign_pvalue(gains, spec.policy.min_improvement_pct)
        constraints = all(x['constraints_pass'] for x in candidates)
        # Protect each repeated window as well as the aggregate. A favorable
        # average must not mask a large intermittent regression.
        regressed = min(gains) < -spec.policy.regression_tolerance_pct
        status = ('REGRESSED' if regressed or not constraints else
                  'PASS' if delta >= spec.policy.min_improvement_pct and p <= alpha else 'NOISE')
        rows.append({'workload_id': workload.id, 'objective': spec.policy.objective,
                     'baseline': b, 'candidate': c, 'improvement_pct': delta,
                     'paired_gains_pct': gains, 'p_value': p, 'alpha_corrected': alpha,
                     'constraints_pass': constraints, 'status': status})
    passed = any(row['status'] == 'PASS' for row in rows)
    safe = all(row['status'] in {'PASS', 'NOISE'} for row in rows)
    return {'status': 'PASS' if passed and safe else 'FAIL',
            'objective': spec.policy.objective, 'rows': rows,
            'test': 'one-sided exact paired sign test; Bonferroni across frozen workloads',
            'scope': 'fixed offered-load objective; not a maximum sustainable capacity claim'}
