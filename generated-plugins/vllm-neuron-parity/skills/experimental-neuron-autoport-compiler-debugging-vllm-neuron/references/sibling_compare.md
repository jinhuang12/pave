# Compare against a working sibling model

When a new architecture fails and you suspect a **compiler/framework bug rather than a
model bug**, the fastest disambiguation is to diff its artifacts against a known-good
sibling that already compiles on the same stack (a previously-ported dense model, or a
reference EP model like gpt_oss / qwen3_moe / deepseek_v32). This is the port analog of
the perf skill's
[`compiler_diff.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/compiler_diff.md)
(which diffs *two compilers on the same HLO*); here you diff *two models on the same compiler*.

## What to compare, and what each diff proves

### 1. Subgraph count (proves an NKI kernel repartitioned the graph)

A new in-place NKI kernel with `operand_output_aliases` silently repartitions the graph
(1 → 14 subgraphs) and can surface a scheduler bug (`ISCH719`) in **unrelated** subgraphs.

```bash
grep -a 'Compilation status: Total subgraphs' <new>/log-neuron-cc.txt
grep -a 'Compilation status: Total subgraphs' <sibling>/log-neuron-cc.txt
# baseline: 1 subgraph; new: 14  ->  the kernel's aliasing changed HLO partitioning
# prove the kernel's OWN sg passed (crash is elsewhere):
for sg in <new>/penguin-sg*/; do grep -q kv_cache_scatter "$sg/penguin.py" && echo "$sg HAS it"; done
grep -a 'post_sched failed\|Subgraph.*failed' <new>/log-neuron-cc.txt
```

### 2. Opcode / op-class histogram (proves which new op-pattern is the culprit)

```bash
python3 scripts/scan_hlo.py act <new>/graph.hlo      # transcendental table count vs sibling
# FX-level (front-end):
grep -oP '= call_function\[target=\K[^\]]+' <new>/fxgraph.txt | sort | uniq -c | sort -rn > /tmp/new.hist
grep -oP '= call_function\[target=\K[^\]]+' <sibling>/fxgraph.txt | sort | uniq -c | sort -rn > /tmp/sib.hist
diff /tmp/sib.hist /tmp/new.hist
```

### 3. Collective topology (proves a refactor hypothesis right or wrong)

Read the compiler-emitted `replica_groups` from the compiled graph — **not** the Python
group objects. This disproved a wrong flat-64 refactor hypothesis on the Inkling port:
finding only `[[0..31]]` node-local and `[[k,k+32]]` peer groups (no flat-64) showed
`get_tp_group` was already 32-rank node-local, so the barrier failure was runtime/fabric,
not a graph bug.

```bash
grep -aoE '\[\[[0-9, ]+\]\]' <run.out> | sort -u
# node-local vs cross-node peer vs flat-world groups
```

### 4. How the sibling gates a shared kernel (proves a config/selection mismatch)

Reference EP models often use a shared kernel only in a context your port also uses it in
incorrectly (e.g. `moe_cte` in prefill-only, not decode). Read their eligibility guards:

```bash
grep -rn '_can_use_kernel\|_can_use_moe_cte_kernel\|ComputationMode' \
    <vllm_neuron>/functional/ <vllm_neuron>/model/<sibling>/
```

## The verdict this produces

- **Same op-pattern present in the sibling and it compiles** → your usage differs
  (kernel mode, batch shape, config). Model/config-fixable.
- **Op-pattern is unique to your model and identical HLO fails at one opt-level/batch but
  passes at another** → genuine compiler bug. Escalate per
  [`crash_triage.md`](../experimental-neuron-perf-compiler-artifact-debugging/references/crash_triage.md)
  Step 7, carrying the byte-identical-op-set evidence and any O2/-O1 perf tradeoff.
- **Beware stale project memories** asserting a wrong root cause ("CSA scoring needs a
  graph split", "IGAA901 is a CSA symptom") — re-verify against current code + artifacts
  before acting.
