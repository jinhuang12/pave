#!/usr/bin/env python3
"""Kaizen Desktop launcher for the vLLM-Neuron serving SWEEP (neuron_sweep.py).

Reworked from the old single-cell `kaizen start-workload` submitter into a
Desktop-based orchestrator: it starts (or reuses) a long-lived Kaizen Desktop on
the model's ECR bench image, stages the sweep driver + config INTO the pod, then
runs `neuron_sweep.py` there — either a full detached sweep, a synchronous ~3-cell
smoke gate, or a dry-run cell preview. It follows the GPU side's "launch, not watch"
philosophy (SKILL.md §"Running via a task-dispatching agent"): the full sweep runs
DETACHED inside the pod (nohup, tee'd to a log on durable FSx) so it survives this
launcher exiting, and a handoff block (+ local file, the instance.txt analogue) tells
the operator how to pull results and build deliverables.

WHY A DESKTOP (not start-workload): the sweep compiles one server per (model,regime,tp)
— MINUTES of NEFF compile, cached in /var/tmp/neuron-compile-cache — and reuses it
across the whole shape×concurrency grid. A persistent Desktop keeps that warm compile
cache and lets `--smoke` and the full sweep share ONE provisioned box.

Usage (the Kaizen profile/region/account come from the environment — see ENV below)::

  # 1. dry-run: preview the cells the config expands to (no server, cheap)
  HF_TOKEN=<tok> python3 submit_neuron_bench.py --config neuron_bench_config.json \\
      --image <ecr-uri> --collect-only

  # 2. go/no-go smoke: ~3 cells (smallest tp, smallest shape) — proves image+config
  #    serve the model & compile; keeps the desktop warm for the full sweep
  HF_TOKEN=<tok> python3 submit_neuron_bench.py --config neuron_bench_config.json \\
      --image <ecr-uri> --smoke

  # 3. full sweep, DETACHED on the warm desktop from step 2 (reuse it)
  HF_TOKEN=<tok> python3 submit_neuron_bench.py --config neuron_bench_config.json \\
      --image <ecr-uri> --reuse

ENV (nothing site-specific is hardcoded; every one of these is overridable)::

  KAIZEN_BIN              path to the kaizen CLI (else $PATH, else ~/.toolbox/bin/kaizen)
  KAIZEN_PROFILE          AWS profile for submit/exec        (default: kaizen-access)
  KAIZEN_CLUSTER_PROFILE  AWS profile for the cluster account, used by `desktop connect`
                          and the pod-S3-home upload         (default: cluster-role)
  KAIZEN_CLUSTER_ACCOUNT  cluster account id — only used to print the `ada` hint
  KAIZEN_CLUSTER_ROLE     cluster role name  — only used to print the `ada` hint
  NEURON_DRIVER_RPM       local Neuron driver RPM matching the image's SDK (else no -nd)
  NEURON_CACHE_ALIAS      per-operator subdir for the persistent FSx NEFF compile cache
                          (default: the pod's $USER, else `shared`)
  IPA_SRC                 InferencePerfAnalyzer checkout src/ to vendor into the pod

Notes:
  * HF_TOKEN is injected into the pod inside a base64 blob (like the old stub) and is
    NEVER printed/logged.
  * --results-root MUST be a durable FSx path ($FSX_TEAM_SHARED_RW/<alias>/neuron-results
    by default) so the driver's state.json resume ledger survives desktop teardown.
  * A single-cell fallback smoke still lives in bench_one_model.sh (kept, not deleted) —
    the sweep is the primary path.

Requires: the kaizen CLI on $PATH (or $KAIZEN_BIN), a refreshed $KAIZEN_PROFILE, and
(for the driver install) the image-matched Neuron driver RPM at --driver.
"""
import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# --- deployment identity: read from the environment, never baked in ----------
# This launcher is account-, profile-, role- and operator-agnostic. The two profile
# NAMES keep a conventional default (they're names, not secrets, and every team's
# ada setup uses them); the account id and role name have NO default — they appear
# only inside the one error message that tells you how to refresh creds, rendered as
# `<placeholders>` when unset so nobody's account leaks into the repo.
KAIZEN = (os.environ.get("KAIZEN_BIN") or shutil.which("kaizen")
          or os.path.expanduser("~/.toolbox/bin/kaizen"))
