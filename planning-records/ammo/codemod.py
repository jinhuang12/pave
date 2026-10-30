#!/usr/bin/env python3
"""Seed generated-plugins/ammo from ammo-server and apply one closed path codemod.

Wave-0 tool for the ammo plugin hand-port (plan.md "worker-1 acceptance").

    python3 codemod.py seed <dir>                 copy every conventions §10 source file to its §1 place
    python3 codemod.py rewrite <dir>              apply the rule tuples once, in order, to the seeded files
    python3 codemod.py inventory <dir> [--content]
                                                  print MISSING / EXTRA / FORBIDDEN / DRIFT lines;
                                                  empty output and exit 0 mean the tree matches the seed

The recorded run (baseline.md) was:

    python3 codemod.py seed    /tmp/ammo-seed/pre
    cp -a /tmp/ammo-seed/pre /tmp/ammo-seed/post
    python3 codemod.py rewrite /tmp/ammo-seed/post
    (cd /tmp/ammo-seed && diff -ru pre post) > codemod.diff
    rsync -a /tmp/ammo-seed/post/ <R>/
    python3 codemod.py inventory <R> --content

Scope. The rules are path tokens only, in the shape of CODEX_RULES in
ammo-server scripts/render_ammo_variants.py: tuples of (old, new) string pairs,
applied with str.replace in order. Three tuples, applied in this order:

    FIXUPS        per-file exact lines: server-side tests find R/scripts and
                  R/hooks; a few tests whose subject moved under §1 import again
    RULES         global: .claude/skills/ammo/..., .codex/skills/ammo/...,
                  .claude/agents/, .claude/schemas, $CLAUDE_PROJECT_DIR/.claude/hooks/
                  to the flat plugin layout (conventions §1, §7)
    SCOPED_RULES  by path glob: hook harnesses moved to hooks/tests/; the
                  kernel_opt_artifacts root in prose examples to .ammo/ (§3)

They do not touch env-var semantics, pins, prose beyond path tokens, match
patterns for kernel_opt_artifacts, or layout idioms in scripts such as
parents[3] or walking up to .claude/schemas (conventions §2 gives those to
plugin_path() in wave 1).

The ammo-server tree is read only. This script never writes under it.
"""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
from pathlib import Path

S = Path("/Users/jinhun/GitHub/ammo-server")
SRC = S / "ai_cli_session"
CLAUDE = SRC / ".claude"
CODEX = SRC / ".codex"
VNP = Path("/Users/jinhun/GitHub/pave/generated-plugins/vllm-neuron-parity")

