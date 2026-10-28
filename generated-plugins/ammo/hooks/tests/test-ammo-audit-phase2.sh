#!/bin/bash
# Test harness for the audit-phase2 PAIR:
#   inject-audit-phase2.sh  (PostToolUse/Write) — writes the sentinel + injects
#                            the Phase 2 instructions
#   audit-phase2-guard.sh   (Stop)              — blocks the auditor from ending
#                            its turn while Phase 2 is unwritten
# Run: bash hooks/tests/test-ammo-audit-phase2.sh
#
# The pair shares ONE documented sentinel-name expression:
#   /tmp/ammo_audit_phase2_injected_${SESSION_ID}__${AGENT_ID:-noagent}__${VERDICT_BASE}
# The injector used to write that name with SINGLE underscores while the guard
# stripped only "${SESSION_ID}_", so VERDICT_BASE kept an AG9_/noagent_ prefix
# and the guard's `find -path "*/audits/${VERDICT_BASE}.md"` never matched. The
# Stop block was therefore INERT, while ammo-auditor.md called the ordering
# "mandatory and enforced by the Stop hook". Repairing it ARMS a previously-dead
# hard block, so the round-trip cases below are the safety net.
#
# Aim the harness at archived copies with AMMO_TEST_INJECT / AMMO_TEST_GUARD to
# see the round-trip cases fail there.
#
# PROJ is an engine root: .ammo/active names campaign tgt at .ammo/tgt/. Both
# hooks act only inside that campaign and only for the auditor (agent_type
# ammo-auditor, or the plugin address ammo:ammo-auditor). The injected text
# names the plugin's own references/audit-invariants.md.
set -euo pipefail

# Hermetic: a live campaign's names must not leak into the fixtures.
for _v in $(compgen -e | grep '^AMMO_' | grep -v -e '^AMMO_TEST_INJECT$' -e '^AMMO_TEST_GUARD$' || true); do unset "$_v"; done
unset CLAUDE_PROJECT_DIR

HOOK_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INJECT="${AMMO_TEST_INJECT:-$HOOK_DIR/inject-audit-phase2.sh}"
GUARD="${AMMO_TEST_GUARD:-$HOOK_DIR/audit-phase2-guard.sh}"
PASS=0
FAIL=0
TOTAL=0

TMPDIR=$(mktemp -d)
PROJ="$TMPDIR/proj"
AUDITS="$PROJ/.ammo/tgt/rounds/1/audits"
mkdir -p "$AUDITS"
printf 'tgt\n' > "$PROJ/.ammo/active"
NOCAMP=$(mktemp -d)
INVARIANTS_PATH="$(cd "$(dirname "$INJECT")/.." && pwd)/references/audit-invariants.md"
cd "$PROJ"

# Sentinels live in /tmp by contract, so use a session id nothing else can
# collide with and clean up only our own.
SID_TAG="a4h$$"
_clear_sentinels() { rm -f /tmp/ammo_audit_phase2_injected_*"${SID_TAG}"* 2>/dev/null || true; }
cleanup() { _clear_sentinels; rm -rf "$TMPDIR" "$NOCAMP" 2>/dev/null || true; }
trap cleanup EXIT
_clear_sentinels

# run_inject: $1 name  $2 verdict_file  $3 session_id  $4 agent_id
#   [$5 agent_type, default the plugin address ammo:ammo-auditor]
#   expects a decision:block injection body on stdout, exit 0
run_inject() {
    local name="$1" vf="$2" sid="$3" aid="$4" atype="${5:-ammo:ammo-auditor}"
    local rc=0
    TOTAL=$((TOTAL + 1))
    local payload
    if [ -n "$aid" ]; then
        payload=$(jq -nc --arg fp "$vf" --arg s "$sid" --arg a "$aid" --arg t "$atype" \
            '{tool_name:"Write",session_id:$s,agent_id:$a,agent_type:$t,tool_input:{file_path:$fp}}')
    else
        payload=$(jq -nc --arg fp "$vf" --arg s "$sid" --arg t "$atype" \
            '{tool_name:"Write",session_id:$s,agent_type:$t,tool_input:{file_path:$fp}}')
    fi
    echo "$payload" | env CLAUDE_PROJECT_DIR="$PROJ" bash "$INJECT" \
        >"$TMPDIR/out" 2>"$TMPDIR/err" || rc=$?
    # -s guard: jq 1.6 exits 0 on empty input even with -e, so an empty out
    # file (the hook's silent-pass path) must be rejected before jq runs.
    if [ "$rc" -eq 0 ] && [ -s "$TMPDIR/out" ] && jq -e '.decision == "block"' "$TMPDIR/out" >/dev/null 2>&1; then
        echo "  PASS [$TOTAL]: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $name (exit=$rc)"
        echo "        out: $(head -c 160 "$TMPDIR/out" 2>/dev/null || echo '(none)')"
        FAIL=$((FAIL + 1))
    fi
}

