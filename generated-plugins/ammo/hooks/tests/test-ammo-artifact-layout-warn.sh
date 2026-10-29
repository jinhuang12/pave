#!/bin/bash
# Test harness for ammo-artifact-layout-warn.sh (PostToolUse, matcher=Write|Edit|Bash)
# Run: bash hooks/tests/test-ammo-artifact-layout-warn.sh
#
# The hook is ADVISORY: it always exits 0 and signals only through
# hookSpecificOutput.additionalContext ("LAYOUT WARN: ..."). So every case here
# asserts on STDOUT presence/absence, not on the exit code.
#
# It derives its allowlist from scripts/artifact_layout.json. Three
# properties matter and each has cases below:
#   1. every canonical slot in the manifest is SILENT (the four regressions that
#      motivated the manifest — evidence.json, validation_summary.json,
#      diff.patch, post_ship_profiling/ — are pinned explicitly),
#   2. the doc's Prohibited Patterns still WARN,
#   3. a missing or corrupt manifest (or one without the campaign_root key)
#      disables checking LOUDLY on stderr and never degrades to an empty
#      allowlist that warns on everything.
#
# ENG is an engine root: .ammo/active names campaign tgt, so the campaign dir
# ART is ENG/.ammo/tgt. The hook classifies a path by containment in the
# campaign dir; outside a campaign it is silent.
set -uo pipefail

# Hermetic: a live campaign's names must not leak into the fixtures.
for _v in $(compgen -e | grep '^AMMO_' | grep -v '^AMMO_TEST_HOOK$' || true); do unset "$_v"; done
unset CLAUDE_PROJECT_DIR

HOOK="${AMMO_TEST_HOOK:-$(cd "$(dirname "$0")/.." && pwd)/ammo-artifact-layout-warn.sh}"
PLUGIN="$(cd "$(dirname "$HOOK")/.." && pwd)"
MANIFEST="$PLUGIN/scripts/artifact_layout.json"
PASS=0
FAIL=0
TOTAL=0

TMPDIR=$(mktemp -d)
NOCAMP=$(mktemp -d)
cleanup() { rm -rf "$TMPDIR" "$NOCAMP" 2>/dev/null || true; }
trap cleanup EXIT
ENG="$TMPDIR/w"
ART="$ENG/.ammo/tgt"
mkdir -p "$ART"
printf 'tgt\n' > "$ENG/.ammo/active"
cd "$ENG"

# run_case: $1 label  $2 silent|warn  $3 payload JSON
run_case() {
    local label="$1" want="$2" payload="$3"
    local rc=0
    TOTAL=$((TOTAL + 1))
    printf '%s' "$payload" | bash "$HOOK" \
        >"$TMPDIR/out" 2>"$TMPDIR/err" || rc=$?

    local pass=true
    [ "$rc" -ne 0 ] && pass=false          # advisory: always exit 0
    if grep -q "LAYOUT WARN:" "$TMPDIR/out" 2>/dev/null; then
        [ "$want" = "warn" ] || pass=false
    else
        [ "$want" = "silent" ] || pass=false
    fi

    if $pass; then
        echo "  PASS [$TOTAL]: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $label (want $want, exit $rc)"
        echo "        stdout: $(head -c 200 "$TMPDIR/out")"
        FAIL=$((FAIL + 1))
    fi
}

write_case() { run_case "$1" "$2" "$(jq -n -c --arg p "$ART/$3" --arg cwd "$ENG" \
    '{tool_name:"Write",cwd:$cwd,tool_input:{file_path:$p}}')"; }

bash_case() { run_case "$1" "$2" "$(jq -n -c --arg c "$3" --arg cwd "$ENG" \
    '{tool_name:"Bash",cwd:$cwd,tool_input:{command:$c}}')"; }

