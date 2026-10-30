#!/usr/bin/env python3
"""Neuron interval adapter coverage for the unchanged Stage-2 math."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mine_trace.py"
_spec = importlib.util.spec_from_file_location("mine_trace_neuron", str(_SCRIPT))
mine_trace = importlib.util.module_from_spec(_spec)
sys.modules["mine_trace_neuron"] = mine_trace
_spec.loader.exec_module(mine_trace)


def test_load_normalized_neuron_events_uses_existing_row_shape(tmp_path):
    path = tmp_path / "events.json"
    path.write_text(
        json.dumps(
            {
                "schema": "ammo_intervals/1",
                "runtime_profile": "vllm-neuron",
                "source": "SystemProfileEvents.parquet",
                "profiler": {
                    "product": "neuron-explorer",
                    "product_version": "2.32.0",
                    "schema_version": "installed-schema",
                },
                "events": [
                    {
                        "start_ns": 0,
                        "end_ns": 100,
                        "symbol": "warmup",
                        "device_id": 0,
                        "stream_id": 0,
                        "process_id": 9,
                        "graph_id": "warmup",
                    },
                    {
                        "start_ns": 1_000_100,
                        "end_ns": 1_001_100,
                        "symbol": "decode_neff",
                        "device_id": 0,
                        "stream_id": 0,
                        "process_id": 9,
                        "graph_id": "decode",
                    },
                    {
                        "start_ns": 1_000_500,
                        "end_ns": 1_001_500,
                        "symbol": "decode_neff",
                        "device_id": 1,
                        "stream_id": 0,
                        "process_id": 10,
                        "graph_id": "decode",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    rows, meta, table = mine_trace.load_trace_rows(
        {"events_json": str(path)}
    )

    assert table == "AMMO_NORMALIZED_INTERVALS"
    assert meta["PROFILER_KIND"] == "neuron-explorer"
    assert meta["EXPORT_PRODUCT_VERSION"] == "2.32.0"
    assert rows[-1] == (
        1_000_500,
        1_001_500,
        "decode_neff",
        1,
        0,
        10,
        "decode",
    )


def test_neuron_trace_reuses_interval_union_and_f_e2e_math(tmp_path):
    events = tmp_path / "events.json"
    events.write_text(
        json.dumps(
            {
                "schema": "ammo_intervals/1",
                "runtime_profile": "vllm-neuron",
                "source": "system profile",
                "profiler": {"product": "neuron-explorer"},
                "events": [
                    {"start_ns": 0, "end_ns": 1000, "symbol": "warmup", "device_id": 0, "stream_id": 0, "process_id": 1, "graph_id": None},
                    {"start_ns": 2_000, "end_ns": 3_000, "symbol": "warmup", "device_id": 0, "stream_id": 0, "process_id": 1, "graph_id": None},
                    {"start_ns": 1_003_000, "end_ns": 1_004_500, "symbol": "decode_neff", "device_id": 0, "stream_id": 0, "process_id": 1, "graph_id": "decode"},
                    {"start_ns": 1_003_500, "end_ns": 1_005_000, "symbol": "decode_neff", "device_id": 1, "stream_id": 0, "process_id": 2, "graph_id": "decode"},
                ],
            }
        ),
        encoding="utf-8",
    )
    sweep = tmp_path / "e2e.json"
    # 2us merged busy per step * 511 steps = 1.022ms decode wall -> busy=1.
    decode = 2e-6 * 511
    sweep.write_text(
        json.dumps(
            {
                "workload": {"num_iters": 10, "output_len": 512},
                "results": [
                    {
                        "batch_size": 32,
                        "output_len": 512,
                        "baseline": {
                            "avg_s": decode / 0.8,
                            "decode_avg_s": decode,
                            "prefill_avg_s": decode / 4,
                            "tpot_s": 2e-6,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    config = {
        "round": 1,
        "artifact_dir": str(tmp_path),
        "arm": "baseline",
        "e2e_results": str(sweep),
        "segmentation": {"policy": "max_gap"},
        "traces": [{"bs": 32, "events_json": str(events), "label": "decode"}],
        "families": [
            {
                "label": "decode_neff",
                "symbol_patterns": ["^decode_neff$"],
                "physical_ceiling": 1.2,
                "prefill_active": False,
            }
        ],
        "allow_residual": 0,
    }

    mined = mine_trace.build(config, [])

    row = mined["per_bs"][0]
    assert row["mean_merged_busy_per_step_s"] == pytest.approx(2e-6)
    assert row["decode_busy"] == pytest.approx(1.0)
    assert row["decode_share_of_e2e"] == pytest.approx(0.8)
    assert row["families"][0]["f_e2e"] == pytest.approx(0.8)
    assert mined["provenance"]["profiler_kind"] == "neuron-explorer"