# run_guard: $1 name  $2 expected BLOCK|PASS  $3 session_id  [$4 agent_type]
#   Pass "" as $4 to omit agent_type entirely (the lead case). `${4-...}` (no
#   colon) keeps an EXPLICIT empty string instead of substituting the default.
run_guard() {
    local name="$1" expect="$2" sid="$3" atype="${4-ammo-auditor}"
    local rc=0
    TOTAL=$((TOTAL + 1))
    local payload
    if [ -n "$atype" ]; then
        payload=$(jq -nc --arg s "$sid" --arg t "$atype" '{session_id:$s,agent_type:$t}')
    else
        payload=$(jq -nc --arg s "$sid" '{session_id:$s}')
    fi
    echo "$payload" | env CLAUDE_PROJECT_DIR="$PROJ" bash "$GUARD" \
        >"$TMPDIR/out" 2>"$TMPDIR/err" || rc=$?
    local got="PASS"
    # -s guard: jq 1.6 exits 0 on empty input even with -e (jq 1.8 exits 4),
    # so an empty out file must stay PASS.
    [ -s "$TMPDIR/out" ] && jq -e '.decision == "block"' "$TMPDIR/out" >/dev/null 2>&1 && got="BLOCK"
    local ok=true
    [ "$got" = "$expect" ] || ok=false
    [ "$rc" -eq 0 ] || ok=false
    if [ "$ok" = "true" ]; then
        echo "  PASS [$TOTAL]: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $name (expected=$expect got=$got exit=$rc)"
        echo "        out: $(head -c 160 "$TMPDIR/out" 2>/dev/null || echo '(none)')"
        FAIL=$((FAIL + 1))
    fi
}

echo "== Sentinel name contract: the pair must agree =="
# The pre-fix pair disagreed, so the guard could never find the verdict file.
# Both cases below are PASS (no block) on an archived pair.
VF="$AUDITS/stage_4_impl.md"
SID="sess_${SID_TAG}_1"
echo "## Phase 1" > "$VF"
run_inject "1 inject on a verdict write emits the Phase 2 body" "$VF" "$SID" "AG_9"

TOTAL=$((TOTAL + 1))
if jq -r '.reason' "$TMPDIR/out" 2>/dev/null | grep -qF "Read ${INVARIANTS_PATH} "; then
    echo "  PASS [$TOTAL]: 1b the body names the plugin's references/audit-invariants.md"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: 1b body does not name $INVARIANTS_PATH: $(head -c 200 "$TMPDIR/out")"
    FAIL=$((FAIL + 1))
fi

TOTAL=$((TOTAL + 1))
EXPECT="/tmp/ammo_audit_phase2_injected_${SID}__AG_9__stage_4_impl"
if [ -e "$EXPECT" ]; then
    echo "  PASS [$TOTAL]: 2 sentinel written at the documented double-underscore path"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: 2 no sentinel at $EXPECT"
    echo "        found: $(ls /tmp/ammo_audit_phase2_injected_*"${SID_TAG}"* 2>/dev/null || echo none)"
    FAIL=$((FAIL + 1))
fi

run_guard "3 Phase 2 missing → Stop BLOCKS (was inert before the fix)" BLOCK "$SID"

echo "## Phase 2: Checklist Verification" >> "$VF"
run_guard "4 Phase 2 present → Stop passes" PASS "$SID"

echo ""; echo "== Underscore-heavy ids must still round-trip =="
# A single-underscore join is ambiguous when ids carry underscores; this is the
# case the double underscore exists for.
VF2="$AUDITS/stage_67_campaign_eval.md"
SID2="claude_sess_${SID_TAG}_2_abc"
echo "## Phase 1" > "$VF2"
run_inject "5 inject with underscored session+agent ids" "$VF2" "$SID2" "AG_12_sub_3"
TOTAL=$((TOTAL + 1))
EXPECT2="/tmp/ammo_audit_phase2_injected_${SID2}__AG_12_sub_3__stage_67_campaign_eval"
if [ -e "$EXPECT2" ]; then
    echo "  PASS [$TOTAL]: 6 sentinel path exact with underscored ids"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: 6 no sentinel at $EXPECT2"
    FAIL=$((FAIL + 1))
fi
run_guard "7 guard recovers the multi-underscore verdict base → BLOCKS" BLOCK "$SID2"
echo "## Phase 2" >> "$VF2"
run_guard "8 same, Phase 2 written → passes" PASS "$SID2"

