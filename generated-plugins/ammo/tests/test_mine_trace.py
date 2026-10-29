"""Tests for mine_trace.py — the sole owner of every mined Stage-2 number.

Fixtures are SYNTHETIC CUPTI sqlite tables built in-test (a real nsys export is
~750 KB and is never committed). Each fixture carries only the columns the
script requires, so a missing-column regression fails here.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "mine_trace.py"
_spec = importlib.util.spec_from_file_location("mine_trace", str(_SCRIPT))
mine_trace = importlib.util.module_from_spec(_spec)
sys.modules["mine_trace"] = mine_trace
_spec.loader.exec_module(mine_trace)


ALL_COLUMNS = (
    "start INTEGER, end INTEGER, deviceId INTEGER, streamId INTEGER, "
    "globalPid INTEGER, demangledName INTEGER, graphId INTEGER"
)


def write_sqlite(
    path: Path,
    kernels,
    table: str = "CUPTI_ACTIVITY_KIND_KERNEL",
    columns: str = ALL_COLUMNS,
    meta: dict | None = None,
):
    """Build a minimal CUPTI-shaped export.

    `kernels` items are (start, end, symbol) or (start, end, symbol, device_id,
    global_pid, graph_id).
    """
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE StringIds (id INTEGER PRIMARY KEY, value TEXT)")
    con.execute(f"CREATE TABLE {table} ({columns})")
    con.execute("CREATE TABLE META_DATA_EXPORT (name TEXT, value TEXT)")
    defaults = {
        "EXPORT_PRODUCT_VERSION": "2026.3.1.157",
        "EXPORT_SCHEMA_VERSION": "3.27.0",
    }
    defaults.update(meta or {})
    con.executemany(
        "INSERT INTO META_DATA_EXPORT (name, value) VALUES (?, ?)", sorted(defaults.items())
    )
    symbols: dict[str, int] = {}
    field_names = [c.split()[0] for c in columns.split(", ")]
    for kernel in kernels:
        start, end, symbol = kernel[0], kernel[1], kernel[2]
        device_id = kernel[3] if len(kernel) > 3 else 0
        global_pid = kernel[4] if len(kernel) > 4 else 111
        graph_id = kernel[5] if len(kernel) > 5 else 1
        if symbol not in symbols:
            symbols[symbol] = len(symbols) + 1
            con.execute(
                "INSERT INTO StringIds (id, value) VALUES (?, ?)", (symbols[symbol], symbol)
            )
        values = {
            "start": start,
            "end": end,
            "deviceId": device_id,
            "streamId": 7,
            "globalPid": global_pid,
            "demangledName": symbols[symbol],
            "graphId": graph_id,
        }
        present = [f for f in field_names if f in values]
        con.execute(
            f"INSERT INTO {table} ({', '.join(present)}) "
            f"VALUES ({', '.join('?' for _ in present)})",
            [values[f] for f in present],
        )
    con.commit()
    con.close()


def transient_plus_step(step_kernels, gap_ns=1_000_000):
    """Prefix a 2-kernel capture-window transient separated by one big gap."""
    prefix = [(0, 1_000, "transient_setup"), (2_000, 3_000, "transient_setup")]
    shift = 3_000 + gap_ns
    return prefix + [
        (k[0] + shift, k[1] + shift) + tuple(k[2:]) for k in step_kernels
    ]


def sweep_json(
    bs=32,
    avg_s=10.0,
    decode_avg_s=9.0,
    prefill_avg_s=0.5,
    output_len=512,
    tpot_s=None,
    stored_share=None,
    extra=None,
):
    metrics = {
        "avg_s": avg_s,
        "decode_avg_s": decode_avg_s,
        "prefill_avg_s": prefill_avg_s,
    }
    if tpot_s is not None:
        metrics["tpot_s"] = tpot_s
    if stored_share is not None:
        metrics["decode_share_of_e2e"] = stored_share
    metrics.update(extra or {})
    return {
        "workload": {"num_iters": 10, "output_len": output_len},
        "results": [{"batch_size": bs, "output_len": output_len, "baseline": metrics}],
    }


def write_config(tmp_path: Path, config: dict, sweep: dict) -> Path:
    (tmp_path / "e2e_latency_results.json").write_text(json.dumps(sweep), encoding="utf-8")
    config.setdefault("round", 1)
    config.setdefault("artifact_dir", str(tmp_path))
    config.setdefault("arm", "baseline")
    config.setdefault("e2e_results", "e2e_latency_results.json")
    config.setdefault("segmentation", {"policy": "max_gap"})
    path = tmp_path / "mine_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def run(tmp_path: Path, config_path: Path, capsys=None):
    """Invoke main(); return (rc, mined_or_None, tables_or_None)."""
    out_json = tmp_path / "mined.json"
    out_md = tmp_path / "tables.md"
    rc = mine_trace.main(
        ["--config", str(config_path), "--out-json", str(out_json), "--out-md", str(out_md)]
    )
    mined = json.loads(out_json.read_text(encoding="utf-8")) if out_json.exists() else None
    tables = out_md.read_text(encoding="utf-8") if out_md.exists() else None
    return rc, mined, tables


# --- 1. union arithmetic never sums concurrent kernels -----------------------


def test_merged_busy_unions_a_constructed_overlap():
    # 100..200 and 150..300 overlap by 50: union 200, sum 250.
    intervals = [(100, 200), (150, 300), (400, 500)]
    assert mine_trace.merged_busy(intervals) == 300
    assert mine_trace.duration_sum(intervals) == 350


def test_overlapping_kernels_report_union_not_sum(tmp_path):
    # Two concurrent streams: 0..1000 and 500..1500 → union 1500, sum 2000.
    step = [
        (0, 1_000, "target_kernel", 0, 111, 1),
        (500, 1_500, "target_kernel", 0, 222, 1),
    ]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step))
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "target", "symbol_patterns": ["target_kernel"]}],
        },
        sweep_json(decode_avg_s=1.5e-6 * 511, tpot_s=1.5e-6),
    )
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    depth = mined["per_bs"][0]["depths"][0]
    assert depth["merged_busy_ns"] == 1_500
    assert depth["sum_ns"] == 2_000
    assert depth["overlap_ns"] == 500
    assert mined["per_bs"][0]["mean_merged_busy_per_step_s"] == pytest.approx(1.5e-6)


# --- 2. segmentation determinism --------------------------------------------


def test_max_gap_segmentation_is_deterministic_and_reports_separation(tmp_path):
    step = [(0, 1_000, "k"), (1_100, 2_000, "k")]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step, gap_ns=500_000))
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
        },
        sweep_json(decode_avg_s=1.9e-6 * 511, tpot_s=1.9e-6),
    )
    first = run(tmp_path, config)[1]["per_bs"][0]["depths"][0]
    second = run(tmp_path, config)[1]["per_bs"][0]["depths"][0]
    assert first["delimiter"] == second["delimiter"]
    assert first["delimiter"]["policy"] == "max_gap"
    assert first["delimiter"]["gap_ns"] == 500_000
    assert first["delimiter"]["at_index"] == 2
    assert first["delimiter"]["first_kernel_after"] == "k"
    # next gap is the 1000 ns intra-transient gap → separation 500x
    assert first["delimiter"]["separation_ratio"] == pytest.approx(500.0)
    assert first["n_kernels_window"] == 2


def test_delimiter_symbol_policy_segments_on_last_occurrence(tmp_path):
    kernels = [
        (0, 100, "warm"),
        (200, 300, "step_marker"),
        (400, 500, "k"),
        (600, 700, "k"),
    ]
    write_sqlite(tmp_path / "t.sqlite", kernels)
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "segmentation": {"policy": "delimiter_symbol", "symbol": "step_marker"},
            "families": [
                {"label": "marker", "symbol_patterns": ["step_marker"]},
                {"label": "k", "symbol_patterns": ["^k$"]},
            ],
        },
        sweep_json(decode_avg_s=3e-7 * 511, tpot_s=3e-7),
    )
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    depth = mined["per_bs"][0]["depths"][0]
    assert depth["delimiter"]["policy"] == "delimiter_symbol"
    assert depth["delimiter"]["occurrences"] == 1
    assert depth["n_kernels_window"] == 3


def test_low_separation_ratio_warns_without_failing(tmp_path):
    # Two comparable gaps → separation_ratio 1.25, below the default floor of 2.
    kernels = [
        (0, 100, "a"),
        (1_100, 1_200, "a"),
        (2_450, 2_550, "k"),
        (2_650, 2_750, "k"),
    ]
    write_sqlite(tmp_path / "t.sqlite", kernels)
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [
                {"label": "a", "symbol_patterns": ["^a$"]},
                {"label": "k", "symbol_patterns": ["^k$"]},
            ],
        },
        sweep_json(decode_avg_s=3e-7 * 511, tpot_s=3e-7),
    )
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    assert any("separation_ratio" in w for w in mined["warnings"])


# --- 3. denominator re-derivation ignores the stored share ------------------


def test_shares_are_rederived_and_stored_phase_sum_warns(tmp_path):
    # 17.437 ms/step x 511 steps ~= the 9 s decode wall → decode_busy ~0.99.
    step = [(0, 17_437_000, "k")]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step, gap_ns=20_000_000))
    avg_s, decode_avg_s, prefill_avg_s = 10.0, 9.0, 0.5
    phase_sum = decode_avg_s / (prefill_avg_s + decode_avg_s)
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
        },
        sweep_json(
            avg_s=avg_s,
            decode_avg_s=decode_avg_s,
            prefill_avg_s=prefill_avg_s,
            tpot_s=decode_avg_s / 511,
            stored_share=phase_sum,
        ),
    )
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    row = mined["per_bs"][0]
    assert row["decode_share_of_e2e"] == pytest.approx(decode_avg_s / avg_s)
    assert row["decode_share_of_e2e"] != pytest.approx(phase_sum)
    assert row["prefill_share"] == pytest.approx((avg_s - decode_avg_s) / avg_s)
    assert row["prefill_share"] != pytest.approx(prefill_avg_s / avg_s)
    assert row["stored_decode_share_of_e2e"] == pytest.approx(phase_sum)
    assert any("stored decode_share_of_e2e" in w for w in mined["warnings"])
    # No marker field is needed: the mismatch alone triggers the warning.
    assert "decode_share_of_phase_sum" not in mined["config_echo"]


# --- 4. residual / exhaustiveness -------------------------------------------


def test_non_exhaustive_partition_is_fatal_with_no_output(tmp_path):
    step = [(0, 1_000, "k"), (1_100, 2_000, "unmapped_kernel")]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step))
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
        },
        sweep_json(decode_avg_s=1.9e-6 * 511, tpot_s=1.9e-6),
    )
    rc, mined, tables = run(tmp_path, config)
    assert rc == 1
    assert mined is None and tables is None


def test_allow_residual_lets_a_disclosed_remainder_through(tmp_path):
    step = [(0, 9_900, "k"), (10_000, 10_100, "unmapped_kernel")]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step))
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
            "allow_residual": 2.0,
        },
        sweep_json(decode_avg_s=1.0e-5 * 511, tpot_s=1.0e-5),
    )
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    row = mined["per_bs"][0]
    assert 0 < row["residual_pct"] <= 2.0
    assert row["partition_coverage"] < 1.0
    assert row["residual_symbols"] == ["unmapped_kernel"]
    assert any("__RESIDUAL__" in w for w in mined["warnings"])


# --- 5. decode_busy bounds --------------------------------------------------


def test_decode_busy_above_one_is_fatal(tmp_path):
    # 1 ms/step x 511 steps = 0.511 s of kernel work against a 0.2 s decode wall.
    step = [(0, 1_000_000, "k")]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step, gap_ns=2_000_000))
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
        },
        sweep_json(avg_s=1.0, decode_avg_s=0.2, prefill_avg_s=0.05),
    )
    rc, mined, tables = run(tmp_path, config)
    assert rc == 1
    assert mined is None and tables is None


def test_decode_busy_below_floor_is_fatal(tmp_path):
    step = [(0, 1_000, "k")]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step))
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
        },
        # 1 us/step x 511 = 0.000511 s against a 9 s decode wall → busy ~0.00006.
        sweep_json(avg_s=10.0, decode_avg_s=9.0, tpot_s=9.0 / 511),
    )
    rc, mined, tables = run(tmp_path, config)
    assert rc == 1
    assert mined is None and tables is None


def test_merged_busy_over_span_is_fatal():
    with pytest.raises(mine_trace.Fatal, match="exceeds window span"):
        mine_trace.check_window_integrity("synthetic", [(0, 100)], 200, 100)


# --- 6. step-count ladder + step_count_source ------------------------------


def _ladder_config(tmp_path, sweep):
    step = [(0, 1_000_000, "k")]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step, gap_ns=5_000_000))
    return write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
        },
        sweep,
    )


def test_step_count_rung1_spec_decode_counters_win(tmp_path):
    sweep = sweep_json(
        avg_s=1.0,
        decode_avg_s=0.9,
        output_len=512,
        tpot_s=0.9 / 511,
        extra={"spec_decode": {"num_drafts": 256 * 10 * 32}},
    )
    rc, mined, _ = run(tmp_path, _ladder_config(tmp_path, sweep))
    assert rc == 0
    row = mined["per_bs"][0]
    assert row["step_count_source"] == "spec_decode.num_drafts"
    assert row["n_decode_steps"] == pytest.approx(256.0)
    assert set(row["step_count_candidates"]) == {
        "spec_decode.num_drafts",
        "decode_avg_s/tpot_s",
        "output_len-1",
    }


def test_step_count_rung2_tpot_is_used_when_no_spec_decode(tmp_path):
    sweep = sweep_json(avg_s=1.0, decode_avg_s=0.9, output_len=512, tpot_s=0.9 / 511)
    rc, mined, _ = run(tmp_path, _ladder_config(tmp_path, sweep))
    assert rc == 0
    row = mined["per_bs"][0]
    assert row["step_count_source"] == "decode_avg_s/tpot_s"
    assert row["n_decode_steps"] == pytest.approx(511.0)
    assert not any("tpot_s absent" in w for w in mined["warnings"])


def test_step_count_rung3_output_len_warns(tmp_path):
    sweep = sweep_json(avg_s=1.0, decode_avg_s=0.9, output_len=512)
    rc, mined, _ = run(tmp_path, _ladder_config(tmp_path, sweep))
    assert rc == 0
    row = mined["per_bs"][0]
    assert row["step_count_source"] == "output_len-1"
    assert row["n_decode_steps"] == pytest.approx(511.0)
    assert any("output_len-1" in w for w in mined["warnings"])


def test_step_count_rungs_disagreeing_is_fatal(tmp_path):
    # tpot implies ~256 steps; output_len-1 implies 511 → 100% apart.
    sweep = sweep_json(avg_s=1.0, decode_avg_s=0.9, output_len=512, tpot_s=0.9 / 256)
    rc, mined, tables = run(tmp_path, _ladder_config(tmp_path, sweep))
    assert rc == 1
    assert mined is None and tables is None


def test_step_count_unresolvable_is_fatal(tmp_path):
    sweep = sweep_json(avg_s=1.0, decode_avg_s=0.9, output_len=1)
    sweep["workload"].pop("output_len")
    rc, mined, tables = run(tmp_path, _ladder_config(tmp_path, sweep))
    assert rc == 1
    assert mined is None and tables is None


# --- 7. kernel-table probe + provenance -----------------------------------


@pytest.mark.parametrize(
    "table", ["CUPTI_ACTIVITY_KIND_KERNEL", "CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL"]
)
def test_kernel_table_probe_accepts_both_names(tmp_path, table):
    step = [(0, 1_000_000, "k")]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step, gap_ns=5_000_000), table=table)
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
        },
        sweep_json(avg_s=1.0, decode_avg_s=0.9, tpot_s=0.9 / 511),
    )
    rc, mined, tables = run(tmp_path, config)
    assert rc == 0
    assert mined["provenance"]["kernel_table"] == table
    assert mined["provenance"]["nsys_product_version"] == "2026.3.1.157"
    assert mined["provenance"]["export_schema_version"] == "3.27.0"
    assert mined["schema"] == "mine_trace/1"
    assert tables.startswith(mine_trace.GENERATED_HEADER)
    assert "## Workload Dilution (per BS)" in tables
    assert "## Top Components (by f_e2e)" in tables


def test_missing_kernel_table_is_fatal(tmp_path):
    con = sqlite3.connect(str(tmp_path / "t.sqlite"))
    con.execute("CREATE TABLE StringIds (id INTEGER PRIMARY KEY, value TEXT)")
    con.execute("CREATE TABLE META_DATA_EXPORT (name TEXT, value TEXT)")
    con.commit()
    con.close()
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
        },
        sweep_json(tpot_s=0.9 / 511),
    )
    rc, mined, tables = run(tmp_path, config)
    assert rc == 1
    assert mined is None and tables is None


# --- 8. missing required column --------------------------------------------


def test_missing_required_column_is_fatal(tmp_path):
    # graphId dropped: in_graph_fraction would silently default to 0 without this.
    columns = (
        "start INTEGER, end INTEGER, deviceId INTEGER, streamId INTEGER, "
        "globalPid INTEGER, demangledName INTEGER"
    )
    step = [(0, 1_000_000, "k")]
    write_sqlite(
        tmp_path / "t.sqlite",
        transient_plus_step(step, gap_ns=5_000_000),
        columns=columns,
    )
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
        },
        sweep_json(avg_s=1.0, decode_avg_s=0.9, tpot_s=0.9 / 511),
    )
    rc, mined, tables = run(tmp_path, config)
    assert rc == 1
    assert mined is None and tables is None


# --- 9. per-rank spread + in-graph fraction --------------------------------


def test_per_rank_spread_warns_and_in_graph_fraction_is_published(tmp_path):
    # rank A is gap-free; rank B idles half its span → spread ~0.5.
    step = [
        (0, 1_000_000, "k", 0, 111, 1),
        (0, 500_000, "k", 1, 222, None),
    ]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step, gap_ns=5_000_000))
    # widen rank B's span so its busy/span falls well below rank A's 1.0
    con = sqlite3.connect(str(tmp_path / "t.sqlite"))
    con.execute("UPDATE CUPTI_ACTIVITY_KIND_KERNEL SET end = start + 250000 WHERE deviceId = 1")
    con.execute(
        "INSERT INTO CUPTI_ACTIVITY_KIND_KERNEL "
        "(start, end, deviceId, streamId, globalPid, demangledName, graphId) "
        "SELECT start + 750000, end + 750000, 1, 7, 222, demangledName, NULL "
        "FROM CUPTI_ACTIVITY_KIND_KERNEL WHERE deviceId = 1 LIMIT 1"
    )
    con.commit()
    con.close()
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
        },
        sweep_json(avg_s=1.0, decode_avg_s=0.9, tpot_s=0.9 / 511),
    )
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    depth = mined["per_bs"][0]["depths"][0]
    assert len(depth["per_rank"]) == 2
    assert depth["per_rank_busy_spread"] > mine_trace.MAX_RANK_BUSY_SPREAD
    assert any("per-rank busy spread" in w for w in mined["warnings"])
    assert 0.0 < depth["in_graph_fraction"] < 1.0


# --- 10. stable mined.json subset other tools may read ---------------------


def test_stable_subset_and_f_e2e_are_structurally_consistent(tmp_path):
    step = [
        (0, 700_000, "big_gemm"),
        (700_100, 1_000_000, "small_norm"),
    ]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step, gap_ns=5_000_000))
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite", "label": "step 512"}],
            "families": [
                {"label": "gemm", "symbol_patterns": ["big_gemm"], "physical_ceiling": 2.0,
                 "prefill_active": True},
                {"label": "norm", "symbol_patterns": ["small_norm"], "prefill_active": False},
            ],
        },
        sweep_json(avg_s=1.0, decode_avg_s=0.9, prefill_avg_s=0.05, tpot_s=0.9 / 511),
    )
    rc, mined, tables = run(tmp_path, config)
    assert rc == 0
    row = mined["per_bs"][0]
    for key in (
        "bs",
        "trace",
        "decode_avg_s",
        "avg_s",
        "decode_share_of_e2e",
        "decode_busy",
        "n_decode_steps",
        "step_count_source",
        "in_graph_fraction",
        "families",
        "residual_pct",
        "partition_coverage",
    ):
        assert key in row, key
    assert row["decode_share_of_e2e"] == pytest.approx(row["decode_avg_s"] / row["avg_s"])
    assert sum(f["f_decode"] for f in row["families"]) == pytest.approx(1.0)
    for fam in row["families"]:
        assert fam["f_e2e"] == pytest.approx(
            fam["f_decode"] * row["decode_busy"] * row["decode_share_of_e2e"]
        )
    assert row["families"][0]["label"] == "gemm"
    assert row["families"][0]["addressable_e2e"] == pytest.approx(
        row["families"][0]["f_e2e"] * 0.5
    )
    assert "2.000x" in tables and "(disclosed)" in tables


# --- 11. generated tables.md stays machine-readable for the mining enrichment -


def test_generated_tables_parse_through_ammo_state_enrichment(tmp_path):
    """ammo_state.parse_mining_md feeds the Stage-7 stop decision from this table."""
    state_script = Path(__file__).resolve().parents[1] / "scripts" / "ammo_state.py"
    spec = importlib.util.spec_from_file_location("ammo_state_for_mine", str(state_script))
    ammo_state = importlib.util.module_from_spec(spec)
    sys.modules["ammo_state_for_mine"] = ammo_state
    spec.loader.exec_module(ammo_state)

    # 1.7613 ms/step x 511 ~= the 0.9 s decode wall, so decode_busy ~1.0 and the
    # gemm family dominates — the realistic shape of a decode-bound campaign.
    step = [(0, 1_232_910, "big_gemm"), (1_233_010, 1_761_300, "small_norm")]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step, gap_ns=5_000_000))
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
            "families": [
                {"label": "gemm", "symbol_patterns": ["big_gemm"], "physical_ceiling": 2.0},
                {"label": "norm", "symbol_patterns": ["small_norm"]},
            ],
        },
        sweep_json(avg_s=1.0, decode_avg_s=0.9, prefill_avg_s=0.05, tpot_s=0.9 / 511),
    )
    rc, mined, tables = run(tmp_path, config)
    assert rc == 0
    parsed = ammo_state.parse_mining_md(tables)
    row = mined["per_bs"][0]
    assert parsed["top_component"] == "gemm"
    assert parsed["top_f_e2e_pct"] == pytest.approx(round(row["families"][0]["f_e2e"], 4) * 100)
    assert parsed["top_addressable_e2e_pct"] == pytest.approx(
        round(row["families"][0]["addressable_e2e"], 4) * 100
    )
    assert parsed["decode_frac"] == pytest.approx(round(row["decode_share_of_e2e"], 4))


# --- 12. the two variant copies stay byte-identical -------------------------


def test_variant_copies_are_byte_identical():
    """mine_trace.py is pure computation: it must never fork per variant."""
    session_root = Path(__file__).resolve().parents[4]
    copies = [
        session_root / variant / "skills" / "ammo" / "scripts" / "mine_trace.py"
        for variant in (".claude", ".codex")
    ]
    present = [c for c in copies if c.is_file()]
    if len(present) < 2:
        pytest.skip("only one variant is materialized in this tree")
    assert present[0].read_bytes() == present[1].read_bytes()


# --- 13. family selection: max f_e2e over buckets, floor, cap ---------------

#: decode_busy and decode_share used by the selection fixtures, so a family's
#: f_e2e is its share of the step's busy time times BUSY x SHARE.
BUSY, SHARE = 0.95, 0.9


def _sequential_step(kernels, gap_ns=100):
    """(duration_ns, symbol) items laid end to end with a small gap."""
    out, cursor = [], 0
    for duration, symbol in kernels:
        out.append((cursor, cursor + duration, symbol))
        cursor += duration + gap_ns
    return out


def _write_state(tmp_path, min_e2e_improvement_pct=0.5, current_round=1):
    state = {"campaign": {"current_round": current_round,
                          "config": {"min_e2e_improvement_pct": min_e2e_improvement_pct}}}
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _multi_bucket_config(tmp_path, steps_by_bs, families, extra=None):
    """One sqlite per bucket; each sweep row puts decode_busy at BUSY, share at SHARE."""
    results, traces = [], []
    for bs, kernels in sorted(steps_by_bs.items()):
        step = _sequential_step(kernels)
        name = f"bs{bs}.sqlite"
        write_sqlite(tmp_path / name, transient_plus_step(step, gap_ns=5_000_000))
        busy_s = sum(duration for duration, _ in kernels) / 1e9
        decode_avg_s = busy_s * 511 / BUSY
        results.append(
            {
                "batch_size": bs,
                "output_len": 512,
                "baseline": {
                    "avg_s": decode_avg_s / SHARE,
                    "decode_avg_s": decode_avg_s,
                    "prefill_avg_s": decode_avg_s / SHARE * 0.05,
                    "tpot_s": decode_avg_s / 511,
                },
            }
        )
        traces.append({"bs": bs, "sqlite": name})
    sweep = {"workload": {"num_iters": 10, "output_len": 512}, "results": results}
    config = {"traces": traces, "families": families}
    config.update(extra or {})
    return write_config(tmp_path, config, sweep)


def _abc_families():
    return [
        {"label": "a", "symbol_patterns": ["^ka$"], "physical_ceiling": 2.0},
        {"label": "b", "symbol_patterns": ["^kb$"]},
        {"label": "c", "symbol_patterns": ["^kc$"]},
    ]


#: BS1 ranks a, c, b; BS32 ranks b, c, a; the mean ties b and c. Only the
#: maximum over buckets ranks b (0.513 at BS32), a (0.4275 at BS1), c (0.342 at BS1).
_ABC_STEPS = {
    1: [(500_000, "ka"), (100_000, "kb"), (400_000, "kc")],
    32: [(100_000, "ka"), (600_000, "kb"), (300_000, "kc")],
}


def test_selection_ranks_families_by_their_highest_f_e2e_across_buckets(tmp_path):
    config = _multi_bucket_config(tmp_path, _ABC_STEPS, _abc_families())
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    selected = mined["selected_families"]
    assert [s["label"] for s in selected] == ["b", "a", "c"]
    assert [s["rank"] for s in selected] == [1, 2, 3]
    assert [s["bs"] for s in selected] == [32, 1, 1]
    scale = BUSY * SHARE
    assert selected[0]["f_e2e"] == pytest.approx(0.6 * scale)
    assert selected[1]["f_e2e"] == pytest.approx(0.5 * scale)
    assert selected[0]["f_e2e_by_bs"] == {
        "1": pytest.approx(0.1 * scale),
        "32": pytest.approx(0.6 * scale),
    }
    # The fields a family packet cites travel with the entry.
    assert selected[1]["physical_ceiling"] == 2.0
    assert selected[1]["addressable_e2e"] == pytest.approx(0.5 * scale * 0.5)
    assert selected[1]["symbol_patterns"] == ["^ka$"]
    assert mined["selection"]["eligible"] == ["b", "a", "c"]
    assert mined["selection"]["dropped_by_cap"] == []
    assert mined["selection"]["cross_cutting"] == "cross_cutting"


def test_selection_floor_is_the_campaign_min_e2e_improvement_pct(tmp_path):
    # 35% in state.json is a 0.35 share: c peaks at 0.342 and drops out.
    _write_state(tmp_path, min_e2e_improvement_pct=35.0)
    config = _multi_bucket_config(tmp_path, _ABC_STEPS, _abc_families())
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    assert [s["label"] for s in mined["selected_families"]] == ["b", "a"]
    assert mined["selection"]["f_e2e_floor"] == pytest.approx(0.35)
    assert mined["selection"]["eligible"] == ["b", "a"]
    assert not any("min_e2e_improvement_pct" in w for w in mined["warnings"])


def test_selection_floor_without_state_json_is_the_schema_default_and_warns(tmp_path):
    config = _multi_bucket_config(tmp_path, _ABC_STEPS, _abc_families())
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    assert mined["selection"]["f_e2e_floor"] == mine_trace.DEFAULT_MIN_E2E_IMPROVEMENT_PCT / 100
    assert mine_trace.DEFAULT_MIN_E2E_IMPROVEMENT_PCT == 0.5
    assert any("min_e2e_improvement_pct" in w for w in mined["warnings"])


def test_selection_cap_is_five_and_lists_the_families_it_drops(tmp_path):
    # Seven families above 0.5% of E2E and one below it (0.2% x 0.855 = 0.17%).
    _write_state(tmp_path, min_e2e_improvement_pct=0.5)
    labels = ["f1", "f2", "f3", "f4", "f5", "f6", "f7"]
    kernels = [(100_000 + 10_000 * i, f"k_{label}") for i, label in enumerate(labels)]
    kernels.append((2_000, "k_tiny"))
    families = [
        {"label": label, "symbol_patterns": [f"^k_{label}$"]} for label in labels + ["tiny"]
    ]
    config = _multi_bucket_config(tmp_path, {8: kernels}, families)
    rc, mined, tables = run(tmp_path, config)
    assert rc == 0
    selection = mined["selection"]
    assert selection["f_e2e_floor"] == pytest.approx(0.005)
    assert selection["max_families"] == mine_trace.SELECTION_MAX_FAMILIES == 5
    assert [s["label"] for s in mined["selected_families"]] == ["f7", "f6", "f5", "f4", "f3"]
    assert selection["dropped_by_cap"] == ["f2", "f1"]
    assert "tiny" not in selection["eligible"]
    # A family the cap drops still has its row in tables.md.
    assert "| f1 | 8 |" in tables and "| f2 | 8 |" in tables


def test_a_config_selection_block_is_refused(tmp_path):
    config = _multi_bucket_config(
        tmp_path, _ABC_STEPS, _abc_families(), {"selection": {"f_e2e_floor": 0.35}}
    )
    rc, mined, tables = run(tmp_path, config)
    assert rc == 1
    assert mined is None and tables is None


def test_a_family_labelled_cross_cutting_is_refused(tmp_path):
    families = _abc_families()
    families[2]["label"] = mine_trace.CROSS_CUTTING_LABEL
    config = _multi_bucket_config(tmp_path, _ABC_STEPS, families)
    rc, mined, tables = run(tmp_path, config)
    assert rc == 1
    assert mined is None and tables is None


def test_selection_never_picks_the_residual(tmp_path):
    kernels = [(900_000, "ka"), (50_000, "unmapped")]
    config = _multi_bucket_config(
        tmp_path, {8: kernels}, _abc_families()[:1], {"allow_residual": 10.0}
    )
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    assert mine_trace.RESIDUAL_LABEL in {f["label"] for f in mined["per_bs"][0]["families"]}
    assert [s["label"] for s in mined["selected_families"]] == ["a"]


def test_mined_json_with_selection_still_feeds_the_mining_enrichment(tmp_path):
    state_script = Path(__file__).resolve().parents[1] / "scripts" / "ammo_state.py"
    spec = importlib.util.spec_from_file_location("ammo_state_for_select", str(state_script))
    ammo_state = importlib.util.module_from_spec(spec)
    sys.modules["ammo_state_for_select"] = ammo_state
    spec.loader.exec_module(ammo_state)

    config = _multi_bucket_config(tmp_path, _ABC_STEPS, _abc_families())
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    assert mined["schema"] == "mine_trace/1"
    assert mined["selected_families"]
    parsed = ammo_state.parse_mined_json(mined)
    # The enrichment keeps its own mean-over-buckets ranking; it only must still parse.
    assert {c["name"] for c in parsed["component_breakdown"]} == {"a", "b", "c"}
    assert parsed["decode_frac"] == pytest.approx(SHARE)


# --- 14. one ordered kernel chain per selected family -----------------------


def test_chain_lists_one_decode_step_in_trace_order_with_counts_and_durations(tmp_path):
    layer = [(100_000, "zeta_gemm_qkv"), (20_000, "norm"), (50_000, "alpha_gemm_out"),
             (20_000, "norm")]
    kernels = layer + layer + [(200_000, "beta_gemm_lm_head")]
    step = _sequential_step(kernels)
    # A gemm in the transient prefix sits outside the step window.
    prefix = [(0, 1_000, "zeta_gemm_qkv"), (2_000, 3_000, "transient_setup")]
    shift = 3_000 + 5_000_000
    rows = prefix + [(s + shift, e + shift, sym) for s, e, sym in step]
    write_sqlite(tmp_path / "t.sqlite", rows)
    busy_s = sum(d for d, _ in kernels) / 1e9
    decode_avg_s = busy_s * 511 / BUSY
    config = write_config(
        tmp_path,
        {
            "traces": [{"bs": 8, "sqlite": "t.sqlite"}],
            "families": [
                {"label": "gemm", "symbol_patterns": ["gemm"]},
                {"label": "norm", "symbol_patterns": ["^norm$"]},
            ],
        },
        sweep_json(
            bs=8, avg_s=decode_avg_s / SHARE, decode_avg_s=decode_avg_s,
            prefill_avg_s=0.01 * decode_avg_s, tpot_s=decode_avg_s / 511,
        ),
    )
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    by_label = {s["label"]: s for s in mined["selected_families"]}
    assert set(by_label) == {"gemm", "norm"}
    chain = by_label["gemm"]["chain"]
    assert chain["bs"] == 8
    assert chain["source"].endswith("t.sqlite")
    assert chain["n_ranks"] == 1
    assert chain["n_kernels"] == 5
    assert chain["sum_ns"] == 500_000
    # Trace order of first launch: not alphabetical, not by duration.
    assert [k["symbol"] for k in chain["kernels"]] == [
        "zeta_gemm_qkv", "alpha_gemm_out", "beta_gemm_lm_head",
    ]
    assert [k["count"] for k in chain["kernels"]] == [2, 2, 1]
    assert [k["sum_ns"] for k in chain["kernels"]] == [200_000, 100_000, 200_000]
    assert [k["mean_ns"] for k in chain["kernels"]] == [100_000, 50_000, 200_000]
    starts = {sym: s for s, _, sym in reversed(step)}
    assert [k["first_offset_ns"] for k in chain["kernels"]] == [
        starts["zeta_gemm_qkv"], starts["alpha_gemm_out"], starts["beta_gemm_lm_head"],
    ]
    norm = by_label["norm"]["chain"]["kernels"]
    assert [(k["symbol"], k["count"], k["sum_ns"]) for k in norm] == [("norm", 4, 80_000)]
    # Chains travel with the selection only; the per-depth output stays as it was.
    assert "_chains" not in mined["per_bs"][0]["depths"][0]


def test_chain_comes_from_the_bucket_that_sets_the_family_rank(tmp_path):
    config = _multi_bucket_config(tmp_path, _ABC_STEPS, _abc_families())
    rc, mined, _ = run(tmp_path, config)
    assert rc == 0
    for entry in mined["selected_families"]:
        assert entry["chain"]["bs"] == entry["bs"]
    b_chain = mined["selected_families"][0]["chain"]
    assert b_chain["source"].endswith("bs32.sqlite")
    assert [(k["symbol"], k["count"], k["sum_ns"]) for k in b_chain["kernels"]] == [
        ("kb", 1, 600_000)
    ]


# --- 15. round defaults to state.json -----------------------------------------


def _round_config(tmp_path):
    step = [(0, 1_000_000, "k")]
    write_sqlite(tmp_path / "t.sqlite", transient_plus_step(step, gap_ns=5_000_000))
    config = {
        "artifact_dir": str(tmp_path),
        "traces": [{"bs": 32, "sqlite": "t.sqlite"}],
        "families": [{"label": "k", "symbol_patterns": ["^k$"]}],
    }
    path = write_config(tmp_path, config, sweep_json(avg_s=1.0, decode_avg_s=0.9,
                                                     tpot_s=0.9 / 511))
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("round")
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_round_comes_from_state_json_when_the_config_has_none(tmp_path):
    config = _round_config(tmp_path)
    (tmp_path / "state.json").write_text(
        json.dumps({"campaign": {"current_round": 3}}), encoding="utf-8"
    )
    assert mine_trace.main(["--config", str(config)]) == 0
    assert (tmp_path / "rounds" / "3" / "mining" / "mined.json").is_file()
    assert (tmp_path / "rounds" / "3" / "mining" / "tables.md").is_file()
    assert not (tmp_path / "rounds" / "1").exists()


def test_round_without_state_json_falls_back_to_one_and_warns(tmp_path):
    config = _round_config(tmp_path)
    assert mine_trace.main(["--config", str(config)]) == 0
    mined_path = tmp_path / "rounds" / "1" / "mining" / "mined.json"
    mined = json.loads(mined_path.read_text(encoding="utf-8"))
    assert any("current_round" in w for w in mined["warnings"])


# --- 16. per-bucket slicer: cut each bucket, end at a complete decode step ---

SESSION_EPOCH_NS = 1_700_000_000_000_000_000
STEP_KERNELS = (("step_start", 10_000), ("gemm", 30_000), ("sampler", 10_000))
STEP_PERIOD_NS = 60_000
SLICE_COLUMNS = ALL_COLUMNS + ", gridX INTEGER"


def _wall(trace_ns):
    """Trace ns -> wall-clock seconds, exact in decimal like profile_meta."""
    return float(f"{(SESSION_EPOCH_NS + trace_ns) // 1_000}e-6")


def _bucket_steps(first_ns, n_steps, bs, grids=None, ranks=((0, 111, 0),)):
    """n_steps full steps, then a step cut after its first kernel (the partial tail).

    ``ranks`` items are (device_id, global_pid, lag_ns); every rank runs the same
    steps, shifted by its lag, as TP ranks do.
    """
    rows = []
    for device, pid, lag in ranks:
        base = first_ns + lag
        rows.append((base - 40_000, base - 20_000, "prefill_attn", device, pid, 1, bs))
        for i in range(n_steps + 1):
            cursor = base + i * STEP_PERIOD_NS
            kernels = STEP_KERNELS if i < n_steps else STEP_KERNELS[:1]
            for symbol, duration in kernels:
                grid = (grids or {}).get(i, bs) if symbol == "sampler" else 1
                rows.append((cursor, cursor + duration, symbol, device, pid, 1, grid))
                cursor += duration + 5_000
    return sorted(rows)


def _write_full_export(path, kernel_rows):
    """A full nsys-shaped export: kernels, strings, session start, timed and untimed extras."""
    write_sqlite(path, [r[:6] for r in kernel_rows], columns=SLICE_COLUMNS)
    con = sqlite3.connect(str(path))
    ids = dict(con.execute("SELECT value, id FROM StringIds"))
    for row in kernel_rows:
        con.execute(
            "UPDATE CUPTI_ACTIVITY_KIND_KERNEL SET gridX = ? "
            "WHERE start = ? AND demangledName = ? AND deviceId = ?",
            (row[6], row[0], ids[row[2]], row[3]),
        )
    con.execute("CREATE TABLE TARGET_INFO_SESSION_START_TIME "
                "(utcEpochNs INTEGER, utcTime TEXT, localTime TEXT)")
    con.execute("INSERT INTO TARGET_INFO_SESSION_START_TIME VALUES (?, 'utc', 'local')",
                (SESSION_EPOCH_NS,))
    con.execute("CREATE TABLE CUPTI_ACTIVITY_KIND_RUNTIME "
                "(start INTEGER, end INTEGER, correlationId INTEGER)")
    con.executemany("INSERT INTO CUPTI_ACTIVITY_KIND_RUNTIME VALUES (?, ?, ?)",
                    [(r[0] - 1_000, r[0] - 500, i) for i, r in enumerate(kernel_rows)])
    con.execute("CREATE TABLE NVTX_EVENTS (start INTEGER, end INTEGER, text TEXT)")
    con.execute("INSERT INTO NVTX_EVENTS VALUES (1200000, NULL, 'mark in bs1 wave')")
    con.execute("INSERT INTO NVTX_EVENTS VALUES (100, NULL, 'mark before any wave')")
    con.execute("CREATE TABLE TARGET_INFO_GPU (id INTEGER, name TEXT)")
    con.execute("INSERT INTO TARGET_INFO_GPU VALUES (0, 'NVIDIA L40S')")
    con.commit()
    con.close()


def _slice_campaign(tmp_path, segmentation=None, bs8_grids=None, ranks=((0, 111, 0),),
                    drop=None):
    """Campaign tree with one full export holding a warmup wave and two profiled buckets.

    ``drop`` removes one kernel row from the bs8 bucket before the export is written.
    """
    nsys_dir = tmp_path / "rounds" / "1" / "profiling" / "nsys"
    nsys_dir.mkdir(parents=True)
    warmup = _bucket_steps(300_000, 3, 1, ranks=ranks)
    bs1 = _bucket_steps(1_100_000, 5, 1, ranks=ranks)
    bs8 = _bucket_steps(2_100_000, 6, 8, grids=bs8_grids, ranks=ranks)
    written_bs8 = [r for r in bs8 if r != drop]
    _write_full_export(nsys_dir / "baseline.sqlite", warmup + bs1 + written_bs8)
    # Each wave ends 2 us after its last partial-step kernel ends.
    def wave(index, bs, rows, warm):
        return {
            "index": index, "label": "baseline", "tag": f"bs{bs}", "batch_size": bs,
            "warmup": warm, "profiled": not warm, "num_errors": 0,
            "start_unix": _wall(rows[0][0] - 50_000),
            "end_unix": _wall(max(r[1] for r in rows) + 2_000),
        }
    meta = {"schema": "AMMO_CAPTURE_V2", "nsys_report": "nsys/baseline.nsys-rep",
            "waves": [wave(1, 1, warmup, True), wave(2, 1, bs1, False),
                      wave(3, 8, bs8, False)]}
    sweeps = tmp_path / "rounds" / "1" / "sweeps" / "profiling"
    sweeps.mkdir(parents=True)
    (sweeps / "e2e_latency_results.json").write_text(
        json.dumps({"results": [], "profile_meta": meta}), encoding="utf-8"
    )
    config = {
        "round": 1,
        "artifact_dir": str(tmp_path),
        "segmentation": segmentation or {"policy": "delimiter_symbol", "symbol": "step_start"},
        "families": [{"label": "all", "symbol_patterns": ["."]}],
        "traces": [],
    }
    path = tmp_path / "mine_config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path, nsys_dir, bs1, bs8


def _tables(path):
    con = sqlite3.connect(str(path))
    try:
        return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()


def _kernel_rows(path, with_rank=False):
    rank = ", k.deviceId" if with_rank else ""
    con = sqlite3.connect(str(path))
    try:
        return con.execute(
            f"SELECT k.start, k.end, s.value, k.gridX{rank} FROM CUPTI_ACTIVITY_KIND_KERNEL k "
            f"JOIN StringIds s ON s.id = k.demangledName ORDER BY k.start, k.end"
        ).fetchall()
    finally:
        con.close()


def test_slice_ends_each_bucket_at_a_complete_decode_step(tmp_path):
    config, nsys_dir, bs1, bs8 = _slice_campaign(tmp_path)
    full = nsys_dir / "baseline.sqlite"
    before = full.read_bytes()
    assert mine_trace.main(["slice", "--config", str(config)]) == 0
    assert full.read_bytes() == before
    segmentation = {"policy": "delimiter_symbol", "symbol": "step_start"}
    for bs, rows, n_steps in ((1, bs1, 5), (8, bs8, 6)):
        out = nsys_dir / f"baseline_bs{bs}.sqlite"
        kept = _kernel_rows(out)
        # Only this bucket's profiled wave, and nothing of the partial last step.
        assert kept[0][0] >= rows[0][0]
        assert kept[-1][1] <= rows[-1][0]
        starts = [r for r in kept if r[2] == "step_start"]
        # The partial step's delimiter closes the last full step, so every full step stays.
        assert len(starts) == n_steps
        assert [r[2] for r in kept[-3:]] == ["step_start", "gemm", "sampler"]
        # mine_trace reads the slice from its last delimiter to the end as one step.
        depth = mine_trace.mine_one_trace(
            {"sqlite": str(out)}, mine_trace.compile_families([{"label": "all",
                                                                 "symbol_patterns": ["."]}]),
            segmentation, [],
        )
        assert depth["n_kernels_window"] == len(STEP_KERNELS)
        assert depth["merged_busy_ns"] == sum(d for _, d in STEP_KERNELS)


def test_slice_keeps_the_export_tables_and_cuts_every_timed_table(tmp_path):
    config, nsys_dir, bs1, _ = _slice_campaign(tmp_path)
    assert mine_trace.main(["slice", "--config", str(config)]) == 0
    full = nsys_dir / "baseline.sqlite"
    out = nsys_dir / "baseline_bs1.sqlite"
    assert _tables(out) == _tables(full)
    con = sqlite3.connect(str(out))
    try:
        kept = _kernel_rows(out)
        runtime = con.execute("SELECT start, end FROM CUPTI_ACTIVITY_KIND_RUNTIME").fetchall()
        # Window [wave start, cut at the partial step's delimiter): every kept kernel's
        # launch, plus the launch of the dropped delimiter, which precedes the cut.
        wave_start, cut = bs1[0][0] - 50_000, bs1[-1][0]
        assert all(wave_start <= s < cut for s, _ in runtime)
        assert len(runtime) == len(kept) + 1
        assert con.execute("SELECT text FROM NVTX_EVENTS").fetchall() == [("mark in bs1 wave",)]
        assert con.execute("SELECT name FROM TARGET_INFO_GPU").fetchall() == [("NVIDIA L40S",)]
        assert con.execute("SELECT utcEpochNs FROM TARGET_INFO_SESSION_START_TIME"
                           ).fetchall() == [(SESSION_EPOCH_NS,)]
        meta = dict(con.execute("SELECT name, value FROM META_DATA_EXPORT"))
        assert meta["EXPORT_PRODUCT_VERSION"] == "2026.3.1.157"
    finally:
        con.close()
    record = json.loads((nsys_dir / "baseline_slices.json").read_text(encoding="utf-8"))
    entry = {s["bs"]: s for s in record["slices"]}[1]
    assert entry["output"].endswith("baseline_bs1.sqlite")
    assert entry["waves"] == [2]
    assert entry["cut_ns"] == bs1[-1][0]
    assert entry["delimiter_launches"] == 5
    assert entry["n_kernels"] == len(kept)
    assert entry["dropped_tail_kernels"] == 1
    assert record["delimiter"] == "step_start"
    assert record["session_start_ns"] == SESSION_EPOCH_NS
    assert record["source"].endswith("baseline.sqlite")


def test_slice_full_batch_symbol_drops_ragged_tail_steps(tmp_path):
    # Steps 4 and 5 of bs8 sampled 3 and 5 of 8 sequences: the slice ends after step 3.
    config, nsys_dir, _, bs8 = _slice_campaign(tmp_path, bs8_grids={4: 3, 5: 5})
    assert mine_trace.main(["slice", "--config", str(config), "--bs", "8",
                            "--full-batch-symbol", "sampler"]) == 0
    kept = _kernel_rows(nsys_dir / "baseline_bs8.sqlite")
    assert not (nsys_dir / "baseline_bs1.sqlite").exists()
    assert len([r for r in kept if r[2] == "step_start"]) == 4
    assert kept[-1][2] == "sampler" and kept[-1][3] == 8


def test_slice_refuses_a_max_gap_config(tmp_path):
    config, nsys_dir, _, _ = _slice_campaign(tmp_path, segmentation={"policy": "max_gap"})
    assert mine_trace.main(["slice", "--config", str(config)]) == 1
    assert not list(nsys_dir.glob("baseline_bs*.sqlite"))


def test_slice_without_a_session_start_is_fatal(tmp_path):
    config, nsys_dir, _, _ = _slice_campaign(tmp_path)
    con = sqlite3.connect(str(nsys_dir / "baseline.sqlite"))
    con.execute("DROP TABLE TARGET_INFO_SESSION_START_TIME")
    con.commit()
    con.close()
    assert mine_trace.main(["slice", "--config", str(config)]) == 1
    assert not list(nsys_dir.glob("baseline_bs*.sqlite"))


#: Two TP ranks; rank 1 runs every kernel 2 us after rank 0.
TP2 = ((0, 111, 0), (1, 222, 2_000))


def test_slice_cuts_each_rank_at_its_own_closing_delimiter(tmp_path):
    config, nsys_dir, _, bs8 = _slice_campaign(tmp_path, ranks=TP2)
    assert mine_trace.main(["slice", "--config", str(config), "--bs", "8"]) == 0
    out = nsys_dir / "baseline_bs8.sqlite"
    kept = _kernel_rows(out, with_rank=True)
    for device in (0, 1):
        mine = [r for r in kept if r[4] == device]
        # Every full step stays on every rank; no rank keeps its partial step's delimiter.
        assert len([r for r in mine if r[2] == "step_start"]) == 6
        assert [r[2] for r in mine[-3:]] == ["step_start", "gemm", "sampler"]
    depth = mine_trace.mine_one_trace(
        {"sqlite": str(out)},
        mine_trace.compile_families([{"label": "all", "symbol_patterns": ["."]}]),
        {"policy": "delimiter_symbol", "symbol": "step_start"}, [],
    )
    assert set(depth["per_rank"]) == {"dev0_pid111", "dev1_pid222"}
    # The miner starts at the later rank's delimiter, so it sees that rank's whole step.
    assert depth["per_rank"]["dev1_pid222"]["n_kernels"] == len(STEP_KERNELS)
    record = json.loads((nsys_dir / "baseline_slices.json").read_text(encoding="utf-8"))
    entry = record["slices"][0]
    partial = {r[3]: r[0] for r in bs8 if r[2] == "step_start"}  # last start per device
    assert entry["rank_cuts_ns"] == {"dev0_pid111": partial[0], "dev1_pid222": partial[1]}
    assert entry["cut_ns"] == partial[0]


def test_slice_with_unequal_delimiter_counts_across_ranks_is_fatal(tmp_path):
    rows = _bucket_steps(2_100_000, 6, 8, ranks=TP2)
    rank1_last_delimiter = [r for r in rows if r[2] == "step_start" and r[3] == 1][-1]
    config, nsys_dir, _, _ = _slice_campaign(tmp_path, ranks=TP2, drop=rank1_last_delimiter)
    assert mine_trace.main(["slice", "--config", str(config), "--bs", "8"]) == 1
    assert not list(nsys_dir.glob("baseline_bs*.sqlite"))
