#!/usr/bin/env bash
# LEAD HOOK PAIR (1 of 2) -- stop-alignment check with the audit check.
# Supports P12 (emit only declared outcomes; traverse only declared edges) by
# re-presenting the run position at the highest-risk context-decay moment. The
# block text (heredoc below) carries one line per active seat with a mandatory
# reply form, then the lead duties as imperatives -- no free-form retrospective,
# no "all clear" reply.
#
# Audit check (same hook, same cooldown; pave-init 2.6.2 audit mode): counts
# declared-node outcomes and write-log bytes since the checkpoint sidecar
# <run-state>.audit-checkpoint.json (written only by this hook and the
# PreToolUse router). When either threshold is crossed the block text IS the
# updater brief (checkpoint id, write_report, dispatch line). An OPEN cycle passes;
# a cycle the revision_log or the findings record closed resets the counters; an
# OPEN cycle that outlives AUDIT_STALL further outcomes is named as a stall
# unless a proposal awaits the user's approval. A graph applied since the run's
# pin is named with the rule-1 route while no decline stands. Silent when the
# project has no revision folder; one reminder line and no audit on a pave-init
# older than 2.6.2. Reads the write log and one proposals/ listing only --
# never a directory scan.
#
# Stop hooks have no non-blocking channel (additionalContext is dropped), so the
# text can only be delivered by blocking ONCE (exit 2). A session-keyed
# cooldown marker then lets the next STOP_EVERY-1 stops pass silently and the
# stop after that fires again -- the stop limit that makes an infinite
# stop loop impossible. The audit check shares it: at most one block per
# STOP_EVERY stops in total.
#
# Silent when stopping is correct: no marker-discovered run state, or the run's
# final_status is set. Lead-only by two gates: the lead-session
# sidecar AND the payload's agent identity fields -- a lead-spawned subagent
# carries the LEAD's session id in its Stop payload, so the sidecar alone
# cannot exclude it (observed 2026-08-31; recorded in the revision folder's
# revision record).
#
# Decline path (hook runtime unavailable): degrades to the lead's resume duty in
# SKILL.md "Run state and resume".
set -uo pipefail
HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PLUGIN_ROOT="$(cd "$HOOK_DIR/../../.." && pwd)"
PY="${VLLM_NEURON_PARITY_PYTHON:-python3}"
PY="${PY/#\~\//$HOME/}"
PAYLOAD="$(cat 2>/dev/null || true)"
command -v "$PY" >/dev/null 2>&1 || exit 0

FIELDS="$(printf '%s' "$PAYLOAD" | "$PY" -c '
import json, sys
def find(node, key):
    if isinstance(node, dict):
        if key in node and isinstance(node[key], (str, int)):
            return str(node[key])
        for v in node.values():
            f = find(v, key)
            if f:
                return f
    elif isinstance(node, list):
        for item in node:
            f = find(item, key)
            if f:
                return f
    return ""
try:
    payload = json.loads(sys.stdin.read())
except Exception:
    print("PARSE_FAIL"); sys.exit(0)   # distinct sentinel: never act on input
if not isinstance(payload, dict):      # we could not read
    print("PARSE_FAIL"); sys.exit(0)
print("OK")
print(str(payload.get("session_id") or "default").replace("\n", " "))
print("1" if payload.get("stop_hook_active") else "0")
print(find(payload, "agent_type").replace("\n", " "))
print(find(payload, "agent_id").replace("\n", " "))
' 2>/dev/null || true)"
[ "$(printf '%s\n' "$FIELDS" | sed -n 1p)" = "OK" ] || exit 0  # fail open
SESSION_ID="$(printf '%s\n' "$FIELDS" | sed -n 2p)"
[ -n "$SESSION_ID" ] || SESSION_ID=default
[ "$(printf '%s\n' "$FIELDS" | sed -n 3p)" = "1" ] && exit 0    # never loop
# Caller check half 1: subagents inherit the lead session id, so the sidecar
# below cannot exclude them -- exclude on the payload's own identity fields.
case "$(printf '%s\n' "$FIELDS" | sed -n 4p)" in ""|main|lead|root|primary) ;; *) exit 0 ;; esac
[ -n "$(printf '%s\n' "$FIELDS" | sed -n 5p)" ] && exit 0   # subagent: not the lead

