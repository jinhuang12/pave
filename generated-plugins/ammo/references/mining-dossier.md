# Mining Dossier

A dossier holds the Stage 2 evidence for one kernel family. One worker writes
one dossier for each entry of `mined.json` `selected_families`, at
`rounds/{N}/mining/families/<LABEL>.md`, and one more for the cross-cutting
slice, at `rounds/{N}/mining/families/cross_cutting.md`.
`orchestration/mining-orchestration.md` sends each worker its packet. The
researcher folds the dossiers into `bottleneck_analysis.md` (`mining_report`
in `agents/ammo-researcher.md`), and `T_AUDIT_S2` reads them with the report.

## Rules for Every Dossier

- Supply facts and bounds only. Do not propose, rank, or score a change.
  Champions own mechanism, projection, and technology choice
  (`agents/ammo-researcher.md` § What You Supply, What Champions Own).
- Cite every claim: a trace row (slice file plus kernel row or start time), a
  `mined.json` field, or a file and line in the engine checkout.
- `scripts/mine_trace.py` owns every mined number. Cite the `mined.json` field.
  Never restate, recompute, or edit a mined number.
- Trace order overrides architecture order.
- Counter claims need targeted NCU. A ceiling claim takes NCU, or a cited
  hardware spec plus the math (`references/nsys-profiling-guide.md` § Targeted
  NCU).
- If a section has no evidence, write what you tried and what is missing. Do
  not fill the gap from memory.

## Family Dossier

Write these sections in this order.

### 1. Kernel Chain in One Decode Step

List the family's kernels in trace order for one decode step, from this
family's `chain` in `mined.json` `selected_families`: symbol, count, and
duration for each kernel. Compare the
occurrence count with layers x decode steps, and give the phase of each
kernel. A kernel absent from decode, or with far more occurrences than layers
x decode steps, is transient until the trace proves otherwise.

### 2. Source Map

Give the dispatch path from the engine entry point to each kernel, with a file
and line for every hop. `references/nsys-profiling-guide.md` § Stage 2 Mining
lists where the Pumice kernels live.

### 3. Physical Ceiling

Quote the family's `physical_ceiling` from `mine_config.json`. Check it
against the bytes moved and the flops per call for this shape and dtype, and
against the peak bandwidth and compute of this GPU. List every assumption. Do
not derive a new ceiling. The miner owns the value: if the check fails, or the
family's `symbol_patterns` match the wrong kernels, show the evidence and end
the dossier `UNGROUNDED` (§ 7. Status Line). The audit sends the finding back
to the miner.

### 4. Removable Fraction

Bound what a fusion or a better kernel could remove: the bytes that need not
move and the kernels that need not launch. State it as a bound with its
boundary, not as an expected gain. `references/fusion-feasibility-heuristics.md`
lists the boundaries to check.

### 5. Technology Landscape

Write this section only when the packet assigns it: the top three families by
`addressable_e2e` in `mined.json`. The other dossiers leave it out. Supply
facts, not recommendations:

```markdown
### <kernel label / source path>
- Authoring class: <Triton | CuTeDSL | CUTLASS | CUDA C++ | library:name | unknown>
- Evidence: <symbol and source-dispatch evidence>
- SM generation (this deployment): <SM80 | SM89 | SM90 | SM100 | SM120 | SM121>
- Op character: <structured tensor-core | irregular / dynamic-shape | novel algorithm | library extension>
- Library coverage for this op+shape+dtype: <nearest mature kernel or grounded none-found result>
```

Read the authoring class off the symbols and the dispatch/source paths, and the op character off the dominant dataflow. Search the engine source and its vendored libraries (`references/nsys-profiling-guide.md` § Stage 2 Mining lists the Pumice paths) before you claim no coverage. Write `unknown` only after you inspect the symbol, trace dispatch, search the vendored sources, document those attempts, and give a concrete follow-up experiment.

### 6. Correctness Invariants

List what a change to this family must preserve: dtype and accumulation
precision, shapes and layouts, CUDA graph capture safety, and every invariant
in `rounds/{N}/constraints.md` that touches these kernels.

### 7. Status Line

The worker writes the last line of the file, one of these:

- `PASS`, after its reviewer returns PASS.
- `UNGROUNDED: <reason>`, when the review loop ends without a PASS or the
  family's patterns or ceiling are wrong
  (`orchestration/mining-orchestration.md` § Ungrounded Dossiers).

## Cross-Cutting Dossier

The cross-cutting dossier covers the time that no single family owns. It keeps
sections 6 and 7, has no Technology Landscape, and replaces sections 1 to 4
with:

1. Gap accounting per decode step: `measured_idle_ns` and `in_graph_fraction`
   from `mined.json`.
2. Launch overhead compared with kernel time.
3. CUDA graph replay boundaries.
4. The MoE routing glue chain, when the model has one.
5. At TP above 1, the per-rank comparison of `references/nsys-profiling-guide.md`
   § Report Checklist: one row per rank, with stragglers and collective skew
   named, and the per-rank check result (§ TP>1 Per-Rank Caveat).
