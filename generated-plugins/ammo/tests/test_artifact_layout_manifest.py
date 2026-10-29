#!/usr/bin/env python3
"""Bidirectional parity between artifact_layout.json and artifact-layout.md.

The manifest owns the PATH FACTS and both variants' layout warn hooks derive
their allowlist from it. The doc owns the JUDGMENT (resolution order,
disambiguation, prohibited-pattern rationale) and remains the authority a
reader consults. Two copies of the same facts is the drift condition that put
`evidence.json`, `validation_summary.json`, `diff.patch` and
`post_ship_profiling/` in three mutually contradicting states, so this module
closes both directions:

  forward: every `slots[].path_template` appears verbatim in the doc.
  reverse: every `--slot` value in the doc's sweep-slot table has a manifest
           slot, and the same slot list drives the hooks.

Content-keyed, not line-keyed: it matches on the template and slot strings, so
reordering or rewording the doc cannot make it red.

Runs anywhere: stdlib plus pytest, no GPU, no vLLM.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_SKILL_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = _SKILL_ROOT / "scripts" / "artifact_layout.json"
DOC = _SKILL_ROOT / "references" / "artifact-layout.md"

_KINDS = {"authoritative", "diagnostic", "derived"}
_SLOT_ROW = re.compile(r"^\|\s*`([A-Za-z0-9_{}/]+)`\s*\|\s*`(rounds/\{N\}/sweeps/[^`]+)`\s*\|")


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def doc_sweep_slots(doc: str) -> dict[str, str]:
    """`--slot` value -> resolved path, from the doc's sweep-slot table."""
    rows: dict[str, str] = {}
    for line in doc.split("\n"):
        match = _SLOT_ROW.match(line.strip())
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


# ---------------------------------------------------------------------------
# manifest shape
# ---------------------------------------------------------------------------


def test_every_slot_declares_the_required_fields(manifest):
    bad = []
    for name, slot in manifest["slots"].items():
        if not isinstance(slot.get("path_template"), str) or not slot["path_template"]:
            bad.append(f"{name}: path_template must be a non-empty string")
        if not isinstance(slot.get("writer"), str) or not slot["writer"]:
            bad.append(f"{name}: writer must be a non-empty string")
        if "gate" not in slot or not isinstance(slot["gate"], (str, type(None))):
            bad.append(f"{name}: gate must be a string or null")
        if not isinstance(slot.get("is_gate_slot"), bool):
            bad.append(f"{name}: is_gate_slot must be a bool")
        if slot.get("kind") not in _KINDS:
            bad.append(f"{name}: kind must be one of {sorted(_KINDS)}")
    assert not bad, "artifact_layout.json slot defects:\n  " + "\n  ".join(bad)


def test_every_placeholder_token_is_declared(manifest):
    """No template may hold a `{token}` the placeholder table does not define.

    An undeclared token would compile to a LITERAL brace in both hooks, so the
    slot would silently never match and its whole subtree would warn.
    """
    declared = set(manifest["placeholders"])
    templates = [slot["path_template"] for slot in manifest["slots"].values()]
    templates += manifest.get("open_dirs", [])
    undeclared = {
        token
        for template in templates
        for token in re.findall(r"\{[^{}]*\}", template)
        if token not in declared
    }
    assert not undeclared, f"templates use undeclared placeholders: {sorted(undeclared)}"


def test_open_dirs_are_ancestors_of_real_slots(manifest):
    """An `open_dirs` prefix must own at least one enumerated slot.

    A prefix with no slot below it would widen the allowlist over a directory
    the manifest never describes.
    """
    templates = [slot["path_template"] for slot in manifest["slots"].values()]
    orphans = [
        prefix
        for prefix in manifest.get("open_dirs", [])
        if not any(t.startswith(prefix) and t != prefix for t in templates)
    ]
    assert not orphans, f"open_dirs prefixes with no slot beneath them: {orphans}"


# ---------------------------------------------------------------------------
# campaign root: where every slot template is rooted (conventions §3)
# ---------------------------------------------------------------------------


def test_campaign_root_names_the_campaign_workspace(manifest):
    """Hooks and the Codex guard read this key instead of a root literal."""
    assert manifest["campaign_root"] == ".ammo/{campaign_id}/"
    assert "{campaign_id}" in manifest["placeholders"]


