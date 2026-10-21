#!/usr/bin/env python3
"""kernel_loop_scaffold.py -- stateless measurement helpers for campaign kernel tasks.

The campaign's NKI specialist tasks use these helpers for isolated source changes and
measurements. They do not own task state, admission, recovery or campaign completion.
Only neuron-campaign advances the workflow; this script performs explicit local operations:

  worktrees   create one git worktree per candidate fix, off a fixed base sha
  diff-guard  is a worktree's src/ actually different from base? (reject no-op commits)
  remeasure   authoritative measure.py of a worktree (the SOLE accept oracle, D19)
  verdict     accept iff cycles < base*(1-margin); name the winner to CONFIRM-remeasure
  deepdive    assemble a per-candidate implementation deep dive (specialist input + kernel
              output + test input + test output + resulting measure) -- the artifact
              the campaign writer must cite for each non-no-op candidate
  finalize    git reset --hard to the winner (or base if none); prune worktrees; keep branches

measure.py remains the only source of pass/fail and cycles. This script never judges a fix;
it only runs the oracle and applies the arithmetic. Writer self-reported numbers are ignored
by construction -- only `remeasure` here counts.

Examples
--------
  S=<kiro>/kernel_loop_scaffold.py ; REPO=/path/to/disposable
  python3 $S worktrees --repo $REPO --base <SHA> --ids fix_a fix_b fix_c
  # ... orchestrator: writer subagent edits+commits inside each worktree ...
  python3 $S remeasure --repo $REPO --base <SHA> --worktree $REPO/.attempts/loop_fix_a \
      --node '<NODE>' --target trn2 --log /tmp/measure_fix_a.log
  python3 $S verdict --base-cycles 74764049 --margin 0.02 \
      --candidates '[{"id":"fix_a","cycles":73000000,"passed":true},...]'
  python3 $S finalize --repo $REPO --base <SHA> --winner-sha <SHA-or-empty>
"""
from __future__ import annotations
import argparse, json, subprocess, sys, shutil
from pathlib import Path

ATTEMPT_DIR = ".attempts"
BRANCH_PREFIX = "attempt/loop_"
WT_PREFIX = "loop_"


def _git(repo, *args, check=False):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


def cmd_worktrees(a) -> int:
    repo = Path(a.repo).resolve()
    _git(repo, "worktree", "prune")
    out = {}
    for fid in a.ids:
        wt = repo / ATTEMPT_DIR / f"{WT_PREFIX}{fid}"
        br = f"{BRANCH_PREFIX}{fid}"
        # -f + -B to be idempotent across reruns
        r = _git(repo, "worktree", "add", "-f", "-B", br, str(wt), a.base)
        out[fid] = {"worktree": str(wt), "branch": br,
                    "ok": r.returncode == 0, "err": (r.stderr or "").strip()[-200:]}
    print(json.dumps(out, indent=2))
    return 0 if all(v["ok"] for v in out.values()) else 1


def cmd_diff_guard(a) -> int:
    """exit 0 = REAL src diff vs base (measure it); exit 3 = NO-OP (discard)."""
    wt = Path(a.worktree).resolve()
    r = _git(wt, "diff", "--quiet", a.base, "HEAD", "--", "src/")
    # git diff --quiet: exit 1 == differences exist, 0 == identical
    noop = (r.returncode == 0)
    files = _git(wt, "diff", "--name-only", a.base, "HEAD", "--", "src/").stdout.split()
    print(json.dumps({"noop": noop, "changed_files": files}))
    return 3 if noop else 0


def cmd_remeasure(a) -> int:
    """Authoritative measure of a worktree. Runs diff-guard first unless --skip-guard."""
    repo = Path(a.repo).resolve()
    wt = Path(a.worktree).resolve()
    if not a.skip_guard:
        r = _git(wt, "diff", "--quiet", a.base, "HEAD", "--", "src/")
        if r.returncode == 0:
            print(json.dumps({"noop": True, "passed": False, "cycles": None,
                              "reason": "src identical to base (no-op commit) -> discard"}))
            return 3
    # measure.py is co-located with this script (vendored into the skill's scripts/, or in the
    # plugin's orchestrator/kiro/). Prefer the sibling; fall back to the KNKL plugin path so an
    # older layout still resolves. (PIDEV_REMOVAL_DISCREPANCIES Item 5: co-packaging.)
    sibling = Path(__file__).resolve().parent / "measure.py"
    measure = sibling if sibling.exists() else (
        repo / "nkilib_agentic_dev_plugin" / "orchestrator" / "kiro" / "measure.py")
    cmd = [sys.executable, str(measure), "--repo", str(wt), "--node", a.node,
           "--target", a.target]
    if a.shadow is not None:
        cmd += ["--shadow", a.shadow]
    if a.log:
        cmd += ["--log", a.log]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout.strip() else "{}"
    try:
        res = json.loads(line)
    except Exception:
        res = {"passed": False, "cycles": None, "reason": "measure.py produced no JSON",
               "raw": (proc.stdout or proc.stderr or "")[-300:]}
    res["noop"] = False
    print(json.dumps(res))
    return 0 if res.get("passed") and res.get("cycles") else 1


