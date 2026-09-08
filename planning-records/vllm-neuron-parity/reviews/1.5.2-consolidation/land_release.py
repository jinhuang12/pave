#!/usr/bin/env python3
"""Land a release patch from a clean index while the working tree carries unrelated uncommitted edits.
Usage: land_152.py <repo> <patch> [--commit <msgfile>] [--add <path> ...]
(1) index==HEAD; (2) git apply --cached; (3) per file: git apply to the working tree, else transplant the
HEAD->index change into the working-tree copy by unique block substitution (escaped one-line TOML values are
split on \\n first); (4) proof: index->worktree diff == HEAD->worktree diff before landing; (5) tomllib parse;
(6) optional commit of the index only."""
import difflib, hashlib, re, subprocess, sys, tomllib
def sh(repo, *args, check=True):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if check and r.returncode: sys.exit(f"FAIL git {' '.join(args)}\n{r.stdout}{r.stderr}")
    return r
def blob(repo, rev, p): return sh(repo, "show", f"{rev}:{p}").stdout
def md_lines(s): return s.split("\\n")
def pairs(a, b):
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return [(tuple(a[i1:i2]), tuple(b[j1:j2])) for t, i1, i2, j1, j2 in sm.get_opcodes() if t != "equal"]
def toks(s): return re.split(r"(\s+)", s)
def intra(a, b):  # token-level change blocks inside one line; pure insertions borrow one token of left (or right) context
    out = []
    A, B = toks(a), toks(b)
    for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, A, B, autojunk=False).get_opcodes():
        if t == "equal": continue
        if i1 == i2:
            if i1 > 0: i1 -= 1; j1 -= 1
            else: i2 += 1; j2 += 1
        out.append((tuple(A[i1:i2]), tuple(B[j1:j2])))
    return out
def find_once(seq, block):
    hits = [k for k in range(len(seq) - len(block) + 1) if tuple(seq[k:k + len(block)]) == block] if block else []
    return hits
def transplant(head_txt, idx_txt, wt_txt, label):
    """Re-apply the HEAD->index change to the working-tree copy. Each changed block is located by exact match,
    widening with 0..5 lines of surrounding context until it matches exactly once (pure insertions need >=1)."""
    H, I, out = head_txt.split("\n"), idx_txt.split("\n"), wt_txt.split("\n")
    ops = [(i1, i2, j1, j2) for t, i1, i2, j1, j2 in difflib.SequenceMatcher(None, H, I, autojunk=False).get_opcodes() if t != "equal"]
    for i1, i2, j1, j2 in ops:
        placed = False
        for n in range(0, 6):
            a, b = max(0, i1 - n), min(len(H), i2 + n)
            old = tuple(H[a:i1]) + tuple(H[i1:i2]) + tuple(H[i2:b])
            if not old: continue
            new = list(H[a:i1]) + list(I[j1:j2]) + list(H[i2:b])
            hits = find_once(out, old)
            if len(hits) == 1:
                k = hits[0]; out = out[:k] + new + out[k + len(old):]; placed = True
                if n: print(f"  context-{n} merge: {label} block at wt line {k + 1 + (i1 - a)}")
                break
        if placed: continue
        old, new = H[i1:i2], I[j1:j2]
        if len(old) == 1 and len(new) == 1 and "\\n" in old[0]:  # escaped one-line value (codex TOML)
            keyname = old[0].split("=", 1)[0].strip()
            cand = [k for k, ln in enumerate(out) if ln.split("=", 1)[0].strip() == keyname]
            if len(cand) != 1: sys.exit(f"FAIL {label}: key {keyname} not unique in working tree ({len(cand)})")
            k = cand[0]
            out = out[:k] + [transplant_value(old[0], new[0], out[k], label)] + out[k + 1:]; continue
        if len(old) == len(new) >= 1:  # the same lines also carry unrelated working-tree edits: per-line token-level substitution
            for o_ln, n_ln in zip(old, new):
                if o_ln == n_ln: continue
                ranked = sorted(((difflib.SequenceMatcher(None, ln, o_ln, autojunk=False).ratio(), k) for k, ln in enumerate(out) if ln), reverse=True)
                if not ranked or ranked[0][0] < 0.8 or (len(ranked) > 1 and ranked[1][0] >= ranked[0][0]): sys.exit(f"FAIL {label}: no unique near-match line for {o_ln[:80]!r}")
                k = ranked[0][1]; w = toks(out[k])
                for ob, nb in intra(o_ln, n_ln):
                    h2 = find_once(w, ob)
                    if len(h2) != 1: sys.exit(f"FAIL {label}: token block {''.join(ob)[:60]!r} found {len(h2)} times in line {k + 1} (need 1)")
                    q = h2[0]; w = w[:q] + list(nb) + w[q + len(ob):]
                out = out[:k] + ["".join(w)] + out[k + 1:]; print(f"  token-level merge: {label} line {k + 1}")
            continue
        sys.exit(f"FAIL {label}: block of {len(old)} lines not found uniquely in working tree: {old[:1]}")
    return "\n".join(out)