# --- run-state discovery (marker-authoritative) -----------------------------
# The lead writes .vllm-neuron-parity-run at run start: one line holding the
# absolute path of the live run-state.json. A scan hit is NOT ownership
# evidence -- a newest-by-mtime match may belong to an abandoned run or another
# session, so this hook acts only on a marker hit. The marker's directory is
# the project root; the revision folder sits at
# <project-root>/.vllm-neuron-parity/evolution.
FOUND_STATE=""
FOUND_VIA=""
PROJECT_ROOT=""
# DECISIONS §1052/§1053 (2026-09-14): try each seed root and then every parent
# up to / -- a seat whose cwd is a campaign subdirectory must still find the
# marker at the project root.
for seed in "${CODEX_PROJECT_DIR:-}" "${CLAUDE_PROJECT_DIR:-}" "$PWD"; do
  [ -n "$seed" ] || continue
  root="$seed"
  while :; do
    marker="$root/.vllm-neuron-parity-run"
    if [ -f "$marker" ]; then
      candidate="$(head -n 1 "$marker" 2>/dev/null | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      if [ -n "$candidate" ] && [ -f "$candidate" ]; then
        FOUND_STATE="$candidate"; FOUND_VIA="marker"; PROJECT_ROOT="$root"; break 2
      fi
    fi
    parent="$(dirname "$root")"
    [ "$parent" != "$root" ] || break
    root="$parent"
  done
done
[ "$FOUND_VIA" = "marker" ] || exit 0

# --- lead-session caller check ---------------------------------------------
# This pair is lead-only, but every full session in the project (teammates,
# scratch sessions) fires the same events. The lead records its session id in
# the sidecar <run-state>.lead-session (one line); when the sidecar exists and
# names a different session, stay silent. No sidecar = fail open (pre-gate
# behavior) so a run without one keeps its coverage.
LEAD_FILE="${FOUND_STATE}.lead-session"
if [ -f "$LEAD_FILE" ]; then
  LEAD_ID="$(head -n 1 "$LEAD_FILE" 2>/dev/null | tr -d '[:space:]')"
  if [ -n "$LEAD_ID" ] && [ "$SESSION_ID" != "$LEAD_ID" ]; then
    exit 0
  fi
fi

STOP_EVERY="${VLLM_NEURON_PARITY_STOP_EVERY:-3}"
case "$STOP_EVERY" in *[!0-9]*|''|0|1) STOP_EVERY=3 ;; esac  # stop limit needs >=2
COOLDOWN="${TMPDIR:-/tmp}/vllm-neuron-parity-stop-nudged-${SESSION_ID}"
if [ -f "$COOLDOWN" ]; then
  LEFT="$(head -n 1 "$COOLDOWN" 2>/dev/null)"
  case "$LEFT" in *[!0-9]*|'') LEFT=1 ;; esac
  # a marker is worth at most N-1 silent stops whatever it holds: the file is lead-writable
  [ "$LEFT" -gt $((STOP_EVERY - 1)) ] && LEFT=$((STOP_EVERY - 1))
  if [ "$LEFT" -gt 1 ]; then
    printf '%s\n' "$((LEFT - 1))" > "$COOLDOWN" 2>/dev/null || true
  else
    rm -f "$COOLDOWN"
  fi
  exit 0
fi

# Audit thresholds (env-tunable; defaults from the audit design).
AUDIT_EVERY="${VLLM_NEURON_PARITY_AUDIT_EVERY:-40}"
case "$AUDIT_EVERY" in *[!0-9]*|''|0) AUDIT_EVERY=40 ;; esac
AUDIT_BYTES="${VLLM_NEURON_PARITY_AUDIT_BYTES:-209715200}"
case "$AUDIT_BYTES" in *[!0-9]*|''|0) AUDIT_BYTES=209715200 ;; esac
AUDIT_STALL="${VLLM_NEURON_PARITY_AUDIT_STALL:-10}"
case "$AUDIT_STALL" in *[!0-9]*|''|0) AUDIT_STALL=10 ;; esac
# Timestamps come from the clock, never typed: both read once, here.
NOW_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
NOW_STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

