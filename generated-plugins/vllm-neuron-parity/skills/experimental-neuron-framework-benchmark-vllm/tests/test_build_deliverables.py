"""Unit tests for build_deliverables.py — the pure parsing / labeling / gating logic.

Focus is the functions where real bugs were found and fixed during development:
  - regime_key / regime_label: EP must NOT fold into the regime (it's a sharding prop)
  - parse_cell + _PATH_RE: sharding-string incl. _ep tag, per_user_otps, usable guard,
    spec-metrics block, and the two-level path regex (no dir-boundary crossing)
  - apply_targets / _ttft_bar_for_isl: ISL-scaled TTFT bar + the pd.NA-ambiguity guard
    (a cell with no TTFT bar that fails on TPOT must not raise)
  - _pareto_frontier: non-dominated upper-right envelope

matplotlib is imported lazily inside the plotting helpers, so importing the module and
exercising these functions needs only pandas.
"""

import json

import pandas as pd
import pytest

import build_deliverables as bd


# --- regime_key / regime_label: EP is a sharding property, not a regime ------

def test_regime_key_basic():
    assert bd.regime_key("bf16", False) == "bf16_nospec"
    assert bd.regime_key("bf16", True) == "bf16_spec"
    assert bd.regime_key("fp8", False) == "fp8_nospec"
    assert bd.regime_key("fp8fp4", True) == "fp8fp4_spec"


def test_regime_label_basic():
    assert bd.regime_label("bf16", False) == "bf16 (no spec)"
    assert bd.regime_label("bf16", True) == "bf16 + spec"


def test_regime_key_does_not_encode_ep():
    # EP-on and EP-off cells of the same precision+spec MUST share a regime_key
    # (EP is expressed in the sharding label, not the regime). Regression guard:
    # regime_key takes exactly (prec, spec) — no ep parameter leaks in.
    assert bd.regime_key("fp8fp4", False) == "fp8fp4_nospec"
    # calling with only two args is the whole contract; a third would be a regression
    import inspect
    params = list(inspect.signature(bd.regime_key).parameters)
    assert params == ["prec", "spec"], f"regime_key signature drifted: {params}"


# --- _PATH_RE: two-level path, EP tag, no dir-boundary crossing --------------
# re groups are STRINGS (parse_cell int()-casts them downstream); dp is None when absent.

@pytest.mark.parametrize("path,exp", [
    ("k__bf16/k_1024_256_tp8/conc_16/result.json", ("1024", "256", "8", None, "16")),
    ("k__bf16/k_8192_1024_tp4_dp2/conc_128/result.json", ("8192", "1024", "4", "2", "128")),
    # EP-tagged sharding dir must still parse (isl/osl/tp/dp/conc), _ep8 ignored
    ("k__nospec_ep/k_1024_256_tp2_dp4_ep8/conc_64/result.json", ("1024", "256", "2", "4", "64")),
    ("k__spec_ep/k_1024_256_tp8_ep8/conc_1/result.json", ("1024", "256", "8", None, "1")),
])
def test_path_re_extracts_dims(path, exp):
    m = bd._PATH_RE.search(path)
    assert m is not None, f"did not match: {path}"
    assert m.groups() == exp


# --- parse_cell: record fields, sharding string, usable guard, spec block ----

def _write_result(tmp_path, subpath, payload):
    p = tmp_path / subpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload))
    return p


