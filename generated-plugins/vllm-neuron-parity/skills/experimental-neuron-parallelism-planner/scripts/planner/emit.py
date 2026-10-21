"""§4-output: serialize a plan() result to the planner JSON artifacts.

Pure serializer -- it RESHAPES the dict plan() already returned (plus the model bundle for
the entitlement metadata); it runs no cost math and never re-invokes cell(). Writes the four
artifacts the old emit_artifacts.py produced, into PARALLELISM_PLANNER_OUTDIR (default
scripts/artifacts):

  - pareto_frontier.json        -- per-bucket 3-axis Pareto decode cells (the `pareto` flag).
  - target_config.json          -- per-bucket selected decode target(s) + serving mode.
  - prefill_target_config.json  -- per-bucket prefill pool target (disaggregated only).
  - entitlement.json            -- model-level corrected weight bytes + min footprint.
"""

from __future__ import annotations

import json
import os

from entitlement_formulas import (
    corrected_weight_bytes, embed_table_bytes, load_hardware,  # noqa: F401
)
from planner.build import ModelBundle
from planner.plan import usable_hbm, min_kv_heads

HERE = os.path.dirname(os.path.abspath(__file__))
# Artifacts dir; override with PARALLELISM_PLANNER_OUTDIR to emit into an analysis directory.
ARTIFACTS = os.environ.get("PARALLELISM_PLANNER_OUTDIR",
                           os.path.join(os.path.dirname(HERE), "artifacts"))


def _min_footprint(a, hw) -> int:
    """Smallest TP that holds the weights -> functional-minimum core count."""
    import math
    return int(math.ceil(corrected_weight_bytes(a) / usable_hbm(hw)))


def emit(b: ModelBundle, p: dict, outdir: str = ARTIFACTS) -> list:
    """Write the four artifacts from the plan dict `p`. Returns the paths written."""
    os.makedirs(outdir, exist_ok=True)
    a, hw = b.arch, b.hw
    model = b.raw.get("model")
    hw_name = b.raw.get("hardware_name", "hw")
    serving = p["serving"]

    pareto_out = {"model": model, "hardware": hw_name, "serving": serving,
                  "disabled_axes": p.get("disabled_axes", []),
                  "buckets": {str(S): [r for r in p["decode_grid"][S] if r.get("pareto")]
                              for S in p["buckets"]}}

    targets_out = {"model": model, "hardware": hw_name, "serving": serving,
                   "cores_per_node": hw.cores_per_node,
                   "disabled_axes": p.get("disabled_axes", []),
                   "prefill_itps_floor": p.get("prefill_itps_floor"),
                   "prefill_ttft_ceiling_ms": p.get("prefill_ttft_ceiling_ms"),
                   "decode_itl_ceiling_ms": p.get("decode_itl_ceiling_ms"), "buckets": {}}
    for S in p["buckets"]:
        if serving == "colocated":
            targets_out["buckets"][str(S)] = {"selected": p["selected"][S]}
        else:
            targets_out["buckets"][str(S)] = {"decode_pool": p["decode_pool"][S]}

    prefill_out = {"model": model, "hardware": hw_name, "serving": serving,
                   "cores_per_node": hw.cores_per_node,
                   "prefill_itps_floor": p.get("prefill_itps_floor"),
                   "prefill_ttft_ceiling_ms": p.get("prefill_ttft_ceiling_ms"),
                   "prefill_allow_kv_replication": p.get("prefill_allow_kv_replication"),
                   "max_nonreplicating_tp": min_kv_heads(a),
                   "buckets": ({str(S): p["prefill_pool"][S] for S in p["buckets"]}
                               if serving == "disaggregated" else {})}

    entitlement_out = {
        "source": f"entitlement (analytic, oracle-validated), {model}, {hw_name}",
        "model": model, "hardware": hw_name,
        "corrected_weight_gb": round(corrected_weight_bytes(a) / 1e9, 2),
        "embed_lm_head_gb": round(embed_table_bytes(a) / 1e9, 2),
        "hbm_capacity_gb_per_core": round(hw.hbm_capacity_bytes_per_core / 1024**3, 1),
        "min_footprint_cores": _min_footprint(a, hw),
    }

    written = []
    for name, obj in (("pareto_frontier.json", pareto_out),
                      ("target_config.json", targets_out),
                      ("prefill_target_config.json", prefill_out),
                      ("entitlement.json", entitlement_out)):
        path = os.path.join(outdir, name)
        with open(path, "w") as f:
            json.dump(obj, f, indent=2)
        written.append(path)
    return written