def cmd_verdict(a) -> int:
    cands = json.loads(a.candidates)
    thr = a.base_cycles * (1.0 - a.margin)
    ranked = sorted((c for c in cands if c.get("passed") and c.get("cycles")),
                    key=lambda c: c["cycles"])
    best = ranked[0] if ranked else None
    accept = bool(best and best["cycles"] < thr)
    out = {
        "base_cycles": a.base_cycles,
        "margin": a.margin,
        "accept_threshold": round(thr, 1),
        "ranked": [{"id": c.get("id"), "cycles": c["cycles"],
                    "pct_vs_base": round(100.0 * (c["cycles"] - a.base_cycles) / a.base_cycles, 3)}
                   for c in ranked],
        "best": (best.get("id") if best else None),
        "best_cycles": (best["cycles"] if best else None),
        "verdict": "ACCEPT_PENDING_CONFIRM" if accept else "STALL",
        "confirm": (best.get("id") if accept else None),
        "note": ("re-measure the 'confirm' candidate once more; accept only if it ALSO "
                 "clears the threshold (guards ~1-1.5% run-to-run noise)"
                 if accept else
                 "no candidate cleared the margin -> keep base (a stall is a valid, honest "
                 "outcome; do not accept a noise-level win)"),
    }
    print(json.dumps(out, indent=2))
    return 0 if accept else 3


def _read_measure(measure_json: str, measure_log: str) -> dict:
    """Return the measure.py result dict from an explicit JSON file, else the last JSON line
    of a measure log, else {}."""
    if measure_json:
        p = Path(measure_json).expanduser()
        if p.exists():
            try:
                return json.loads(p.read_text())
            except Exception:
                pass
    if measure_log:
        p = Path(measure_log).expanduser()
        if p.exists():
            for ln in reversed(p.read_text().splitlines()):
                s = ln.strip()
                if s.startswith("{") and s.endswith("}"):
                    try:
                        return json.loads(s)
                    except Exception:
                        continue
    return {}


def cmd_deepdive(a) -> int:
    """Assemble a per-candidate implementation deep dive: specialist input, kernel output (src
    diff), test input (node), test output (correctness), and the resulting measure. This is the
    artifact the campaign writer cites for each non-no-op candidate, so every kernel
    implementation the loop tries leaves an auditable record of WHY it was tried, WHAT it changed,
    HOW it was tested, and WHAT the oracle measured."""
    import time
    repo = Path(a.repo).resolve()
    wt = Path(a.worktree).resolve() if a.worktree else repo
    # kernel output = the src/ diff vs base (the actual change the writer produced)
    diff, changed = "", []
    if a.base:
        diff = _git(wt, "diff", a.base, "HEAD", "--", "src/").stdout
        changed = _git(wt, "diff", "--name-only", a.base, "HEAD", "--", "src/").stdout.split()
    # specialist input = the answer-blind findings that motivated this candidate
    specialists = []
    if a.specialist_findings:
        try:
            specialists = json.loads(a.specialist_findings)
        except Exception as e:
            print(json.dumps({"ok": False, "error": f"bad --specialist-findings JSON: {e}"}))
            return 2
    # test output + resulting measure = the device oracle result
    measure = _read_measure(a.measure_json, a.measure_log)

    if a.out:
        out = Path(a.out).expanduser()
    elif a.state_dir:
        out = Path(a.state_dir).expanduser() / f"{a.candidate_id}.md"
    else:
        out = repo / "agent_artifacts" / "autobridge" / (a.run_id or "default") / "deepdives" / f"{a.candidate_id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    L = [f"# Kernel implementation deep dive - candidate `{a.candidate_id}`", ""]
    L += [f"- run-id: `{a.run_id or ''}`",
          f"- kernel: `{a.kernel_name or ''}`",
          f"- node (test input): `{a.node or ''}`",
          f"- target: `{a.target}`",
          f"- base sha: `{(a.base or '')[:12]}`   worktree: `{wt}`",
          f"- generated: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}", ""]

    L += ["## 1. Specialist input (answer-blind analysis)"]
    if specialists:
        for s in specialists:
            name = s.get("specialist", "?")
            fp = (s.get("findings_path") or "").strip()
            L += [f"### {name}", f"_source: `{fp}`_", ""]
            fpath = Path(fp).expanduser() if fp else None
            L += [(fpath.read_text().rstrip() if fpath and fpath.exists()
                   else "(findings file not found on disk)"), ""]
    else:
        L += ["(no specialist findings cited)", ""]

    L += ["## 2. Kernel output (src/ diff vs base)",
          f"Changed files: {changed or '(none detected)'}", "", "```diff",
          (diff.rstrip() if diff.strip() else "(no diff captured - pass --base and --worktree)"),
          "```", ""]

    L += ["## 3. Test input",
          f"- pytest node: `{a.node or ''}`"]
    if a.shape_tokens:
        L += [f"- shape tokens: `{a.shape_tokens}`"]
    L += [f"- measure command: `measure.py --repo {wt} --node '{a.node or ''}' --target {a.target}`", ""]

    L += ["## 4. Test output (correctness)",
          f"- passed: `{measure.get('passed')}`"]
    if measure.get("reason"):
        L += [f"- reason: {measure.get('reason')}"]
    if a.measure_log:
        L += [f"- measure log: `{a.measure_log}`"]
    L += [""]

    L += ["## 5. Resulting measure (device oracle - the sole accept/fail source)"]
    if measure:
        for k in ("cycles", "n_samples", "cycles_min", "cycles_max", "cycles_spread", "qcd_max"):
            if k in measure:
                L += [f"- {k}: `{measure[k]}`"]
        if "per_core" in measure:
            L += [f"- per_core: `{measure['per_core']}`"]
        L += ["", "```json", json.dumps(measure, indent=2), "```"]
    else:
        L += ["(no measure JSON parsed - pass --measure-json or --measure-log)"]
    L += [""]

    out.write_text("\n".join(L) + "\n")
    print(json.dumps({"ok": True, "deepdive": str(out), "candidate_id": a.candidate_id,
                      "changed_files": changed, "passed": measure.get("passed"),
                      "cycles": measure.get("cycles")}, indent=2))
    return 0