# ---------------------------------------------------------------------------
# 1. Copy map: (source dir, glob, destination dir under R). Conventions §10 -> §1.
#    A glob ending in "**" copies the tree below the source dir, keeping the
#    relative layout.
# ---------------------------------------------------------------------------
COPY_MAP: tuple[tuple[Path, str, str], ...] = (
    # Claude dialect (§10 "copy source")
    (CLAUDE / "skills/ammo/scripts", "**", "scripts"),
    (CLAUDE / "skills/ammo/references", "*", "references"),
    (CLAUDE / "skills/ammo/orchestration", "*", "orchestration"),
    (CLAUDE / "skills/ammo/data", "*", "data"),
    (CLAUDE / "skills/ammo/tests", "**", "tests"),
    (CLAUDE / "skills/ammo", "SKILL.md", "skills/ammo"),
    (CLAUDE / "skills/ammo/report", "SKILL.md", "skills/ammo-report"),
    (CLAUDE / "agents", "*.md", "agents"),
    (CLAUDE / "hooks", "test-*.sh", "hooks/tests"),
    (CLAUDE / "hooks", "*", "hooks"),            # test-*.sh excluded below
    (CLAUDE / "schemas", "*", "schemas"),
    # Codex-only (§10)
    (CODEX / "agents", "*.toml", "codex/agents"),
    (CODEX / "hooks", "*.py", "codex/hooks"),
    (CODEX, "hooks.json", "codex"),
    (CODEX / "skills/ammo", "RUNTIME.md", "codex"),
    (CODEX / "skills/ammo/instrumentation", "*", "codex/instrumentation"),
    (CODEX / "skills/ammo/scripts", "codex_agent_registry.py", "codex/scripts"),
    (CODEX / "skills/ammo/scripts", "monitor_queue_ack.py", "codex/scripts"),
    (CODEX / "skills/ammo/scripts", "resolve_codex_transcript.py", "codex/scripts"),
    (CODEX / "skills/ammo/scripts", "create_worktree_with_build.sh", "codex/scripts"),
    (CODEX / "skills/ammo/scripts", "remove_worktree_cleanup.sh", "codex/scripts"),
    (CODEX / "skills/ammo/tests", "test_agent_runtime_contracts.py", "tests"),
    (CODEX / "skills/ammo/tests", "test_codex_v1_runtime.py", "tests"),
    (CODEX / "skills/ammo/tests", "test_context_recovery.py", "tests"),
    (CODEX / "skills/ammo/tests", "test_drift_compat.py", "tests"),
    (CODEX / "skills/ammo/tests", "test_hook_semantics.py", "tests"),
    (CODEX / "skills/ammo/tests", "test_resolve_codex_transcript.py", "tests"),
    # Server-side (§10)
    (S / "shared", "pumice_pins.py", "scripts"),
    (S / "tests/unit", "test_ammo_serving.py", "tests"),
    (S / "tests/unit", "test_ammo_serving_campaign.py", "tests"),
    (S / "tests/unit", "test_ammo_serving_runtime.py", "tests"),
    (S / "tests/unit", "test_pumice_pins.py", "tests"),
    (S / "tests/unit", "test_codex_hook_campaign_resolution.py", "tests"),
    (S / "tests/unit", "test_env_default_guard_hook.py", "tests"),
    (S / "tests/unit", "test_worktree_hook.py", "tests"),
    # Copied unchanged from vllm-neuron-parity for worker-5 to adapt (plan.md)
    (VNP / "codex", "install_agents.py", "codex"),
    (VNP / "tests", "test_codex_port.py", "tests"),
)

# Never copied (requirements 13, plan.md worker-1 acceptance, and the conventions
# §9 ruling: a test that imports from an unshipped dir is not shipped). Matched against
# the path relative to the source dir and against every path part.
EXCLUDE_PARTS = frozenset({"__pycache__", ".pytest_cache", ".DS_Store"})
EXCLUDE_REL = (
    "agents/*",                    # tests/agents/: unrun role-play prompts
    "preflight_vllm_api.py",       # replaced by preflight_pumice.py
    "test_preflight_vllm_api.py",
    "test_parse_artifacts.py",     # imports eval/scripts, not shipped (conventions §9 ruling)
)
# Source dir -> rel globs skipped for that entry only.
EXCLUDE_PER_ENTRY = {
    (CLAUDE / "hooks", "*"): ("test-*.sh",),   # harnesses go to hooks/tests/
}

# Files under R that belong to other wave-0 workers (plan.md). The seed must
# not create them, and the inventory does not report them as extra.
OTHER_WORKERS = frozenset({
    # worker-2
    "scripts/campaign_paths.py", "scripts/ammo", "hooks/_find_campaign.sh",
    "hooks/hooks.json", "hooks/tests/test-find-campaign.sh",
    "references/campaign-env.md", "tests/conftest.py", "tests/test_campaign_paths.py",
    "tests/test_path_refs.py", "tests/test_env_inventory.py", "tests/test_hooks_silent.py",
    "tests/test_prose_bans.py", "tests/test_hooks_json.py",
    # worker-4
    "workflow.pave.yaml", "revisions.yaml", "history/.gitkeep",
    "scripts/validate_pave.py", "scripts/record_revision.py",
    "references/pave.schema.json", "references/pave-composition.schema.json",
    "tests/test_workflow_pave.py", "README.md", "VERSION",
})
OTHER_WORKER_DIRS = ("history/",)

