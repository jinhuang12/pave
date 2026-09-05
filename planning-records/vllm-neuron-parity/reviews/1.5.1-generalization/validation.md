# Validation output

The exact source patch applies and reproduces the candidate. The skill description check has the same pre-existing failure on base and result.

Working directory: `/tmp/parity-151-updater-c9xt45dw/reproduced/vllm-neuron-parity`. The last command runs in the untouched base directory.

```text
$ git apply --check /home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/release-1.5.1.patch
exit: 0

$ git apply /home/jinhun/pave/planning-records/vllm-neuron-parity/reviews/1.5.1-generalization/release-1.5.1.patch
exit: 0

PASS: applying the exact patch reproduced all 54 recorded file hashes.

$ python -m pytest tests -q
..........................................                               [100%]
42 passed in 4.39s
exit: 0

$ bash tests/test_hooks.sh
PASS  reader: first campaigns/ .md write reminds
PASS  reader: 2nd write same session is silent
PASS  reader: 3rd write same session is silent
PASS  reader: 4th write same session reminds
PASS  reader: different session reminds immediately
PASS  reader: campaigns/*/attempts/ write is silent
PASS  reader: run/delta/index/ write is silent
PASS  reader: run/backlog/ write reminds
PASS  reader: .yaml write is silent
PASS  reader: no marker is silent
PASS  reader: terminal_classification.status set is silent
PASS  reader: <root>/README.md outside workspace is silent
PASS  reader: garbage stdin exits 0 with empty stdout
PASS  reader: Edit payload reminds
PASS  reader: marker via payload cwd reminds
PASS  reader: campaigns/index/design/ write reminds (campaign name not tested)
PASS  reader: over-cap document is named past the throttle
PASS  reader: over-cap notice fires once per session and file
PASS  reader: cap follows VLLM_NEURON_PARITY_CAP_LINES
PASS  guard: editing the live graph in an evolution root is denied
PASS  guard: a landing in progress passes
PASS  guard: a child graph beside the ledger is guarded too
PASS  guard: a .pave.yaml with no ledger beside it passes
PASS  guard: a non-graph path in the root passes
PASS  guard: editing the ledger itself is denied
PASS  guard: the ledger under a landing in progress passes
PASS  guard: creating a ledger where none exists passes
PASS  guard: a payload without file_path passes
PASS  guard: a subagent editing the live graph is denied too
PASS  guard: unparsable payload fails open
PASS  guard: registered in hooks/hooks.json under PreToolUse Edit|Write|MultiEdit

31 passed, 0 failed
exit: 0

$ python scripts/validate_pave.py workflow.pave.yaml
PASS workflow.pave.yaml: 32 nodes, 95 edges, 5 control endpoints
exit: 0

$ python scripts/record_revision.py verify .
PASS: . is intact at revision 5 (6 ledger entries) sha256:f4d76a53e78c1047f442d21c477298d73a5a36e86837f4a504301918110879f7
exit: 0

$ python /home/jinhun/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/vllm-neuron-parity
Description is too long (1096 characters). Maximum is 1024 characters.
exit: 1

$ python /home/jinhun/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/vllm-neuron-parity
Description is too long (1096 characters). Maximum is 1024 characters.
exit: 1
```