# ══════════════════════════════════════════════
echo "== The four slots the hand-written allowlist warned on =="
# ══════════════════════════════════════════════
write_case "evidence.json is canonical" silent "rounds/1/tracks/op-001/evidence.json"
write_case "per-track validation_summary.json is canonical" silent "rounds/1/tracks/op-001/validation_summary.json"
write_case "diff.patch is canonical" silent "rounds/1/tracks/OP-001/diff.patch"
write_case "post_ship_profiling slot is canonical" silent "rounds/1/sweeps/post_ship_profiling/json/x.json"

# ══════════════════════════════════════════════
echo ""; echo "== Rest of the canonical surface =="
# ══════════════════════════════════════════════
write_case "state.json at root" silent "state.json"
write_case "target.json at root" silent "target.json"
write_case "REPORT.md at root" silent "REPORT.md"
write_case "report_assets subtree" silent "report_assets/fig1.png"
write_case "blockers are cross-round" silent "blockers/stage_5_2026-05-05.md"
write_case "round constraints" silent "rounds/3/constraints.md"
write_case "cohort gate report" silent "rounds/3/validation_gate_report.json"
write_case "nsys traces" silent "rounds/1/profiling/nsys/baseline_bs8.nsys-rep"
write_case "neuron profiling export" silent "rounds/1/profiling/neuron/cc_ops_field_report.json"
write_case "neuron NTFF capture in a subdir" silent "rounds/1/profiling/neuron/rank0/decode.ntff"
write_case "opt sweep with op_id" silent "rounds/1/sweeps/opt/op007/e2e_latency_results.json"
write_case "opt_correctness sweep" silent "rounds/1/sweeps/opt_correctness/op007/json/correctness_verdict.json"
write_case "mined.json" silent "rounds/1/mining/mined.json"
write_case "mine_config.json" silent "rounds/1/mining/mine_config.json"
write_case "debate sub-round argument" silent "rounds/1/debate/round_2/op007_argument.md"
write_case "runtime_pkg.patch" silent "rounds/1/tracks/op-001/runtime_pkg.patch"
write_case "monitor audits under the track" silent "rounds/1/tracks/op-001/monitor_audits/m1_observations.md"
write_case "track scratch" silent "rounds/1/tracks/op-001/_scratch/draft.md"
write_case "audit verdict" silent "rounds/1/audits/stage_45.md"
write_case "audit re-audit cycle" silent "rounds/12/audits/stage_45_cycle_2.md"
write_case "early per-track S45 evidence" silent "rounds/1/audits/stage_45_partial_op007.md"
write_case "archive keeps its timestamp" silent "rounds/1/_archive/baseline_2026-05-05T181212Z/x.json"
write_case "two-digit round number" silent "rounds/12/sweeps/baseline/e2e_latency_results.json"

# ══════════════════════════════════════════════
echo ""; echo "== Prohibited Patterns still warn =="
# ══════════════════════════════════════════════
write_case "monitor log at campaign root" warn "monitor_log_champion_1.md"
write_case "DRAFT verdict at track root" warn "rounds/1/tracks/op-001/validation_results_DRAFT.md"
write_case "ad-hoc e2e_latency_opt dir" warn "e2e_latency_opt3/x.json"
write_case "removed investigation/ dir" warn "investigation/notes.md"
write_case "timestamped ACTIVE slot" warn "rounds/1/sweeps/baseline_2026-05-05T181212Z/x.json"
write_case "bottleneck_analysis at root" warn "bottleneck_analysis.md"
write_case "timestamped profiling tool dir" warn "rounds/1/profiling/neuron_2026-09-24/x.json"
# KNOWN GAP, pre-dates the manifest: a sweep slot is a directory PREFIX, so
# anything under it conforms and prohibited pattern #9 (nsys traces inside a
# sweep output dir) is not detected here. The hand-written allowlist had the
# same prefix semantics. The sweep script is the real owner — it writes traces
# to the profiling/ sibling itself (_v2_profiling_dir), so an agent cannot
# reach this path through the sanctioned tool.
write_case "nsys inside a sweep dir is NOT detected (documented gap)" silent \
    "rounds/1/sweeps/baseline/nsys/t.nsys-rep"
