"""Unit tests for build_cost_deliverables.py — the tokens/$ math and cost tables.

Focus is the pure logic where mistakes are easy and consequential (this is the file
that produces the $ numbers teammates quote):
  - load(): usable-filter, price join, and the tokens/$ formula. Includes a regression
    lock on the exact output-tokens/$ number (475,265) that surfaced the float-truncation
    discrepancy (7266.27 must NOT be truncated to 7266 before multiplying).
  - load(): unpriced instances are DROPPED with a warning (not a crash); all-unpriced
    raises SystemExit.
  - best_cost_table(): picks the max-tokens/$ cell per (instance, regime, shape) and
    flags when that max sits at the top of the swept concurrency (lower-bound signal).
  - _efficiency_frontier(): non-dominated top-right envelope on (per_user_otps, tok/$).

Only pandas is needed at import (matplotlib is imported lazily inside the plot helpers).
"""

import pandas as pd
import pytest

import build_cost_deliverables as bc


# --- fixture: write a minimal all_cells.csv --------------------------------

# Base (pre-cost) columns that load() reads or best_cost_table() later needs. load()
# derives price_hr / total_tps / tok_per_dollar_* from these.
_BASE_COLS = ["usable", "instance", "regime_key", "regime", "shape", "sharding",
              "conc", "otps", "itps", "tpot_p50", "per_user_otps"]


def _write_cells(tmp_path, rows):
    df = pd.DataFrame(rows)
    # fill any base column a row omitted with a harmless default so read_csv is happy
    for c in _BASE_COLS:
        if c not in df.columns:
            df[c] = 0
    p = tmp_path / "all_cells.csv"
    df.to_csv(p, index=False)
    return p


# --- load(): tokens/$ formula + the truncation regression -------------------

def test_load_tokens_per_dollar_formula(tmp_path):
    p = _write_cells(tmp_path, [
        {"usable": True, "instance": "p5", "regime_key": "bf16_nospec",
         "regime": "bf16 (no spec)", "shape": "1024/256", "sharding": "tp8",
         "conc": 256, "otps": 7266.27, "itps": 1000.0, "tpot_p50": 12.0,
         "per_user_otps": 28.38},
    ])
    df = bc.load(p)
    row = df.iloc[0]
    assert row["price_hr"] == 55.040
    assert row["total_tps"] == pytest.approx(8266.27)
    # total: (7266.27 + 1000) * 3600 / 55.040
    assert row["tok_per_dollar_total"] == pytest.approx(8266.27 * 3600 / 55.040)
    # output-only: 7266.27 * 3600 / 55.040
    assert row["tok_per_dollar_out"] == pytest.approx(7266.27 * 3600 / 55.040)


def test_load_output_tokens_per_dollar_regression(tmp_path):
    # Regression lock: the exact number the user verified (475,265), which caught a
    # truncation bug — 7266.27 must be used at full precision, NOT floored to 7266
    # (which yields 475,247, an 18 tok/$ error) before the multiply.
    p = _write_cells(tmp_path, [
        {"usable": True, "instance": "p5", "regime_key": "bf16_nospec",
         "regime": "bf16 (no spec)", "shape": "1024/256", "sharding": "tp8",
         "conc": 256, "otps": 7266.27, "itps": 0.0, "tpot_p50": 12.0,
         "per_user_otps": 28.38},
    ])
    df = bc.load(p)
    assert round(df.iloc[0]["tok_per_dollar_out"]) == 475265


def test_load_filters_unusable(tmp_path):
    p = _write_cells(tmp_path, [
        {"usable": True, "instance": "p5", "otps": 1000.0, "itps": 100.0},
        {"usable": False, "instance": "p5", "otps": 9999.0, "itps": 100.0},
    ])
    df = bc.load(p)
    assert len(df) == 1
    assert df.iloc[0]["otps"] == 1000.0     # the unusable saturated cell is gone


