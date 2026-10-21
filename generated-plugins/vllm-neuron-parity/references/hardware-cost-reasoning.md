# Hardware cost reasoning — Trainium2-class NeuronCores

How to predict what an op costs on this hardware before you build it, how to read a profile down to the bound that sets its time, and how far each kind of prediction can be trusted. The graph owns every rule and bar: the cost-before-code principle and the design's cost table. The run's hardware calibration record owns every rate. This file teaches the shape of each cost, so that a cost row names the binding resource and cites the right calibration row. Where this file and the graph disagree, the graph wins.

## Contents

0. [How to use this](#0-how-to-use-this)
1. [The machine](#1-the-machine): engines, memories, DMA, the Tensor Engine
2. [Costs from first principles](#2-costs-from-first-principles): rates, fixed costs, chains, the throttle, floors
3. [How code reaches the engines](#3-how-code-reaches-the-engines): kernels, compiler code, caches, hazards
4. [Find the bound](#4-find-the-bound): the profile funnel, five bounds, the decision tree, Amdahl
5. [Choose a lever](#5-choose-a-lever): lever families, stacking
6. [Prove it](#6-prove-it): test levels, correctness, pre-registration, trust, the carry, landing
7. [The cost row, worked](#7-the-cost-row-worked)

## 0. How to use this

| Seat | When | Read |
|---|---|---|
| investigator | costing routes; the screen's untried-lever question | §2, §4.4, §5 |
| implementer | the execution map, cost table and numerics plans | §1, §2, §7, §6.2 |
| implementer | before a kernel build and its kernel-alone ratio run | §3, §6.1-§6.3 |
| measurer | the calibration suite; a served profile and its cap row | §2.4, §4.1, §6.5 |
| adjudicator | a failed performance gate: which row is over | §4.2, §6.4, §7 |

Every number here carries a label:

- MEASURED: read from a device run. MEASURED PROXY: read on a stand-in, such as a profiled server or one profiled call.
- DERIVED: arithmetic on labelled numbers.
- ESTIMATE: a model, a schedule or a guess with no device anchor.
- DOC: stated by the vendor documentation for this hardware class. Confirm it at the pin.

Case numbers are dated evidence rows from one prior campaign on this hardware class (2026-10). Each one proves a rule. None is a rate to cite: cite the run's calibration record. Name a calibration row by its cost class and its access pattern, because a row for another pattern is no row.

Terms: a **roofline** is the time at one resource's peak. The **critical path** is the longest chain of dependent steps. A **serial chain** is steps that each wait on the previous. The **throttle** is a hardware cap on Tensor Engine rate. The **carry** is the served gain over the kernel-alone gain. The **A/A floor** is the time gap between identical runs. The **oracle** is the fp64 reference the output matches.

## 1. The machine

### 1.1 Engines, and the slowest one on the critical path

A NeuronCore has four compute engines and a Sync engine. Each engine runs its own instruction stream in program order (DOC).

| Engine | Its job | Calibration rows a cost row cites |
|---|---|---|
| Tensor (the PE array) | matmul, transpose | PE rate per dtype; PE instruction floor |
| Vector | elementwise ops; reductions along the free axis | engine rate; instruction floor; compare rate |
| Scalar | activations such as exp; copies out of PSUM | engine rate; instruction floor |
| GpSimd | programmable processors; software DMA descriptors | compare rate; gather cost |
| Sync | starts DMAs | DMA descriptor cost |

The engines run at the same time. An engine waits only when its next instruction needs another engine's result. So a kernel's time is its critical path, and a faster engine off that path changes nothing. Cost the work per engine, then find the longest chain. Evidence, 2026-10: in a sparse attention call the PE was busy 61 % of the call and sat idle while only GpSimd worked for another 25 % (MEASURED). GpSimd was writing gather descriptors (§2.3).

On an LNC2 core (two physical cores run as one) each physical core has its own engines and half of the logical core's HBM share (DOC). Cost per physical core, and cite rates per physical core.

### 1.2 Memories, partitions and tiles

```
HBM (off-chip: weights, KV cache) --DMA--> SBUF (on-chip, 128 partitions) <-- every engine reads and writes here
SBUF --stationary + moving operands--> [Tensor Engine] --> PSUM (matmul accumulator) --Vector or Scalar copy--> SBUF
```
Caption: the structure is DOC. Read sizes from the docs at the pin and rates from the calibration record.

- A partition is one of 128 parallel lanes (DOC). One instruction works on all lanes. A tile is a block of data across the partitions.
- The compute engines cannot read HBM. Only Vector and Scalar read PSUM; DMA and GpSimd cannot (DOC). So every matmul result costs a Vector or Scalar copy before a DMA can move it.

Every byte an engine uses crosses a DMA first. So the cost of an op is often the cost of its DMAs, and a row that counts FLOPs alone misses it.

### 1.3 DMA: queues, descriptors and the two descriptor generators

A DMA engine copies bytes while the compute engines work. A descriptor is one DMA work item: from where, to where, how many bytes. Each DMA engine has several queues, and a transfer goes to one queue ID across all of the core's DMA engines (DOC). Small, frequent transfers pay a large overhead; transfers run best at 4 KiB or more per partition (DOC). A DMA can also transpose a tile between SBUF locations, which takes layout work off the compute engines (DOC).

| Descriptor writer | Indexed gather or scatter? | Cost shape | Calibration row |
|---|---|---|---|
| hardware generator, driven by Sync or Scalar | no, on this class (DOC) | fixed per DMA | DMA descriptor cost |
| software generator, run on GpSimd | yes, the only path (DOC) | fixed per DMA + per offset | gather or scatter cost, per pattern |

A gather copies rows named by an index list; a scatter writes rows to addresses named by data. Both run through GpSimd, so a gather-heavy op competes with all other GpSimd work and queues on the software generator (§2.3).

### 1.4 The Tensor Engine: stationary, moving, and why layouts force transposes

A matmul has a stationary operand, loaded into the array once, and a moving operand, streamed through it column by column. The result lands in PSUM. Three rules decide where data sits (DOC):

1. The PE sums over the partition axis, so both inputs hold the summed index on the partitions.
2. The output puts the stationary operand's free axis on the partitions.
3. Vector reduces only along the free axis. To reduce across partitions, transpose first.

When two steps in a row need different axes on the partitions, the data turns between them. Attention is the common case: the score matmul sums over the head dimension, softmax reduces over keys with queries on the partitions, and the output matmul sums over keys. No one layout suits all three, so each key tile and each probability tile turns at least once. Put the transposes in the row as PE instructions or DMA transposes. Evidence, 2026-10: in one attention kernel the transposes took 28 % of PE time (DERIVED).

Width: a product streams max(rows, 64) PE cycles (DOC). So a matmul with one useful column costs about what one with 64 costs. Count PE time by streamed columns, not by useful FLOPs.

## 2. Costs from first principles

Build a prediction from four shapes: a rate per resource, a fixed cost per item, a serial chain, and the throttle. Then add the waits. Each shape cites a different calibration row.

### 2.1 A roofline per resource

A resource's roofline is the work it must do divided by its calibrated rate. The floor of the work is the largest roofline over all resources.

| Resource | Work to count | Rate row to cite |
|---|---|---|
| Tensor Engine | instructions × streamed columns, at the dtype | PE rate per dtype; PE instruction floor |
| Vector, Scalar, GpSimd | instructions × elements | engine rate; instruction floor |
| HBM | bytes read and written | HBM rate per physical core, per pattern |
| DMA | descriptors, and bytes per descriptor | descriptor cost; gather or scatter cost |
| collectives | calls, and bytes per call | collective cost at the served rank count |

The roofline ratio is r = measured time ÷ roofline, and the graph sets the bar. Build the roofline from the op's minimum work (the bytes it must move and the instructions it must issue), never from the chosen form's own work, because a form's floor excuses the form.

| Evidence, 2026-10: one sparse attention call | Time | Label |
|---|---:|---|
| each needed row read once: the op's minimum bytes | 0.21 ms | DERIVED |
| every byte the form gathered, at the HBM rate | 3.19 ms | DERIVED |
| useful FLOPs at the PE peak | 0.03 ms | DERIVED |
| the serial gather chain (§2.3) | 9.78 ms | DERIVED |
| measured | 10.2 ms | MEASURED |

Caption: only the chain floor is near the measured time. A bytes-or-FLOPs row names the wrong bound, and the roofline is the first row, not the second.

### 2.2 Fixed costs: per instruction, per DMA, per transfer

A fixed cost is paid per item, whatever its size. When items are small, fixed costs win. Count the items, and cite a fixed-cost row for each kind.

| Item | Fixed cost shape | Calibration row |
|---|---|---|
| PE instruction | about the same at any width up to a full tile | PE instruction floor |
| PE product | at least 64 streamed columns (DOC) | PE rate per dtype |
| hardware-generated DMA | fixed per DMA | DMA descriptor cost |
| software gather or scatter | fixed per DMA + per offset | gather or scatter cost, per pattern |
| collective call | fixed per call | collective cost at the served rank count |

The PE rule is fewer, wider instructions. Evidence, 2026-10: a 32 × 32 PE matmul cost the same as a full tile, about 270 ns each (MEASURED). A lever that tiled finer ran 2.7 times the PE instructions and lost 8 µs per call, and 15 µs at twice the rows (MEASURED).

The descriptor rule: split a transfer into its fixed part and its proportional part before you scale either one. A software gather costs `t_gather = t_fixed + n_offsets × t_offset + bytes ÷ rate_pattern`, where t_fixed holds the first-byte latency and the queue restart. Evidence, 2026-10: a gather of about 2,200 rows took 19 µs, and about half of it was descriptor and queue cost that does not shrink with bytes (MEASURED, DERIVED). Halving its bytes could shorten only the other half.

### 2.3 Serial chains

A serial chain's time is the sum of its step latencies. More engines, more queues and better overlap do not help it; only shorter steps or fewer steps do. Look for three kinds:

- A queue chain. The software generator starts a gather only after every DMA before it on its queue completes (DOC). When all of a core's indirect DMAs share one queue, its gathers run one after another.
- A state recurrence. A carried state makes one dependent step per sub-chunk: T ÷ C steps for T tokens in sub-chunks of C. A larger C cuts the steps and changes the numerics, so cost both.
- A chain between engines. Engine A waits on B, which waits on C for an item two steps ahead. The engines can be level per item and still wait.

Evidence, 2026-10: on the device, 0 of 511 neighbouring gather pairs overlapped (MEASURED), so 512 gathers × 19.1 µs = 9.78 ms per call (DERIVED) against 10.2 ms (MEASURED). In a balanced kernel the engines were near level per item (PE 9.4, Vector 9.2, Scalar 8.6 µs busy), yet a probability copy started a median 3.0 µs after its data was ready (MEASURED): the waits came from the chain, not from a full engine.

A census of work per engine assumes the work overlaps. A chain does not overlap. Put the chain length in the row, and cost the chain by its critical path.

### 2.4 The throttle and the served utilization cap

The Tensor Engine has its own current limiter. It starts each burst at about half its maximum rate and ramps to full over about 3 µs; if the PE runs under half busy in any 3 µs window, the cap comes back (DOC). The docs name two causes: a fast rise in current from idle to busy, and sustained power or heat near the chip's budget (DOC). So a kernel that runs the PE in short bursts is capped most of the time.

Read it in a profile:

- Throttle rows are hardware notices, not events. Their k/n is the allowed utilization; the common record was 4/8, a 50 % cap (MEASURED, 2026-10).
- u is the mean allowed PE utilization over a call. r_adj = r × u is the roofline ratio with the cap taken out. Read both.
- No host counter shows the throttle, and device power read in single-digit percent (MEASURED, 2026-10). Power readings do not reveal it.

Price every PE time under a cap row. Cite the served cap row for that engine when the calibration record holds one, and the suite's synthetic-load cap row until then. A time priced under the synthetic row is re-rated at measurement: t_served = t_priced × (u_priced ÷ u_served).

| Evidence, 2026-10: kernel run alone | Throttle on | u | Label |
|---|---:|---:|---|
| PE in short bursts between gathers | 59 % | 0.70 | MEASURED |
| PE streaming wide matmuls | 17 % | 0.92 | MEASURED |

The docs give three fixes (DOC): keep the PE at half busy or more, overlap DMA with compute, and in a kernel bridge idle gaps with dummy matmuls. Evidence, 2026-10: filling the PE gaps cut the throttle loss from 14-15 % to 5-6 % of PE time (MEASURED PROXY).

### 2.5 From op floors to a phase floor

A phase floor adds the floors of every op over every chunk of the phase. State what it leaves out: a floor that sets collectives, gaps and staging to zero is a lower bound, not a target. A floor also carries its access-pattern assumptions: one that assumes each chunk reads the prefix once is wrong for a form that reads each row many times.

Evidence, 2026-10: a per-op cost model of the whole prompt held the measured time inside its range at two lengths (ESTIMATE against MEASURED), so use one to rank, as a range. A floor that assumed one read of the prefix per chunk missed a sparse form that moved each needed row about 16 times per call (MEASURED). After every landed lever the served time at one length was still 3.2 times the floor that zeroes collectives and gaps (DERIVED): expect the rest in waits, collectives and compiler-made code.

## 3. How code reaches the engines

A served graph holds two kinds of code. An NKI kernel names each engine instruction, DMA and SBUF tile. Compiler-made code is the torch glue around the kernels, lowered op by op. A profile shows compiler-made code with no kernel name as unattributed time, and the space between ops as gaps. Give each a row when it is above the design's tolerance.

The compiler overlaps a collective with work that does not need its result, and it reorders each engine's stream. It also makes costs that no kernel row predicts. Evidence, 2026-10 (MEASURED PROXY unless marked): it recomputed a scale on every gathered row in each MoE layer, 3.4 ms per chunk; it added and cast outputs in separate full-size passes, part of 14 ms per chunk; it built an attention mask as a scatter, 6 ms per call (MEASURED); and it refused a gather form with one offset per group of rows (MEASURED). Its schedule model put two kernels 5 µs apart that the device put 838 µs apart (ESTIMATE against MEASURED). A schedule is a hypothesis; only the device ranks two forms.

Caches, keys and flags:

- The kernel-config cache keys a kernel by file base name, function name, kernel source and argument shapes, not by the helpers it calls (MEASURED, 2026-10). Variants that differ only in helpers share a key, and the device runs the first one for all. Give each variant its own key, and gate each timed row on the key it loaded.
- The served runner compiled with the verifier off (DOC, at that pin). A kernel-alone compile that copies those flags can pass a form the in-graph compile refuses. Compile each variant in the graph with the verifier on before you time it.

Hazards seen at this layer in 2026-10, on that campaign's compiler, each worth a probe before a design depends on it: a zero-fill dropped before a partial-partition write; a device-loop kernel after a fixed-address kernel fails to compile; an in-graph dtype view fails to lower; the PE transpose mode refused on a 32 × 32 tile. The rules for reading compiler and runtime evidence are in `references/toolchain-evidence-pitfalls.md`.

## 4. Find the bound

### 4.1 The profile funnel and its traps

Each level splits the one above it and names the next target. The rows of a level add up to the level above; a level that does not add up hides a cost.

```
[served phase time, at the client] -> [one chunk: the late ones cost most] -> [op classes: attention,
collectives, MoE, glue, gaps, unattributed] -> [one call: busy time per engine] -> [what the critical engine waits on]
```
Caption: each arrow is one more profile or one more read.

A profile is a device trace of each instruction, DMA and wait, and the only way to see the engines. Read it through its traps:

| Trap | What to do |
|---|---|
| a profiled server runs with async scheduling off | label rows MEASURED PROXY; check they sum to the served time |
| one profiled call | label throttle and engine rows MEASURED PROXY |
| a PE row can carry another instruction's name | treat a named time as a lower bound |
| Throttle rows | read k/n as an allowed utilization |
| the profile reads some ranks only | treat a cross-rank wait as a lower bound |
| the profiler's own start-up wait | remove it before you attribute |
| one benchmark input | confirm the pattern on a second input first |

Evidence, 2026-10: profiled chunk rows summed to the served time within 2 % (DERIVED), and a late-rank pattern came from one repeated text, so no lever was built for it. Traps outside profiles are in `references/measurement-pitfalls.md`.

### 4.2 Five kinds of bound

| Bound | Sign in a profile | Lever family | Evidence, 2026-10 |
|---|---|---|---|
| engine work | one engine busy most of the span, near its roofline | cut the work; change the data | PE and Vector each over target (ESTIMATE) |
| bandwidth | bytes ÷ rate near the measured time | fewer bytes; better pattern | bytes 3.2 of 10.2 ms: not bound (DERIVED) |
| instruction issue | many small instructions at fixed cost | fewer, wider items | 2.7x PE instructions, slower (MEASURED) |
| serial chain | one queue or one dependency, no overlap | shorten or share steps; new algorithm | 512 gathers in a row (MEASURED) |
| wait on an engine, core or rank | the critical engine idles while it waits | move or reorder work; balance load | PE waits on Scalar copies (MEASURED) |

### 4.3 The decision tree

```
Is one engine (or queue, or link) busy for most of the span?
├── yes: is its work near that resource's roofline?
│   ├── yes -> engine work or bandwidth: cut the work, or change the data
│   └── no  -> instruction issue: count instructions; fewer, wider ones
└── no: what does the critical engine wait on?
    ├── its own previous step on one queue -> serial chain: shorten or share the steps
    ├── another engine's output            -> engine wait: move or reorder work
    └── another core or another rank       -> rank wait: balance the load
```
Caption: one profile of one call answers the first question. A read of the waits answers the second.

Evidence, 2026-10: one attention call walked the tree four times. The sparse form was a serial chain on the gather queue; its replacement was engine work on PE and Vector; a Vector-bound version moved work to Scalar; the balanced version was an engine wait on Scalar copies. Each lever moved the bound, so walk the tree again after each one.

### 4.4 Amdahl: from a kernel gain to a served gain

`new time = old time × ((1 − p) + p ÷ s)`, where p is the share of time in the part you speed up and s is how many times faster it gets. As s grows without limit, the new time falls only to old time × (1 − p). So a part that is p of the time can never save more than p.

Rank work by its end-to-end gain, not by how easy it is to prove. Set a gain line before the run, as a share of the phase time, and drop on its page a lever predicted under it. Evidence, 2026-10: in one long-context chunk attention was 53 % of the time and MoE with its router 9 % (DERIVED from one MEASURED chunk profile). Doubling attention's speed saves 26 %; an infinitely fast MoE saves at most 9 % (DERIVED).

## 5. Choose a lever

### 5.1 The lever families

Each bound has levers that attack it, and each family has a way to backfire.

| Family | Attacks | Backfires when | Evidence, 2026-10 |
|---|---|---|---|
| change the algorithm | a chain; a bound class | a new side cost appears | read-once form 57 % faster alone; a sharing form closed |
| change the data | bytes; op count | precision class; fixed part stays | fp8 rows gave 0.28-0.48 of prediction |
| move work between engines | engine work; engine wait | new engine slower than its borrowed rate | GpSimd compares 1.6-2.7x estimate |
| cut instructions or descriptors | issue; fixed costs | compiler refuses; narrower instructions | split gather 11-14 % faster |
| overlap | a wait | work beside a collective slows it | collective beside compute about 2x |
| restructure communication | collective bytes; repeats | new kernel contracts | sequence parallelism −3.6 s per prompt |
| fuse compiler-made code | passes; recomputation | precision change inside it | two fusions −0.49 s per prompt |
| cut per-chunk fixed costs | per-chunk costs | every kernel needs a new shape | larger chunk 9 % faster, older build |

Caption: each evidence cell is MEASURED, except the fp8 and GpSimd ratios, which are DERIVED.

Two rules come from those rows beside the ones in §2. Price a new engine rate on the device first, because a borrowed rate missed by about 2 times (§6.4). At many ranks collectives do not hide under compute: a collective beside compute took about 2 times its time alone (MEASURED, 2026-10).

### 5.2 Do levers stack?

Levers on different parts of a phase add. Levers inside one kernel can stack, each step faster than the last (MEASURED, 2026-10). Different forms of one call replace each other: a window runs only one form. Levers can conflict: GpSimd compares slowed the Vector work beside them and the kernel ran 2 times slower (MEASURED, 2026-10). A lever can depend on a form: narrower rows speed up only a gather form. So cost a combination as one row, never as the sum of the levers' separate gains.

## 6. Prove it

### 6.1 Three test levels, and what each cannot see

| Level | What it shows | What it cannot see |
|---|---|---|
| kernel alone (arms A, B, A2) | kernel time, busy per engine, throttle, output words | served cap; graph-only compile checks; neighbour work |
| in the graph, profiled | the call beside the model's other work, per engine | served timing (rows are PROXY) |
| served, timed at the client | the user's number | which part of the run moved |

Evidence, 2026-10: one lever, three levels. Kernel alone it was 838 µs faster per call (MEASURED). In the graph it was 7 µs slower, inside a 60 µs noise (MEASURED). Served, it gave 17 % of its estimate (DERIVED). A kernel-alone gain is a hypothesis about the served gain, not a measurement of it (§6.5).

### 6.2 Correctness classes and the numerics plan

A float add rounds after every step, so (a + b) + c can differ from a + (b + c). In fp32, with a = 2^24 and b = c = 1, the first order gives 2^24 and the second gives 2^24 + 2 (DERIVED). So most speed levers change the last bits.

| Class | What changes | How it is proven |
|---|---|---|
| bit-equal | nothing | compare every output word as an integer |
| value-equal | the order of a sum | every error inside a derived rounding bound |
| precision class | stored or computed precision | an error bound against the oracle, then a task-quality check |

Compare as integers, because a float compare passes +0 against −0 and fails every NaN, so it can pass outputs whose bits differ. Each class beyond bit-equal needs the owner's word before a lever in it lands. A task-quality check has its own noise. Evidence, 2026-10: at 200 questions the standard error was about 1.7 questions (DERIVED), so one run cannot tell a loss of one or two from noise.

The numerics plan carries the class, the oracle and the regimes the graph lists. It refuses two forms: a mask applied by a multiply to an operand that can hold a non-finite value, because the multiply spreads a NaN; and an algebraic identity trusted without a finite-precision bound.

### 6.3 Pre-registration, the A/A floor and the device execution of record

Write the reading rules before the run, and apply them in order, so a result cannot be read to fit a hope. The plan states the arms and windows; the bars (what FASTER, EQUAL and SLOWER mean, and the correctness class); the reading rules in order; the stop rules; and the time cap with its failure codes.

- Run A (base), B (candidate), then A2 (base again) in one stage. The time of record is the median of the timed sets' medians.
- The A/A floor is the A-to-A2 difference. A gain under it is noise. Kernel-alone floors are tiny; served floors are not.
- A served row reads FASTER only when its gain exceeds the registered noise limit, and it lands only above the gain line (§4.4). The prior campaign set the limit at 2 times the spread of its three timed requests (DOC, 2026-10).
- Before any A/B, run one short device run of the new kernel with its own checks: its device execution of record. Some faults show first on the device. Evidence, 2026-10: a count step summed partitions that nothing had written, and the device faulted (MEASURED).
- Write a design's stop rule before its probe. For example, a design that needs a DMA rate states the rate under which it closes.

### 6.4 How far each kind of prediction missed

The kind of prediction tells you how much to trust the next one.

| Kind of prediction, evidence 2026-10 | How far it missed | Rule |
|---|---|---|
| a compiler schedule, no device anchor | 3 to 15 times; cannot rank two kernels | never decide on it alone; run the device |
| an engine rate borrowed from other kernels | about 2 times | price the rate on the device first |
| a byte saving counted on the whole cost | 2 to 4 times too high | split the cost into parts first |
| a design from the same kernel's profiles | inside its range to 16 % slow | a good guide alone; still measure |
| kernel-alone saving × calls, for the served gain | 6 times too high to 3 times too low | a hypothesis; read the carry |
| a per-op cost model of the whole phase | inside its range | good for ranking, as a range |

Caption: each miss is DERIVED from a MEASURED result against an ESTIMATE or DERIVED prediction.

The wrong answers behind those misses had five cost causes, each with a rule. No device anchor: label the number ESTIMATE and anchor it before a decision. A fixed or side cost left out: split the cost into its parts before you scale one. A size or edge case not checked: check it at the real shapes. A hardware form not tested: test it with the smallest probe. A cause guessed from a pattern: name a mechanism only from the docs or a measurement. Date every fact, because a true fact can change with the code.

### 6.5 The carry

The carry is the served gain divided by the kernel-alone estimate. Evidence, 2026-10: it was 141 % for one lever and 17 % for the next (DERIVED). The second lever made the Tensor Engine the binding engine, and the served cap took its gain.

| Per call, same window, evidence 2026-10 | Lever alone | Lever in graph | Base alone | Base in graph |
|---|---:|---:|---:|---:|
| binding engine | Tensor | Tensor | Vector | Vector |
| Tensor busy lost to the throttle | 3 % | 18 % | 7 % | 17 % |
| allowed utilization u | 0.97 | 0.80 | 0.93 | 0.82 |

Caption: MEASURED over 8 ranks and 9 calls. The base's throttle loss fell in idle Tensor time, so its call did not grow.

Predict the served call as the longer of two paths: `max(Tensor work ÷ u_served + Tensor waits, Vector busy + Vector waits)`. After the fact, on one build, this came within 0.5 % of the measured call (DERIVED, 2026-10). Use it as a forecast: apply the served cap row to the kernel-alone profile, engine by engine, before you file a served run. A lever that makes the Tensor Engine bind is over-stated by its kernel-alone gain.

### 6.6 What "landed" needs

`[kernel bar: FASTER, class proven] -> [served gain over the noise limit and the gain line] -> [token ids equal, in range, NaN checks unchanged] -> [task quality, when not bit-equal] -> [the owner's decision] -> [the hand-off branch compiles the measured programs]`

The owner decides at the fifth step and can land a lever the gain line closed. A variant replaces another for future work only when it is FASTER at every window and in the same correctness class. A hand-off branch that is not served again proves itself by program equality: each changed call compiles, at every served window, to the program that was measured.

## 7. The cost row, worked

The op: for each of Q items, gather R rows of B bytes from HBM by an index list, then one matmul of the item's 1 × D vector against its R × D rows (B = 2D in bf16). Paged attention, embedding lookups and token-to-expert gathers share this shape. The reference form issues one indirect DMA per item.

Step 1. Write the op's minimum work, independent of any form: unique bytes U, each needed row read once, at most min(Q × R, rows in the window) × B; and FLOPs, 2 × Q × R × D.

Step 2. Fill the row for the reference form. Each value is DERIVED from the shapes or cites a calibration row.

| Field | Reference form: one indirect DMA per item | Calibration row it cites |
|---|---|---|
| op, shapes, substrate | gather + matmul at the served Q, R, B, D; kernel-class | none |
| bytes moved | Q × R × B: items re-read shared rows | none |
| DMA descriptors | Q gathers of R offsets, software generator | none |
| engine mapping | GpSimd descriptors; DMA moves; PE products; Vector or Scalar reads PSUM | none |
| PE instructions | per item, one product per row tile; each streams at least 64 columns | PE instruction floor |
| serial-chain length | Q gathers per physical core, one queue | none |
| step time | t_fixed + R × t_offset + R × B ÷ rate_pattern | gather cost, row-per-index pattern |
| bytes floor | Q × R × B ÷ HBM rate | HBM rate per physical core, gather pattern |
| PE floor | instructions × max(instruction floor, columns ÷ column rate) ÷ u | PE rate per dtype; cap row |
| chain floor | Q × step time | gather cost, row-per-index pattern |
| predicted time | max(chain, bytes, PE floors) + waits: the chain binds | served cap row, else synthetic-load row |
| roofline | max(U ÷ HBM rate, FLOPs ÷ (PE rate × u)) | HBM rate; PE rate per dtype; cap row |
| predicted ratio | predicted time ÷ roofline: far over the bar | none |
| hazards avoided | none named yet | the run's hazard items |
| hardware reason | none valid: the binding floor is the form's own chain | none |

Caption: every value is DERIVED from the shapes and the cited rows. The fields are the graph's cost-row fields.

Step 3. Read the row. Bytes and FLOPs say the op is cheap; the chain floor says it is not. The binding resource is the software descriptor queue: a serial chain of items, each with a fixed cost. The ratio is over the bar, and "the form is at its floor" is no hardware reason, because the roofline is the op's minimum work. So the design re-derives the form now, before any code.

Step 4. Cost the other forms the same way, and choose at the roofline:

| Form | What changes | New bound to check |
|---|---|---|
| read the window once, mask unselected rows | bytes fall to U; no chain; wide products | PE or Vector work; mask cost; throttle u |
| share one gather among items with the same rows | fewer, wider gathers | the sharing producer's cost |
| narrower dtype per row | the transfer part only | fixed half stays; a precision class |

Caption: an op that a form adds (a mask, a producer, a transpose) is costed inside this row.

Step 5. Anchor before you trust. When the record has no row for a pattern a form uses, the graph's anchor rule applies: run the costed form kernel-alone under a job lease and append its row first. Then read the kernel-alone ratio against this row, and forecast the served call with the served cap (§6.5).

Evidence, 2026-10, that this reading holds: the chain floor came within 5 % of the measured call (DERIVED against MEASURED). The read-once form ran 57 % faster kernel-alone (MEASURED). The sharing form's producer, priced from a compiler schedule with no anchor, took 13.5 times its prediction, and the form closed (MEASURED).