def cmd_finalize(a) -> int:
    repo = Path(a.repo).resolve()
    target = a.winner_sha or a.base
    r = _git(repo, "reset", "--hard", target)
    _git(repo, "worktree", "prune")
    att = repo / ATTEMPT_DIR
    if att.exists():
        shutil.rmtree(att, ignore_errors=True)
    _git(repo, "worktree", "prune")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    branches = [b.strip() for b in _git(repo, "branch", "--list", f"{BRANCH_PREFIX}*").stdout.splitlines()]
    print(json.dumps({
        "reset_to": target, "is_winner": bool(a.winner_sha),
        "head": head, "reset_ok": r.returncode == 0,
        "attempt_branches_kept": branches,
    }, indent=2))
    return 0 if r.returncode == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("worktrees"); c.add_argument("--repo", required=True)
    c.add_argument("--base", required=True); c.add_argument("--ids", nargs="+", required=True)
    c.set_defaults(func=cmd_worktrees)

    c = sub.add_parser("diff-guard"); c.add_argument("--worktree", required=True)
    c.add_argument("--base", required=True); c.set_defaults(func=cmd_diff_guard)

    c = sub.add_parser("remeasure"); c.add_argument("--repo", required=True)
    c.add_argument("--worktree", required=True); c.add_argument("--base", required=True)
    c.add_argument("--node", required=True); c.add_argument("--target", default="trn2")
    c.add_argument("--log", default=None); c.add_argument("--skip-guard", action="store_true")
    c.add_argument("--shadow", default=None,
                   help="forwarded to measure.py; pass '' to disable the wheel-shadow and use the "
                        "INSTALLED nkilib_testing (decoupled staging has no repo-local test/utils).")
    c.set_defaults(func=cmd_remeasure)

    c = sub.add_parser("verdict"); c.add_argument("--base-cycles", type=int, required=True)
    c.add_argument("--margin", type=float, default=0.02)
    c.add_argument("--candidates", required=True, help="JSON list of {id,cycles,passed}")
    c.set_defaults(func=cmd_verdict)

    c = sub.add_parser("finalize"); c.add_argument("--repo", required=True)
    c.add_argument("--base", required=True); c.add_argument("--winner-sha", default="")
    c.set_defaults(func=cmd_finalize)

    c = sub.add_parser("deepdive",
                       help="assemble a per-candidate implementation deep dive (specialist input, "
                            "kernel output, test input, test output, resulting measure)")
    c.add_argument("--candidate-id", required=True)
    c.add_argument("--repo", required=True)
    c.add_argument("--worktree", default="", help="the candidate's worktree (default: --repo)")
    c.add_argument("--base", default="", help="base sha; the src/ diff vs HEAD is the kernel output")
    c.add_argument("--node", default="", help="pytest node = the test input")
    c.add_argument("--target", default="trn2")
    c.add_argument("--kernel-name", default="")
    c.add_argument("--run-id", default="")
    c.add_argument("--shape-tokens", default="")
    c.add_argument("--specialist-findings", default="",
                   help="JSON list of {specialist, findings_path}: the answer-blind analysis input "
                        "(same list cited to the campaign NKI analysis); contents are embedded.")
    c.add_argument("--measure-json", default="", help="path to a measure.py JSON result")
    c.add_argument("--measure-log", default="", help="measure.py log; its last JSON line is parsed")
    c.add_argument("--state-dir", default="",
                   help="dir for the deep dive (default: <repo>/agent_artifacts/autobridge/<run-id>/deepdives)")
    c.add_argument("--out", default="", help="explicit output path (overrides --state-dir)")
    c.set_defaults(func=cmd_deepdive)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