BLOCK="$("$PY" - "$FOUND_STATE" "$PROJECT_ROOT" "$PLUGIN_ROOT" "$PY" \
    "$AUDIT_EVERY" "$AUDIT_BYTES" "$AUDIT_STALL" "$STOP_EVERY" "$NOW_ISO" "$NOW_STAMP" <<'PYEOF' 2>/dev/null
import json, os, re, subprocess, sys, time
(path, project_root, plugin_root, py, audit_every, audit_bytes, audit_stall,
 stop_every, now_iso, now_stamp) = sys.argv[1:11]
audit_every, audit_bytes, audit_stall = int(audit_every), int(audit_bytes), int(audit_stall)
try:
    state = json.load(open(path, encoding="utf-8"))
except Exception:
    sys.exit(0)          # unparsable state is validate_run_state.py business
if not isinstance(state, dict):
    sys.exit(0)
terminal = state.get("final_status")
decided = bool(terminal.get("status") or terminal.get("classification")) \
    if isinstance(terminal, dict) else bool(terminal)
if decided:
    print("TERMINAL"); sys.exit(0)


def label(entry, *keys):
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        for k in keys:
            if entry.get(k):
                return str(entry[k])
    return ""


def age_of(when):
    """Minutes since an ISO-8601 UTC time stamp, or None when unreadable."""
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d)(?::(\d\d))?", str(when or ""))
    if not m:
        return None
    y, mo, d, h, mi, s = (int(x or 0) for x in m.groups())
    try:
        import calendar
        then = calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))
    except Exception:
        return None
    return max(0, int((time.time() - then) / 60))


# --- seat lines --------------------------------------------------------------
seats = []
for e in (state.get("active_node_runs") or []):
    node = label(e, "node", "node_id", "name")
    seat = label(e, "seat", "role") or node or "<unnamed seat>"
    started = label(e, "started")
    inst = label(e, "campaign", "instance", "target")
    age = age_of(started)
    where = "%s[%s]" % (node, inst) if inst else (node or "<node?>")
    when = ("%d min" % age) if age is not None else (started or "start unrecorded")
    seats.append("  %s on %s, running %s" % (seat, where, when))
done = list(state.get("completed_outcomes") or [])
last = done[-1] if done else None
last_txt = "<none>"
if last is not None:
    node = label(last, "node", "node_id", "name")
    outcome = label(last, "outcome", "result")
    last_txt = ("%s.%s" % (node or "<node?>", outcome or "<outcome?>")
                if (node or outcome) else str(last)[:80])
identity = state.get("workflow_identity") if isinstance(state.get("workflow_identity"), dict) else {}
run_id = label(identity, "run_id", "run_identity", "id") or "<unset>"
age_min = int((time.time() - os.path.getmtime(path)) / 60)

# --- audit check ------------------------------------------------------------
evo = os.path.join(project_root, ".vllm-neuron-parity", "evolution")
sidecar_path = path + ".audit-checkpoint.json"
audit_lines = []      # text placed above the seat lines
brief = None          # when set, the block text is the brief alone


def read_text(p, limit=None):
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit) if limit else fh.read()
    except Exception:
        return ""


def declared_nodes(graph_path):
    """Node ids of the graph: keys of the nodes mapping, or dash-id items."""
    ids = set()
    text = read_text(graph_path)
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)nodes:\s*$", line)
        if not m:
            continue
        base = len(m.group(1))
        for nxt in lines[i + 1:]:
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue
            indent = len(nxt) - len(nxt.lstrip())
            if indent <= base:
                break
            k = re.match(r"^\s*([A-Za-z_][\w.-]*):\s*$", nxt)
            if k and indent == base + 2:
                ids.add(k.group(1))
            k = re.match(r"^\s*-\s*id:\s*[\x27\"]?([\w.-]+)", nxt)
            if k:
                ids.add(k.group(1))
        break
    if not ids:
        ids.update(re.findall(r"^\s*-\s*id:\s*[\x27\"]?([\w.-]+)", text, re.M))
    return ids