def transplant_value(old_line, new_line, wt_line, label):
    """Same merge for one escaped TOML value: split on the escaped newline, merge, re-join."""
    merged = transplant("\n".join(md_lines(old_line)), "\n".join(md_lines(new_line)), "\n".join(md_lines(wt_line)), label + " (value)")
    return "\\n".join(merged.split("\n"))
def flat(prs):  # normalize a change list so unrelated edits on the same line compare equal: escaped TOML values split, equal-length blocks compared token-wise per line
    o = []
    for a, b in prs:
        if len(a) == 1 and len(b) == 1 and "\\n" in a[0]: o += flat(pairs(md_lines(a[0]), md_lines(b[0])))
        elif len(a) == len(b): o += [("L", tuple(intra(x, y))) for x, y in zip(a, b) if x != y]
        else: o.append((a, b))
    return o
def main():
    repo, patch = sys.argv[1], sys.argv[2]
    msg = sys.argv[sys.argv.index("--commit") + 1] if "--commit" in sys.argv else None
    adds = [sys.argv[i + 1] for i, a in enumerate(sys.argv) if a == "--add"]
    print("patch sha256", hashlib.sha256(open(patch, "rb").read()).hexdigest()); print("HEAD", sh(repo, "rev-parse", "--short", "HEAD").stdout.strip())
    if sh(repo, "diff", "--cached", "--quiet", check=False).returncode: sys.exit("FAIL: index differs from HEAD")
    user_files = sorted(sh(repo, "diff", "--name-only").stdout.split())
    user_pairs = {p: pairs(blob(repo, "HEAD", p).split("\n"), open(f"{repo}/{p}").read().split("\n")) for p in user_files}
    print("user-edited files:", len(user_files))
    files = [l.rstrip("\n").split(" b/", 1)[1] for l in open(patch) if l.startswith("diff --git ")]
    print("patch files:", len(files))
    sh(repo, "apply", "--check", "--cached", patch); sh(repo, "apply", "--cached", patch); print("index: patch applied")
    direct, transplanted = 0, 0
    for f in files:
        if sh(repo, "apply", "--check", f"--include={f}", patch, check=False).returncode == 0:
            sh(repo, "apply", f"--include={f}", patch); direct += 1
        else:
            new_wt = transplant(blob(repo, "HEAD", f), blob(repo, "", f), open(f"{repo}/{f}").read(), f)
            if f.endswith(".toml"): tomllib.loads(new_wt)
            open(f"{repo}/{f}", "w").write(new_wt); transplanted += 1; print("  transplanted:", f)
    print(f"working tree: {direct} applied directly, {transplanted} transplanted")
    after = sorted(sh(repo, "diff", "--name-only").stdout.split())
    if after != user_files: sys.exit(f"FAIL: index->worktree file set changed\n before {user_files}\n after  {after}")
    for p in user_files:
        now = pairs(blob(repo, "", p).split("\n"), open(f"{repo}/{p}").read().split("\n"))
        if flat(now) != flat(user_pairs[p]): sys.exit(f"FAIL: {p}: residual diff is not the user's edits\n user {flat(user_pairs[p])}\n now  {flat(now)}")
    print("proof: index->worktree diff == user's pre-existing edits, for all", len(user_files), "files")
    for a in adds: sh(repo, "add", "--", a); print("staged", a)
    if msg:
        sh(repo, "commit", "-q", "-F", msg); print("committed", sh(repo, "rev-parse", "--short", "HEAD").stdout.strip()); print(sh(repo, "show", "--stat", "--format=%h %s", "HEAD").stdout)
    else: print("no --commit: index holds the patch; working tree merged; nothing committed")
main()