# Must not exist under R after the seed (plan.md worker-1 acceptance).
FORBIDDEN = ("tests/agents", "eval", "scripts/preflight_vllm_api.py",
             "tests/test_preflight_vllm_api.py", "tests/test_parse_artifacts.py")

# ---------------------------------------------------------------------------
# 2. Rewrite rules. Closed and ordered, applied with str.replace.
# ---------------------------------------------------------------------------

# Per-file fixups: exact replacements against the SOURCE text, applied before
# RULES. A missing anchor is an error, so a source change cannot slip past.
# Two groups, nothing else:
# - Server-side tests (conventions §10): the lines that find the plugin's own
#   code (scripts dir, hook path, schema dir, the one import that names the
#   server package), so they import from R/scripts and R/hooks.
# - Placement fixups: a test whose subject moved under §1 (Codex-only scripts
#   to codex/scripts/, SKILL.md to skills/ammo/, report/ to skills/ammo-report/,
#   the dialect dir role to R) and that fails at import or collection without
#   the change. Runtime failures from layout idioms (parents[3], walking up to
#   .claude/schemas) are NOT fixed here: conventions §2 gives them to
#   plugin_path() in wave 1, and baseline.md lists them.
FIXUPS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("tests/test_ammo_serving.py", (
        ("ROOT = Path(__file__).resolve().parents[2]\nSCRIPTS = ROOT / 'ai_cli_session' / '_ammo_shared' / 'scripts'\n",
         "ROOT = Path(__file__).resolve().parents[1]\nSCRIPTS = ROOT / 'scripts'\n"),
        ("ROOT/'ai_cli_session/_ammo_shared/schemas'/name", "ROOT/'schemas'/name"),
    )),
    ("tests/test_ammo_serving_runtime.py", (
        ("ROOT = Path(__file__).resolve().parents[2]\nSCRIPTS = ROOT / 'ai_cli_session/_ammo_shared/scripts'\n",
         "ROOT = Path(__file__).resolve().parents[1]\nSCRIPTS = ROOT / 'scripts'\n"),
    )),
    ("tests/test_ammo_serving_campaign.py", (
        ("from tests.unit.test_ammo_serving import", "from test_ammo_serving import"),
        ("ROOT / f'ai_cli_session/.{request.param}/skills/ammo/scripts'", "ROOT / 'scripts'"),
        ("ROOT / f'ai_cli_session/.{request.param}/schemas/state.schema.json'", "ROOT / 'schemas/state.schema.json'"),
        ("ROOT/f'ai_cli_session/.{variant}/skills/ammo/scripts'", "ROOT/'scripts'"),
    )),
    ("tests/test_pumice_pins.py", (
        ("from shared import pumice_pins\n",
         "sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'scripts'))\n"
         "import pumice_pins\n"),
    )),
    ("tests/test_codex_hook_campaign_resolution.py", (
        ('ROOT = Path(__file__).resolve().parents[2]\nCODEX_SRC = ROOT / "ai_cli_session" / ".codex"\n',
         'ROOT = Path(__file__).resolve().parents[1]\nCODEX_SRC = ROOT / "codex"\n'),
    )),
    ("tests/test_env_default_guard_hook.py", (
        ('ROOT = Path(__file__).parent.parent.parent\nHOOK = ROOT / "ai_cli_session" / ".claude" / "hooks" / "ammo-env-default-guard.sh"\n',
         'ROOT = Path(__file__).resolve().parents[1]\nHOOK = ROOT / "hooks" / "ammo-env-default-guard.sh"\n'),
        ('ALLOWLIST = ROOT / "ai_cli_session" / "_ammo_shared" / "hooks" / "pumice_env_allowlist.txt"',
         'ALLOWLIST = ROOT / "hooks" / "pumice_env_allowlist.txt"'),
        ('CLAUDE_COPY = ROOT / "ai_cli_session" / ".claude" / "hooks" / "pumice_env_allowlist.txt"',
         'CLAUDE_COPY = ROOT / "hooks" / "pumice_env_allowlist.txt"'),
        ('pretool = ROOT / "ai_cli_session" / ".claude" / "hooks" / "ammo-pretool-guard.sh"',
         'pretool = ROOT / "hooks" / "ammo-pretool-guard.sh"'),
    )),
    ("tests/test_worktree_hook.py", (
        ('Path(__file__).parent.parent.parent / "ai_cli_session" / ".claude" / "hooks" / "worktree-create-with-build.sh",',
         'Path(__file__).resolve().parents[1] / "hooks" / "worktree-create-with-build.sh",'),
        ('        Path(__file__).parent.parent.parent\n        / "ai_cli_session"\n        / ".codex"\n'
         '        / "skills"\n        / "ammo"\n        / "scripts"\n        / "create_worktree_with_build.sh"\n',
         '        Path(__file__).resolve().parents[1]\n        / "codex"\n        / "scripts"\n'
         '        / "create_worktree_with_build.sh"\n'),
    )),
    # --- placement fixups ---
    ("tests/test_resolve_codex_transcript.py", (
        ('SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"\n',
         'SCRIPTS = Path(__file__).resolve().parent.parent / "codex" / "scripts"\n'),
    )),
    ("tests/test_codex_v1_runtime.py", (
        ('sys.path.insert(0, str(ROOT / ".codex/skills/ammo/scripts"))\n',
         'sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "codex" / "scripts"))\n'),
    )),
    ("tests/test_guidance_semantic_contract.py", (
        ("CLAUDE_DIR = ROOT.parents[1]\n", "CLAUDE_DIR = ROOT\n"),
        ('ROOT / "report" / "SKILL.md"', 'ROOT / "skills" / "ammo-report" / "SKILL.md"'),
        ('ROOT / "SKILL.md"', 'ROOT / "skills" / "ammo" / "SKILL.md"'),
        ('_text("report/SKILL.md")', '_text("skills/ammo-report/SKILL.md")'),
    )),
)