def load_sidecar():
    try:
        doc = json.load(open(sidecar_path, encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except Exception:
        return None


def write_sidecar(doc):
    tmp = sidecar_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, sidecar_path)


def write_log_bytes(since_at):
    """(total, since): current sizes of distinct logged paths; no directory scan."""
    seen_total, seen_since = {}, {}
    for gen in (path + ".write-log.1.jsonl", path + ".write-log.jsonl"):
        try:
            fh = open(gen, encoding="utf-8", errors="replace")
        except Exception:
            continue
        with fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                p = rec.get("path") if isinstance(rec, dict) else None
                if not isinstance(p, str) or not p:
                    continue
                if rec.get("written") is False:      # a Bash mention of an old file: a read
                    continue
                if p not in seen_total:
                    try:
                        seen_total[p] = os.path.getsize(p)
                    except OSError:
                        seen_total[p] = 0
                if (since_at is None or str(rec.get("at") or "") >= since_at) and p not in seen_since:
                    seen_since[p] = seen_total[p]
    return sum(seen_total.values()), sum(seen_since.values())


def revision_log_updater_close(revision_log_path, at, side):
    """applied_at of a graph|run-setup entry newer than the checkpoint hook_record at, drafted by the updater, whose
    patch bytes equal a proposal THIS sidecar hook_recorded (size and mtime unchanged since the
    hook_record). Any such entry counts - a later pin never hides it. No yaml dependency."""
    text = read_text(revision_log_path)
    blocks = re.split(r"(?m)^-\s+revision:", text)
    hook_records = [x for x in (side.get("hook_recorded_writes") or []) if isinstance(x, dict)]
    if len(blocks) < 2 or not at or not hook_records:
        return ""

    def field(block, name):
        m = re.search(r"(?m)^\s+%s:\s*[\x27\"]?([^\x27\"\n]+)" % name, block)
        return m.group(1).strip() if m else ""

    for block in reversed(blocks[1:]):
        applied = field(block, "applied_at")
        if field(block, "kind") not in ("graph", "run_setup") or not applied or applied <= at:
            continue
        if field(block, "written_by") != "workflow-updater":
            continue
        patch = field(block, "patch")
        try:
            patch_bytes = open(os.path.join(evo, patch), "rb").read() if patch else None
        except OSError:
            patch_bytes = None
        if patch_bytes is None:
            continue
        for st in hook_records:
            try:
                p = str(st.get("path"))
                info = os.stat(p)
                if info.st_size == int(st.get("size")) and abs(info.st_mtime - float(st.get("mtime"))) < 1e-6 \
                        and open(p, "rb").read() == patch_bytes:
                    return applied
            except (OSError, TypeError, ValueError):
                continue
    return ""


def trend_rows(text):
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) == 4 and re.match(r"^cp-\w+$", parts[0]):
            try:
                rows.append((parts[0], float(parts[3])))
            except ValueError:
                rows.append((parts[0], None))
    return rows


def findings_close(findings_path, checkpoint_id, side):
    """A no-change close: the router hook_recorded the updater findings record (state OPEN,
    findings_record set), the record names this checkpoint and says no_change_needed,
    and, when the trend rose, the router hook_recorded the update-reviewer line review: PASS.
    Text the lead could type never closes on its own."""
    if str(side.get("state") or "").upper() != "OPEN":
        return False
    hook_recorded = side.get("findings_record")
    if not hook_recorded or os.path.realpath(str(hook_recorded)) != os.path.realpath(findings_path):
        return False
    stat = side.get("findings_stat") or {}
    try:                                         # the bytes the updater or reviewer left, untouched since
        cur = os.stat(findings_path)
    except OSError:
        return False
    if cur.st_size != stat.get("size") or cur.st_mtime != stat.get("mtime"):
        return False
    text = read_text(findings_path)
    first = text.splitlines()[0].strip() if text.strip() else ""
    if not re.match(r"^checkpoint:\s*%s\s*$" % re.escape(checkpoint_id or "<none>"), first):
        return False
    if "no_change_needed" not in text:
        return False
    rows = trend_rows(text)
    for i, (cid, value) in enumerate(rows):
        if cid == checkpoint_id:
            if i == 0:
                return True                      # first checkpoint: nothing to compare against
            prev = rows[i - 1][1]
            if value is not None and prev is not None and value <= prev:
                return True                      # the trend fell or held: no reviewer needed
            return side.get("review") == "PASS"  # rose, or unreadable: the reviewer decides
    return side.get("review") == "PASS"          # no Trend row for this checkpoint: same