echo ""; echo "== Missing agent_id falls back to 'noagent' =="
VF3="$AUDITS/stage_2_mining.md"
SID3="sess_${SID_TAG}_3"
echo "## Phase 1" > "$VF3"
run_inject "9 inject with no agent_id" "$VF3" "$SID3" ""
TOTAL=$((TOTAL + 1))
if [ -e "/tmp/ammo_audit_phase2_injected_${SID3}__noagent__stage_2_mining" ]; then
    echo "  PASS [$TOTAL]: 10 noagent fallback in the sentinel name"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: 10 noagent sentinel missing"
    FAIL=$((FAIL + 1))
fi
run_guard "11 noagent sentinel still resolves the verdict file → BLOCKS" BLOCK "$SID3"

echo ""; echo "== One-shot: inject does not re-fire on the same file =="
TOTAL=$((TOTAL + 1))
rc=0
jq -nc --arg fp "$VF3" --arg s "$SID3" '{tool_name:"Write",session_id:$s,agent_type:"ammo:ammo-auditor",tool_input:{file_path:$fp}}' \
    | env CLAUDE_PROJECT_DIR="$PROJ" bash "$INJECT" >"$TMPDIR/out" 2>/dev/null || rc=$?
if [ "$rc" -eq 0 ] && [ ! -s "$TMPDIR/out" ]; then
    echo "  PASS [$TOTAL]: 12 second write on the same verdict file injects nothing"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: 12 inject re-fired (exit=$rc, out=$(head -c 80 "$TMPDIR/out"))"
    FAIL=$((FAIL + 1))
fi

echo ""; echo "== Scope: non-verdict paths and non-subagents =="
TOTAL=$((TOTAL + 1))
rc=0
jq -nc --arg s "$SID3" '{tool_name:"Write",session_id:$s,agent_type:"ammo:ammo-auditor",tool_input:{file_path:"/w/notes.md"}}' \
    | env CLAUDE_PROJECT_DIR="$PROJ" bash "$INJECT" >"$TMPDIR/out" 2>/dev/null || rc=$?
if [ "$rc" -eq 0 ] && [ ! -s "$TMPDIR/out" ]; then
    echo "  PASS [$TOTAL]: 13 non-verdict path injects nothing"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: 13 injected on a non-verdict path"
    FAIL=$((FAIL + 1))
fi

# The Stop guard is auditor-only: no agent_type means the lead, which must never
# be trapped by an auditor's sentinel.
SID4="sess_${SID_TAG}_4"
VF4="$AUDITS/stage_3_debate.md"
echo "## Phase 1" > "$VF4"
run_inject "14 seed a sentinel for the auditor-only check" "$VF4" "$SID4" "AG_1"
run_guard "15 no agent_type (lead) → guard passes even with a live sentinel" PASS "$SID4" ""

run_guard "16 unknown session id → guard passes (no sentinel)" PASS "sess_${SID_TAG}_none"
run_guard "16b non-auditor agent_type → guard passes even with a live sentinel" PASS "$SID4" "ammo:ammo-champion"
run_guard "16c plugin address ammo:ammo-auditor → guard BLOCKS on the live sentinel" BLOCK "$SID4" "ammo:ammo-auditor"

echo ""; echo "== Inject is auditor-only =="
# inject_silent: $1 name  $2 json → exit 0, empty stdout, no new sentinel.
inject_silent() {
    local name="$1" json="$2" rc=0 before after
    TOTAL=$((TOTAL + 1))
    before=$( (ls /tmp/ammo_audit_phase2_injected_*"${SID_TAG}"* 2>/dev/null || true) | wc -l | tr -d ' ')
    echo "$json" | env CLAUDE_PROJECT_DIR="$PROJ" bash "$INJECT" >"$TMPDIR/out" 2>"$TMPDIR/err" || rc=$?
    after=$( (ls /tmp/ammo_audit_phase2_injected_*"${SID_TAG}"* 2>/dev/null || true) | wc -l | tr -d ' ')
    if [ "$rc" -eq 0 ] && [ ! -s "$TMPDIR/out" ] && [ "$before" = "$after" ]; then
        echo "  PASS [$TOTAL]: $name"
        PASS=$((PASS + 1))
    else
        echo "  FAIL [$TOTAL]: $name (exit=$rc sentinels ${before} to ${after} out=$(head -c 80 "$TMPDIR/out"))"
        FAIL=$((FAIL + 1))
    fi
}
VF5="$AUDITS/stage_45_tracks.md"
echo "## Phase 1" > "$VF5"
inject_silent "19 non-auditor agent_type (champion) writing a verdict path → exit 0, silent" \
    "$(jq -nc --arg fp "$VF5" --arg s "sess_${SID_TAG}_5" '{tool_name:"Write",session_id:$s,agent_type:"ammo:ammo-champion",tool_input:{file_path:$fp}}')"
