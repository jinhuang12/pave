# Apply the approved 1.5.1 proposal

The user approved application, commit, and push with the exact instruction:

> Apply, commit & push

Approval applies to the reviewed package patch `release-1.5.1.patch`, SHA256 `02926c35f6fe08e90fdc1407f80bf128e090b021db444e7c9ce37e2961f4fad3`. The independent reviewer passed it in round 1. The source and index application checks passed before application.

The package remains a reference/release change at version 1.5.1. Graph revision 5 and its bundle digest `sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7` remain unchanged. No graph-ledger entry or active-run migration is needed.

The root `.claude-plugin/marketplace.json` version for this plugin changes from 1.5.0 to 1.5.1, as the proposal's distribution note requires. No other marketplace entry changes. This applies source metadata; it does not install a plugin into a local cache.

The commit includes the reviewed seven-file package patch, this marketplace update, and the proposal/review evidence. Existing model-binding edits and other unrelated worktree changes are excluded. The reviewed candidate included those existing edits; the staged snapshot is validated separately to check the exact content being committed.

The target branch is `main` on `https://github.com/jinhuang12/pave.git`. Before this change it was three commits ahead of `origin/main`: revision 5, its landing-commit record, and release 1.5.0. The requested push includes those prerequisite commits.

The staged source was exported from tree `18bbc4ab63a081715188ed90e76c1581b8e9cad4` to an isolated scratch directory. It passed 42 pytest tests and all 31 hook checks. The graph validator and revision verifier passed at the unchanged revision and digest above. Both package manifests, VERSION, and the marketplace entry all declare 1.5.1. Only this application record was completed after that export; the tested source did not change.

The source whitespace check passed. The saved exact patch is excluded from that check because its unified-diff blank context lines contain the required leading space; its reviewed SHA256 remains unchanged. The unchanged skill description's length-limit failure remains disclosed in `validation.md`.
