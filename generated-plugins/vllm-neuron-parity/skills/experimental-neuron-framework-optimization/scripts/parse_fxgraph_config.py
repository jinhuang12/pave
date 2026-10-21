#!/usr/bin/env python3
"""parse_fxgraph_config.py — recover kernel dispatch config from the compiler's OWN artifact.

GROUND TRUTH, no runtime flag / no interceptor / no worker injection / TP-agnostic: every NKI
kernel lowered through ``wrap_nki`` appears in the compiler's ``fxgraph.txt`` (in each per-graph
compile-cache dir) as an ``nki_kernel_wrapper`` node whose scalar dispatch params ride INLINE as
plain text — ``kwargs["args"]`` positionally aligned to ``kwargs["arg_names"]``. Example node:

    ... nki_kernel_wrapper](args = (), kwargs = {kernel_idx: 4, ..., args: (%q_kernel, ...,
        16, 2, 1, 1280, 256, 32, 1.0, 1, False, True, 1280),
        arg_names: [q_hbm, ..., bs, q_head, s_active, s_prior, d_head, block_len, softmax_scale,
        use_pos_id, fold_wout, partitioned_kv_load, batch_shard_sprior_max], constant_args_key: -1})

zip(arg_names, args), drop the ``%tensor`` operands, and the scalar literals ARE the effective
dispatch config (``bs=16, s_prior=1280, d_head=256, block_len=32, partitioned_kv_load=True, ...``).
This is what the compiler encoded to hand to neuronx-cc — so it is exactly what the model runs.

Why fxgraph.txt and not the NEFF/HLO: the HLO/NEFF ``backend_config`` is an opaque base64 blob
(scalars compiled away); the profiler DB exposes only ``func_name`` + ``mac_count``. Only
``fxgraph.txt`` carries the readable scalars. Caveat: it is written on the COLD compile path and
PERSISTS in the cache dir; a warm run reads it, but a NEFF-only cache entry lacks it (force one
cold trace to regenerate).

Emits ``dispatch_fingerprint.json`` (schema consumed by the campaign kernel-evidence guard):
    {schema_version, source:"fxgraph.txt", kernel_entry, sources:[...], configs:[{kwargs, count,
     arg_names, kernel_idx}]}

Pure text parsing — device-free and unit-tested.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_WRAPPER = "nki_kernel_wrapper"


def _bracket_body(line: str, key: str) -> str | None:
    """Return the text inside the bracket that ``key`` opens (balanced). ``key`` must END with
    the opening bracket, e.g. ``"arg_names: ["`` or ``", args: ("``."""
    open_ch = key[-1]
    close_ch = {"[": "]", "(": ")", "{": "}"}[open_ch]
    idx = line.find(key)
    if idx < 0:
        return None
    p = idx + len(key)
    depth, out = 1, []
    while p < len(line) and depth > 0:
        c = line[p]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
        if depth > 0:
            out.append(c)
        p += 1
    return "".join(out) if depth == 0 else None


def _split_top(s: str) -> list[str]:
    """Split on top-level commas (ignoring commas nested in (), [], {})."""
    parts, depth, cur = [], 0, []
    for c in s:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


_SKIP = object()  # sentinel: a tensor operand (not a scalar config value)


def _parse_val(tok: str):
    tok = tok.strip()
    if tok.startswith("%"):
        return _SKIP                      # tensor operand
    if tok in ("True", "False"):
        return tok == "True"
    if tok == "None":
        return None
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)                 # handles 1.0, 1e-06
    except ValueError:
        pass
    if len(tok) >= 2 and tok[0] in "'\"" and tok[-1] == tok[0]:
        return tok[1:-1]
    return tok                            # enums (ActFnType.GELU_...), other identifiers


def parse_node(line: str) -> dict | None:
    """Parse one ``nki_kernel_wrapper`` node line into {kernel_idx, arg_names, kwargs}."""
    if _WRAPPER not in line:
        return None
    names_body = _bracket_body(line, "arg_names: [")
    args_body = _bracket_body(line, ", args: (")
    if names_body is None or args_body is None:
        return None
    names = [n.strip() for n in _split_top(names_body)]
    vals = _split_top(args_body)
    kidx = None
    ki = line.find("kernel_idx: ")
    if ki >= 0:
        try:
            kidx = int(line[ki + len("kernel_idx: "):].split(",")[0].strip())
        except ValueError:
            pass
    kwargs = {}
    for name, tok in zip(names, vals):
        v = _parse_val(tok)
        if v is _SKIP:
            continue
        kwargs[name] = v
    return {"kernel_idx": kidx, "arg_names": names, "kwargs": kwargs}


def parse_fxgraph(path: str) -> list[dict]:
    nodes = []
    for line in Path(path).read_text(errors="ignore").splitlines():
        if _WRAPPER in line:
            n = parse_node(line)
            if n:
                nodes.append(n)
    return nodes


def find_fxgraphs(root: str) -> list[str]:
    return [str(p) for p in sorted(Path(root).rglob("fxgraph.txt"))]


def collect(fxgraphs: list[str], require_arg_names: list[str]) -> list[dict]:
    """Parse the given fxgraph.txt files, keep nodes whose arg_names include every required name
    (the target-kernel discriminator, e.g. 'partitioned_kv_load'), and dedup configs by kwargs."""
    merged: dict[str, dict] = {}
    req = set(require_arg_names or [])
    for fx in fxgraphs:
        for node in parse_fxgraph(fx):
            if req and not req.issubset(set(node["arg_names"])):
                continue
            key = json.dumps(node["kwargs"], sort_keys=True)
            if key in merged:
                merged[key]["count"] += 1
            else:
                merged[key] = {"kwargs": node["kwargs"], "count": 1,
                               "arg_names": node["arg_names"], "kernel_idx": node["kernel_idx"]}
    return list(merged.values())


def _coerce(s: str):
    s = s.strip()
    if s in ("True", "False"):
        return s == "True"
    if s == "None":
        return None
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def select(configs: list[dict], selectors: list[str]) -> list[dict]:
    """Filter configs to those matching every ``key=value`` selector — pins the fingerprint to a
    specific bucket (e.g. ``--select bs=16``) for target-specific optimization. `bs` is kept in
    the kwargs (it is the per-bucket dispatch batch), so this narrows WHICH bucket, without
    discarding it."""
    preds = []
    for sel in selectors:
        k, _, v = sel.partition("=")
        preds.append((k.strip(), _coerce(v)))
    return [c for c in configs if all(c["kwargs"].get(k) == v for k, v in preds)]


def branch_signatures(configs: list[dict], ignore_keys: list[str]) -> list[dict]:
    """Readable summary: distinct BRANCH signatures (kwargs minus the volatile keys, default
    ``bs``), each with the bucket values it spans. Does NOT replace ``configs`` — bs stays there
    for bucket-specific targeting; this is just the human/loop-facing 'which branches exist' view."""
    ignore = set(ignore_keys or [])
    groups: dict[str, dict] = {}
    for c in configs:
        kw = c["kwargs"]
        sig = {k: v for k, v in kw.items() if k not in ignore}
        key = json.dumps(sig, sort_keys=True)
        g = groups.setdefault(key, {"signature": sig, "spans": {}, "count": 0})
        g["count"] += c.get("count", 1)
        for ik in ignore:
            if ik in kw and kw[ik] not in g["spans"].setdefault(ik, []):
                g["spans"][ik].append(kw[ik])
    for g in groups.values():
        for ik in g["spans"]:
            try:
                g["spans"][ik] = sorted(g["spans"][ik])
            except TypeError:
                pass
    return list(groups.values())


def build_fingerprint(configs: list[dict], kernel_entry: str, sources: list[str],
                      branch_sigs: list[dict] | None = None) -> dict:
    fp = {
        "schema_version": "1.0",
        "source": "fxgraph.txt",
        "kernel_entry": kernel_entry,
        "sources": sources,
        "configs": configs,
    }
    if branch_sigs is not None:
        fp["branch_signatures"] = branch_sigs
    return fp


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--fxgraph", help="a single fxgraph.txt to parse")
    src.add_argument("--cache-root", help="a compile-cache root (or VLLM_CACHE_ROOT); scans **/fxgraph.txt")
    p.add_argument("--require-arg-name", action="append", default=[],
                   help="keep only kernels whose arg_names include this (repeatable) — the target "
                        "discriminator, e.g. --require-arg-name partitioned_kv_load for gemma4 attn")
    p.add_argument("--select", action="append", default=[], metavar="KEY=VAL",
                   help="pin the emitted configs to a bucket/variant (repeatable), e.g. "
                        "--select bs=16 for target-specific optimization at the 16-seq bucket. "
                        "bs is preserved in kwargs; this only narrows which configs are emitted.")
    p.add_argument("--branch-ignore", action="append", default=None, metavar="KEY",
                   help="keys treated as volatile when summarizing branch_signatures "
                        "(repeatable; default: bs).")
    p.add_argument("--kernel-entry", default="", help="kernel_target.kernel_entry (recorded)")
    p.add_argument("--out", required=True, help="path to write dispatch_fingerprint.json")
    args = p.parse_args(argv)

    if args.fxgraph:
        fxgraphs = [os.path.expanduser(args.fxgraph)]
    else:
        root = os.path.expanduser(args.cache_root)
        fxgraphs = find_fxgraphs(root)
        if not fxgraphs:
            print(f"parse_fxgraph_config: no fxgraph.txt under {root}. A NEFF-only cache lacks it "
                  "(cold-path artifact) — force one cold trace to regenerate.", file=sys.stderr)
            return 1

    all_configs = collect(fxgraphs, args.require_arg_name)
    sigs = branch_signatures(all_configs, args.branch_ignore if args.branch_ignore is not None else ["bs"])
    configs = select(all_configs, args.select) if args.select else all_configs
    fp = build_fingerprint(configs, args.kernel_entry, fxgraphs, branch_sigs=sigs)
    Path(os.path.expanduser(args.out)).write_text(json.dumps(fp, indent=2) + "\n")

    sel = f" (selected {args.select})" if args.select else ""
    print(f"parse_fxgraph_config: {len(fxgraphs)} fxgraph.txt -> {len(all_configs)} config(s), "
          f"{len(sigs)} branch signature(s); emitting {len(configs)}{sel} -> {args.out}", flush=True)
    print("  branch_signatures (bs-independent):", flush=True)
    for g in sigs:
        s = g["signature"]
        hot = {k: s[k] for k in ("partitioned_kv_load", "s_prior", "d_head", "block_len") if k in s}
        print(f"    {hot}  spans bs={g['spans'].get('bs')}  count={g['count']}", flush=True)
    print("  emitted configs:", flush=True)
    for c in configs:
        kw = c["kwargs"]
        hot = {k: kw[k] for k in ("partitioned_kv_load", "s_prior", "d_head", "block_len", "bs")
               if k in kw}
        print(f"    kernel_idx={c['kernel_idx']} count={c['count']} {hot}", flush=True)
    return 0 if configs else 1


if __name__ == "__main__":
    sys.exit(main())