# Global rules, every seeded text file, applied in order after the fixups.
# Longer and more specific forms come first so a shorter rule never splits
# them (CODEX_RULES does the same).
RULES: tuple[tuple[str, str], ...] = (
    # Hook registration form (conventions §7). Both brace styles.
    ("$CLAUDE_PROJECT_DIR/.claude/hooks/", "${CLAUDE_PLUGIN_ROOT}/hooks/"),
    ("${CLAUDE_PROJECT_DIR}/.claude/hooks/", "${CLAUDE_PLUGIN_ROOT}/hooks/"),
    # Repo-relative spelling of the skill dirs: drop the server repo prefix so
    # the rules below see the dialect-relative form.
    ("ai_cli_session/.claude/skills/", ".claude/skills/"),
    ("ai_cli_session/.codex/skills/", ".codex/skills/"),
    # Regex-escaped spelling first: ".claude/agents/" is a substring of it.
    ("\\.claude/agents/", "agents/"),
    # Codex role docs. The Claude dialect ships, so the Codex twin names map
    # back to the Claude file (the inverse of CODEX_RULES' two renames).
    (".codex/skills/ammo/agents/ammo-implementer", "agents/ammo-impl-champion"),
    (".codex/skills/ammo/agents/ammo-report.md", "agents/ammo-report-writer.md"),
    (".codex/skills/ammo/agents/", "agents/"),
    # .codex/skills/ammo/ -> flat layout. The Codex-only parts first.
    (".codex/skills/ammo/RUNTIME.md", "codex/RUNTIME.md"),
    (".codex/skills/ammo/instrumentation/", "codex/instrumentation/"),
    (".codex/skills/ammo/scripts/codex_agent_registry.py", "codex/scripts/codex_agent_registry.py"),
    (".codex/skills/ammo/scripts/monitor_queue_ack.py", "codex/scripts/monitor_queue_ack.py"),
    (".codex/skills/ammo/scripts/resolve_codex_transcript.py", "codex/scripts/resolve_codex_transcript.py"),
    (".codex/skills/ammo/scripts/create_worktree_with_build.sh", "codex/scripts/create_worktree_with_build.sh"),
    (".codex/skills/ammo/scripts/remove_worktree_cleanup.sh", "codex/scripts/remove_worktree_cleanup.sh"),
    (".codex/skills/ammo/report/", "skills/ammo-report/"),
    (".codex/skills/ammo/SKILL.md", "skills/ammo/SKILL.md"),
    (".codex/skills/ammo/scripts", "scripts"),
    (".codex/skills/ammo/references", "references"),
    (".codex/skills/ammo/orchestration", "orchestration"),
    (".codex/skills/ammo/tests", "tests"),
    (".codex/skills/ammo/data", "data"),
    # .claude/skills/ammo/ -> flat layout (conventions §1).
    (".claude/skills/ammo/report/", "skills/ammo-report/"),
    (".claude/skills/ammo/SKILL.md", "skills/ammo/SKILL.md"),
    (".claude/skills/ammo/scripts", "scripts"),
    (".claude/skills/ammo/references", "references"),
    (".claude/skills/ammo/orchestration", "orchestration"),
    (".claude/skills/ammo/tests", "tests"),
    (".claude/skills/ammo/data", "data"),
    # The same layout spelled relative to the dialect dir: hooks reach scripts
    # as <hooks>/../skills/ammo/scripts, harnesses stage <dir>/skills/ammo/scripts,
    # prose says "skills/ammo/scripts/". In R these are <R>/scripts etc.
    # skills/ammo/SKILL.md is already the R path, so it has no rule.
    ("skills/ammo/report/", "skills/ammo-report/"),
    ("skills/ammo/scripts", "scripts"),
    ("skills/ammo/references", "references"),
    ("skills/ammo/orchestration", "orchestration"),
    ("skills/ammo/tests", "tests"),
    ("skills/ammo/data", "data"),
    # .claude/agents/ and .claude/schemas/ (conventions §1).
    (".claude/agents/", "agents/"),
    (".claude/schemas", "schemas"),   # no slash: harnesses also mkdir the bare dir
)