def test_campaign_id_placeholder_matches_the_layer_id_rule(manifest):
    """The placeholder accepts exactly the ids campaign_paths reads from the marker."""
    import sys

    scripts = _SKILL_ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import campaign_paths

    placeholder = re.compile("^(?:" + manifest["placeholders"]["{campaign_id}"] + ")$")
    for value in ("20261028-120000-abc123", "gptoss-l40s-1", "c1", "a.b_c-d",
                  "..", ".", "-lead", "a/b", "", "x y"):
        layer = bool(campaign_paths._ID.fullmatch(value)) and value not in (".", "..")
        assert bool(placeholder.match(value)) == layer, value


def test_campaign_root_is_where_the_layer_finds_the_campaign(manifest, tmp_path):
    import sys

    scripts = _SKILL_ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import campaign_paths

    cid = "20261028-120000-abc123"
    campaign = tmp_path / manifest["campaign_root"].replace("{campaign_id}", cid)
    campaign.mkdir(parents=True)
    (tmp_path / ".ammo" / "active").write_text(cid + "\n", encoding="utf-8")
    assert campaign_paths.find_campaign_dir(tmp_path, env={}) == campaign.resolve()


def test_slot_templates_are_relative_to_the_campaign_root(manifest):
    """A template holding the root itself would match only a nested campaign."""
    root = manifest["campaign_root"].split("/")[0]
    templates = [slot["path_template"] for slot in manifest["slots"].values()]
    templates += manifest.get("open_dirs", [])
    rooted = [t for t in templates if t.startswith("/") or t.split("/")[0] == root]
    assert not rooted, rooted


def test_doc_names_the_campaign_root(manifest, doc):
    assert manifest["campaign_root"].replace("{campaign_id}", "<id>") in doc


# ---------------------------------------------------------------------------
# forward: manifest -> doc
# ---------------------------------------------------------------------------


def test_every_path_template_appears_in_the_doc(manifest, doc):
    """The doc a reader consults must name every path the hooks accept.

    A template the doc omits is a path an agent cannot look up, which is how
    `post_ship_profiling/` and `diff.patch` became enforcer-only facts.
    """
    missing = [
        f"{name} -> {slot['path_template']}"
        for name, slot in manifest["slots"].items()
        if slot["path_template"] not in doc
    ]
    assert not missing, (
        "path_templates absent from artifact-layout.md (add the row, or fix the "
        "template):\n  " + "\n  ".join(missing)
    )


# ---------------------------------------------------------------------------
# reverse: doc -> manifest
# ---------------------------------------------------------------------------


def test_doc_sweep_table_is_not_empty(doc_sweep_slots):
    """Guard the parser: a reformatted table must not silently pass the pair."""
    assert len(doc_sweep_slots) >= 10, (
        f"parsed only {len(doc_sweep_slots)} rows from the doc's `--slot` table; "
        "the table format changed and _SLOT_ROW needs updating"
    )


def test_every_doc_sweep_slot_exists_in_the_manifest(manifest, doc_sweep_slots):
    """A documented slot the hooks do not accept warns on correct behavior."""
    templates = {slot["path_template"] for slot in manifest["slots"].values()}
    missing = [
        f"--slot {value} -> {path}"
        for value, path in doc_sweep_slots.items()
        if path not in templates
    ]
    assert not missing, (
        "sweep slots documented but absent from artifact_layout.json:\n  "
        + "\n  ".join(missing)
    )


def test_sweep_slot_values_match_their_documented_paths(doc_sweep_slots):
    """Each row's `--slot` value must be the tail of the path it resolves to."""
    bad = [
        f"--slot {value} resolves to {path}"
        for value, path in doc_sweep_slots.items()
        if path.rstrip("/") != f"rounds/{{N}}/sweeps/{value}"
    ]
    assert not bad, "sweep-slot rows whose value and path disagree:\n  " + "\n  ".join(bad)


def test_manifest_covers_every_sweep_slot_the_doc_lists(manifest, doc_sweep_slots):
    """Both directions on the sweep table specifically, keyed by slot name."""
    manifest_sweeps = {
        slot["path_template"]
        for slot in manifest["slots"].values()
        if slot["path_template"].startswith("rounds/{N}/sweeps/")
    }
    doc_sweeps = {path for path in doc_sweep_slots.values()}
    assert manifest_sweeps == doc_sweeps, (
        "sweep-slot sets differ.\n"
        f"  manifest only: {sorted(manifest_sweeps - doc_sweeps)}\n"
        f"  doc only:      {sorted(doc_sweeps - manifest_sweeps)}"
    )


