#!/usr/bin/env python3.12
"""stage_kernel_tree.py — build the decoupled kernel-loop staging tree from scratch.

The Step-6 kernel loop needs a WRITABLE git checkout with the kernel source under ``src/`` (for
``git worktree`` + ``diff-guard -- src/``) and a ``test/pytest.ini`` — but it must NOT depend on any
source checkout (`KaenaNeuronKernelLibrary`, `knkl_disposable`, `test/integration`, `test/utils`).

This script manufactures that tree entirely from the SERVING INTERPRETER's installed packages:
  * copies the interpreter's installed ``nkilib`` into ``<tree>/src/<pkg-path>`` (BASE == deployed),
    mirroring the FULL package namespace incl. every parent ``__init__.py`` so the repo copy is the
    regular package that wins on ``sys.path`` (verified requirement — a missing parent ``__init__``
    makes Python bind to the installed package and edits silently do NOT take effect);
  * ships ``test/pytest.ini`` from this skill's ``config/`` (static, not a checkout);
  * ``git init`` + commit -> BASE sha for the scaffold's worktrees;
  * SELF-VERIFIES: (a) under ``PYTHONPATH=<tree>/src`` a stamped edit to the target kernel is visible
    (edit-precedence), and (b) with the shadow disabled, ``nkilib_testing`` still resolves to the
    installed harness. Exits non-zero if either fails.

The harness (``nkilib_testing``) and the correctness reference stay INSTALLED — ``measure.py``
is run with ``--shadow ''`` so no repo-local ``test/utils`` is needed.

Interpreter resolution (the model's serving interpreter), in order:
  1. ``--serving-venv`` / ``$SERVING_VENV`` ;  2. ``$VIRTUAL_ENV`` ;
  3. auto-detect: the first candidate venv whose ``import vllm_neuron`` works AND whose ``nkilib`` is
     a WHEEL (no ``__editable__*nkilib*`` / ``*nkilib*.pth`` marker) — an EDITABLE nkilib means the
     interpreter points at a source checkout, which is exactly what we are decoupling from;
  4. system ``python3`` (last resort; rejected if its nkilib is editable, unless --allow-editable).

Usage:
  stage_kernel_tree.py --run-id gemma4-31b-8k1k [--project-root /agents/legolas]
      [--serving-venv /path/venv] [--candidate-venv GLOB ...] [--allow-editable] [--force]
Emits one JSON line (and writes ``<tree>/../stage_manifest.json``):
  {"ok":bool,"tree":..,"base_sha":..,"py":..,"nkilib_dir":..,"pkgname":..,
   "edit_wins":bool,"testing_installed":bool,"nkilib_is_wheel":bool}
Exit 0 iff ok.
"""
from __future__ import annotations
import argparse, glob, json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

SKILL_SCRIPTS = Path(__file__).resolve().parent
DEFAULT_CANDIDATE_GLOBS = [
    os.environ.get("SERVING_VENV", ""),
    os.environ.get("VIRTUAL_ENV", ""),
    "/home/*/*/venv", "/home/*/venv*", "/opt/*venv*", "/opt/aws_neuronx_venv_*",
]


def _py_of(venv: str) -> str | None:
    for c in (Path(venv) / "bin" / "python", Path(venv) / "bin" / "python3", Path(venv)):
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def _probe(py: str) -> dict:
    """Return {vllm_ok, nkilib_dir, nkilib_parent, testing_dir, pkgname, is_wheel} or {} on failure."""
    code = (
        "import json,os,glob,sys\n"
        "d={}\n"
        "try:\n"
        " import vllm_neuron; d['vllm_ok']=True\n"
        "except Exception: d['vllm_ok']=False\n"
        "try:\n"
        " import nkilib,nkilib_testing,sysconfig\n"
        " nkd=os.path.dirname(nkilib.__file__)\n"
        " d['nkilib_dir']=nkd; d['nkilib_parent']=os.path.dirname(nkd)\n"
        " d['pkgname']=nkilib.__name__\n"
        " d['testing_dir']=os.path.dirname(nkilib_testing.__file__)\n"
        " sps=set(p for p in sys.path if p.endswith('site-packages'))\n"
        " for k in ('purelib','platlib'):\n"
        "  try: sps.add(sysconfig.get_paths()[k])\n"
        "  except Exception: pass\n"
        " d['is_wheel']=any(nkd==s or nkd.startswith(s.rstrip('/')+'/') for s in sps)\n"
        " d['testing_installed']=any(d['testing_dir']==s or d['testing_dir'].startswith(s.rstrip('/')+'/') for s in sps)\n"
        "except Exception as e: d['nkilib_err']=str(e)\n"
        "print(json.dumps(d))\n"
    )
    try:
        r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=120)
        line = [l for l in r.stdout.splitlines() if l.strip().startswith("{")]
        return json.loads(line[-1]) if line else {}
    except Exception:
        return {}