write_case "debate nesting by campaign round" warn "rounds/1/debate/campaign_round_2/x.md"
write_case "audits keyed by round at root" warn "audits/stage_45_round_2.md"

# ══════════════════════════════════════════════
echo ""; echo "== Directory tokens are references, not drift =="
# ══════════════════════════════════════════════
# The Bash extractor yields bare directory tokens from ordinary ls/grep, and a
# proper ancestor of a canonical slot must stay silent.
bash_case "ls a round dir" silent "ls $ART/rounds/12"
bash_case "ls the sweeps dir" silent "ls $ART/rounds/1/sweeps"
bash_case "ls the opt slot parent" silent "ls $ART/rounds/1/sweeps/opt"
bash_case "ls the tracks dir" silent "ls $ART/rounds/1/tracks"
bash_case "ls the neuron profiling dir" silent "ls $ART/rounds/1/profiling/neuron/"
bash_case "engine-relative token (no leading slash)" silent "cat .ammo/tgt/rounds/1/mining/mined.json"
bash_case "engine-relative drift token" warn "mkdir -p .ammo/tgt/monitoring"
bash_case "bash drift is still caught" warn "mkdir -p $ART/monitoring/logs"
bash_case '$AMMO_ARTIFACT_DIR token, canonical' silent 'ls "$AMMO_ARTIFACT_DIR/rounds/1/sweeps"'
bash_case '${AMMO_ARTIFACT_DIR} token, drift' warn 'mkdir -p ${AMMO_ARTIFACT_DIR}/monitoring/logs'
# The same ancestor rule through a command that writes, so it does not rest
# on the read-only skip below.
bash_case "mkdir of a round dir" silent "mkdir -p $ART/rounds/12"
bash_case "mkdir of the opt slot parent" silent "mkdir -p $ART/rounds/1/sweeps/opt"
bash_case "cp into a canonical slot" silent "cp /tmp/m.json .ammo/tgt/rounds/1/mining/mined.json"

# ══════════════════════════════════════════════
echo ""; echo "== Read-only commands land no file =="
# ══════════════════════════════════════════════
# A read lands nothing, so it never warns. The read/write judgement is the
# shared classifier's (scripts/hook_cmd_classify.py --mode readonly): every
# segment inspection-class, no redirection, no command substitution. A live
# lead's `ls rounds/1/profiling/*` warned "outside the canonical layout".
bash_case "ls with a glob under a canonical dir" silent "ls $ART/rounds/1/profiling/*"
bash_case "ls | head with a glob" silent "ls $ART/rounds/1/profiling/* | head -5"
bash_case "cat of a drift-shaped path (a read)" silent "cat $ART/monitoring/logs/x.md"
bash_case "grep in a drift-shaped dir (a read)" silent "grep -r TODO .ammo/tgt/investigation"
bash_case "a write after a read still warns" warn "ls $ART/rounds/1 && mkdir -p $ART/monitoring/logs"
bash_case "a redirect into the campaign still warns" warn "cat /tmp/x > $ART/monitor_log_x.md"
bash_case "tee into the campaign still warns" warn "echo hi | tee $ART/monitor_log_x.md"

