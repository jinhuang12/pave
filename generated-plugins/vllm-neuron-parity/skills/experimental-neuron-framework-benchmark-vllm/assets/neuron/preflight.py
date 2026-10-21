#!/usr/bin/env python3
"""Fail-FAST preflight for a neuron_bench_config.json — BEFORE any Kaizen provisioning.

A vLLM-Neuron sweep on Trainium is expensive to get wrong: a bad bucket set or an
unfittable tp costs MINUTES of NEFF compile before it OOMs at load, and every desktop
minute is a provisioned box. This stdlib-only checker catches the cheap-to-detect
mistakes locally, before submit_neuron_bench.py rents anything:

  * config parses and carries the required fields the driver reads,
  * every sharding is schedulable — tp*dp fits the instance's logical cores (each DP
    replica gets its OWN window of tp cores, so cores multiply across dp), and an
    EP-on sharding with dp>1 has ep_degree == tp*dp (a plugin hard requirement),
  * the ECR `image` is a real URI (not the `<...>` template placeholder),
  * the workload is the cross-platform-comparable one (random + range 0.0) — a WARN,
    not a FAIL, because sharegpt/nonzero-jitter is valid for a Neuron-only study but
    silently breaks Neuron-vs-GPU comparability (bucket padding),
  * BASELINE sanity vs the Performance Milestones doc — under baseline='controlled'
    (the PRIMARY milestone baseline: APC+chunked+spec all OFF) a regime carrying
    'speculative' is a hard FAIL (matches the driver, which refuses it), and
    server.enable_prefix_caching=true is a WARN (the driver forces APC off anyway),
  * MILESTONE shape check — a WARN if neither of the two standard milestone shapes
    (8192/1024, 32768/2048) is present, or if a shape is non-standard: fine for a
    one-off, but a milestone NUMBER must use the standard shapes,
  * (DOCUMENTED, not run locally) the model arch is registered in the vLLM-Neuron
    image — that needs the image/pod, so we only print the command to confirm it.

Exit code: 0 on PASS (warnings allowed), non-zero on FAIL.

    python3 preflight.py neuron_bench_config.json
    python3 preflight.py --config path/to/neuron_bench_config.json

The logical-core limits are read from the sibling neuron_sweep.py's `_LOGICAL_CORES`
dict (via ast, no IPA import needed) so this stays in sync with the driver's own
scheduling limit; if that parse fails we fall back to a hardcoded copy with a warning.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "neuron_sweep.py"

# Fallback ONLY — the live values are read from neuron_sweep.py's _LOGICAL_CORES so the
# two never drift. Keep this in sync with that dict if the ast read ever fails.
_LOGICAL_CORES_FALLBACK = {
    "trn2.48xlarge": 64,   # 16 chips x 128 physical, LNC=2 -> 64 logical cores
    "trn1.32xlarge": 32,
}


def _load_logical_cores() -> Tuple[Dict[str, int], List[str]]:
    """Extract `_LOGICAL_CORES` from neuron_sweep.py without importing it (importing the
    driver pulls in IPA, which isn't installed locally). Returns (dict, warnings)."""
    warns: List[str] = []
    try:
        tree = ast.parse(DRIVER.read_text())
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_LOGICAL_CORES" for t in node.targets
            ):
                cores = ast.literal_eval(node.value)
                return {str(k): int(v) for k, v in cores.items()}, warns
        warns.append(f"could not find _LOGICAL_CORES in {DRIVER.name}; using fallback table")
    except Exception as exc:  # noqa: BLE001 — any parse issue -> fall back, don't crash
        warns.append(f"could not read {DRIVER.name} ({exc}); using fallback logical-core table")
    return dict(_LOGICAL_CORES_FALLBACK), warns


