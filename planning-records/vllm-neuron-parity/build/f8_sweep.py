#!/usr/bin/env python3
"""F8 item-35 gate-2 sweep — every register-booked rule present in the
BUILT DELIVERABLE SET (assembled workflow + built references).

Method per R2's warnings (checklist F8 row): whitespace-flattened,
case-insensitive matching on distinctive single-line fragments — never
literal line greps (multi-line block scalars wrap booked clauses; a
qualifier on the next line reads as an un-qualified copy). Zero misses
on a first pass = check the method before believing the result.

Re-runnable; run from the workspace root. Exit 1 on any MISS.
"""
import sys

TARGETS = {
    "graph": "workflow.draft.pave.yaml",
    "layout": "build/artifact-layout-reference.md",
    "enforce": "build/enforcement-record.md",
}

# (register item, distinctive fragment, expected homes)
# Fragments chosen to be phrase-unique and single-concept; homes name
# where the rule must survive. "graph" = assembled workflow.
RULES = [
    # --- A column: measure budget + smoke/serving (26, c13, 27p-t, c11/c12) ---
    ("c13 tier-1 qualifier", "without new evidence", ["graph", "layout"]),
    ("c13 tier-1 magnitude", "suggested three repair passes", ["graph", "layout"]),
    ("c13 tier-2 magnitude", "suggested nine total", ["graph", "layout"]),
    ("c13 two-predicate split", "counts the subset whose novelty field is false", ["graph", "layout"]),
    ("c13 novelty field", "new-relative-to-prior file-list field", ["graph", "layout"]),
    ("26 either-tier spend (graph)", "at either tier", ["graph"]),
    ("26 either-tier spend (layout wording)", "at either threshold", ["layout"]),
    ("26(d) at-ceiling", "already at the ceiling when a further defect appears", ["graph"]),
    ("27(r) tie-breaker", "settles only when no declared measurement retains repair budget", ["graph"]),
    ("27(r) reciprocal", "takes precedence over declared_measurement_unproducible", ["graph"]),
    ("27(p) revision-scoped smoke", "that cites that procedure revision", ["graph"]),
    ("27(p) smoke join (layout wording)", "valid only against a passing smoke record", ["layout"]),
    ("27(q) retry magnitude", "suggested two bring-up retries per lost serving state", ["graph"]),
    ("27(t) revision entries", "version each realization and name the comparisons", ["graph"]),
    ("c11 durable scoping", "persistent writes including cache writes", ["graph", "layout"]),
    ("c11 boundary ruling", "run-scoped scratch removed at teardown", ["layout"]),
    ("c11/A11 refusal door (graph)", "cannot be redirected to run-scoped scratch", ["graph"]),
    ("c11/A11 door cross-ref (layout)", "unredirectable-persistent-cache-write disjunct", ["layout"]),
    ("c12 digest disjunct", "live digest contradicts its design-record pin", ["graph"]),
    # --- B column: design family (27e/f/h/n/u, 31) ---
    ("27(e) screen ordering", "reported ahead of progress_exhausted when both hold", ["graph"]),
    ("27(e) ambiguity rule", "falls through to the progress screen", ["graph", "enforce"]),
    ("27(e) detector basis", "the note records the detector basis", ["graph"]),
    ("27(e) prereg conditional inputs", "neither absence is a gap", ["graph"]),
    ("27(e) none-declaration", "none-declaration when no patch surface is touched", ["graph"]),
    ("27(e) stable row id", "a stable row id", ["graph"]),
    ("27(f) venv plan split", "venv plan (freeze-replicate", ["graph"]),
    ("27(f) lease plan split", "hardware lease plan", ["graph"]),
    ("27(n) assembler refusal (graph)", "refuses to run without the current lead-minted design-entry id", ["graph"]),
    ("27(n) assembler refusal (layout)", "refuses to run without one", ["layout"]),
    ("27(n) detector id-scoping", "lap entries carrying the current design-entry id", ["graph"]),
    ("27(u) stability declaration", "minimum re-read spacing", ["graph", "layout"]),
    ("31(b) comparator commitment", "the lead commits on consuming acceptance_preregistered", ["graph"]),
    ("31 addendum re-emission (structural)", "on_failure_route: preregister_acceptance", ["graph"]),
    ("27(h) record precedence", "outranks both evidence-bearing outcomes", ["graph"]),
    ("29 detector qualifier (two-qualifier form)", "gap-closing artifact for that gap", ["graph", "layout"]),
    ("31 d7 exemption", "exempt from the detector", ["layout"]),
    # --- C column: hardware (27i/k/l/m, c7, c10) ---
    ("27(k) freeze-screening", "screen the freeze before replay", ["graph"]),
    ("27(k) screen content (apostrophe-safe)", "own entry and every editable", ["graph"]),
    ("27(l) pre-retry read", "read the persisted per-target attempt counts", ["graph"]),
    ("27(l) threshold exit", "fingerprints attached instead of retrying", ["graph"]),
    ("27(l) worktree ownership", "campaign-worktree creation under the campaign's own directory", ["graph"]),
    ("27(m) venv fault precedence", "only a build failure on a healthy host is a recipe failure", ["graph"]),
    ("27(m) not-charged", "never charged to the per-target budget", ["graph"]),
    ("27(m) no-rollback", "re-trips the breaker on resume", ["graph"]),
    ("27(m) reciprocal yield", "yields to host_faulted", ["graph"]),
    ("27(i) conditional rung", "rung-1 provenance binding where a boot identifier exists", ["graph"]),
    ("c7/D10 units (all copies)", "budget-counted hardware attempt", ["graph"]),
    ("27(m)/D8 fault exemption", "host faults are recorded but never charged", ["graph"]),
    ("recovery allowance", "one successful recovery per host per campaign", ["graph"]),
    ("c10 allowance exclusion", "recovery allowance for this campaign is exhausted", ["graph"]),
    # --- D column: root (c1-c8, 18, 33, tidies) ---
    ("c7 tier-1 exhaustion (child wording)", "no materially different attempt", ["graph"]),
    ("c7 venv dead end (child + edge rationale)", "recipe dead end", ["graph"]),
    ("D2 two-place coverage", "every scorecard row and every debt-ledger entry", ["graph"]),
    ("D2 rationale (verbatim both places)", "unrequested targets go stale here or nowhere", ["graph"]),
    ("18 costing_stalled widening", "persisting across two consecutive delta-scan re-entries", ["graph"]),
    ("33 absent-empty-set", "legitimate empty set", ["layout"]),
    # --- E column (shipping halves) ---
    ("E1a contradiction precedence", "outranks both other outcomes when results co-hold", ["graph"]),
    ("E1a last door", "the last door in the precedence", ["graph"]),
    ("27(g) asymmetry reason", "the asymmetry is deliberate", ["graph"]),
    # --- checks / routing (25, 31a, 26a) ---
    ("31(a) socratic guard", "never the note author", ["graph", "enforce"]),
    ("27(p) smoke check scoping", "passing smoke record", ["graph", "layout"]),
    ("revision_stamped check", "git-issued revision identifier", ["graph"]),
    ("registration shape (9)", "subject, digest, timestamp", ["layout"]),
    ("exit-code discipline (23)", "a missing exit code makes the transcript non-evidence", ["layout"]),
    ("ported-code definition (23)", "never whole files, never upstream context lines", ["layout"]),
    ("report-hash joint entry (22/24)", "hash changed => a tracer wrote", ["layout"]),
    ("17 five fields", "measurement content hash", ["layout"]),
    ("no-identical-retry (P8)", "identical to a recorded failure", ["graph"]),
    # --- pair-1 repair (R2 whole-bundle HIGH) ---
    ("pair-1 consumer citation", "the artifact-layout impl/review pair entry is the shape authority", ["graph"]),
    ("pair-1 producer obligation", "additionally carries the binding fingerprint triple", ["graph", "layout"]),
    ("pair-1 falsifier (layout)", "false-fires on converging work", ["layout"]),
    # --- whole-bundle round, R1 findings ---
    ("19 binding seat (as booked)", "never the seat of any trace_target_delta instance whose report it judges", ["graph"]),
    ("R1 LOW-5 autoport classification", "competing tool per the system map, never a prerequisite", ["graph"]),
    ("R1 LOW-6 runner-default boundary at intake", "runner-default boundary one release above the pin", ["graph"]),
    ("R2 MEDIUM anchor clause", "the anchor is the answering record", ["graph"]),
    ("R1 HIGH-3 anchor pin (impl binding)", "since the triple was last answered", ["layout"]),
    ("R1 HIGH-3 anchor is part of the pin", "the anchor is part of the pin", ["layout"]),
    ("R1 HIGH-3 design-binding anchor", "no answering event exists for gaps", ["layout"]),
    ("R1 LOW-9 limb-2 anchor scoping", "binds limb 1 alone", ["layout"]),
    # --- kernel-substrate rule (user-directed amendment 2026-08-26) ---
    ("NKI rule principle", "implemented in nki, never as torch-level fallback", ["graph"]),
    ("NKI rule design declaration (HIGH-4 c1)", "every increment records a substrate decision", ["graph"]),
    ("NKI rule design-review rubric", "kernel-substrate declarations", ["graph"]),
    ("NKI rule impl-review rubric", "torch-implemented kernel-class item is a material finding", ["graph"]),
    ("NKI rule enforcement entry (P13)", "kernel-substrate rule", ["enforce"]),
    # --- HIGH-4 / MEDIUM-2 / LOW-11 (R1 NKI-amendment review) ---
    ("HIGH-4 c2 register self-check", "substrate register is present", ["graph"]),
    ("HIGH-4 c3 gate-2 classification challenge", "recorded non-kernel-class whose planned work is kernel-class", ["graph"]),
    ("HIGH-4 c3 impl-review classification challenge", "rode a non-kernel-class declaration", ["graph"]),
    ("MEDIUM-2 fidelity predicate (graph + P13)", "zero nki usage in a declared-nki increment", ["graph", "enforce"]),
    ("LOW-11 simulator route", "nki_simulator=1", ["graph"]),
    ("R2 LOW-12 principle carries the split rung", "mechanical substrate-fidelity check for items declared kernel-class", ["graph"]),
]