inject_silent "20 no agent_type (the lead) writing a verdict path → exit 0, silent" \
    "$(jq -nc --arg fp "$VF5" --arg s "sess_${SID_TAG}_5" '{tool_name:"Write",session_id:$s,tool_input:{file_path:$fp}}')"
run_inject "21 bare ammo-auditor agent_type still injects" "$VF5" "sess_${SID_TAG}_5" "AG_5" "ammo-auditor"

echo ""; echo "== Campaign scope =="
mkdir -p "$NOCAMP/x/rounds/1/audits"
echo "## Phase 1" > "$NOCAMP/x/rounds/1/audits/stage_1_baseline.md"
TOTAL=$((TOTAL + 1))
rc=0
jq -nc --arg fp "$NOCAMP/x/rounds/1/audits/stage_1_baseline.md" --arg s "sess_${SID_TAG}_6" --arg c "$NOCAMP" \
    '{tool_name:"Write",session_id:$s,cwd:$c,agent_type:"ammo:ammo-auditor",tool_input:{file_path:$fp}}' \
    | (cd "$NOCAMP" && bash "$INJECT") >"$TMPDIR/out" 2>/dev/null || rc=$?
if [ "$rc" -eq 0 ] && [ ! -s "$TMPDIR/out" ] && [ ! -e "/tmp/ammo_audit_phase2_injected_sess_${SID_TAG}_6__noagent__stage_1_baseline" ]; then
    echo "  PASS [$TOTAL]: 22 no campaign: inject is silent and writes no sentinel"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: 22 no campaign: inject exit=$rc out=$(head -c 80 "$TMPDIR/out")"
    FAIL=$((FAIL + 1))
fi
inject_silent "23 in a campaign, a verdict-shaped path outside the campaign dir → silent" \
    "$(jq -nc --arg fp "$NOCAMP/x/rounds/1/audits/stage_1_baseline.md" --arg s "sess_${SID_TAG}_7" '{tool_name:"Write",session_id:$s,agent_type:"ammo:ammo-auditor",tool_input:{file_path:$fp}}')"
TOTAL=$((TOTAL + 1))
rc=0
jq -nc --arg s "$SID4" --arg c "$NOCAMP" '{session_id:$s,cwd:$c,agent_type:"ammo:ammo-auditor"}' \
    | (cd "$NOCAMP" && bash "$GUARD") >"$TMPDIR/out" 2>/dev/null || rc=$?
if [ "$rc" -eq 0 ] && [ ! -s "$TMPDIR/out" ]; then
    echo "  PASS [$TOTAL]: 24 no campaign: guard is silent even with a live sentinel"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: 24 no campaign: guard exit=$rc out=$(head -c 80 "$TMPDIR/out")"
    FAIL=$((FAIL + 1))
fi

echo ""; echo "== Fail-open =="
TOTAL=$((TOTAL + 1))
MIN_PATH="$TMPDIR/minbin-nojq"
mkdir -p "$MIN_PATH"
for t in bash sh cat echo env printf grep sed find basename dirname test rm ls head touch; do
    real=$(command -v "$t" 2>/dev/null || true)
    [ -n "$real" ] && ln -sf "$real" "$MIN_PATH/$t"
done
rc=0
jq -nc --arg s "$SID" '{session_id:$s,agent_type:"ammo-auditor"}' > "$TMPDIR/payload.json"
env PATH="$MIN_PATH" bash "$GUARD" < "$TMPDIR/payload.json" >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 0 ]; then
    echo "  PASS [$TOTAL]: 17 guard fails open without jq"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: 17 guard exit=$rc without jq"
    FAIL=$((FAIL + 1))
fi

TOTAL=$((TOTAL + 1))
rc=0
jq -nc --arg fp "$AUDITS/stage_5_gates.md" --arg s "$SID" \
    '{tool_name:"Write",session_id:$s,tool_input:{file_path:$fp}}' > "$TMPDIR/payload2.json"
env PATH="$MIN_PATH" bash "$INJECT" < "$TMPDIR/payload2.json" >/dev/null 2>&1 || rc=$?
if [ "$rc" -eq 0 ]; then
    echo "  PASS [$TOTAL]: 18 inject fails open without jq"
    PASS=$((PASS + 1))
else
    echo "  FAIL [$TOTAL]: 18 inject exit=$rc without jq"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "================================"
echo "Results: $PASS passed, $FAIL failed out of $TOTAL tests"
echo "================================"
[ "$FAIL" -gt 0 ] && exit 1
exit 0