class Report:
    """Accumulates PASS/WARN/FAIL lines and renders a summary."""

    def __init__(self) -> None:
        self.fails: List[str] = []
        self.warns: List[str] = []
        self.passes: List[str] = []

    def ok(self, msg: str) -> None:
        self.passes.append(msg)

    def warn(self, msg: str) -> None:
        self.warns.append(msg)

    def fail(self, msg: str) -> None:
        self.fails.append(msg)

    def render(self) -> int:
        for m in self.passes:
            print(f"PASS  {m}")
        for m in self.warns:
            print(f"WARN  {m}")
        for m in self.fails:
            print(f"FAIL  {m}")
        print("-" * 60)
        print(f"{len(self.passes)} passed, {len(self.warns)} warning(s), "
              f"{len(self.fails)} failure(s)")
        if self.fails:
            print("PREFLIGHT: FAIL — fix the above before submitting (nothing provisioned).")
            return 1
        if self.warns:
            print("PREFLIGHT: PASS (with warnings) — review the warnings above.")
            return 0
        print("PREFLIGHT: PASS — config looks safe to submit.")
        return 0


def load_config(path: Path, rep: Report) -> Dict[str, Any]:
    if not path.exists():
        rep.fail(f"config not found: {path}")
        return {}
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        rep.fail(f"config is not valid JSON: {exc}")
        return {}
    rep.ok(f"config parses as JSON: {path}")
    # Drop the _comment/_field doc keys the same way the driver does.
    return {k: v for k, v in raw.items() if not str(k).startswith("_")}


def check_required(cfg: Dict[str, Any], rep: Report) -> None:
    if not cfg.get("image"):
        rep.fail("missing required field: `image` (ECR URI of the vLLM-Neuron bench image)")
    if not cfg.get("instance"):
        rep.warn("no `instance` set — driver defaults to trn2.48xlarge")

    models = cfg.get("models")
    if not models or not isinstance(models, list):
        rep.fail("missing/empty `models` list")
        models = []
    for i, m in enumerate(models):
        if not m.get("hf_id"):
            rep.fail(f"models[{i}] ({m.get('key', '?')}) missing required `hf_id`")
        if not m.get("key"):
            rep.warn(f"models[{i}] missing `key` (output-dir prefix / label)")

    # The sharding axis may live globally or per-model (the driver accepts either), under
    # either `shardings` ([{tp, dp?, ep?}] — GPU-parity) or the legacy `tp_list` (dp=1).
    has_global_sharding = bool(cfg.get("shardings") or cfg.get("tp_list"))

    def _has_sharding(m: Dict[str, Any]) -> bool:
        return bool(has_global_sharding or m.get("shardings") or m.get("tp_list"))

    for m in models:
        if not _has_sharding(m):
            rep.fail(f"no sharding axis for model {m.get('key', '?')}: set `shardings` "
                     '(e.g. [{"tp": 16, "dp": 4}], global or per-model) or the legacy '
                     "`tp_list`")

    shapes = cfg.get("shapes")
    if not shapes or not isinstance(shapes, list):
        rep.fail("missing/empty `shapes` list")
    else:
        for i, s in enumerate(shapes):
            if "isl" not in s or "osl" not in s:
                rep.fail(f"shapes[{i}] must have both `isl` and `osl`")

    if not cfg.get("concurrencies") and not any(
        m.get("concurrencies") for m in models
    ):
        rep.fail("missing `concurrencies` (global or per-model)")

    if models and shapes and all(_has_sharding(m) for m in models):
        rep.ok("required fields present (image, models[].hf_id, shardings/tp_list, "
               "shapes, concurrencies)")


def check_image(cfg: Dict[str, Any], rep: Report) -> None:
    image = str(cfg.get("image") or "")
    if not image:
        return  # already failed in check_required
    if image.startswith("<") or image.endswith(">") or "ECR-URI" in image:
        rep.fail(f"`image` is still the template placeholder ({image!r}) — set a real ECR URI")
        return
    # A real-looking ECR URI: <acct>.dkr.ecr.<region>.amazonaws.com/<repo>[:tag]
    if ".dkr.ecr." in image and ".amazonaws.com/" in image:
        rep.ok(f"`image` looks like a real ECR URI ({image})")
    else:
        rep.warn(f"`image` ({image}) does not look like an ECR URI "
                 "(<acct>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>) — double-check it")