DEFAULT_PROFILE = os.environ.get("KAIZEN_PROFILE", "kaizen-access")
DEFAULT_CLUSTER_PROFILE = os.environ.get("KAIZEN_CLUSTER_PROFILE", "cluster-role")
CLUSTER_ACCOUNT = os.environ.get("KAIZEN_CLUSTER_ACCOUNT", "<cluster-acct-id>")
CLUSTER_ROLE = os.environ.get("KAIZEN_CLUSTER_ROLE", "<cluster-role-name>")
# No default path: an RPM that only exists on one operator's box is worse than none
# (start would fail on the missing file), so unset simply means "start without -nd".
DEFAULT_DRIVER = os.environ.get("NEURON_DRIVER_RPM")
# neuron_sweep.py ships next to this launcher; stage it into the pod.
HERE = Path(__file__).resolve().parent
STAGE_DIR = "$HOME/neuron-bench-staging"  # in-pod, expanded by the pod shell
# The bench image ships vLLM-Neuron but NOT inference_perf_analyzer (IPA), which the
# driver imports for its framework-agnostic core (run_bench/paths/sweep_state). IPA is
# internal (not on PyPI), so we VENDOR it: tar the `inference_perf_analyzer` package
# from a local checkout and stage it into the pod on PYTHONPATH. Its only third-party
# dep for the paths the driver uses is `requests`, already in the image. Point
# --ipa-src at a checkout's `src/` (holding inference_perf_analyzer/).

# Subdir of the shared FSx compile cache. $NEURON_CACHE_ALIAS pins it explicitly (so two
# operators can deliberately SHARE one warm cache); unset falls back to the pod's own
# $USER, and `shared` if even that is empty. Resolved to a shell expression, not a name,
# so no operator identity is baked into this file.
CACHE_ALIAS = os.environ.get("NEURON_CACHE_ALIAS") or "${USER:-shared}"

# Pod-level env for vLLM-Neuron (the sweep sets the serve argv itself; this is the
# process env the plugin + runtime need). Mirrors the old stub's block.
POD_ENV = (
    "export PYTHONUNBUFFERED=1 VLLM_PLUGINS=neuron TOKENIZERS_PARALLELISM=false "
    "NEURON_SKIP_EFA_AFFINITY=1 HF_HUB_ENABLE_HF_TRANSFER=1\n"
    "export VLLM_LOGGING_LEVEL=ERROR VLLM_CONFIGURE_LOGGING=0 NEURON_RT_LOG_LEVEL=ERROR "
    "NEURON_RT_LOG_LOCATION=console PYTHONWARNINGS=ignore TQDM_DISABLE=1\n"
    # PERSISTENT NEFF compile cache on FSx (survives pod RESCHEDULE — /var/tmp is wiped
    # when Kaizen reschedules the pod, which otherwise restarts a multi-minute MoE compile
    # from scratch every time and can never finish if reschedules outpace it). NxD/
    # neuronx-cc read NEURON_COMPILE_CACHE_URL; a per-alias FSx dir persists + is shareable.
    f"export NEURON_COMPILE_CACHE_URL=$FSX_TEAM_SHARED_RW/{CACHE_ALIAS}/neuron-compile-cache\n"
    "mkdir -p \"$NEURON_COMPILE_CACHE_URL\" 2>/dev/null || true\n"
)


# --- config helpers ---------------------------------------------------------


def load_cfg(path):
    if not path.exists():
        sys.exit(f"config not found: {path}")
    raw = json.loads(path.read_text())
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def all_shardings(cfg):
    """Every (tp, dp) pair the config will sweep, smallest core-count first.

    Reads BOTH sharding keys the driver accepts: `shardings` ([{tp, dp?}], per-model or
    global — the GPU-parity key) and the legacy `tp_list` (which implies dp=1). Sorted by
    total cores so [0] is the cheapest sharding to smoke-test.
    """
    pairs = set()

    def _add(scope):
        for s in scope.get("shardings") or []:
            pairs.add((int(s["tp"]), int(s.get("dp", 1))))
        for t in scope.get("tp_list") or []:
            pairs.add((int(t), 1))

    _add(cfg)
    for m in cfg.get("models", []):
        _add(m)
    return sorted(pairs, key=lambda p: (p[0] * p[1], p[0])) or [(8, 1)]


def min_isl(cfg):
    isls = [int(s["isl"]) for s in cfg.get("shapes", []) if "isl" in s]
    return min(isls) if isls else None


