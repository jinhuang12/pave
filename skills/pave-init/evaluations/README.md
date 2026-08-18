# Evaluations

Source-of-truth scenarios for pave-init behavior, in the skill-authoring evaluation format (query, setup, `expected_behavior`). Run one by giving a fresh session the query (plus the setup, where a scenario names one) and grading the run against every expected behavior. A skill change that regresses an expected behavior is a defect, whatever the prose says.

- `01-multi-boundary-port.json` — a real decomposing goal (the recorded dry run of 2026-08-15 is its first execution).
- `02-trivial-single-node.json` — the anti-inflation brake: a goal that must NOT grow ceremony.
- `03-resume-mid-planning.json` — position rebuilt from persisted state only.