def _shardings(cfg: Dict[str, Any]) -> List[Tuple[str, List[Tuple[int, int]]]]:
    """Yield (model_label, [(tp, dp), ...]) for every model's effective sharding axis.

    Mirrors neuron_sweep._shardings' resolution order exactly: per-model `shardings` wins
    over global `shardings`, which wins over per-model `tp_list`, which wins over global
    `tp_list`. A `tp_list` entry implies dp=1.
    """
    out: List[Tuple[str, List[Tuple[int, int]]]] = []
    for m in cfg.get("models", []):
        raw = m.get("shardings") or cfg.get("shardings")
        pairs: List[Tuple[int, int]] = []
        try:
            if raw:
                pairs = [(int(s["tp"]), int(s.get("dp", 1))) for s in raw]
            else:
                pairs = [(int(t), 1) for t in (m.get("tp_list") or cfg.get("tp_list") or [])]
        except (TypeError, ValueError, KeyError):
            pairs = []
        out.append((m.get("key", "?"), pairs))
    return out


def _tag(tp: int, dp: int) -> str:
    # Same shape as IPA's server_tag, which is what ends up in the output paths.
    return f"tp{tp}" if dp == 1 else f"tp{tp}_dp{dp}"


def check_sharding(cfg: Dict[str, Any], cores_table: Dict[str, int], rep: Report) -> None:
    """Core-budget + EP-degree legality for every sharding, and a partial-box notice.

    tp*dp (not tp) is the core budget: vLLM-Neuron gives each DP replica its own disjoint
    window of tp logical cores, so a tp16_dp4 server occupies 64 cores. Unlike the GPU
    sweep, tp*dp is NOT required to EQUAL the box — under-filling is legal here, but it
    makes the cell a partial-box number, so we say so.
    """
    instance = cfg.get("instance", "trn2.48xlarge")
    cores = cores_table.get(instance)
    if cores is None:
        rep.warn(f"instance {instance!r} not in the logical-core table "
                 f"{sorted(cores_table)} — cannot check sharding bounds")
        return
    by_key = {m.get("key", "?"): m for m in cfg.get("models", [])}
    regimes = cfg.get("regimes") or []
    any_bad = False
    for key, pairs in _shardings(cfg):
        model = by_key.get(key, {})
        for tp, dp in pairs:
            if tp <= 0 or dp <= 0:
                any_bad = True
                rep.fail(f"model {key}: {_tag(tp, dp)} — tp and dp must both be >= 1")
                continue
            if tp * dp > cores:
                any_bad = True
                rep.fail(f"model {key}: {_tag(tp, dp)} needs {tp * dp} logical cores "
                         f"(tp {tp} x dp {dp}) but {instance} has {cores}. Each DP replica "
                         f"gets its own window of tp cores, so cores multiply across dp — "
                         f"the driver will refuse this before provisioning")
                continue
            if tp * dp < cores:
                rep.warn(f"model {key}: {_tag(tp, dp)} uses {tp * dp} of {instance}'s "
                         f"{cores} logical cores — {cores - tp * dp} idle. Legal on "
                         f"Trainium (unlike the GPU sweep's tp*dp == gpu_count rule), but "
                         f"this cell's tok/s is a PARTIAL-BOX number; don't compare it "
                         f"per-instance against a full-box GPU cell")
            # EP + dp>1 requires ep_degree == tp*dp (vLLM-Neuron NeuronWorker raises
            # otherwise). The driver defaults ep_degree to tp*dp, so this only trips on an
            # explicit pin — which is exactly the silent-mismatch case worth catching here.
            if dp > 1:
                for src_name, src in [("models[].neuron_config_extra",
                                       model.get("neuron_config_extra") or {})] + [
                        (f"regimes[{r.get('name', '?')}].neuron_config",
                         r.get("neuron_config") or {}) for r in regimes]:
                    if "ep_degree" in src and int(src["ep_degree"]) != tp * dp:
                        any_bad = True
                        rep.fail(f"model {key}: {_tag(tp, dp)} pins ep_degree="
                                 f"{src['ep_degree']} in {src_name}, but with EP on and "
                                 f"dp>1 vLLM-Neuron requires ep_degree == tp*dp == "
                                 f"{tp * dp}. Unpin it (the driver defaults to tp*dp) or "
                                 f"set it to {tp * dp}")
    if not any_bad:
        rep.ok(f"every sharding's tp*dp fits {instance}'s {cores} logical cores")