def smoke_filter(cfg):
    """Derive a -k for ~a handful of cells: cheapest sharding + smallest shape. This starts
    ONE server (proves compile+load+serve) and runs that shape's concurrencies — the
    go/no-go gate before authorizing the full spend (SKILL.md 'Step-0 smoke gate')."""
    tp, dp = all_shardings(cfg)[0]
    # Must match the driver's cell-id token exactly: IPA's server_tag is 'tp<N>' at dp=1
    # and 'tp<N>_dp<M>' above it. `-k` is a substring match, and 'tp8' would also match
    # 'tp8_dp8' — so at dp>1 emit the full tag to keep the smoke set to ONE server.
    toks = [f"tp{tp}" if dp == 1 else f"tp{tp}_dp{dp}"]
    isl = min_isl(cfg)
    if isl is not None:
        toks.append(f"isl{isl}")
    return " ".join(toks)


def sanitize_name(s):
    # desktop --name: 1-32 chars, lowercase letters, digits, hyphen.
    out = "".join(c if (c.isalnum() or c == "-") else "-" for c in s.lower())
    return (out.strip("-") or "neuron-bench")[:32]


# --- kaizen wrappers --------------------------------------------------------


def kz(args, *, profile, check=True, capture=True):
    cmd = [KAIZEN] + args + ["--profile", profile]
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if check and r.returncode != 0:
        tail = ((r.stdout or "") + (r.stderr or ""))[-800:]
        sys.exit(f"kaizen {' '.join(args[:2])} FAILED (rc={r.returncode}):\n{tail}")
    return r


def desktop_exists(name, profile):
    r = kz(["desktop", "list"], profile=profile, check=False)
    return name in (r.stdout or "")


def desktop_status(name, profile):
    """Current STATUS token for `name` from `desktop list`, or None if not listed.

    `desktop list` prints a table `NAME  WORKLOAD_ID  INSTANCE_TYPE  STATUS  REGION`;
    return the STATUS column for our row (PENDING / RUNNING / ...)."""
    r = kz(["desktop", "list"], profile=profile, check=False)
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if parts and parts[0] == name:
            # STATUS is the 4th column (NAME, WORKLOAD_ID, INSTANCE_TYPE, STATUS, REGION)
            return parts[3] if len(parts) >= 4 else None
    return None


def wait_for_running(name, profile, *, timeout_s=900, poll_s=15):
    """Block until the desktop is RUNNING, or exit on timeout / terminal status.

    `desktop start` returns while the workload is still PENDING — staging or connecting
    before it reaches RUNNING fails with 'connection refused' (the pod's control plane
    isn't up yet). Poll `desktop list` until RUNNING."""
    import time
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        st = desktop_status(name, profile)
        if st != last:
            print(f"[wait] desktop '{name}' status: {st}", file=sys.stderr)
            last = st
        if st == "RUNNING":
            return
        if st in ("FAILED", "CANCELLED", "STOPPED", "TERMINATED"):
            sys.exit(f"desktop '{name}' reached terminal status {st} before RUNNING")
        time.sleep(poll_s)
    sys.exit(f"desktop '{name}' not RUNNING within {timeout_s}s (last status: {last})")


def connect_cmd(name, inner_bash, profile, *, capture=True):
    """Run `inner_bash` in the pod via `desktop connect --cmd` (bash -c, $HOME cwd).

    inner_bash is base64'd so multi-line scripts + secrets ride as one arg (same
    trick the old stub used). Callers NEVER log the command when the payload carries
    HF_TOKEN — only a short description is printed.
    """
    enc = base64.b64encode(inner_bash.encode()).decode()
    one_shot = f"echo {enc} | base64 -d | bash"
    # check=False always: callers inspect the return code themselves (a smoke that
    # fails must be REPORTED, not sys.exit'd; a detached sweep's rc is the connect's).
    return kz(["desktop", "connect", "--name", name, "--cmd", one_shot],
              profile=profile, check=False, capture=capture)


# --- inner-script builders (run in the pod) ---------------------------------