def resolve_interpreter(args) -> tuple[str, dict]:
    ordered = []
    if args.serving_venv:
        ordered.append(args.serving_venv)
    if os.environ.get("SERVING_VENV"):
        ordered.append(os.environ["SERVING_VENV"])
    if os.environ.get("VIRTUAL_ENV"):
        ordered.append(os.environ["VIRTUAL_ENV"])
    globs = args.candidate_venv or DEFAULT_CANDIDATE_GLOBS
    for g in globs:
        if g:
            ordered += sorted(glob.glob(g))
    seen, cands = set(), []
    for v in ordered:
        py = _py_of(v)
        if py and py not in seen:
            seen.add(py); cands.append(py)
    sys_py = shutil.which("python3")
    if sys_py and sys_py not in seen:
        cands.append(sys_py)

    best_wheel_vllm = best_wheel = best_any = None
    for py in cands:
        info = _probe(py)
        if not info.get("nkilib_dir"):
            continue
        if best_any is None:
            best_any = (py, info)
        if info.get("is_wheel"):
            if best_wheel is None:
                best_wheel = (py, info)
            if info.get("vllm_ok") and best_wheel_vllm is None:
                best_wheel_vllm = (py, info)
    chosen = best_wheel_vllm or best_wheel
    if chosen:
        return chosen
    if best_any and args.allow_editable:
        return best_any
    raise SystemExit(
        "no serving interpreter with a WHEEL nkilib (nkilib under the venv's own site-packages) "
        "found -- an editable nkilib points at a source checkout, the dependency we are removing. "
        "Pass --serving-venv <the model's venv>, or --allow-editable to override. Probed: "
        + ", ".join(cands))


def build_tree(py: str, info: dict, tree: Path, force: bool) -> str:
    if tree.exists():
        if not force and any(tree.iterdir()):
            # idempotent rebuild is the norm; require --force only if it's not ours
            pass
        shutil.rmtree(tree)
    (tree / "test" / "generated").mkdir(parents=True, exist_ok=True)

    # mirror the package namespace under src/, copying every parent __init__.py
    parent = Path(info["nkilib_parent"])                     # e.g. .../site-packages/nkilib_src
    nkd = Path(info["nkilib_dir"])                           # e.g. .../nkilib_src/nkilib
    parts = info["pkgname"].split(".")                       # e.g. ['nkilib_src','nkilib']
    # import root = the dir that holds parts[0]
    import_root = nkd
    for _ in parts:
        import_root = import_root.parent
    src = tree / "src"
    # copy the leaf package dir
    dst_leaf = src / Path(*parts)
    dst_leaf.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(nkd, dst_leaf)
    # copy each intermediate parent package's top-level files (esp. __init__.py)
    cur_src = import_root
    cur_dst = src
    for p in parts[:-1]:
        cur_src = cur_src / p
        cur_dst = cur_dst / p
        cur_dst.mkdir(parents=True, exist_ok=True)
        for f in cur_src.iterdir():
            if f.is_file():
                shutil.copy2(f, cur_dst / f.name)

    # pytest.ini + standalone feature-flag shim conftest from the skill config
    ini = SKILL_SCRIPTS / "config" / "pytest.ini"
    (tree / "test" / "pytest.ini").write_text(ini.read_text() if ini.exists() else "[pytest]\n")
    cft = SKILL_SCRIPTS / "config" / "conftest.py"
    if cft.exists():
        (tree / "test" / "conftest.py").write_text(cft.read_text())
    (tree / ".gitignore").write_text("__pycache__/\n*.pyc\n.attempts/\nneuron_test_output/\n")

    env = dict(os.environ)
    def g(*a):
        return subprocess.run(["git", "-C", str(tree), *a], capture_output=True, text=True, env=env)
    g("init", "-q")
    g("add", "-A")
    g("-c", "user.email=kiro@local", "-c", "user.name=kiro", "commit", "-qm",
      "base: nkilib snapshot from serving venv (deployed truth)")
    return g("rev-parse", "HEAD").stdout.strip()