def pending_approval(proposals_dir, side):
    """The newest proposal awaits the user: the router hook_recorded it as the updater wrote it
    (path, size, mtime unchanged) and it carries pending. A file anyone
    else drops in proposals/ exempts nothing."""
    try:
        names = os.listdir(proposals_dir)
    except OSError:
        return False
    newest, newest_m = None, -1
    for n in names:
        p = os.path.join(proposals_dir, n)
        try:
            m = os.path.getmtime(p)
        except OSError:
            continue
        if os.path.isfile(p) and m > newest_m:
            newest, newest_m = p, m
    if not newest:
        return False
    try:
        st = os.stat(newest)
    except OSError:
        return False
    hook_recorded = any(isinstance(x, dict) and os.path.realpath(str(x.get("path") or "")) == os.path.realpath(newest)
                  and x.get("size") == st.st_size and x.get("mtime") == st.st_mtime
                  for x in (side.get("hook_recorded_writes") or []))
    return hook_recorded and "user_only_changes: pending" in read_text(newest, 65536)


pave_init_root = ""


def pave_init_version():
    """Newest pave-init in the plugin cache, as a tuple; None when none is found.
    Records that installation directory in pave_init_root for the DUE brief."""
    global pave_init_root
    import glob
    candidates = []
    override = os.environ.get("VLLM_NEURON_PARITY_PAVE_INIT_ROOT")
    if override:
        candidates.append(override)
    cache = os.path.dirname(os.path.dirname(os.path.abspath(plugin_root)))   # <cache>/<marketplace>
    candidates += sorted(glob.glob(os.path.join(cache, "pave-init", "*")))
    for harness in ("~/.claude", "~/.codex"):
        candidates += sorted(glob.glob(os.path.expanduser(harness + "/plugins/cache/*/pave-init/*")))
    best = None
    for d in candidates:
        for rel in ("VERSION", os.path.join("skills", "pave-init", "VERSION")):
            m = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", read_text(os.path.join(d, rel), 4096))
            if m:
                v = tuple(int(x) for x in m.groups())
                if best is None or v > best:
                    best, pave_init_root = v, d
                break
    return best


def write_report(since_flag):
    """The write_report summary via the shipped script, else raw counts."""
    script = os.path.join(plugin_root, "scripts", "run_write_report.py")
    if os.path.isfile(script):
        cmd = [py, script, "--state", path, "--revision-folder", evo]
        import glob
        # the layout-declared decision record: <workspace-root>/campaigns/<name>/approvals/DECISIONS.md
        decisions = sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(path)),
                                                  "campaigns", "*", "approvals", "DECISIONS.md")))
        if decisions:
            cmd += ["--decisions", decisions[0]]
        if since_flag:
            cmd.append("--since-checkpoint")
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    return None