def vendor_ipa_to_s3(ipa_src, s3_home, cluster_profile):
    """Tar the inference_perf_analyzer package and upload it to the desktop's S3 home.

    The pod's $HOME is an S3-backed mount (s3SyncUri from `desktop info`); a file put in
    that prefix appears in the pod. This is how we get IPA (~150 KB) into the pod — the
    inline base64-via-`--cmd` path blows past ARG_MAX for anything package-sized. Upload
    uses the SAME cluster profile `desktop connect` uses ($KAIZEN_CLUSTER_PROFILE), not
    the service profile: the S3 bucket lives in the CLUSTER account (per the wiki's
    s3SyncHint), not the Kaizen service account. Returns the in-pod tar path.
    """
    import io, tarfile, tempfile
    pkg = Path(ipa_src) / "inference_perf_analyzer"
    if not pkg.is_dir():
        sys.exit(f"--ipa-src {ipa_src} has no inference_perf_analyzer/ package")
    tmp = Path(tempfile.gettempdir()) / "ipa_pkg.tgz"
    with tarfile.open(tmp, "w:gz") as tf:
        for p in sorted(pkg.rglob("*.py")):  # .py only: skip tests/pyc/data
            if "/unit_tests/" in p.as_posix() or "/tests/" in p.as_posix():
                continue
            tf.add(p, arcname=str(p.relative_to(Path(ipa_src))))
    dest = s3_home.rstrip("/") + "/ipa_pkg.tgz"
    # S3 bucket lives in the cluster account → the cluster profile, not the service one.
    r = subprocess.run(["aws", "s3", "cp", str(tmp), dest, "--profile", cluster_profile],
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"IPA upload to pod S3 home FAILED (is `{cluster_profile}` refreshed? "
                 f"`ada credentials update --account={CLUSTER_ACCOUNT} --provider=isengard "
                 f"--role={CLUSTER_ROLE} --profile={cluster_profile} --once` — set "
                 "$KAIZEN_CLUSTER_ACCOUNT / $KAIZEN_CLUSTER_ROLE to fill the "
                 f"placeholders):\n{r.stderr[-500:]}")
    return "$HOME/ipa_pkg.tgz"


def stage_script(cfg_json_text, ipa_tar_in_pod):
    """Bash that writes neuron_sweep.py + config.json into STAGE_DIR and (if given)
    extracts the S3-home IPA tar onto PYTHONPATH. Small files ride inline (base64);
    IPA comes via the S3 home (see vendor_ipa_to_s3 — inline is too big for ARG_MAX)."""
    sweep_b64 = base64.b64encode((HERE / "neuron_sweep.py").read_bytes()).decode()
    cfg_b64 = base64.b64encode(cfg_json_text.encode()).decode()
    lines = [
        "set -euo pipefail",
        f"mkdir -p {STAGE_DIR}/ipa",
        f"echo {sweep_b64} | base64 -d > {STAGE_DIR}/neuron_sweep.py",
        f"echo {cfg_b64} | base64 -d > {STAGE_DIR}/config.json",
    ]
    if ipa_tar_in_pod:
        lines += [
            f"tar -xzf {ipa_tar_in_pod} -C {STAGE_DIR}/ipa",
            f'test -d {STAGE_DIR}/ipa/inference_perf_analyzer '
            '|| { echo "IPA_UNPACK_FAILED"; exit 1; }',
        ]
    lines.append(f"echo STAGED_OK {STAGE_DIR}")
    return "\n".join(lines) + "\n"


def desktop_s3_home(name, profile):
    """The desktop's S3-home sync URI (from `desktop info` s3SyncUri)."""
    r = kz(["desktop", "info", "--name", name], profile=profile, check=False)
    for line in (r.stdout or "").splitlines():
        if "s3SyncUri" in line:
            return line.split(":", 1)[1].strip()
    return None