# ══════════════════════════════════════════════
echo ""; echo "== One JSON object per call; glob tokens are not paths =="
# ══════════════════════════════════════════════
# A live ammo-delegate command flagged three glob tokens and the hook printed
# three JSON objects, one per path. Claude Code read the whole stdout as one
# JSON value, rejected it ("Hook output looks like a JSON object but is not
# valid JSON"), and dropped every warning.
# json_case LABEL PAYLOAD N: stdout is exactly one valid JSON object whose
# additionalContext names N flagged paths (N=0: stdout is empty).
json_case() {
    local label="$1" payload="$2" want_n="$3" rc=0 pass=true got_n
    TOTAL=$((TOTAL + 1))
    printf '%s' "$payload" | bash "$HOOK" >"$TMPDIR/out" 2>"$TMPDIR/err" || rc=$?
    [ "$rc" -ne 0 ] && pass=false
    if [ "$want_n" = 0 ]; then
        [ -s "$TMPDIR/out" ] && pass=false
    else
        python3 -c 'import json,sys; json.load(sys.stdin)' <"$TMPDIR/out" 2>/dev/null || pass=false
        got_n=$(jq -r '.hookSpecificOutput.additionalContext' "$TMPDIR/out" 2>/dev/null | grep -c 'LAYOUT WARN:' || true)
        [ "$got_n" = "$want_n" ] || pass=false
    fi
    if $pass; then
        echo "  PASS [$TOTAL]: $label"; PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $label (want $want_n warnings in one JSON object, exit $rc)"
        echo "        stdout: $(head -c 300 "$TMPDIR/out")"
        FAIL=$((FAIL + 1))
    fi
}
bash_json() { jq -n -c --arg c "$1" --arg cwd "$ENG" '{tool_name:"Bash",cwd:$cwd,tool_input:{command:$c}}'; }

json_case "two flagged paths: one valid JSON object, both named" \
    "$(bash_json "mkdir -p $ART/monitoring/logs $ART/investigation/notes")" 2
json_case "three flagged paths across segments: one valid JSON object" \
    "$(bash_json "mkdir -p $ART/monitoring && touch $ART/monitor_log_x.md; cp /tmp/a $ART/e2e_latency_opt3/a.json")" 3
json_case "a flagged path next to a canonical one: one object, one warning" \
    "$(bash_json "mkdir -p $ART/rounds/1/sweeps/baseline $ART/monitoring")" 1

# The exact live command (ammo-delegate, campaign path replaced by ART).
WORKER11_CMD="find $ART/rounds/1/sweeps/integration* -maxdepth 2 2>/dev/null
echo \"---\"
find $ART/rounds/1/sweeps/opt* -maxdepth 2 2>/dev/null
echo \"---status files---\"
for f in $ART/rounds/1/sweeps/*/status/*.json; do echo \"== \$f ==\"; cat \"\$f\" 2>/dev/null | python3 -c \"import json,sys; d=json.load(sys.stdin); print(d if isinstance(d,dict) else d)\" 2>/dev/null | head -5; done"
json_case "the live find/for glob command: no output" "$(bash_json "$WORKER11_CMD")" 0
json_case "a glob in a write command is skipped" "$(bash_json "rm -f $ART/rounds/1/sweeps/*/status/*.tmp $ART/monitor_log_?.md")" 0
json_case "a [class] glob is skipped" "$(bash_json "rm -f $ART/rounds/[0-9]/scratch_*.md")" 0
json_case "a glob does not hide a literal drift path" \
    "$(bash_json "rm -f $ART/rounds/1/sweeps/*/x.tmp && mkdir -p $ART/monitoring/logs")" 1

