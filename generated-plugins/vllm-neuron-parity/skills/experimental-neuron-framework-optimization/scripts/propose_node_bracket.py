#!/usr/bin/env python3
"""propose_node_bracket.py -- turn a profiled kernel's shape into a VALID pytest NODE id.

The model-level profile-analysis leaf detects a kernel (e.g. mlp_tkg) and the shape it
ran at (hidden, intermediate, dtype, vnc/TP, decode vs prefill, ...). To drive the kernel
optimize loop we need a RUNNABLE pytest node id for that kernel's unit test at that shape.

Rendering a node id from scratch is fragile: each test owns its own param order,
abbreviation map, and enum->int encoding, and `tpbSgCyclesSum` is a GOLDEN REFERENCE baked
into each parametrize vector that you cannot invent. So this helper does NOT render -- it
lets pytest render every real id via `--collect-only`, then SELECTS the one matching the
required tokens. That makes the output provably runnable (it came from collection) or an
honest "no existing vector -> add one".

Usage
-----
  # list every collected id for a test (ground truth):
  propose_node_bracket.py --repo <R> --testfile test/.../test_mlp_tkg.py \
      --node-prefix 'TestMlpTkgKernel::test_mlp_tkg_unit' --target trn2 --list

  # select the vector matching a profiled shape (all --require substrings must be present):
  propose_node_bracket.py --repo <R> --testfile test/.../test_mlp_tkg.py \
      --node-prefix 'TestMlpTkgKernel::test_mlp_tkg_unit' --target trn2 \
      --require vnc-1 --require b-1 --require s-1 --require h-8192 --require i-448 \
      --require dtype-bfloat16

Emits one JSON line:
  {"status": "unique"|"none"|"ambiguous", "node": <full id or null>,
   "matches": [...], "n_collected": int, "require": [...]}
Exit code: 0 iff status == "unique".
"""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys
from pathlib import Path


def build_shadow(repo: Path, shadow: Path) -> None:
    """Mirror measure.py: symlink nkilib_testing -> test/utils so local wins the wheel."""
    shadow.mkdir(parents=True, exist_ok=True)
    link = shadow / "nkilib_testing"
    src = repo / "test" / "utils"
    if link.is_symlink() or link.exists():
        try:
            link.unlink()
        except IsADirectoryError:
            shutil.rmtree(link)
    if src.exists():
        link.symlink_to(src, target_is_directory=True)


def collect(repo: Path, testfile: str, node_prefix: str, target: str,
            shadow: Path | None, timeout_s: int) -> tuple[list[str], str]:
    env = dict(os.environ)
    pp = []
    if shadow is not None:
        build_shadow(repo, shadow)
        pp.append(str(shadow))
    pp.append(str(repo / "src"))
    if env.get("PYTHONPATH"):
        pp.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = ":".join(pp)

    selector = testfile if not node_prefix else f"{testfile}::{node_prefix}"
    cmd = [sys.executable, "-m", "pytest", "-c", "test/pytest.ini", selector,
           "--platform-target", target, "--test-mode", "compile-and-infer",
           "--collect-only", "-q", "-p", "no:cacheprovider"]
    try:
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True,
                              text=True, timeout=timeout_s)
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return [], "collect timed out"
    # collected node ids are the lines containing the test file and a '['..']' bracket
    base = os.path.basename(testfile)
    ids = []
    for ln in out.splitlines():
        ln = ln.strip()
        if base in ln and "[" in ln and ln.endswith("]") and "::" in ln:
            # pytest prints ids relative to its rootdir (where pytest.ini lives, e.g. test/), but
            # measure.py runs pytest with cwd=repo and passes the node verbatim -> normalize the
            # file-path prefix to the caller's repo-relative --testfile so the node is runnable.
            ids.append(testfile + ln[ln.index("::"):])
    return ids, out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", required=True)
    p.add_argument("--testfile", required=True,
                   help="path to the kernel's test file, relative to --repo")
    p.add_argument("--node-prefix", default="",
                   help="Class::method to scope collection (recommended)")
    p.add_argument("--target", default="trn2")
    p.add_argument("--require", action="append", default=[],
                   help="token substring that MUST appear in the bracket (repeatable). "
                        "e.g. h-8192 i-448 vnc-1 dtype-bfloat16")
    p.add_argument("--list", action="store_true", help="just print all collected ids")
    p.add_argument("--shadow", default="/tmp/nkilib_shadow")
    p.add_argument("--timeout-s", type=int, default=600)
    args = p.parse_args(argv)

    repo = Path(args.repo).resolve()
    shadow = Path(args.shadow) if args.shadow else None
    ids, raw = collect(repo, args.testfile, args.node_prefix, args.target,
                       shadow, args.timeout_s)

    if args.list:
        print(json.dumps({"n_collected": len(ids), "ids": ids}, indent=2))
        return 0 if ids else 1

    matches = [i for i in ids if all(tok in i for tok in args.require)]

    def _measure_form(nid: str) -> str:
        # pytest prints ids relative to rootdir (e.g. 'integration/...'); measure.py /
        # the loop expect the repo-relative 'test/integration/...' form. Normalize so the
        # emitted node is runnable as-is.
        if nid and not nid.startswith("test/") and nid.startswith("integration/"):
            return "test/" + nid
        return nid

    if len(matches) == 1:
        status, node = "unique", _measure_form(matches[0])
    elif not matches:
        status, node = "none", None
    else:
        status, node = "ambiguous", None

    print(json.dumps({
        "status": status,
        "node": node,
        "matches": matches,
        "n_collected": len(ids),
        "require": args.require,
        "hint": ("" if status == "unique" else
                 ("no collected vector matches all --require tokens: the model shape has NO "
                  "existing test vector -> add a parametrize row (you cannot invent "
                  "tpbSgCyclesSum) " if status == "none" else
                  "multiple vectors match; add more --require tokens to disambiguate")),
    }))
    return 0 if status == "unique" else 1


if __name__ == "__main__":
    sys.exit(main())