# Multi-site rules: EXACT flattened-count assertions (R2 whole-bundle
# MEDIUM - a presence matcher is blind to a reverted copy; the rule
# named "all copies" must count them).
COUNTS = [
    ("c7/D10 units phrase (principles + loop + ordinal)", "budget-counted hardware attempt", "graph", 3),
    ("c7/D10 units token (adds -retries and -attempts copies)", "budget-counted", "graph", 5),
    ("either-tier spend (all copies)", "at either tier", "graph", 5),
    ("27(m) not-charged (both copies)", "never charged to the per-target budget", "graph", 2),
    ("27(q) retry magnitude (two sites)", "suggested two bring-up retries per lost serving state", "graph", 2),
    ("c13 tier-1 magnitude (three sites)", "suggested three repair passes", "graph", 3),
    ("c13 tier-2 magnitude (three sites)", "suggested nine total", "graph", 3),
    ("D2 rationale (one clause covering BOTH artifact kinds)", "unrequested targets go stale here or nowhere", "graph", 1),
    ("pair-1 triple (purpose + no_new_route + producer)", "increment id + surface + defect class", "graph", 3),
    ("pair-1 triple (layout, pair entry + 4.3 binding)", "increment id + surface + defect class", "layout", 2),
    ("passing qualifier (all four limb statements)", "new passing increment evidence", "graph", 4),
    ("item scoping (all four limb statements)", "for that item", "graph", 4),
    ("narrowing stated (both falsifier homes)", "narrows, never eliminates", "layout", 2),
    ("HIGH-4 c1 register (purpose + activity + self-check + impl-review clause)", "non-kernel-class declaration", "graph", 4),
    ("MEDIUM-2 gap class (d) + scoping enumeration (realize home says 'check re-run', no 'hit')", "substrate-fidelity hit", "graph", 2),
    ("MEDIUM-2 re-run acceptance (class d + both enumerations)", "substrate-fidelity check re-run", "graph", 3),
]