# ══════════════════════════════════════════════
echo ""; echo "== Placeholder regex forms: (...) and (?:...) =="
# ══════════════════════════════════════════════
# The hook matches in python re, which reads both group forms. It fed them to
# grep -E, which printed "grep: warning: ? at start of expression" on every
# Bash call for a (?:...) fragment (GNU grep) or never matched it (BSD grep).
# Each case also asserts an empty stderr.
# form_case LABEL silent|warn HOOK PAYLOAD
form_case() {
    local label="$1" want="$2" hook="$3" payload="$4" rc=0 pass=true
    TOTAL=$((TOTAL + 1))
    printf '%s' "$payload" | bash "$hook" >"$TMPDIR/out" 2>"$TMPDIR/err" || rc=$?
    [ "$rc" -ne 0 ] && pass=false
    if grep -q "LAYOUT WARN:" "$TMPDIR/out" 2>/dev/null; then
        [ "$want" = "warn" ] || pass=false
    else
        [ "$want" = "silent" ] || pass=false
    fi
    [ -s "$TMPDIR/err" ] && pass=false
    if $pass; then
        echo "  PASS [$TOTAL]: $label"; PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $label (want $want, exit $rc)"
        echo "        stdout: $(head -c 200 "$TMPDIR/out")"
        echo "        stderr: $(head -c 200 "$TMPDIR/err")"
        FAIL=$((FAIL + 1))
    fi
}
REC_WRITE=$(jq -n -c --arg p "$ART/recovery/thr1/a/b_c/context.md" --arg cwd "$ENG" '{tool_name:"Write",cwd:$cwd,tool_input:{file_path:$p}}')
REC_BASH=$(jq -n -c --arg c "mkdir -p $ART/recovery/thr1/a/b_c && cp /tmp/n.md $ART/recovery/thr1/a/b_c/context.md" --arg cwd "$ENG" '{tool_name:"Bash",cwd:$cwd,tool_input:{command:$c}}')
DRIFT_BASH=$(jq -n -c --arg c "mkdir -p $ART/monitoring/logs" --arg cwd "$ENG" '{tool_name:"Bash",cwd:$cwd,tool_input:{command:$c}}')
for form in plain noncap; do
    FORM_DIR="$TMPDIR/form-$form"
    mkdir -p "$FORM_DIR"
    cp -R "$PLUGIN/hooks" "$PLUGIN/scripts" "$FORM_DIR/"
    cp "$HOOK" "$FORM_DIR/hooks/ammo-artifact-layout-warn.sh"
    if [ "$form" = plain ]; then frag='[a-z0-9_]+(/[a-z0-9_]+)*'; else frag='[a-z0-9_]+(?:/[a-z0-9_]+)*'; fi
    python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); m["placeholders"]["{task_path_without_leading_slash}"]=sys.argv[3]; json.dump(m, open(sys.argv[2],"w"))' \
        "$MANIFEST" "$FORM_DIR/scripts/artifact_layout.json" "$frag"
    FORM_HOOK="$FORM_DIR/hooks/ammo-artifact-layout-warn.sh"
    form_case "$form form: recovery note Write is canonical, clean stderr" silent "$FORM_HOOK" "$REC_WRITE"
    form_case "$form form: recovery note via Bash is canonical, clean stderr" silent "$FORM_HOOK" "$REC_BASH"
    form_case "$form form: Bash drift still warns, clean stderr" warn "$FORM_HOOK" "$DRIFT_BASH"
done

# ══════════════════════════════════════════════
echo ""; echo "== Out of scope =="
# ══════════════════════════════════════════════
run_case "path outside the campaign" silent \
    '{"tool_name":"Write","tool_input":{"file_path":"/etc/hosts"}}'
write_case "a file in .ammo itself is not in the campaign dir" silent "../state.json"
write_case "a campaign dir the marker does not name is out of scope" silent "../old/monitor_log_x.md"
run_case "no campaign: drift-shaped path → silent" silent \
    "$(jq -n -c --arg p "$NOCAMP/.ammo/x/monitor_log_x.md" --arg cwd "$NOCAMP" '{tool_name:"Write",cwd:$cwd,tool_input:{file_path:$p}}')"
run_case "unhandled tool name" silent '{"tool_name":"Read","tool_input":{"file_path":"/x"}}'
run_case "missing file_path" silent '{"tool_name":"Write","tool_input":{}}'

# ══════════════════════════════════════════════
echo ""; echo "== Manifest failure is LOUD and fails open =="
# ══════════════════════════════════════════════
# A silent empty allowlist would warn on every correct write, which is worse
# than not checking: it trains the reader to ignore the channel.
STAGE="$TMPDIR/stage"
mkdir -p "$STAGE/hooks" "$STAGE/scripts"
cp "$HOOK" "$PLUGIN/hooks/_find_campaign.sh" "$STAGE/hooks/"
cp "$PLUGIN/scripts/campaign_paths.py" "$PLUGIN/scripts/ammo" "$STAGE/scripts/"
STAGED_HOOK="$STAGE/hooks/$(basename "$HOOK")"
GOOD_PAYLOAD=$(jq -n -c --arg p "$ART/state.json" '{tool_name:"Write",tool_input:{file_path:$p}}')
DRIFT_PAYLOAD=$(jq -n -c --arg p "$ART/monitor_log_x.md" '{tool_name:"Write",tool_input:{file_path:$p}}')

