#!/usr/bin/env python3
"""Extract the plugin's agents/*.md + skills/ into Kiro-native locations.

Kiro consumes:
  - agents as .kiro/agents/<name>.json  (name, description, prompt, tools,
    model, resources[skill://...])
  - skills as skill:// resources over .kiro/skills/<name>/SKILL.md

The plugin already ships agents as agentskills.io-style markdown (--- YAML
frontmatter + prose body) and skills as SKILL.md dirs, so this is a pure
transcription: parse the frontmatter, map Claude tool names -> Kiro tool
names, turn the `skills:` list into skill:// resources, and write JSON. The
prose body becomes the agent `prompt` verbatim.

Answer-blind / behavior-preserving: no prompt text is altered. Only the
container format changes (md -> json) and the skill references are rewritten
to skill:// URIs pointing at the copied skills.

Usage:
  python3 extract_agents.py --plugin-root <plugin> --out-root <repo-or-home>
    [--skills-dest .kiro/skills] [--agents-dest .kiro/agents] [--link|--copy]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

# Claude Code tool name -> Kiro built-in tool name.
# Skill/Agent have no Kiro tool equivalent: skills load via skill:// resources,
# and sub-agent spawning is done by the orchestrator via the subagent tool, not
# by a per-agent tool. So they are dropped from the allowlist (same decision the
# Pi personas.mjs made).
TOOL_MAP = {
    "Read": "read", "Write": "write", "Edit": "write", "Bash": "shell",
    "Grep": "grep", "Glob": "glob", "LS": "read",
    "Skill": None, "Agent": None,
}


def parse_frontmatter(text: str):
    """Return (meta: dict, body: str). Handles flat scalars + '- ' list items
    and the multi-line description block (key: | ... indented)."""
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
    if not m:
        return {}, text
    fm, body = m.group(1), m.group(2)
    meta: dict = {}
    cur = None
    block_key = None  # key whose value is a '|' folded/multiline block
    for raw in fm.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            continue
        # continuation of a '|' block or an indented value line
        kv = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        is_top = kv and not (line.startswith(" ") or line.startswith("\t"))
        if is_top:
            cur = kv.group(1)
            val = kv.group(2).strip()
            block_key = cur if val == "|" else None
            if val in ("", "|"):
                meta[cur] = "" if val == "|" else []
            else:
                meta[cur] = val
        elif cur and re.match(r"^\s*-\s+", line):
            if not isinstance(meta.get(cur), list):
                meta[cur] = []
            meta[cur].append(re.sub(r"^\s*-\s+", "", line).strip())
        elif cur and (line.startswith("  ") or line.startswith("\t")):
            # indented continuation (multiline description / '|' block)
            prev = meta.get(cur, "")
            if isinstance(prev, str):
                meta[cur] = (prev + " " + line.strip()).strip()
    return meta, body.lstrip("\n")


def normalize_model(m):
    if not m:
        return None
    # strip trailing "[1m]" context-window tag; Bedrock ids don't carry it.
    m = re.sub(r"\[[^\]]*\]\s*$", "", str(m)).strip()
    return m or None


def tool_list(meta_tools):
    if not meta_tools:
        return None
    names = meta_tools if isinstance(meta_tools, list) else [
        s.strip() for s in str(meta_tools).split(",") if s.strip()]
    out = []
    for t in names:
        mapped = TOOL_MAP.get(t, t.lower())
        if mapped:
            out.append(mapped)
    # de-dup, stable order
    seen, dedup = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            dedup.append(t)
    return dedup or None


def skill_names(meta_skills):
    if not meta_skills:
        return []
    if isinstance(meta_skills, list):
        return [s for s in meta_skills if s]
    return [s.strip() for s in str(meta_skills).split(",") if s.strip()]


def convert(plugin_root: Path, out_root: Path, skills_dest_rel: str,
            agents_dest_rel: str, mode: str, keep_model: bool = False,
            emit: str = "kiro-json"):
    plugin_root = plugin_root.resolve()
    out_root = out_root.resolve()
    skills_src = plugin_root / "skills"
    agents_src = plugin_root / "agents"
    skills_dest = out_root / skills_dest_rel
    agents_dest = out_root / agents_dest_rel
    skills_dest.mkdir(parents=True, exist_ok=True)
    agents_dest.mkdir(parents=True, exist_ok=True)

    # 1) skills: link/copy each skill dir into .kiro/skills/<name>
    skill_dirs = sorted(d for d in skills_src.iterdir() if (d / "SKILL.md").exists())
    for d in skill_dirs:
        target = skills_dest / d.name
        if target.exists() or target.is_symlink():
            if target.is_symlink() or target.is_file():
                target.unlink()
            else:
                shutil.rmtree(target)
        if mode == "link":
            target.symlink_to(d, target_is_directory=True)
        else:
            shutil.copytree(d, target)
    print(f"skills: {len(skill_dirs)} -> {skills_dest} ({mode})")

    # skill:// resource for a skill name (glob-safe absolute path)
    def skill_uri(name: str) -> str:
        return f"skill://{(skills_dest / name / 'SKILL.md')}"

    # 2) agents: md -> json
    agent_files = sorted(agents_src.glob("*.md"))
    written = []
    for f in agent_files:
        meta, body = parse_frontmatter(f.read_text())
        name = meta.get("name") or f.stem
        skills = skill_names(meta.get("skills"))
        # keep only skills that were actually extracted
        resources = [skill_uri(s) for s in skills if (skills_dest / s / "SKILL.md").exists()]
        missing = [s for s in skills if not (skills_dest / s / "SKILL.md").exists()]
        cfg = {
            "name": name,
            "description": (meta.get("description") or f"{name} (NKI specialist)").strip(),
            "prompt": body.strip(),
            "tools": tool_list(meta.get("tools")) or ["read", "shell", "grep", "glob"],
        }
        model = normalize_model(meta.get("model"))
        # Drop model pins that this Kiro backend does not accept. The plugin's
        # agents pin Bedrock ids (global.anthropic.claude-opus-4-8[1m]) and the
        # bare 'opus' alias — neither resolves here (INVALID_MODEL_ID). Leaving
        # `model` unset makes the agent use Kiro's default model, which works.
        # Override with --keep-model if a valid id is desired.
        _UNAVAILABLE = ("opus", "global.anthropic.claude-opus-4-8", "claude-opus-4-8")
        if model and keep_model and model not in _UNAVAILABLE:
            cfg["model"] = model
        if resources:
            cfg["resources"] = resources
        if emit == "agent-spec":
            # Wrap into the NeuronAgenticDevelopment `.agent-spec.json` schema so the
            # transcribed specialist/writer agents are drop-in for that loader. Skills
            # become dependencies.skills.skillNames; bodies still load via resources.
            skills_kept = [s for s in skills if (skills_dest / s / "SKILL.md").exists()]
            spec = {
                "schemaVersion": "1",
                "name": name,
                "config": {
                    "description": cfg["description"],
                    "systemPrompt": cfg["prompt"],
                    **({"model": cfg["model"]} if "model" in cfg else {}),
                },
                "dependencies": {"skills": {"skillNames": skills_kept}},
                "clientConfig": {"kiroCli": {
                    "tools": ["@builtin"],
                    "allowedTools": cfg["tools"],
                    "resources": resources,
                }},
            }
            (agents_dest / f"{name}.agent-spec.json").write_text(json.dumps(spec, indent=2) + "\n")
        else:
            (agents_dest / f"{name}.json").write_text(json.dumps(cfg, indent=2) + "\n")
        written.append((name, len(resources), missing))

    print(f"agents: {len(written)} -> {agents_dest}")
    for name, nres, missing in written:
        note = f"  (missing skills: {', '.join(missing)})" if missing else ""
        print(f"  {name}: {nres} skill resource(s){note}")
    return written


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    here = Path(__file__).resolve()
    default_plugin = here.parents[2]  # nkilib_agentic_dev_plugin/
    p.add_argument("--plugin-root", default=str(default_plugin))
    p.add_argument("--out-root", default=str(default_plugin.parent),
                   help="Where .kiro/ lives (default: the KNKL repo root).")
    p.add_argument("--skills-dest", default=".kiro/skills")
    p.add_argument("--agents-dest", default=".kiro/agents")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--link", dest="mode", action="store_const", const="link",
                     help="symlink skills (default) — keeps one editable source")
    grp.add_argument("--copy", dest="mode", action="store_const", const="copy",
                     help="copy skills (self-contained, portable)")
    p.add_argument("--keep-model", action="store_true",
                   help="keep the persona model pin (default: drop it, use Kiro default)")
    p.add_argument("--emit", choices=("kiro-json", "agent-spec"), default="kiro-json",
                   help="output format: 'kiro-json' (flat .json, default) or 'agent-spec' "
                        "(NeuronAgenticDevelopment .agent-spec.json schema)")
    p.set_defaults(mode="link")
    args = p.parse_args(argv)
    convert(Path(args.plugin_root), Path(args.out_root),
            args.skills_dest, args.agents_dest, args.mode, keep_model=args.keep_model,
            emit=args.emit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