def check_workload(cfg: Dict[str, Any], rep: Report) -> None:
    wl = cfg.get("workload", {}) or {}
    dataset = wl.get("dataset", "random")
    rr = wl.get("random_range_ratio", 0.0)
    if dataset != "random":
        rep.warn(f"workload.dataset = {dataset!r}, not 'random' — VALID only as a "
                 "Neuron-only study (e.g. APC/prefix-hit). It is INVALID for a "
                 "Neuron-vs-GPU comparison: Neuron pads every request up to a compiled "
                 "bucket, so variable-length prompts are padded on Neuron but not on GPU, "
                 "silently confounding the comparison. Use dataset='random' + "
                 "random_range_ratio=0.0 for a matched cross-platform run.")
        return
    try:
        rr_f = float(rr)
    except (TypeError, ValueError):
        rep.fail(f"workload.random_range_ratio = {rr!r} is not a number")
        return
    if rr_f != 0.0:
        rep.warn(f"workload.random_range_ratio = {rr_f} (not 0.0) — length jitter means "
                 "requests land on DIFFERENT compiled buckets and pad up on Neuron, "
                 "breaking comparability with GPU. Set 0.0 (exact shapes) for a matched "
                 "Neuron-vs-GPU comparison.")
    else:
        rep.ok("workload is the cross-platform-comparable one (random + range 0.0)")


# The two milestone STANDARD workload shapes (Performance Milestones doc, Table 1),
# keyed "isl,osl". A milestone NUMBER must use one of these; other shapes are one-off.
_STANDARD_SHAPES = {(8192, 1024), (32768, 2048)}