check_disabled() {
    local label="$1" payload="$2"
    local rc=0
    TOTAL=$((TOTAL + 1))
    printf '%s' "$payload" | bash "$STAGED_HOOK" \
        >"$TMPDIR/out" 2>"$TMPDIR/err" || rc=$?
    local pass=true
    [ "$rc" -ne 0 ] && pass=false
    grep -q "LAYOUT WARN:" "$TMPDIR/out" 2>/dev/null && pass=false
    grep -q "LAYOUT WARN HOOK DISABLED" "$TMPDIR/err" 2>/dev/null || pass=false
    if $pass; then
        echo "  PASS [$TOTAL]: $label"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $label (exit $rc)"
        echo "        stdout: $(head -c 160 "$TMPDIR/out")"
        echo "        stderr: $(head -c 160 "$TMPDIR/err")"
        FAIL=$((FAIL + 1))
    fi
}

check_disabled "missing manifest: loud stderr, no warn on a canonical path" "$GOOD_PAYLOAD"
check_disabled "missing manifest: loud stderr, no warn on real drift either" "$DRIFT_PAYLOAD"

printf '{ not json' > "$STAGE/scripts/artifact_layout.json"
check_disabled "corrupt manifest: loud stderr, checking disabled" "$GOOD_PAYLOAD"

printf '{"placeholders":{},"slots":{}}' > "$STAGE/scripts/artifact_layout.json"
check_disabled "empty slots: loud stderr, never an empty allowlist" "$GOOD_PAYLOAD"

# The campaign root comes from the manifest key, never a literal in the hook.
python3 -c 'import json,sys; m=json.load(open(sys.argv[1])); m.pop("campaign_root"); json.dump(m, open(sys.argv[2],"w"))' \
    "$MANIFEST" "$STAGE/scripts/artifact_layout.json"
check_disabled "no campaign_root key: loud stderr, checking disabled" "$DRIFT_PAYLOAD"

# Restoring a good manifest must re-enable checking, so a disabled hook is a
# transient state and not a latch.
cp "$MANIFEST" "$STAGE/scripts/artifact_layout.json"
TOTAL=$((TOTAL + 1))
restore_rc=0
printf '%s' "$DRIFT_PAYLOAD" | bash "$STAGED_HOOK" \
    >"$TMPDIR/out" 2>"$TMPDIR/err" || restore_rc=$?
if [ "$restore_rc" -eq 0 ] && grep -q "LAYOUT WARN:" "$TMPDIR/out" 2>/dev/null; then
    echo "  PASS [$TOTAL]: restored manifest re-enables checking"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: restored manifest did not re-enable checking (exit $restore_rc)"
    FAIL=$((FAIL + 1))
fi

# ══════════════════════════════════════════════
echo ""; echo "== jq unavailable =="
# ══════════════════════════════════════════════
MIN_PATH="$TMPDIR/minbin-nojq"
mkdir -p "$MIN_PATH"
for t in bash sh cat echo env grep printf python3 dirname pwd test rm; do
    real=$(command -v "$t" 2>/dev/null || true)
    [ -n "$real" ] && ln -sf "$real" "$MIN_PATH/$t"
done
TOTAL=$((TOTAL + 1))
nojq_rc=0
printf '%s' "$DRIFT_PAYLOAD" | env PATH="$MIN_PATH" bash "$HOOK" \
    >"$TMPDIR/out" 2>/dev/null || nojq_rc=$?
if [ "$nojq_rc" -eq 0 ] && ! grep -q "LAYOUT WARN:" "$TMPDIR/out" 2>/dev/null; then
    echo "  PASS [$TOTAL]: no jq → silent fail-open"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: no jq → expected silent exit 0, got $nojq_rc"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed out of $TOTAL tests"
echo "================================"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