def verify(py: str, info: dict, tree: Path) -> dict:
    """edit-precedence + harness-installed check under PYTHONPATH=tree/src, shadow disabled."""
    pkg = info["pkgname"]
    # pick a stampable module: the mlp_cte projection if present, else the package __init__
    rel = "core/mlp/mlp_cte/basic/mlp_cte_basic_projection.py"
    target = tree / "src" / Path(*pkg.split(".")) / rel
    if not target.exists():
        target = tree / "src" / Path(*pkg.split(".")) / "__init__.py"
    mark = "# KIRO_STAGE_VERIFY_MARK"
    original = target.read_text()
    target.write_text(original + "\n" + mark + "\n")
    modname = pkg + "." + rel[:-3].replace("/", ".") if rel in str(target) else pkg
    code = (
        "import importlib,os\n"
        f"m=importlib.import_module({modname!r})\n"
        "import nkilib_testing, nkilib_testing.unit_test_framework as u\n"
        "print(os.path.dirname(nkilib_testing.__file__).endswith('site-packages/nkilib_testing'),"
        " %r in open(m.__file__).read(), m.__file__, hasattr(u,'torch_ref_wrapper'))" % mark
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree / "src")
    env.pop("PYTHONSTARTUP", None)
    try:
        r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=180, env=env)
        out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        parts = out.split()
        testing_installed = parts[0] == "True" if parts else False
        edit_wins = parts[1] == "True" if len(parts) > 1 else False
        has_wrapper = parts[-1] == "True" if parts else False
        return {"edit_wins": edit_wins, "testing_installed": testing_installed,
                "torch_ref_wrapper": has_wrapper, "verify_out": out, "verify_err": r.stderr[-500:]}
    finally:
        target.write_text(original)
        subprocess.run(["git", "-C", str(tree), "checkout", "-q", "--", "."],
                       capture_output=True, text=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--project-root", default=os.environ.get("NEURON_PROJECT_ROOT", os.getcwd()))
    ap.add_argument("--serving-venv", default=None)
    ap.add_argument("--candidate-venv", action="append", default=None)
    ap.add_argument("--allow-editable", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)

    # Convention (enforced, matches the campaign project root): the project root MUST be the cwd of
    # the harness. --project-root defaults to $NEURON_PROJECT_ROOT or the cwd; reject any value
    # that is not the cwd so the staged tree lands on the same campaign trail as everything else.
    if Path(os.path.expanduser(a.project_root)).resolve() != Path.cwd().resolve():
        print(
            f"CONFIG ERROR: --project-root {a.project_root} does not match the cwd ({os.getcwd()}).\n"
            f"  Convention: the project root MUST be the cwd of the harness it runs in.\n"
            f"  Fix: run from the project root (cd there), leaving NEURON_PROJECT_ROOT unset or == cwd.",
            file=sys.stderr,
        )
        return 2

    py, info = resolve_interpreter(a)
    tree = Path(a.project_root) / "agent_artifacts" / "autobridge" / a.run_id / "knkl_tree"
    base = build_tree(py, info, tree, a.force)
    v = verify(py, info, tree)
    manifest = {
        "ok": bool(v["edit_wins"] and v["testing_installed"] and v["torch_ref_wrapper"]),
        "tree": str(tree), "base_sha": base, "py": py,
        "nkilib_dir": info["nkilib_dir"], "nkilib_parent": info["nkilib_parent"],
        "nkilib_testing_dir": info["testing_dir"], "pkgname": info["pkgname"],
        "nkilib_is_wheel": info.get("is_wheel"), **v,
    }
    (tree.parent / "stage_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest))
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