def _regimes_for(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The effective regime list (mirrors the driver's default when none is set)."""
    regimes = cfg.get("regimes")
    if not regimes:
        return [{"name": "bf16", "prec": "bf16"}]
    return regimes


def check_baseline(cfg: Dict[str, Any], rep: Report) -> None:
    """Baseline sanity vs the Performance Milestones doc.

    controlled (PRIMARY milestone baseline) = APC + chunked prefill + spec-decode all
    OFF (DI off by construction). The driver REJECTS a spec regime under controlled and
    forces APC off — so a spec regime here is a hard FAIL (it would abort the sweep),
    and enable_prefix_caching=true is a WARN (silently overridden)."""
    baseline = cfg.get("baseline", "controlled")
    if baseline not in ("controlled", "production"):
        rep.fail(f"baseline = {baseline!r} — must be 'controlled' or 'production'")
        return
    if baseline == "controlled":
        spec = [r.get("name", "?") for r in _regimes_for(cfg) if r.get("speculative")]
        if spec:
            rep.fail(
                f"baseline='controlled' but regime(s) {spec} set 'speculative' — the "
                "controlled milestone baseline is APC+chunked+spec all OFF, and the "
                "driver REFUSES this (SystemExit). Move spec regimes to a "
                "baseline='production' run, or drop 'speculative'.")
        server = cfg.get("server", {}) or {}
        if server.get("enable_prefix_caching"):
            rep.warn(
                "baseline='controlled' but server.enable_prefix_caching=true — the "
                "controlled baseline forces APC OFF, so the driver will override this "
                "to false. Set it false to match the reported config.")
        if not spec and not server.get("enable_prefix_caching"):
            rep.ok("baseline='controlled' is clean (no spec regimes, APC off) — the "
                   "PRIMARY milestone baseline")
    else:
        rep.ok("baseline='production' (secondary, GTM/customer-facing) — set the SAME "
               "baseline on the GPU config for a feature-matched comparison")


def check_milestone_shapes(cfg: Dict[str, Any], rep: Report) -> None:
    """WARN if a milestone run's shapes aren't the two standard shapes. A non-standard
    shape is fine for a one-off but not a milestone number (Performance Milestones doc,
    Table 1: 8192/1024 and 32768/2048)."""
    shapes = cfg.get("shapes") or []
    present = set()
    non_standard = []
    for s in shapes:
        try:
            pair = (int(s["isl"]), int(s["osl"]))
        except (KeyError, TypeError, ValueError):
            continue
        if pair in _STANDARD_SHAPES:
            present.add(pair)
        elif pair == (512, 1):
            continue  # the documented cheap smoke point — not a milestone shape, ignore
        else:
            non_standard.append(f"{pair[0]}/{pair[1]}")
    if not present:
        rep.warn("no standard milestone shape present (neither 8192/1024 nor "
                 "32768/2048) — fine for a one-off, but a milestone NUMBER must use a "
                 "standard shape (Performance Milestones doc, Table 1).")
    else:
        missing = _STANDARD_SHAPES - present
        if missing:
            miss = ", ".join(f"{i}/{o}" for i, o in sorted(missing))
            rep.warn(f"only some standard milestone shapes present; missing {miss}. A "
                     "full milestone report covers BOTH 8192/1024 and 32768/2048.")
        else:
            rep.ok("both standard milestone shapes present (8192/1024 and 32768/2048)")
    if non_standard:
        rep.warn(f"non-standard shape(s) {non_standard} — fine for a one-off, but not a "
                 "milestone number (the doc's standard shapes are 8192/1024, 32768/2048; "
                 "512/1 is the smoke point).")


def check_arch_registration(cfg: Dict[str, Any], rep: Report) -> None:
    """DOCUMENTED, NOT run locally. Confirming the arch is registered in the vLLM-Neuron
    plugin requires the image/pod — we do NOT import the plugin here. The driver catches
    an unregistered arch at first server start (the whole (model,regime,tp) group is
    recorded FAILED with a 'server start' error). Print the exact confirm command."""
    image = cfg.get("image", "<ecr-uri>")
    for m in cfg.get("models", []):
        hf_id = m.get("hf_id", "<hf-id>")
        rep.warn(
            f"arch registration for {m.get('key', '?')} ({hf_id}) is NOT checked locally "
            "(needs the image). To confirm inside the pod/image before the full sweep, run:\n"
            f"      kaizen desktop connect --name <desktop> "
            f"--profile \"${{KAIZEN_PROFILE:-kaizen-access}}\" --cmd \\\n"
            f"        'python3 -c \"import vllm; from vllm import ModelRegistry; "
            f"print(ModelRegistry.is_registered(__import__(\\'transformers\\')"
            f".AutoConfig.from_pretrained(\\'{hf_id}\\', trust_remote_code=True)"
            f".architectures[0]))\"'\n"
            "    An unregistered arch surfaces as a server-start FAILURE (the driver marks "
            "the whole group FAILED). If missing: port + build the image with "
            "neuron-framework-autoport-vllm-neuron. (Image: "
            f"{image})")


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Fail-fast preflight for a neuron_bench_config.json")
    p.add_argument("config", nargs="?", type=Path, default=None,
                   help="path to neuron_bench_config.json")
    p.add_argument("--config", dest="config_opt", type=Path, default=None,
                   help="path to neuron_bench_config.json (alternative to positional)")
    args = p.parse_args(argv)
    cfg_path = args.config or args.config_opt or Path("neuron_bench_config.json")

    rep = Report()
    cores_table, core_warns = _load_logical_cores()
    for w in core_warns:
        rep.warn(w)

    cfg = load_config(cfg_path, rep)
    if cfg:
        check_required(cfg, rep)
        check_image(cfg, rep)
        check_sharding(cfg, cores_table, rep)
        check_workload(cfg, rep)
        check_baseline(cfg, rep)
        check_milestone_shapes(cfg, rep)
        check_arch_registration(cfg, rep)

    return rep.render()


if __name__ == "__main__":
    sys.exit(main())