def run_script(hf_token, results_root, *, mode, k, startup_timeout):
    """Bash that runs the sweep in the pod. mode in {full, smoke, collect}.

    ALL run modes are DETACHED with the live log on LOCAL pod disk (/tmp), NOT a pipe
    and NOT the S3-backed FSx home. This is load-bearing, learned the hard way on the
    first real run:
      * A synchronous `connect --cmd` that blocks on the sweep dies when the client's
        timeout fires during the multi-minute NEFF compile — orphaning the server.
      * The orphaned (or even attached) server then WEDGES on `pipe_write`: vLLM's
        stdout fills the pipe buffer during compile and blocks forever with nothing
        draining it. Writing to a local file instead means the writer never blocks.
      * FSx/S3-home has high write latency that ALSO stalls the log writer, so the
        live log must be local; results are synced to the durable results-root
        separately, on an interval, in the background.
    `collect` is the only cheap/fast mode, but we still detach it uniformly for one
    code path. The launcher polls the local log + syncs results out via get-artifact.
    """
    base = [
        "set -uo pipefail",
        POD_ENV.rstrip("\n"),
        # NEVER echo this line's value.
        f"export HF_TOKEN='{hf_token}'",
        f'RR="{results_root}"',
        'mkdir -p "$RR"',
        f"cd {STAGE_DIR}",
        # vendored IPA on PYTHONPATH (bench image lacks inference_perf_analyzer).
        f'export PYTHONPATH="{STAGE_DIR}/ipa:${{PYTHONPATH:-}}"',
    ]
    # Live log on LOCAL disk (drains freely — the anti-wedge fix); the driver's own
    # per-server serve logs also go to /tmp (see neuron_sweep.start_server_to_file).
    inv = ["python3 neuron_sweep.py", "--config config.json", '--results-root "$RR"',
           f"--startup-timeout {int(startup_timeout)}"]
    if mode == "collect":
        inv.append("--collect-only")
    if k:
        inv.append(f'-k "{k}"')
    invocation = " ".join(inv)

    if mode == "collect":
        # cheap + fast — run inline (no compile, no server); prints cells to stdout.
        base.append(invocation)
        return "\n".join(base) + "\n"

    # smoke + full: DETACHED SUPERVISOR LOOP, live log on FSx (survives pod RESCHEDULE).
    # Two hazards this handles: (1) the pipe_write wedge — we redirect to a FILE, never a
    # pipe; (2) Kaizen RESCHEDULING the pod, which SIGKILLs the detached process AND wipes
    # /tmp. So the log lives on FSx (`$RR/sweep.log`, durable) and a `setsid` supervisor
    # RE-INVOKES the driver until the resume ledger is satisfied — each restart skips
    # already-PASSED cells (ledger on FSx) and reuses the FSx NEFF compile cache, so a
    # reschedule costs at most the in-flight cell, not the whole run. The supervisor exits
    # when the driver returns 0 (all cells done/clean) or after a bounded retry budget.
    # Build the supervisor as a self-contained script FILE with all paths hard-baked
    # (not shell vars) — `setsid bash <file>` runs a FRESH shell that does NOT inherit
    # $RR/$LOG/$PYTHONPATH from this connect session, so referencing them by name silently
    # yields empty strings (the bug that produced no logs + an empty --results-root). We
    # write the script to a fixed path, then setsid it detached.
    rr = results_root                      # e.g. $FSX_TEAM_SHARED_RW/... (expands in-pod)
    stage = STAGE_DIR
    sup_body = (
        "#!/bin/bash\n"
        "set -uo pipefail\n"
        f'RR="{rr}"\n'
        'mkdir -p "$RR"\n'
        f'export PYTHONPATH="{stage}/ipa:${{PYTHONPATH:-}}"\n'
        f'cd "{stage}"\n'
        'LOG="$RR/sweep.log"; SUPLOG="$RR/supervisor.log"\n'
        'n=0\n'
        'while [ $n -lt 12 ]; do\n'
        '  n=$((n+1))\n'
        '  echo "[sup] attempt $n $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$SUPLOG"\n'
        f'  {invocation} >> "$LOG" 2>&1\n'
        '  rc=$?\n'
        '  echo "[sup] driver rc=$rc $(date -u +%H:%M:%S)" >> "$SUPLOG"\n'
        '  [ $rc -eq 0 ] && break\n'
        '  sleep 15\n'
        'done\n'
    )
    sup_b64 = base64.b64encode(sup_body.encode()).decode()
    base += [
        'LOG="$RR/sweep.log"',
        f'echo {sup_b64} | base64 -d > "$RR/supervisor.sh"',
        'chmod +x "$RR/supervisor.sh"',
        'setsid bash "$RR/supervisor.sh" < /dev/null >/dev/null 2>&1 &',
        'echo "SWEEP_PID=$!"',
        'echo "SWEEP_LOG=$LOG (FSx — durable; supervisor: $RR/supervisor.log, script: $RR/supervisor.sh)"',
        'disown || true',
        'sleep 8',
        'echo "--- supervisor.log ---"; tail -4 "$RR/supervisor.log" 2>/dev/null || echo "(sup not written yet)"',
    ]
    return "\n".join(base) + "\n"


