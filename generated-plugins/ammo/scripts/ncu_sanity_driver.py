#!/usr/bin/env python3
"""Optional claim-driven NCU driver — minimal steady-state replay target.

Use this only for a specific unresolved hardware-counter claim. The wrapper
(`ncu_sanity.sh`) runs it under `ncu --replay-mode application`. The driver
launches ONE `pumice-server` (paying the cold start / torch.compile /
CUDA-graph capture just once), warms up for a handful of decode waves, then
runs the exact number of steady-state decode waves the caller asks for. Under
`--replay-mode application`, ncu re-runs this entire process once per metric
pass, so the driver does NOT loop internally on a per-pass basis — it runs
ONE benchmark from start to finish, ncu handles pass orchestration externally.

Why a dedicated driver instead of the campaign sweep runner:

`run_pumice_grpc_bench.py` runs a full multi-bucket, multi-launch sweep with
warmup waves, correctness phases, and artifact assembly. Under ncu
application-replay that whole program re-runs once per metric pass, and ncu
captures EVERY kernel launch in the target. With L decoder layers and a broad
kernel filter, a single ncu run easily replays hundreds of kernel launches per
pass × 18+ passes for default `--set` metrics = tens of minutes just on the
cold-start-adjacent launches. Adding `--launch-count 30` to ncu helps, but only
bounds what ncu *records* — the underlying engine still pays the full warmup
every pass.

This driver fixes that by making warmup tiny (default 3) and steady-state
decode waves explicit (default 10). A caller who wants a 30-launch ncu window
picks `--warmup 3 --iters 10` and sets ncu's `--launch-count 30`; the two
numbers multiply to a predictable kernel-launch budget.

## Pumice capture topology (differs from the vLLM-era driver)

Pumice's engine is a SEPARATE PROCESS: this driver is a gRPC client, and every
kernel launch happens inside `pumice-server`. Two consequences:

1. ncu must be given `--target-processes all` so it follows the server child.
   `ncu_sanity.sh` already passes it.
2. `--profile-from-start off` gates capture on `cudaProfilerStart()` in the
   process that launches kernels — the SERVER, not this driver. Calling
   `torch.cuda.cudart().cudaProfilerStart()` here would gate the wrong CUDA
   context (this process has none). The correct arming mechanism is the gRPC
   `StartProfile` / `StopProfile` RPC pair, which sets the Rust engine loop's
   pending cudaProfilerStart/Stop flags (`pumice-rust/src/async_llm.rs`,
   `pumice-rust/src/grpc_server.rs`). That is what this driver uses, and it is
   the same mechanism `run_pumice_grpc_bench.py --nsys-profile` uses.

Target invocation (called by ncu_sanity.sh):
  ncu ... --target-processes all --launch-count 30 -- \\
    python ncu_sanity_driver.py \\
      --target-json <path> --batch-size 8 --warmup 3 --iters 10
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path


def _load_bench_module():
    """Import ``run_pumice_grpc_bench`` from this script's own directory.

    The driver reuses the campaign runner's server launch, health-poll and
    wave-driving primitives so the profiled workload is the SAME code path the
    sweep measures. Importing by path keeps the driver runnable from any cwd and
    from either variant tree (`.claude/` or `.codex/`).
    """
    here = Path(__file__).resolve().parent
    target = here / "run_pumice_grpc_bench.py"
    if not target.is_file():
        raise SystemExit(
            f"cannot find run_pumice_grpc_bench.py next to {__file__}; the NCU "
            "driver reuses its server-launch and wave-driving helpers."
        )
    spec = importlib.util.spec_from_file_location("run_pumice_grpc_bench", target)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_pumice_grpc_bench"] = module
    spec.loader.exec_module(module)
    return module


def _read_target(target_json: Path) -> dict:
    with target_json.open() as f:
        return json.load(f)


def _pick_bucket(target: dict, batch_size: int) -> dict:
    workload = target.get("workload", {})
    matrix = workload.get("workload_matrix")
    if matrix:
        for b in matrix:
            if int(b["batch_size"]) == batch_size:
                return {
                    "input_len": int(b["input_len"]),
                    "output_len": int(b["output_len"]),
                    "batch_size": batch_size,
                }
        raise SystemExit(
            f"batch_size={batch_size} not in workload_matrix; "
            f"available: {[b['batch_size'] for b in matrix]}"
        )
    return {
        "input_len": int(workload.get("input_len", 64)),
        "output_len": int(workload.get("output_len", 512)),
        "batch_size": batch_size,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target-json", type=Path, required=True,
                   help="target.json — used for model_id, tp, max_model_len, workload shape.")
    p.add_argument("--batch-size", type=int, required=True,
                   help="Which decode bucket to replay. Must be present in workload.")
    p.add_argument("--warmup", type=int, default=3,
                   help="Steady-state warmup waves before the measurement window. "
                        "Small by design — ncu application-replay pays this per pass.")
    p.add_argument("--iters", type=int, default=10,
                   help="Steady-state waves to time. ncu will capture the first "
                        "`--launch-count` kernel launches within these waves; 10 waves × "
                        "L decoder layers is the upper bound on captured launches per pass.")
    p.add_argument("--output-len", type=int, default=None,
                   help="Override workload.output_len. Defaults to the bucket's output_len. "
                        "Short OL (e.g. 8) is fine for ncu — we're measuring kernel counters, "
                        "not latency — and it keeps each wave cheap.")
    p.add_argument("--repo-root", type=Path, default=None,
                   help="Pumice checkout root used to resolve the server binary "
                        "(default: CWD).")
    p.add_argument("--health-timeout-s", type=float, default=1800.0,
                   help="Server health-poll budget. The default is generous because a "
                        "fresh FlashInfer cache JIT-compiles on the first launch.")
    args = p.parse_args()

    bench = _load_bench_module()

    target = _read_target(args.target_json)
    bucket = _pick_bucket(target, args.batch_size)
    if args.output_len is not None:
        bucket["output_len"] = args.output_len

    tgt_section = target["target"]
    model_id = tgt_section["model_id"]
    tp = int(tgt_section.get("tp", 1))
    max_model_len = int(tgt_section.get("max_model_len", 4096))
    bench_section = target.get("bench", {}) or {}
    server_cmd_spec = bench_section.get("server_cmd", "pumice-server")
    extra_args = list(bench_section.get("extra_args") or [])

    repo_root = (args.repo_root or Path.cwd()).resolve()
    server_exe = bench.resolve_server_exe(server_cmd_spec, repo_root)
    model_path = bench._resolve_local_model_path(model_id)
    port = bench._pick_free_port()

    # Enforce eager is NOT set: the counters must describe the production
    # CUDA-graph path. `--replay-mode application` is what makes that possible.
    server_argv = bench.build_server_cmd(
        server_exe=server_exe,
        model_id=model_path,
        port=port,
        max_model_len=max_model_len,
        max_num_seqs=bucket["batch_size"],
        tensor_parallel_size=tp,
        extra_args=extra_args,
    )

    # The engine's own metrics/stat logging adds launches we do not want inside
    # an ncu-profiled process. Pinned explicitly rather than inherited.
    env = dict(os.environ)
    env.setdefault("PUMICE_DISABLE_LOG_STATS", "1")
    env = bench._ensure_worktree_pythonpath(env, cwd=str(repo_root))

    log_path = Path(os.environ.get("NCU_DRIVER_LOG", "ncu_sanity_driver.server.log"))

    print(f"[driver] model={model_path} tp={tp} bs={bucket['batch_size']} "
          f"il={bucket['input_len']} ol={bucket['output_len']} "
          f"warmup={args.warmup} iters={args.iters} port={port}", flush=True)

    server = bench.ServerHandle(
        cmd=server_argv,
        env=env,
        port=port,
        log_path=log_path,
        cwd=str(repo_root),
    )
    rc = 0
    try:
        server.start()
        server.connect()
        server.wait_healthy(timeout_s=args.health_timeout_s)

        prompts = bench.build_random_prompt_ids(
            input_len=bucket["input_len"],
            output_len=bucket["output_len"],
            batch_size=bucket["batch_size"],
        )

        def _wave(wave_id: str) -> None:
            bench.drive_wave(
                stub=server.stub,
                pb2=server.pb2,
                prompts=prompts,
                output_len=bucket["output_len"],
                temperature=0.0,
                ignore_eos=True,
                timeout_s=3600.0,
                wave_id=wave_id,
            )

        print("[driver] warmup...", flush=True)
        for i in range(args.warmup):
            _wave(f"warmup-{i}")

        # Application-replay: ncu runs this whole process (and, with
        # --target-processes all, the server child) multiple times, capturing a
        # bounded kernel window per pass. Gate the measured region with the gRPC
        # StartProfile/StopProfile pair so that when ncu is launched with
        # `--profile-from-start off` the cold start + warmup launches stay out of
        # the captured launch-count window. The RPCs arm cudaProfilerStart/Stop
        # inside the SERVER, which is the process that launches kernels.
        print("[driver] measure...", flush=True)
        profiler_started = False
        try:
            server.start_profile()
            profiler_started = True
        except Exception as exc:  # noqa: BLE001 — we need to surface any failure mode
            # Without StartProfile, `--profile-from-start off` means ncu captures
            # nothing. Warn loudly — silent degradation here produces an empty CSV
            # that looks superficially valid and wastes a full rerun to diagnose.
            print(f"[driver] WARNING: StartProfile RPC failed ({exc!r}); "
                  f"ncu capture window may be empty if --profile-from-start off is set.",
                  file=sys.stderr, flush=True)

        t0 = time.perf_counter()
        for i in range(args.iters):
            _wave(f"measure-{i}")
        dt = time.perf_counter() - t0

        if profiler_started:
            try:
                server.stop_profile()
            except Exception as exc:  # noqa: BLE001
                print(f"[driver] WARNING: StopProfile RPC failed ({exc!r})",
                      file=sys.stderr, flush=True)

        print(f"[driver] done: {args.iters} waves in {dt:.3f}s "
              f"({dt / max(1, args.iters):.3f}s/wave avg)", flush=True)
    finally:
        server.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
