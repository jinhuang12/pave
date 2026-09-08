# Revision 6 validation — mechanical evidence

Every check below is pasted output, not a claim. The proposal
(`generated-plugins/vllm-neuron-parity/history/v6.patch`) applies cleanly,
validates, and reproduces a fully green package in the landed state; the live
root is still revision 5.

## 1. The head this was drafted against, before and after

```
$ python3 scripts/record_revision.py verify .        # before any work
PASS: . is intact at revision 5 (6 ledger entries) sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7

$ python3 scripts/record_revision.py verify .        # after writing the proposal
PASS: . is intact at revision 5 (6 ledger entries) sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7
```

`git status --porcelain` inside the evolution root reports exactly one new path,
`history/v6.patch`. `workflow.pave.yaml` and `revisions.yaml` are unmodified and
equal committed HEAD `cf4fabb`. The user's 22 uncommitted release-layer and TOML
files were never staged, committed or edited.

## 2. propose

```
$ python3 scripts/record_revision.py propose . --patch history/v6.patch
PASS: the proposal applies to . and validates; digest_after sha256:d00e951b0e874ef88edbeae69798f1fcf5ff8694713212c99b21654806e35f12
  kind: graph
  envelope_check: changed_with_approval
  plan_evidence: verified
  usage_evidence: none
```

Preamble accepted with no minted field: only `kind`, `semantic_diff`,
`envelope_check`, `plan_evidence`, `usage_evidence` and `changelog_entry` are
set. `approval` and `review` are left for the lead at landing.

## 3. validate_pave

```
$ python3 scripts/validate_pave.py workflow.pave.yaml          # live, revision 5
PASS workflow.pave.yaml: 32 nodes, 95 edges, 5 control endpoints

$ python3 scripts/validate_pave.py workflow.pave.yaml          # patched
PASS workflow.pave.yaml: 32 nodes, 95 edges, 5 control endpoints
```

## 4. Structural invariance, by parsing both graphs

Both graphs parsed with `yaml.safe_load` and compared field by field:

```
live    {'nodes': 32, 'edges': 95, 'checks': 16, 'evidence': 24, 'endpoints': 5}
scratch {'nodes': 32, 'edges': 95, 'checks': 16, 'evidence': 24, 'endpoints': 5}
node ids equal: True
check ids equal: True
edge ids equal: True
evidence ids equal: True
outcome sets equal: True
roles/intent/instance_per equal: True
effects/consumes/produces equal: True
check style/evaluator/route equal: True
required_evidence equal: True
```

## 5. Diff accounting

```
added 28  removed 36  hunks 9  net -8   (patch file 358 lines; diff 134 lines)
@@ -253,8 +253,8 @@      kickoff_contract_approved question
@@ -331,7 +331,8 @@      acceptance_threshold_evaluated question
@@ -341,7 +342,10 @@     acceptance_threshold_evaluated rationale
@@ -1668,10 +1672,7 @@   record_changeset purpose
@@ -2044,10 +2045,9 @@   realize_measurement_procedures activity
@@ -2091,10 +2091,9 @@   capture_baseline_reference purpose
@@ -2157,13 +2156,9 @@   run_candidate_measurements activity
@@ -2244,17 +2239,14 @@   stabilize_and_package_evidence, two activities
@@ -2275,11 +2267,11 @@   declared_measurement_unproducible meaning
```

Five nodes and two checks touched. Nothing outside `workflow.pave.yaml`:
`agents/*.md`, references, `SKILL.md`, README, hooks, tests and VERSION are
untouched, so the patch cannot blend with the parallel release-layer amendment.

## 6. Package tests, in three configurations

Each configuration is a throwaway copy of the plugin under a directory named
`vllm-neuron-parity` (the codex manifest test asserts the directory name).

**A. Baseline, unpatched** — establishes there is no pre-existing red:

```
PASS workflow.pave.yaml: 32 nodes, 95 edges, 5 control endpoints
42 passed in 4.36s
31 passed, 0 failed          (tests/test_hooks.sh)
```

**B. Patch applied, ledger entry not yet appended** — two expected failures:

```
PASS workflow.pave.yaml: 32 nodes, 95 edges, 5 control endpoints
FAILED tests/test_codex_port.py::RevisionLedgerTests::test_install_seeds_a_project_root_that_verifies
FAILED tests/test_codex_port.py::RevisionLedgerTests::test_packaged_root_verifies
2 failed, 40 passed in 4.35s
31 passed, 0 failed          (tests/test_hooks.sh)
```

Both failures are the ledger telling the truth: `verify` reports "unrecorded
edit: the live digest sha256:d00e951b… is not revision 5 digest_after". A graph
that moved without an entry is exactly the state these tests exist to catch, and
only the lead's landing clears it. They are not defects in the patch.

**C. Landed simulation** — the same throwaway copy after
`record_revision.py land . 6` with placeholder approval and review strings
(a `/tmp` copy, never the live root):

```
PASS: . is intact at revision 6 (7 ledger entries) sha256:d00e951b0e874ef88edbeae69798f1fcf5ff8694713212c99b21654806e35f12
PASS workflow.pave.yaml: 32 nodes, 95 edges, 5 control endpoints
42 passed in 4.42s
31 passed, 0 failed          (tests/test_hooks.sh)
```

The landed digest equals the digest `propose` predicted, and the package is fully
green. The lead's real landing supplies the user's verbatim approval and the
reviewer's verdict in place of the placeholders.

## 7. What the reviewer should re-derive independently

1. `references/measurement-pitfalls.md:46-50` against the cut clause at
   `:2047-2050` — that the authority blames chunk-derived counting in any harness
   and calls the stock tool's accounting unverified.
2. `references/artifact-layout.md:302-306` against the new `:334` question — that
   the pinned record is per criterion the bundle realizes.
3. `references/artifact-layout.md:245-247` and `agents/measurer.md:106-108`
   against the four cut magnitude copies.
4. That the release layer's own copies (`agents/measurer.md:57-59` and
   `references/measurement-pitfalls.md:32`) are out of scope for a graph patch
   and are named for the lead to route, not silently left.