# --- handoff ----------------------------------------------------------------


def fsx_relative(results_root):
    """Best-effort relative source for `kaizen get-artifact` (path within the FSx
    location). Only reliable when results_root starts with $FSX_TEAM_SHARED_RW."""
    prefix = "$FSX_TEAM_SHARED_RW/"
    if results_root.startswith(prefix):
        return results_root[len(prefix):]
    return None


def write_handoff(path, *, name, instance, image, results_root, profile, mode, log_hint):
    rel = fsx_relative(results_root)
    if rel:
        get_cmd = (f"{KAIZEN} get-artifact --location FSX_TEAM_SHARED_RW "
                   f"--source {rel} --destination ./results --directory --profile {profile}")
    else:
        get_cmd = ("# results-root is not under $FSX_TEAM_SHARED_RW — set --source to its "
                   "path within one of get-artifact's FSx locations:\n"
                   f"# {KAIZEN} get-artifact --location <FSX_*_RW> --source <rel-path> "
                   f"--destination ./results --directory --profile {profile}")
    lines = [
        "# Neuron bench desktop handoff (instance.txt analogue) — 'launch, not watch'.",
        f"desktop_name : {name}",
        f"instance     : {instance}",
        f"image        : {image}",
        f"profile      : {profile}",
        f"results_root : {results_root}  (durable FSx; state.json resume ledger lives here)",
        f"mode         : {mode}",
        f"log          : {log_hint}",
        "",
        "# Inspect the desktop:",
        f"{KAIZEN} desktop info --name {name} --profile {profile}",
        f"{KAIZEN} desktop connect --name {name} --profile {profile} "
        f'--cmd \'tail -n 60 "{results_root}/sweep.log"\'',
        "",
        "# Pull results out (after the sweep):",
        get_cmd,
        "",
        "# VERIFY BEFORE TRUSTING: a cell's numbers are only valid if it actually ran to",
        "# completion. The driver's ledger (state.json) records `completed` per cell; a",
        "# cell is clean when completed == num_prompts (num_prompts = max(conc*"
        "num_prompts_per_conc, min_prompts)).",
        f"{KAIZEN} desktop connect --name {name} --profile {profile} "
        f'--cmd \'python3 -c "import json;print(json.load(open(\\"{results_root}/state.json\\")))"\'',
        "",
        "# Build deliverables LOCALLY once results are pulled:",
        "python3 build_deliverables.py ./results --multi --instance trn2 --out ./deliverables",
        "",
        f"# Stop the desktop when done (frees the box): {KAIZEN} desktop stop --name {name} "
        f"--profile {profile}",
    ]
    text = "\n".join(lines) + "\n"
    Path(path).write_text(text)
    return text


# --- main -------------------------------------------------------------------