def test_load_drops_unpriced_instance_with_warning(tmp_path, capsys):
    p = _write_cells(tmp_path, [
        {"usable": True, "instance": "p5", "otps": 1000.0, "itps": 100.0},
        {"usable": True, "instance": "trn2", "otps": 2000.0, "itps": 200.0},  # unpriced
    ])
    df = bc.load(p)
    assert set(df["instance"]) == {"p5"}          # trn2 dropped (no PRICE_PER_HR entry)
    assert "trn2" in capsys.readouterr().out       # ...loudly
    assert df["price_hr"].notna().all()


def test_load_all_unpriced_raises(tmp_path):
    p = _write_cells(tmp_path, [
        {"usable": True, "instance": "trn2", "otps": 2000.0, "itps": 200.0},
    ])
    with pytest.raises(SystemExit):
        bc.load(p)


# --- best_cost_table(): max tokens/$ cell + lower-bound flag ---------------

def test_best_cost_table_picks_max_and_flags_conc(tmp_path):
    # two cells, same (instance, regime, shape); the higher-throughput one wins and
    # sits at the top swept concurrency → max_at_max_swept_conc should be True.
    p = _write_cells(tmp_path, [
        {"usable": True, "instance": "p5", "regime_key": "bf16_nospec",
         "regime": "bf16 (no spec)", "shape": "1024/256", "sharding": "tp8",
         "conc": 64, "otps": 3000.0, "itps": 500.0, "tpot_p50": 10.0,
         "per_user_otps": 46.9},
        {"usable": True, "instance": "p5", "regime_key": "bf16_nospec",
         "regime": "bf16 (no spec)", "shape": "1024/256", "sharding": "tp8",
         "conc": 256, "otps": 7000.0, "itps": 900.0, "tpot_p50": 14.0,
         "per_user_otps": 27.3},
    ])
    mc = bc.best_cost_table(bc.load(p))
    assert len(mc) == 1
    row = mc.iloc[0]
    assert row["at_conc"] == 256                    # the higher-throughput cell won
    assert bool(row["max_at_max_swept_conc"])       # ...at the top of the sweep
    # max tokens/$ (total) = (7000+900)*3600/55.040, rounded to int
    assert row["max_tok_per_dollar_total"] == int(round(7900.0 * 3600 / 55.040))


def test_best_cost_table_flag_false_when_peak_below_max_conc(tmp_path):
    # peak efficiency at conc=64 while the sweep went up to 256 → not a lower bound
    p = _write_cells(tmp_path, [
        {"usable": True, "instance": "p5", "regime_key": "bf16_nospec",
         "regime": "bf16 (no spec)", "shape": "1024/256", "sharding": "tp8",
         "conc": 64, "otps": 8000.0, "itps": 900.0, "tpot_p50": 10.0,
         "per_user_otps": 125.0},
        {"usable": True, "instance": "p5", "regime_key": "bf16_nospec",
         "regime": "bf16 (no spec)", "shape": "1024/256", "sharding": "tp8",
         "conc": 256, "otps": 6000.0, "itps": 800.0, "tpot_p50": 30.0,
         "per_user_otps": 23.4},
    ])
    mc = bc.best_cost_table(bc.load(p))
    row = mc.iloc[0]
    assert row["at_conc"] == 64
    assert not bool(row["max_at_max_swept_conc"])


# --- _efficiency_frontier(): top-right envelope on (rate, tokens/$) --------

def test_efficiency_frontier_drops_dominated():
    rows = [
        {"per_user_otps": 100.0, "tok_per_dollar_total": 1_000_000.0},  # dominates next
        {"per_user_otps": 50.0, "tok_per_dollar_total": 500_000.0},     # dominated → drop
        {"per_user_otps": 20.0, "tok_per_dollar_total": 2_000_000.0},   # wins on tok/$
    ]
    front = bc._efficiency_frontier(rows)
    xs = [r["per_user_otps"] for r in front]
    assert 50.0 not in xs
    assert 100.0 in xs and 20.0 in xs
    assert xs == sorted(xs)         # sorted by per_user_otps