def test_parse_cell_basic_fields(tmp_path):
    p = _write_result(
        tmp_path, "m__bf16/m_1024_256_tp8/conc_16/result.json",
        {"completed": 80, "failed": 0, "duration": 10.0,
         "total_input_tokens": 81920, "output_throughput": 800.0,
         "median_ttft_ms": 100.0, "median_tpot_ms": 12.0},
    )
    rec = bd.parse_cell(p, prec="bf16", spec=False, instance="p5", run_id="m__bf16")
    assert rec is not None
    assert rec["isl"] == 1024 and rec["osl"] == 256
    assert rec["tp"] == 8 and rec["dp"] == 1 and rec["gpus"] == 8
    assert rec["sharding"] == "tp8"          # dp==1 → no _dp suffix
    assert rec["conc"] == 16
    assert rec["otps"] == 800.0
    assert rec["itps"] == round(81920 / 10.0, 1)      # 8192.0
    assert rec["per_user_otps"] == round(800.0 / 16, 2)   # 50.0
    assert rec["regime_key"] == "bf16_nospec"
    assert rec["usable"] is True and rec["fail_frac"] == 0.0


def test_parse_cell_ep_sharding_label(tmp_path):
    p = _write_result(
        tmp_path, "m__nospec_ep/m_1024_256_tp4_dp2_ep8/conc_64/result.json",
        {"completed": 50, "failed": 0, "duration": 5.0,
         "total_input_tokens": 51200, "output_throughput": 500.0},
    )
    rec = bd.parse_cell(p, prec="fp8fp4", spec=False, instance="p5",
                        run_id="m__nospec_ep", ep=True)
    # EP degree = tp*dp = 8; label reads tp4_dp2_ep8
    assert rec["sharding"] == "tp4_dp2_ep8"
    assert rec["ep"] is True
    # ...but the regime is unchanged by EP
    assert rec["regime_key"] == "fp8fp4_nospec"


def test_parse_cell_usable_guard(tmp_path):
    # failed fraction above FAIL_THRESHOLD (0.05) → not usable
    p = _write_result(
        tmp_path, "m__bf16/m_1024_256_tp8/conc_512/result.json",
        {"completed": 90, "failed": 10, "duration": 5.0,
         "total_input_tokens": 92160, "output_throughput": 900.0},
    )
    rec = bd.parse_cell(p, prec="bf16", spec=False, instance="p5", run_id="m__bf16")
    assert rec["fail_frac"] == 0.1
    assert rec["usable"] is False


def test_parse_cell_spec_metrics_block(tmp_path):
    p = _write_result(
        tmp_path, "m__spec/m_1024_256_tp8/conc_16/result.json",
        {"completed": 80, "failed": 0, "duration": 10.0,
         "total_input_tokens": 81920, "output_throughput": 800.0,
         "spec_decode_acceptance_rate": 4.77,
         "spec_decode_acceptance_length": 1.33,
         "spec_decode_num_drafts": 100, "spec_decode_draft_tokens": 700,
         "spec_decode_per_position_acceptance_rates": [0.17, 0.08, 0.05]},
    )
    rec = bd.parse_cell(p, prec="fp8fp4", spec=True, instance="p5", run_id="m__spec")
    assert rec["accept_rate"] == 4.8            # _r(4.77, 1)
    assert rec["accept_len"] == 1.33
    assert rec["k_spec"] == 7                   # 700 draft / 100 drafts
    assert rec["per_pos"] == "0.17/0.08/0.05"


def test_parse_cell_no_spec_block_when_absent(tmp_path):
    p = _write_result(
        tmp_path, "m__bf16/m_1024_256_tp8/conc_16/result.json",
        {"completed": 80, "failed": 0, "duration": 10.0,
         "total_input_tokens": 81920, "output_throughput": 800.0},
    )
    rec = bd.parse_cell(p, prec="bf16", spec=False, instance="p5", run_id="m__bf16")
    assert "accept_rate" not in rec             # no spec fields on a no-spec cell


def test_parse_cell_unparseable_path_returns_none(tmp_path):
    p = _write_result(tmp_path, "garbage/result.json", {"completed": 1})
    assert bd.parse_cell(p, prec="bf16", spec=False, instance="p5", run_id="x") is None