def main(argv=None):
    p = argparse.ArgumentParser(description="Kaizen Desktop launcher for neuron_sweep.py")
    p.add_argument("--config", type=Path, required=True,
                   help="path to neuron_bench_config.json")
    alias = os.environ.get("USER", "user")
    p.add_argument("--results-root",
                   default=f"$FSX_TEAM_SHARED_RW/{alias}/neuron-results",
                   help="durable FSx results/ledger root (expanded IN the pod); "
                        "default $FSX_TEAM_SHARED_RW/<alias>/neuron-results")
    p.add_argument("--name", default=None, help="desktop name (default derived from config)")
    p.add_argument("--instance", default=None, help="override config's `instance` field")
    p.add_argument("--image", default=None, help="override config's `image` (ECR URI)")
    p.add_argument("--profile", default=DEFAULT_PROFILE,
                   help="AWS profile for submit/exec (default $KAIZEN_PROFILE, "
                        f"currently {DEFAULT_PROFILE!r})")
    p.add_argument("--cluster-profile", default=DEFAULT_CLUSTER_PROFILE,
                   help="AWS profile for the CLUSTER account — the pod's S3 home lives "
                        "there, so the IPA upload uses this, not --profile (default "
                        f"$KAIZEN_CLUSTER_PROFILE, currently {DEFAULT_CLUSTER_PROFILE!r})")
    p.add_argument("--ipa-src", default=os.environ.get("IPA_SRC"),
                   help="path to an InferencePerfAnalyzer checkout's src/ (holding "
                        "inference_perf_analyzer/). VENDORED into the pod because the "
                        "bench image lacks IPA. Or set $IPA_SRC. If the image already "
                        "has IPA installed, pass --no-vendor-ipa to skip.")
    p.add_argument("--no-vendor-ipa", action="store_true",
                   help="don't vendor IPA (use only if it's already importable in the image)")
    p.add_argument("--driver", default=DEFAULT_DRIVER,
                   help="local Neuron driver RPM matching the image's SDK, installed on "
                        "the desktop host via -nd (default $NEURON_DRIVER_RPM; unset → "
                        "start without -nd)")
    p.add_argument("--desktop-timeout", type=int, default=28800,
                   help="max desktop lifetime seconds (must exceed the sweep; default 8h)")
    p.add_argument("--priority", default="high")
    p.add_argument("--smoke", action="store_true",
                   help="run only ~3 cells (smallest tp+shape) synchronously as a go/no-go gate")
    p.add_argument("--collect-only", action="store_true",
                   help="preview the cells the config expands to, then exit (no server)")
    p.add_argument("--reuse", action="store_true",
                   help="reuse an already-running desktop of this --name (skip start)")
    p.add_argument("--keep-desktop", action="store_true",
                   help="never stop the desktop at the end (default keeps it for smoke/full)")
    p.add_argument("-k", dest="k", default=None,
                   help="cell filter passed to the driver's -k (overrides --smoke's default)")
    args = p.parse_args(argv)

    hf = os.environ.get("HF_TOKEN")
    if not hf:
        sys.exit("HF_TOKEN must be set in the environment")

    cfg = load_cfg(args.config)
    cfg_text = args.config.read_text()  # stage the raw file (comments preserved)
    image = args.image or cfg.get("image")
    instance = args.instance or cfg.get("instance", "trn2.48xlarge")
    if not image or str(image).startswith("<"):
        sys.exit("no image: set --image or a real `image` (ECR URI) in the config")
    model_key = (cfg.get("models") or [{}])[0].get("key", "model")
    name = sanitize_name(args.name or f"neuron-{model_key}")

    mode = "collect" if args.collect_only else ("smoke" if args.smoke else "full")
    k = args.k or (smoke_filter(cfg) if mode == "smoke" else None)

    # 1/2. Start (or reuse) the desktop on the model's ECR image + instance.
    if args.reuse or desktop_exists(name, args.profile):
        if not desktop_exists(name, args.profile):
            sys.exit(f"--reuse given but no desktop named '{name}' is tracked "
                     f"(see `{KAIZEN} desktop list --profile {args.profile}`)")
        print(f"[reuse] desktop '{name}' already running — skipping start", file=sys.stderr)
    else:
        start = ["desktop", "start", "--name", name, "--image", image,
                 "--instanceType", instance, "--nodeCount", "1",
                 "--timeout", str(args.desktop_timeout), "--priority", args.priority]
        # -nd installs the Neuron driver on the host BEFORE the workload; skip if no RPM
        # was given or it isn't present locally (start would fail on the missing file).
        if args.driver and os.path.exists(args.driver):
            start += ["--neuron-driver-pkg-file", args.driver]
        else:
            where = f"not found at {args.driver}" if args.driver else \
                    "not set ($NEURON_DRIVER_RPM / --driver)"
            print(f"[warn] driver RPM {where} — starting without -nd (pass --driver <path> "
                  f"if the image needs an SDK-matched driver)", file=sys.stderr)
        # Default --command is 'sleep infinity' (pod stays up for connect) — don't override.
        print(f"[start] desktop '{name}' on {instance} with image {image}", file=sys.stderr)
        kz(start, profile=args.profile, capture=False)

    # 2b. Wait for RUNNING — `desktop start` returns while PENDING, and staging/connect
    # before the pod's control plane is up fails with 'connection refused'.
    wait_for_running(name, args.profile)

    # 3. Vendor IPA (bench image lacks it) via the pod's S3 home, then stage the driver
    #    + config inline. IPA is ~150 KB — too big for inline base64 (ARG_MAX), so it
    #    goes through the S3-backed $HOME mount (uploaded with --cluster-profile).
    ipa_tar = None
    if not args.no_vendor_ipa:
        if not args.ipa_src:
            sys.exit("IPA is not in the bench image — pass --ipa-src <IPA checkout>/src "
                     "(or set $IPA_SRC) to vendor it, or --no-vendor-ipa if the image "
                     "already has inference_perf_analyzer installed.")
        s3_home = desktop_s3_home(name, args.profile)
        if not s3_home:
            sys.exit("could not resolve the desktop's s3SyncUri (needed to vendor IPA)")
        print(f"[vendor] uploading IPA to pod S3 home ({s3_home})", file=sys.stderr)
        ipa_tar = vendor_ipa_to_s3(args.ipa_src, s3_home, args.cluster_profile)
    print(f"[stage] writing neuron_sweep.py + config"
          f"{' + extracting IPA' if ipa_tar else ''} into {STAGE_DIR}", file=sys.stderr)
    r = connect_cmd(name, stage_script(cfg_text, ipa_tar), args.profile, capture=True)
    if r.returncode != 0 or "STAGED_OK" not in (r.stdout or ""):
        sys.exit("staging FAILED:\n" + ((r.stdout or "") + (r.stderr or ""))[-800:])

    # 4-6. Run the sweep. collect is inline (fast); smoke + full launch DETACHED (setsid)
    # with the live log on LOCAL pod disk, so a slow/killed `connect` can never orphan or
    # wedge the run (the pipe_write deadlock + synchronous-timeout traps from the first
    # real run). The driver writes results straight to the durable results-root.
    startup_timeout = int(cfg.get("startup_timeout_s", cfg.get("bench_timeout_s", 3600)))
    script = run_script(hf, args.results_root, mode=mode, k=k, startup_timeout=startup_timeout)
    if mode == "collect":
        print(f"[collect-only] previewing cells (k={k or 'ALL'})", file=sys.stderr)
        r = connect_cmd(name, script, args.profile, capture=False)  # cells print to stdout
        rc = r.returncode
    else:  # smoke or full — both DETACHED
        tag = "smoke" if mode == "smoke" else "full"
        print(f"[{tag}] launching DETACHED sweep in the pod (setsid; local log)"
              f"{f' — go/no-go cells -k \"{k}\"' if mode == 'smoke' else ''}", file=sys.stderr)
        r = connect_cmd(name, script, args.profile, capture=True)  # token in payload — don't echo raw
        rc = r.returncode
        for line in (r.stdout or "").splitlines():  # only safe launcher lines
            if line.startswith(("SWEEP_PID=", "SWEEP_LOG=", "--- sweep.log")) or \
               (line and not line.startswith(("echo", "export", "python3", "setsid"))):
                if line.startswith(("SWEEP_PID=", "SWEEP_LOG=")):
                    print(f"[{tag}] " + line, file=sys.stderr)
        print(f"[{tag}] launched (connect rc={rc}). Poll progress with:\n"
              f"    {KAIZEN} desktop connect --name {name} --profile {args.profile} "
              f"--cmd 'tail -30 {args.results_root}/sweep.log; tail -5 {args.results_root}/supervisor.log'\n"
              f"  then pull results + build deliverables (see handoff below). For smoke, "
              f"verify state.json cells show completed==num_prompts before the full sweep.",
              file=sys.stderr)

    # 7. Handoff block (+ local file). Desktop is kept alive unless collect-only.
    log_hint = f"{args.results_root}/sweep.log (FSx, durable across reschedule)" if mode != "collect" else "(inline — see console above)"
    handoff_path = f"neuron-bench-{name}.txt"
    text = write_handoff(handoff_path, name=name, instance=instance, image=image,
                         results_root=args.results_root, profile=args.profile,
                         mode=mode, log_hint=log_hint)
    print("\n" + text)
    print(f"[handoff] written to ./{handoff_path}", file=sys.stderr)

    # Teardown policy: full & smoke KEEP the desktop (full sweep is detached ON it;
    # smoke leaves it warm for the full sweep). Only collect-only auto-stops.
    if mode == "collect" and not args.keep_desktop and not args.reuse:
        print(f"[stop] collect-only done — stopping desktop '{name}'", file=sys.stderr)
        kz(["desktop", "stop", "--name", name], profile=args.profile, check=False, capture=False)
    elif not args.keep_desktop:
        print(f"[keep] desktop '{name}' left RUNNING (stop it manually when done: "
              f"{KAIZEN} desktop stop --name {name} --profile {args.profile})", file=sys.stderr)

    return rc


if __name__ == "__main__":
    sys.exit(main())