# Scoped rules: (glob on the R-relative path, (old, new) pairs), applied after
# RULES. Two reasons for scope:
# - The hook harnesses move from hooks/ to hooks/tests/ (conventions §1). A
#   harness that finds its hooks as "the dir I am in" must look one level up,
#   and one that reached scripts as <hooks>/../scripts must go one more.
# - kernel_opt_artifacts is rewritten only where it is a literal example path
#   in prose. In hooks and tests it is a match pattern or a fixture that a
#   pattern consumes; those pairs move together in wave 1 (in_campaign, §3),
#   and tests/test_prose_bans.py flags every one that remains.
SCOPED_RULES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("hooks/tests/test-*.sh", (
        ('$(cd "$(dirname "$0")" && pwd)', '$(cd "$(dirname "$0")/.." && pwd)'),
        ('$(cd "$(dirname "$0")/../scripts" && pwd)', '$(cd "$(dirname "$0")/../../scripts" && pwd)'),
    )),
    ("references/*.md", (
        ("kernel_opt_artifacts/{target}/", ".ammo/<id>/"),
        ("kernel_opt_artifacts/", ".ammo/"),
    )),
    ("schemas/state.schema.json", (
        ("kernel_opt_artifacts/state.json", ".ammo/<id>/state.json"),
    )),
    ("scripts/new_target.py", (
        ("--artifact-dir kernel_opt_artifacts/", "--artifact-dir .ammo/"),
    )),
)


def _excluded(entry: tuple[Path, str, str], rel: Path) -> bool:
    if any(part in EXCLUDE_PARTS for part in rel.parts):
        return True
    rel_s = rel.as_posix()
    if any(fnmatch.fnmatch(rel_s, pat) for pat in EXCLUDE_REL):
        return True
    for pat in EXCLUDE_PER_ENTRY.get((entry[0], entry[1]), ()):
        if fnmatch.fnmatch(rel_s, pat):
            return True
    return False