def test_parse_cell_truncated_json_returns_none(tmp_path):
    # The parser's most-likely real bad input: a result.json half-written when the
    # box was torn down mid-bench (e.g. a capacity block auto-terminating at its
    # window end — how we lost 3 tail cells). The path parses fine, but the JSON is
    # truncated, so the json.JSONDecodeError guard must yield None (skip the cell),
    # not raise and abort the whole report.
    p = tmp_path / "m__bf16" / "m_1024_256_tp8" / "conc_16" / "result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"completed": 80, "failed": 0, "output_throughput": 800.')  # cut off
    assert bd.parse_cell(p, prec="bf16", spec=False, instance="p5",
                         run_id="m__bf16") is None


# --- _ttft_bar_for_isl: smallest key >= isl, fallback to largest -------------

def test_ttft_bar_for_isl_scaling():
    by_isl = {"2048": 500, "8192": 800, "32768": 1400}
    assert bd._ttft_bar_for_isl(1024, by_isl) == 500.0   # <= smallest key
    assert bd._ttft_bar_for_isl(2048, by_isl) == 500.0   # exact
    assert bd._ttft_bar_for_isl(4096, by_isl) == 800.0   # next key up
    assert bd._ttft_bar_for_isl(131072, by_isl) == 1400.0  # beyond all → largest
    assert bd._ttft_bar_for_isl(1024, {}) is None        # no table


# --- apply_targets: PASS/FAIL gating + the pd.NA-ambiguity guard -------------

def _df(rows):
    return pd.DataFrame(rows)


def test_apply_targets_pass_and_fail():
    df = _df([
        # passes both bars
        {"isl": 2048, "usable": True, "ttft_p50": 400.0, "tpot_p50": 10.0},
        # fails TPOT bar
        {"isl": 2048, "usable": True, "ttft_p50": 400.0, "tpot_p50": 20.0},
    ])
    out = bd.apply_targets(df, {"ttft_p50_ms_by_isl": {"2048": 500}, "tpot_p50_ms": 13})
    assert out.loc[0, "pass"] == True   # noqa: E712
    assert out.loc[1, "pass"] == False  # noqa: E712


def test_apply_targets_unusable_is_na():
    df = _df([{"isl": 2048, "usable": False, "ttft_p50": 400.0, "tpot_p50": 10.0}])
    out = bd.apply_targets(df, {"ttft_p50_ms_by_isl": {"2048": 500}, "tpot_p50_ms": 13})
    assert pd.isna(out.loc[0, "pass"])   # saturated cell → NA, not a real pass/fail


def test_apply_targets_no_ttft_bar_does_not_crash():
    # Regression for the pd.NA-ambiguity bug: an ISL with NO configured TTFT bar,
    # gated only on TPOT. _ttft_bar_for_isl returns None → ttft_bar_ms is NaN. The
    # pass logic must not raise "boolean value of NA is ambiguous".
    df = _df([{"isl": 131072, "usable": True, "ttft_p50": 9000.0, "tpot_p50": 20.0}])
    out = bd.apply_targets(df, {"ttft_p50_ms_by_isl": {}, "tpot_p50_ms": 13})
    # no TTFT bar → TTFT check skipped; TPOT 20 > 13 → overall fail (not a crash, not NA)
    assert out.loc[0, "pass"] == False  # noqa: E712


# --- _pareto_frontier: non-dominated upper-right envelope --------------------

def test_pareto_frontier_drops_dominated():
    # a point dominated on BOTH axes is excluded; the frontier is sorted by x.
    rows = [
        {"per_user_otps": 100.0, "otps": 1000.0},   # dominates the next
        {"per_user_otps": 50.0, "otps": 500.0},     # dominated → dropped
        {"per_user_otps": 30.0, "otps": 2000.0},    # wins on otps → kept
    ]
    front = bd._pareto_frontier(rows)
    xs = [r["per_user_otps"] for r in front]
    assert 50.0 not in xs           # dominated point excluded
    assert 100.0 in xs and 30.0 in xs
    assert xs == sorted(xs)         # returned sorted by per_user_otps