# Negative assertions: bare phrase must never appear un-qualified, and
# the shipped graph must carry zero planning-file citations (F4).
NEG_QUALIFIED = [
    ("threshold copies all exempted", "graph", "hardware attempts per target", "budget-counted hardware attempts per target"),
    ("ordinal copies all exempted", "graph", "hardware attempt on this target", "budget-counted hardware attempt on this target"),
]
NEG_ZERO = [
    ("no planning-draft citations (F4)", "graph", "draft.pave.yaml"),
    ("no planning-dir references (F4)", "graph", "planning/"),
    ("no x_planning keys ship", "graph", "x_planning"),
    # R1 LOW-7: no fragment-version suffixes anywhere in the shipped graph
    ("no .v1 node refs", "graph", ".v1"),
    ("no .v2 node refs", "graph", ".v2"),
    ("no .v3 node refs", "graph", ".v3"),
    ("no .v4 node refs", "graph", ".v4"),
    ("no .v5 node refs", "graph", ".v5"),
    ("no .v6 node refs", "graph", ".v6"),
    ("no .v7 node refs", "graph", ".v7"),
]

def flat(path):
    return " ".join(open(path).read().lower().split())

def main():
    texts = {k: flat(p) for k, p in TARGETS.items()}
    misses = []
    for item, frag, homes in RULES:
        f = " ".join(frag.lower().split())
        for h in homes:
            if f not in texts[h]:
                misses.append(f"MISS [{h}] {item}: {frag!r}")
    for item, frag, h, exp in COUNTS:
        n = texts[h].count(" ".join(frag.lower().split()))
        if n != exp:
            misses.append(f"COUNT [{h}] {item}: {n} != expected {exp}")
    for item, h, bare, qual in NEG_QUALIFIED:
        b, q = texts[h].count(bare), texts[h].count(qual)
        if b != q:
            misses.append(f"NEG [{h}] {item}: bare={b} qualified={q} -> {b - q} un-exempted")
    for item, h, frag in NEG_ZERO:
        n = texts[h].count(frag)
        if n != 0:
            misses.append(f"NEG [{h}] {item}: {n} occurrences, expected 0")
    print(f"F8 sweep: {len(RULES)} presence + {len(COUNTS)} count + "
          f"{len(NEG_QUALIFIED) + len(NEG_ZERO)} negative rules over {list(TARGETS)}")
    if misses:
        print(f"\nFAILURES ({len(misses)}):")
        for m in misses:
            print(f"  {m}")
        sys.exit(1)
    print("ALL PRESENT — per F8 method rule, a zero-miss first pass means: "
          "re-check fragment distinctiveness before believing the result. "
          "Count and negative rules bound the incomplete-landing class; "
          "POSITION is still unproven - the positional subset needs reading.")

if __name__ == "__main__":
    main()