# ---------------------------------------------------------------------------
# gate-slot semantics
# ---------------------------------------------------------------------------


def test_gate_slots_match_the_sweep_script(manifest):
    """`is_gate_slot` must agree with run_vllm_bench_latency_sweep.py.

    The script's `_is_gate_slot` decides whether a sweep produces binding
    Stage 5/6 evidence. A manifest that disagrees would document a different
    gate surface than the one the script enforces.
    """
    import sys

    scripts = _SKILL_ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from run_vllm_bench_latency_sweep import _is_gate_slot

    prefix = "rounds/{N}/sweeps/"
    bad = []
    for name, slot in manifest["slots"].items():
        template = slot["path_template"]
        if not template.startswith(prefix):
            if slot["is_gate_slot"]:
                bad.append(f"{name}: is_gate_slot=true but it is not a sweep slot")
            continue
        value = template[len(prefix) :].rstrip("/").replace("{op_id}", "op001")
        if _is_gate_slot(value) != slot["is_gate_slot"]:
            bad.append(
                f"{name}: manifest is_gate_slot={slot['is_gate_slot']} but "
                f"_is_gate_slot({value!r})={_is_gate_slot(value)}"
            )
    assert not bad, "gate-slot disagreements with the sweep script:\n  " + "\n  ".join(bad)


# ---------------------------------------------------------------------------
# the hooks' derivation, reproduced here
# ---------------------------------------------------------------------------


def _allow_patterns(manifest: dict) -> list[re.Pattern[str]]:
    """The same derivation both hooks apply, so a manifest edit is testable."""
    placeholders = manifest["placeholders"]
    tokens = sorted(placeholders, key=len, reverse=True)
    special = set(r".^$*+?()[]{}|\\")

    def compile_template(template: str) -> str:
        out: list[str] = []
        index = 0
        while index < len(template):
            for token in tokens:
                if template.startswith(token, index):
                    out.append(placeholders[token])
                    index += len(token)
                    break
            else:
                char = template[index]
                out.append("\\" + char if char in special else char)
                index += 1
        return "".join(out)

    templates = [slot["path_template"] for slot in manifest["slots"].values()]
    templates += manifest.get("open_dirs", [])
    patterns: list[str] = []
    ancestors: set[str] = set()
    for template in templates:
        body = compile_template(template.rstrip("/"))
        patterns.append("^" + body + ("(/|$)" if template.endswith("/") else "$"))
        segments = template.strip("/").split("/")
        for cut in range(1, len(segments)):
            ancestors.add("^" + compile_template("/".join(segments[:cut])) + "$")
    return [re.compile(p) for p in patterns + sorted(ancestors)]


@pytest.mark.parametrize(
    "rel",
    [
        # the four slots that used to false-warn
        "rounds/1/tracks/op-001/evidence.json",
        "rounds/1/tracks/op-001/validation_summary.json",
        "rounds/1/tracks/op-001/diff.patch",
        "rounds/1/sweeps/post_ship_profiling/json/x.json",
        # the rest of the canonical surface
        "state.json",
        # the flock file every state.json writer serializes on
        "state.json.lock",
        "rounds/12/audits/stage_45_cycle_2.md",
        "rounds/1/validation_gate_report.json",
        "rounds/1/mining/mined.json",
        "rounds/1/sweeps/opt/op007/e2e_latency_results.json",
        # Neuron attribution: NTFF captures, view exports, normalized events
        "rounds/1/profiling/neuron/cc_ops_field_report.json",
        "rounds/1/profiling/neuron/rank0/decode.ntff",
        "rounds/1/_archive/baseline_2026-05-05T181212Z/x.json",
        # bare directory tokens a Bash `ls` yields
        "rounds/1/sweeps/opt",
        "rounds/12",
    ],
)
def test_canonical_paths_are_allowed(manifest, rel):
    patterns = _allow_patterns(manifest)
    assert any(p.search(rel) for p in patterns), f"{rel} should conform"


@pytest.mark.parametrize(
    "rel",
    [
        "monitor_log_champion_1.md",
        "rounds/1/tracks/op-001/validation_results_DRAFT.md",
        "e2e_latency_opt3/x.json",
        "investigation/notes.md",
        "rounds/1/sweeps/baseline_2026-05-05T181212Z/x.json",
        "bottleneck_analysis.md",
        "rounds/1/profiling/neuron_2026-09-24/x.json",
    ],
)
def test_prohibited_paths_still_warn(manifest, rel):
    """The allowlist must not widen into the doc's Prohibited Patterns."""
    patterns = _allow_patterns(manifest)
    assert not any(p.search(rel) for p in patterns), f"{rel} should warn"