def run_audit():
    global brief
    if not os.path.isdir(evo):
        return                                   # silent: no revision folder
    # Rule-1 route: a graph applied since the pin, while no decline stands.
    rev, digest = identity.get("active_revision"), identity.get("bundle_digest")
    tool = os.path.join(plugin_root, "scripts", "record_revision.py")
    if rev is not None and digest and not identity.get("move_declined") and os.path.isfile(tool):
        try:
            out = subprocess.run([py, tool, "verify", evo, "--pinned-revision", str(rev),
                                  "--pinned-digest", str(digest)],
                                 capture_output=True, text=True, timeout=10)
            if "graph applied since pin" in (out.stdout + out.stderr):
                audit_lines.append(
                    "GRAPH APPLIED SINCE PIN (rule 1 of the revision rules): %s. Read the"
                    " semantic_diff of the newer revision, then move the run to it or record the"
                    " decline of the user verbatim in workflow_identity.move_declined; do not stop"
                    " before one of the two is in run state." % (out.stdout.strip().splitlines() or ["route"])[-1])
        except Exception:
            pass
    version = pave_init_version()
    if version is None:
        audit_lines.append(
            "AUDIT DEGRADED: no pave-init installation found (probed VLLM_NEURON_PARITY_PAVE_INIT_ROOT,"
            " the plugin cache beside this plugin, and the ~/.claude and ~/.codex plugin caches);"
            " the audit stays off until pave-init 2.6.2 or newer is installed or"
            " that variable names its root.")
        return
    if version < (2, 6, 2):
        audit_lines.append(
            "AUDIT DEGRADED: pave-init %s is older than 2.6.2 (no audit mode); the audit"
            " audit stays off until the plugin cache holds 2.6.2 or newer."
            % ".".join(map(str, version)))
        return
    nodes = declared_nodes(os.path.join(evo, "workflow.pave.yaml"))
    total_outcomes = sum(1 for o in done if isinstance(o, dict) and o.get("node") in nodes)
    side = load_sidecar()
    if side is not None:
        # A future hook_record is a planted file (the hook never writes one): reseed. A
        # baseline above the run count is planted OR the declared node set shrank
        # (a graph write); with no cycle open there is nothing to lose, so reseed;
        # with a DUE or OPEN cycle keep it, clamp the count in memory and say so.
        try:
            future = str(side.get("at") or "") > now_iso
            over = int(side.get("outcomes_at") or 0) > total_outcomes
        except (TypeError, ValueError):
            future, over = True, False
        cycle_open = str(side.get("state") or "").upper() in ("DUE", "OPEN")
        if future or (over and not cycle_open):
            side = None
        elif over:
            audit_lines.append(
                "AUDIT COUNTER DISAGREEMENT: the sidecar baseline (%d) exceeds the run\x27s"
                " declared-node outcomes (%d); checkpoint %s stays %s - check the live graph\x27s"
                " node ids and run state." % (int(side.get("outcomes_at") or 0), total_outcomes,
                                              side.get("checkpoint_id"), side.get("state")))
            side["outcomes_at"] = total_outcomes
    if side is None:
        # First sight of this run: seed the baseline and pass. The first checkpoint
        # covers the next window, never the run history against an empty write log.
        total_bytes, _ = write_log_bytes(None)
        side = {"checkpoint_id": None, "at": now_iso, "outcomes_at": total_outcomes,
                "bytes_at": total_bytes, "state": "IDLE", "findings_record": None,
                "hook_recorded_writes": [], "review": None, "closed_by": "seeded"}
        write_sidecar(side)
        return
    at = side.get("at")
    total_bytes, since_bytes = write_log_bytes(at)
    outcomes_since = total_outcomes - int(side.get("outcomes_at") or 0)
    st = str(side.get("state") or "IDLE").upper()
    cp = side.get("checkpoint_id")

    def reset(closed_by):
        side.update({"state": "IDLE", "at": now_iso, "outcomes_at": total_outcomes,
                     "bytes_at": total_bytes, "checkpoint_id": None, "findings_record": None,
                     "hook_recorded_writes": [], "review": None, "closed_by": closed_by})
        write_sidecar(side)
        audit_lines.append("AUDIT CLOSED: checkpoint %s closed by %s; counters reset." % (cp, closed_by))

    if st in ("DUE", "OPEN"):
        applied = revision_log_updater_close(os.path.join(evo, "revisions.yaml"), at, side)
        if applied:
            reset("revision_log:%s" % applied)
            return
        if findings_close(os.path.join(evo, "audit-findings.md"), cp, side):
            reset("findings:no_change_needed")
            return
    if st == "OPEN":
        if outcomes_since >= audit_stall and not pending_approval(os.path.join(evo, "proposals"), side):
            audit_lines.append(
                "AUDIT STALL: checkpoint %s has been open for %d declared-node outcomes (limit %d)"
                " with no applied updater proposal and no no-change close. Apply the reviewed"
                " proposal with: record_revision.py apply <root> <N> --approval ... --review ..."
                " --proposal <revision-folder>/proposals/%s-<run-setup|graph>.patch --hook-records %s"
                " (the hook record is what closes it), or have the updater record the no-change"
                " close, before more outcomes." % (cp, outcomes_since, audit_stall, cp, sidecar_path))
        return
    if st == "DUE":
        pass                                     # re-present the same brief below
    elif outcomes_since >= audit_every or since_bytes > audit_bytes:
        summary = write_report(side.get("checkpoint_id") is not None)
        cp = "cp-" + now_stamp
        side.update({"checkpoint_id": cp, "at": now_iso, "outcomes_at": total_outcomes,
                     "bytes_at": total_bytes, "state": "DUE", "findings_record": None,
                     "hook_recorded_writes": [], "review": None, "closed_by": None})
        report_path = path + ".audit-write-report-" + cp + ".txt"
        body = summary if summary is not None else (
            "write report unavailable (script absent, failed, or timed out); raw counts: %d declared-node outcomes and %d bytes written"
            " since the last checkpoint (%s)" % (outcomes_since, since_bytes, at or "run start"))
        try:
            with open(report_path, "w", encoding="utf-8") as fh:
                fh.write(body + "\n")
        except Exception:
            report_path = "<inline>"
        write_sidecar(side)
        side["_report_path"], side["_report_body"] = report_path, body
    else:
        return
    report_path = side.get("_report_path") or path + ".audit-write-report-" + str(cp) + ".txt"
    body = side.get("_report_body") or read_text(report_path).strip() or (
        "raw counts: %d declared-node outcomes and %d bytes written since the checkpoint"
        % (outcomes_since, since_bytes))
    brief = "\n".join([
        "AUDIT DUE: checkpoint %s (sidecar %s)." % (cp, sidecar_path),
        "write_report: %s" % report_path,
        "revision folder: %s" % evo,
        "pave-init root: %s" % (pave_init_root or "<plugin cache>"),
        body,
        "Forward this block verbatim to pave-init:workflow-updater in audit mode (checkpoint id,"
        " write report path, revision folder, pave-init root); add nothing you compose.",
    ])