def plan() -> list[tuple[Path, str]]:
    """Return (source file, R-relative destination) for every seeded file."""
    out: dict[str, Path] = {}
    for entry in COPY_MAP:
        src_dir, pattern, dest = entry
        if pattern == "**":
            cands = [p for p in sorted(src_dir.rglob("*")) if p.is_file()]
        else:
            cands = [p for p in sorted(src_dir.glob(pattern)) if p.is_file()]
        if not cands:
            raise SystemExit(f"copy map entry matched nothing: {src_dir}/{pattern}")
        for src in cands:
            rel = src.relative_to(src_dir)
            if _excluded(entry, rel):
                continue
            dst = f"{dest}/{rel.as_posix()}"
            if dst in out and out[dst] != src:
                raise SystemExit(f"two sources for {dst}: {out[dst]} and {src}")
            if dst in OTHER_WORKERS or dst.startswith(OTHER_WORKER_DIRS):
                raise SystemExit(f"seed would write another worker's file: {dst}")
            out[dst] = src
    return sorted(((src, dst) for dst, src in out.items()), key=lambda t: t[1])


def rewrite_text(rel: str, text: str) -> str:
    for path, pairs in FIXUPS:
        if rel == path:
            for old, new in pairs:
                if old not in text:
                    raise SystemExit(f"fixup anchor not found in {rel}: {old!r}")
                text = text.replace(old, new)
    for old, new in RULES:
        text = text.replace(old, new)
    for pattern, pairs in SCOPED_RULES:
        if fnmatch.fnmatch(rel, pattern):
            for old, new in pairs:
                text = text.replace(old, new)
    return text


def rewrite_bytes(rel: str, data: bytes) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    return rewrite_text(rel, text).encode("utf-8")


def cmd_seed(root: Path) -> int:
    root.mkdir(parents=True, exist_ok=True)
    n = 0
    for src, dst in plan():
        target = root / dst
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        n += 1
    print(f"seeded {n} files into {root}")
    return 0


def cmd_rewrite(root: Path) -> int:
    changed = 0
    for _src, dst in plan():
        target = root / dst
        data = target.read_bytes()
        new = rewrite_bytes(dst, data)
        if new != data:
            target.write_bytes(new)
            changed += 1
    print(f"rewrote {changed} files under {root}")
    return 0


def _ignored(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in EXCLUDE_PARTS for p in parts)


def cmd_inventory(root: Path, content: bool) -> int:
    expected = plan()
    want = {dst for _src, dst in expected}
    bad = 0
    for src, dst in expected:
        target = root / dst
        if not target.is_file():
            print(f"MISSING {dst}  (from {src})")
            bad += 1
        elif content and target.read_bytes() != rewrite_bytes(dst, src.read_bytes()):
            print(f"DRIFT {dst}  (differs from codemod(source))")
            bad += 1
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _ignored(rel) or rel in want or rel in OTHER_WORKERS or rel.startswith(OTHER_WORKER_DIRS):
            continue
        print(f"EXTRA {rel}")
        bad += 1
    for rel in FORBIDDEN:
        if (root / rel).exists():
            print(f"FORBIDDEN {rel}")
            bad += 1
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("seed", "rewrite", "inventory"):
        p = sub.add_parser(name)
        p.add_argument("root", type=Path)
        if name == "inventory":
            p.add_argument("--content", action="store_true",
                           help="also require each file to equal codemod(source)")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    if root == SRC or SRC in root.parents or root == S:
        raise SystemExit("refusing to write under ammo-server")
    if args.cmd == "seed":
        return cmd_seed(root)
    if args.cmd == "rewrite":
        return cmd_rewrite(root)
    return cmd_inventory(root, args.content)


if __name__ == "__main__":
    sys.exit(main())