# ---------------------------------------------------------------------------
# one regex dialect: Python re and POSIX ERE (grep -E) read every pattern alike
# ---------------------------------------------------------------------------

#: Python-only regex syntax that POSIX ERE does not have: `(?` groups, flags and
#: lookarounds, the `\d`-style class escapes and anchors, and lazy quantifiers.
#: GNU grep -E prints "? at start of expression" for `(?:` and then fails to
#: match; macOS grep accepts it, so this check makes it red on every host.
_NOT_ERE = re.compile(r"\(\?|\\[dDwWsSbBAZz]|[*+?}]\?")

#: One value per placeholder that its fragment must accept.
_SAMPLES = {
    "{campaign_id}": "20261028-120000-abc123",
    "{N}": "12",
    "{D}": "2",
    "{C}": "3",
    "{BS}": "32",
    "{op_id}": "op-007",
    "{champion_id}": "champion_2",
    "{monitor_id}": "monitor-1",
    "{stage}": "stage_45",
    "{date}": "2026-10-28T12:00:00Z",
    "{slot}": "opt_correctness",
    "{timestamp}": "2026-10-28T181212Z",
    "{root_thread_id}": "thread_01-ab",
    "{task_path_without_leading_slash}": "audit_gate/stage_1/cycle_2",
}

#: Values a fragment must refuse, so the dialect change keeps the match set.
_REFUSED = {
    "{campaign_id}": "-lead",
    "{N}": "1a",
    "{task_path_without_leading_slash}": "audit_gate//stage_1",
}


def _grep_e(patterns: list[str], lines: list[str], tmp_path: Path) -> tuple[set[str], str]:
    """Lines that `grep -E -f patterns` selects, and grep's stderr."""
    grep = shutil.which("grep")
    if grep is None:
        pytest.skip("grep is not on PATH")
    pattern_file = tmp_path / "patterns.txt"
    pattern_file.write_text("\n".join(patterns) + "\n", encoding="utf-8")
    line_file = tmp_path / "lines.txt"
    line_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = subprocess.run([grep, "-E", "-f", str(pattern_file), str(line_file)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode in (0, 1), result.stderr
    return set(result.stdout.splitlines()), result.stderr


def _sample_path(template: str) -> str:
    for token, value in sorted(_SAMPLES.items(), key=lambda kv: -len(kv[0])):
        template = template.replace(token, value)
    return template.rstrip("/")


def test_every_placeholder_has_a_sample(manifest):
    assert set(manifest["placeholders"]) == set(_SAMPLES)


@pytest.mark.parametrize("token", sorted(_SAMPLES))
def test_placeholder_fragments_use_posix_ere_syntax(manifest, token):
    fragment = manifest["placeholders"][token]
    assert not _NOT_ERE.search(fragment), f"{token}: {fragment!r} is not POSIX ERE"
    re.compile(fragment)


def test_placeholder_fragments_match_alike_in_python_and_grep_e(manifest, tmp_path):
    for token, fragment in manifest["placeholders"].items():
        anchored = f"^({fragment})$"
        lines = [_SAMPLES[token]] + ([_REFUSED[token]] if token in _REFUSED else [])
        python = {line for line in lines if re.search(anchored, line)}
        grep, stderr = _grep_e([anchored], lines, tmp_path)
        assert stderr == "", f"{token}: {stderr}"
        assert python == grep == {_SAMPLES[token]}, (token, python, grep)


def test_allowlist_matches_alike_in_python_and_grep_e(manifest, tmp_path):
    """Every slot sample conforms and every prohibited path warns, in both dialects."""
    patterns = _allow_patterns(manifest)
    templates = [slot["path_template"] for slot in manifest["slots"].values()]
    templates += manifest.get("open_dirs", [])
    allowed = sorted({_sample_path(t) for t in templates})
    prohibited = ["monitor_log_champion_1.md", "e2e_latency_opt3/x.json",
                  "rounds/1/tracks/op-001/validation_results_DRAFT.md",
                  "recovery/thread_01/Audit/context.md"]
    lines = allowed + prohibited
    python = {line for line in lines if any(p.search(line) for p in patterns)}
    grep, stderr = _grep_e([p.pattern for p in patterns], lines, tmp_path)
    assert stderr == ""
    assert python == grep == set(allowed)