try:
    run_audit()
except Exception:
    pass                                         # the audit never takes the stop check down

print("ACTIVE")
print("%s Active run %s (state %s): last recorded outcome %s, run state last written"
      " %d min ago." % ("[stop-guard]", run_id, path, last_txt, age_min))
for line in audit_lines:
    print(line)
if brief:
    print(brief)
    sys.exit(0)
print("You decided to stop. Reply with one line per active seat, in this form:")
print("  <seat>: waits on <grant | frozen value | design change | seat working | nothing>;"
      " next act: <...>")
print("\"nothing\" means act before you stop. Active seats:")
for s in seats or ["  <none recorded>"]:
    print(s)
print("Then, before you stop again:")
print("  Emit only a declared outcome and traverse only a declared edge (P12); propose a graph"
      " change for anything else.")
print("  Write every fact routing depends on to run state now; you are its single writer (P10).")
print("  Retire every idle seat now.")
print("  Restate every codename in your last reply in plain words.")
print("  Send landed work no review has seen to a batch review before the next round builds on it.")
print("The next %d stops pass before this fires again." % (int(stop_every) - 1))
PYEOF
)" || exit 0
[ "$(printf '%s\n' "$BLOCK" | sed -n 1p)" = "ACTIVE" ] || exit 0

printf '%s\n' "$((STOP_EVERY - 1))" > "$COOLDOWN" 2>/dev/null || true
printf '%s\n' "$BLOCK" | sed 1d >&2
exit 2
