---
name: neuron-framework-autoport-vllm-neuron
description: Port a HuggingFace model architecture to the vLLM-Neuron Trainium2 backend. Handles architecture research, code generation, and validation.
allowed-tools: Read Write Edit Bash Grep Glob WebFetch Agent
argument-hint: "<model-name> <hf-model-id> [--review]"
---

# vLLM-Neuron Model Porting Skill

Port the HuggingFace model `$1` to the vLLM-Neuron Trainium2 backend as `$0`.

## Conventions

- `MODEL_NAME` = `$0` (snake_case, e.g., `yi`, `deepseek_v2`)
- `HF_MODEL_ID` = `$1` (e.g., `01-ai/Yi-6B-Chat`)
- `--review` flag (if present in `$ARGUMENTS`): pause at Step 2 for user confirmation
- Derive `PascalName` from MODEL_NAME (e.g., `yi` → `Yi`, `gpt_neox` → `GPTNeoX`)
- Model code goes in `vllm_neuron/model` 
- Example script goes in `examples/vllm_neuron/models/MODEL_NAME/`

## Dry-run

When the user specifies `dry-run`:
- **Skip** all agent-level prerequisites (package import checks, NeuronCore check via `neuron-ls`)
- **Activate** the provided venv and resolve source paths by filesystem lookup (do NOT use `import` — dependencies are not installed):
  ```bash
  export PATH=<pathToVenv>/bin:$PATH
  VLLM_NEURON_SRC=<node artifact dir>/scratch/vllm_neuron   # run-artifact path (vllm-neuron-parity references/artifact-layout.md §3)
  TRANSFORMERS_SRC=$(python3 -c "import site; print(site.getsitepackages()[0])")/transformers
  ```
- **Do not run any code** — no compilation, inference, or validation (no Trainium hardware available)

## Before You Start

Ensure the agent-level prerequisites have been completed (venv activation, package verification, NeuronCore check). These steps are owned by the agent prompt and should not be re-executed here.

> **Note:** Never clear `/var/tmp/neuron-compile-cache` — it is a shared system directory and other processes or users depend on it (the vllm-neuron-parity plugin's P2). On a `[NLA001]` JSON parse error or `FileNotFoundError` on neff_output paths, compile into a fresh cache root you own instead.

## Step 0: Parse & Detect State

1. Package check: Ensure that `private_vllm_neuron` or `vllm_neuron` package exists in the user's workspace. If it does not, STOP and do not proceed without informing the user.
2. Parse `$ARGUMENTS` — extract model-name, hf-model-id, and `--review` flag.
3. Check if `vllm_neuron/model/MODEL_NAME/` already exists:
   - If model.py + registry entry both exist → skip to **Phase C** (validation)
   - If model.py exists but not registered → resume at **Step 5** (register)
   - If directory doesn't exist → proceed to **Phase A**

---

## Phase A: Research & Analysis

### Step 1: Architecture Research

Gather all information needed to port the model. Do NOT write any files yet.

**1a. Fetch HF config:**
```python
python3 -c "
from transformers import AutoConfig
import json
c = AutoConfig.from_pretrained('HF_MODEL_ID', trust_remote_code=True)
print(json.dumps(c.to_dict(), indent=2, default=str))
"
```

Extract and record:
- `architectures` field (exact string for registry, e.g., `"YiForCausalLM"`)
- `hidden_size`, `intermediate_size`, `num_hidden_layers`
- `num_attention_heads`, `num_key_value_heads`, `head_dim` (compute if missing: hidden_size / num_attention_heads)
- `vocab_size`, `max_position_embeddings`
- `rms_norm_eps` or `layer_norm_epsilon`
- `rope_theta`, `rope_scaling` (NOTE: some models use `rope_parameters` instead — check both)
- `hidden_act` (activation function)
- `tie_word_embeddings`
- Bias flags: `attention_bias`, `mlp_bias`, `lm_head_bias` (or check transformers source)
- Any model-specific fields: `sliding_window`, `num_local_experts`, `partial_rotary_factor`, etc.

**1b. Read HF transformers source:**
Find the modeling file for this architecture. Check:
- Exact weight names (for checkpoint key mapping in `load_weights`)
- Whether Q, K, V projections are fused or separate in the checkpoint
- Bias presence on each projection (Q, K, V, O, gate, up, down, layernorm)
- Activation function implementation
- Any non-standard behavior (e.g., parallel residual, special normalization)

**CRITICAL — Verify normalization type by reading the forward() implementation, not the class name.**
Some models name their norm class "RMSNorm" but actually implement full LayerNorm (with mean subtraction and bias). The difference:
- RMSNorm: `variance = x.pow(2).mean(); x = x * rsqrt(variance + eps)` — no mean subtraction, no bias
- LayerNorm: `mean = x.mean(); variance = (x - mean).pow(2).mean(); x = (x - mean) / sqrt(variance + eps)` — has mean subtraction, may have bias

Using the wrong normalization produces subtly wrong output that passes smoke tests but fails accuracy validation.

**1c. Read the vLLM-Neuron porting guide:**
Read the canonical model bringup guide at `doc/vllm_neuron/source/design/framework/model_bringup.md`. It designates GPT-OSS BF16 (`vllm_neuron/model/gpt_oss/model_bf16.py`) as the canonical annotated reference: `# >>> PARALLELISM <<<` blocks are infrastructure to keep as-is when porting; `# <-- MODEL-SPECIFIC` blocks are what change per architecture. The canonical code is a structural template, not a spec for the target model — derive all model-specific decisions from the target's HF `modeling_*.py` and `config.json`, never copy MODEL-SPECIFIC sections blindly. Read the parallelism design docs it links (`doc/vllm_neuron/source/design/parallelism/` — TP/SP, DP, EP) before porting; misunderstanding a collective or shard dimension causes silent accuracy bugs.

**1d. Select best reference model:**
Based on the architecture analysis, pick the closest existing vLLM-Neuron model as your copy source:

| If the model has... | Use reference |
|---|---|
| Standard GQA + RoPE (most common) | `llama3/` |
| QKV bias | `llama3/` (add bias params to QKV/O projections) |
| Q/K per-head RMSNorm | `qwen3/` (dense) or `qwen3_moe/` (MoE) |
| ALiBi attention (no RoPE) | `bloom/` |
| Mixture of Experts (fits in TP) | `qwen3_moe/` |
| Mixture of Experts (needs EP) | `gpt_oss/` (see EP section below) |
| Large MoE with MLA/multi-latent attention | `deepseek_v32/` |
| Learned position embeddings | `gpt2/` |
| Parallel residual | `gptj/` |
| Non-gated MLP (no gate_proj) | `starcoder2/` |
| Multi-Query Attention (1 KV head) | `starcoder2/` (set num_key_value_heads=1) |
| Vision-language model | `qwen3_vl/` |
| Full LayerNorm with bias (not RMSNorm) | `starcoder2/` |

Read the selected reference model's `model.py`, `config.py`, and `factory.py`.

**1e. Compute valid TP sizes:**
All 5 rules must be satisfied:
1. `num_attention_heads % tp_size == 0`
2. `(num_attention_heads / tp_size)` is **even** (NKI decode megakernel constraint)
3. `num_key_value_heads % tp_size == 0` OR `tp_size % num_key_value_heads == 0` (GQA replication)
4. `intermediate_size % tp_size == 0`
5. `vocab_size % tp_size == 0` (or use padding)

**Memory constraint for real hardware:** Each Neuron device has ~24GB HBM (shared between 2 NeuronCores in LNC=2 config). With TP=N, each rank holds ~(model_size_bytes / N) of weights plus KV cache. If per-rank weight memory exceeds ~20GB, increase TP size. MoE models are especially memory-hungry — 16 experts × 3 projections × hidden × intermediate × 2 bytes adds up fast.

Recommend the smallest valid TP size ≥ 2 that also fits the model in memory (rule of thumb: 2x model params in bytes < tp_size * 96GB per device).

**1f. Inspect checkpoint weight keys:**
```python
python3 -c "
from safetensors import safe_open
import glob
files = sorted(glob.glob('/path/to/weights/*.safetensors'))
for f in files:
    with safe_open(f, framework='pt') as sf:
        for k in sf.keys():
            if 'bias' in k or 'layer.0' in k.replace('layers.0', 'layer.0'):
                print(f'{k}: {sf.get_tensor(k).shape}')
"
```
This reveals which layers have biases, the exact key naming convention, and weight shapes. Essential for building the `load_weights` mapping correctly.

**1g. Evaluate Expert Parallelism (EP) need (MoE models only):**

If the model is MoE and no single TP size satisfies all 5 rules above while also fitting in memory, the model needs **Expert Parallelism (EP)**. EP uses a two-level parallelism: TP_sub for attention/dense layers, EP for distributing experts across groups.

Determine EP need:
- Compute `model_bytes = num_params * 2` (bf16)
- If `model_bytes / max_valid_tp > 24GB per NC` (with lnc=2 on trn2.48xlarge, 96GB per device) → EP required
- EP config: `world_size = 64` (full trn2.48xlarge), `ep_degree = world_size / tp_sub`
- Verify: `num_local_experts % ep_degree == 0` (experts divide evenly across EP groups)

**CRITICAL EP CONSTRAINT — Unified sp_group:**
The Neuron DGE cannot handle two different collective group sizes in the same NEFF. ALL collectives (attention, MLP, MoE, embedding, LM head, sampler) must use a single group — the full world group (64 ranks). Weight sizing uses the sub-group (tp_sub ranks) but collectives go through one unified group. Violating this causes `NEFF Warmup failed with status 1006`.

Reference: `gpt_oss/model_bf16.py` for the working EP pattern.

### Step 2: Architecture Analysis

Present a summary comparing this model to the reference:

```
## Architecture Analysis: MODEL_NAME

Reference model: <selected reference>
HF architecture: <exact string>
Recommended TP: <size>

| Section | Reference | This Model | Change Needed |
|---------|-----------|------------|---------------|
| Norm    | RMSNorm   | ...        | ...           |
| RoPE    | Llama3    | ...        | ...           |
| Attention | No bias | ...        | ...           |
| MLP     | SwiGLU    | ...        | ...           |
| MoE     | N/A       | ...        | ...           |
| EP      | N/A       | ...        | ...           |
| Decoder | Pre-norm  | ...        | ...           |
| Backbone | Standard | ...        | ...           |
| LM Head | Tied      | ...        | ...           |

Known pitfalls: <list any that apply>
EP required: <yes/no — if yes, list ep_degree, tp_sub, world_size>
```

If `--review` flag is set: ask the user to confirm or override decisions.
Otherwise: print the analysis and proceed automatically.

---

## Phase B: Code Generation

### CRITICAL RULE: Search Before Implementing

Before implementing ANY module (norm, RoPE, attention, MLP, etc.), search for an existing implementation in this priority order:

1. **Shared functional ops**: `vllm_neuron/functional/` — `qkv_proj`, `o_proj`, `flash_attention`, `attention_decode`, `mlp`, `sample`, etc.
2. **Shared nn modules**: `vllm_neuron/nn/` — `ColumnParallelLinear`, `RowParallelLinear`, `VocabDimShardedEmbedding`, `Sampler`
3. **Other ported models**: `vllm_neuron/model/*/model.py` — many models share identical implementations of norms, RoPE, etc.
4. **NKI pre-built kernels**: Search the installed `neuronxcc` package for pre-prod kernels (e.g., `python3 -c "import neuronxcc; print(neuronxcc.__path__)"` then look under `nki/_pre_prod_kernels/`): `rms_norm/`, `layernorm_tkg.py`, `mlp/`, `qkv.py`, `attention_token_gen*.py`, `attn_fwd*.py`, `output_proj.py`, `moe_token_gen.py`, `topk/`
5. **NKI production kernels**: Search the installed `neuronxcc` package under `nki/kernels/`

**Only implement from scratch if no existing implementation matches.**

### Shared imports (ALWAYS use these, never reimplement):

```python
# TP layers
from vllm_neuron.nn import ColumnParallelLinear, RowParallelLinear
# Embedding
from vllm_neuron.nn.embedding import VocabDimShardedEmbedding
# Sampler
from vllm_neuron.nn.sampler import Sampler
# Functional ops (import as NF)
import vllm_neuron.functional as NF
# KV cache
from vllm_neuron.model.kv_cache import KVSpec, LayerSpec
# Weight loaders
from vllm_neuron.utils.checkpoints import SafetensorsCheckpoint
from vllm_neuron.utils.weight_loader import (
    SafetensorsWeightLoader, fused_qkv_weight_loader,
    sharding_weight_loader, sharding_weight_loader_with_padding,
    last_dim_padding_weight_loader, set_weight_loader,
)
# EP parallel state (only needed for EP models)
from vllm_neuron.parallel.neuron_parallel_state import (
    get_neuron_ep_degree, get_neuron_ep_rank,
    get_neuron_ep_group, get_neuron_ep_tp_group,
)
```

### Whether the stock NKI MLP kernel activates, vs. falling back to PyTorch

`functional/mlp.py::_can_use_kernel` decides, **per TP degree**, whether the fused nkilib
MLP kernel (gate+up+SiLU+mul+down, one launch, no HBM round-trip between stages) is used,
or whether the model silently falls back to a slower, unfused PyTorch MLP. This is decided
by tiling arithmetic on the **per-rank** intermediate size, not the full model's
`intermediate_size` — so the same model can be kernel-accelerated at one TP degree and
PyTorch-fallback at another, with no error or warning either way. Concretely:

```
I_per_rank = intermediate_size // tensor_parallel_size
tiles = ceil(I_per_rank / 512)
_can_use_kernel returns True  iff  tiles <= 8   (and other gates: T > _TKG_BS_SEQLEN_THRESHOLD
                                                   selects CTE vs TKG branch; can_shard_on_i
                                                   requires the FULL hidden_size >= 7168,
                                                   independent of TP)
```

Real example (`intermediate_size=14336`, `TP=16` → `I_per_rank=896` → `tiles=ceil(896/512)=2`
→ kernel **active**): recompute this per model/TP rather than assuming a specific TP degree
generalizes — **do not assume kernel activation is TP-invariant.** Verify empirically at the TP degree you're actually
shipping by inspecting the compiled FX graph
(`/tmp/vllm_cache_*/.../fxgraph.txt`, grep for `torch.ops.higher_order.nki_kernel_wrapper`
around the MLP call) rather than assuming from a "should tile cleanly" mental estimate —
the tiling condition is a `ceil()` boundary check, not a simple "power of 128" rule, and a
close-to-boundary `I_per_rank` is easy to get wrong by hand. If the kernel is NOT active
(pure PyTorch fallback in the graph — visible as `torch.ops.aten.silu`/manual
elementwise-multiply instead of the `nki_kernel_wrapper` call), that fallback path's own
correctness gates apply (see the SiLU/GELU fusion note in "Confirmed levers" in
AutoBridge.md-style workflows, and check `_get_activation_fn` for whether it's using
fused or decomposed activations on that fallback path).

### Step 3: Generate Boilerplate

Generate these 3 files using the templates in this skill's `templates/` directory, filled with values from Step 1:

1. `vllm_neuron/model/MODEL_NAME/config.py` — from `templates/config.py.template`
2. `vllm_neuron/model/MODEL_NAME/factory.py` — from `templates/factory.py.template`
3. `vllm_neuron/model/MODEL_NAME/__init__.py` — from `templates/init.py.template`

### Step 4: Generate model.py

Copy the selected reference model's `model.py` and modify **section by section**:

**Section 1 (Normalization)**: Use RMSNorm or LayerNorm as needed. Match eps value. If the model has layernorm biases, add a `bias` parameter and include it in `load_weights` mappings.

**Section 2 (RoPE)**:
Match the model's rotary embedding variant. Remove scaling if standard.

**CRITICAL — Meta tensor avoidance:** vLLM creates models on `torch.device("meta")` first, then loads weights. Any tensor computation in `__init__` (like RoPE inv_freq via `ROPE_INIT_FUNCTIONS`) creates meta tensors that fail later with "Cannot copy out of meta tensor; no data!". Solution: store only scalar config values in `__init__`, compute inv_freq lazily in a `_compute_inv_freq(device)` method called from `forward()`. This matches the Llama3 pattern.

**CRITICAL — No `.to(device)` in forward path:** Never call `.to(device)` on tensors inside the forward path. On CPU simulator this is a no-op, but on real Neuron hardware, `torch.compile` traces the graph and `.to(device)` becomes a cross-device copy (`xla:0 → neuron:N`) which raises `NotImplementedError: unimplemented _copy_from`. All tensors passed into `forward()` are already on the correct device.

**CRITICAL — No `.item()` in forward path:** Calling `.item()`, `.max().item()`, or any scalar extraction breaks `torch.compile` with "Unsupported Tensor.item() call with capture_scalar_outputs=False". Use static values or tensor operations instead.

**Section 3 (Attention)**:
Add/remove bias. Handle sliding window, GQA/MQA head counts.

**CRITICAL — Bias shapes for NKI kernels:** The `NF.attention_decode` megakernel requires bias tensors to be 2D `[1, size]`, not 1D `(size,)`. When passing `bias_qkv` or `bias_out`, always unsqueeze:
```python
bias_qkv=self.qkv_proj_bias.unsqueeze(0) if self.qkv_proj_bias is not None else None
```
This passes on CPU simulator (which accepts 1D) but fails on real Neuron hardware with: "Bias shape must be [1, I], got (768,), expected (1, 768)".

For the prefill path, biases can be applied manually after the matmul (1D is fine for `torch.matmul` + bias addition). Only the decode megakernel has the 2D requirement.

**Section 4 (MLP)**: Match activation function and gating. SwiGLU (gate+up+down) vs non-gated (up+down).

For MoE models: keep router gate weights in float32, not bf16. The softmax over num_experts is extremely sensitive to precision — a tiny difference in bf16 can select a completely different pair of experts. In the model init:
```python
self.gate_weight = nn.Parameter(torch.empty(..., dtype=torch.float32))
```
In `load_weights`, preserve float32 for router weights:
```python
if 'gate_weight' in name:
    rank_sharded[name] = tensor.to(torch.float32)
```

**Section 4b (MoE with EP)**: If EP is required, implement the unified sp_group pattern:
```python
# In every module that does collectives:
ep_degree = get_neuron_ep_degree()
if ep_degree > 1:
    self.tp_group = get_neuron_ep_tp_group()   # sub-group, for WEIGHT SIZING only
    self.sp_group = get_tp_group()              # full world, for ALL COLLECTIVES
else:
    self.tp_group = get_tp_group()
    self.sp_group = self.tp_group
```
- Use `self.tp_group.world_size` for parameter dimensions (num_heads_per_rank, etc.)
- Use `self.sp_group` for every all_gather, reduce_scatter, all_reduce call
- This applies to ALL modules: Attention, MLP, MoE, Embedding, LM Head, Sampler
- MoE forward: loop over LOCAL experts only (`num_local_experts = num_experts / ep_degree`)
- Expert weight loading: filter by EP rank — only load `range(ep_rank * num_local_experts, (ep_rank+1) * num_local_experts)`
- After loading weights, scale replicated layers: `o_proj_weight.div_(ep_degree)`, `down_proj_weight.div_(ep_degree)` for non-MoE layers. Do NOT scale MoE expert weights.
- Reference: `gpt_oss/model_bf16.py` for the complete working EP pattern.

**Section 5 (Decoder)**: Match norm placement and residual connection pattern.
**Section 6 (Backbone)**: Handle vocab padding for TP alignment. Handle position embeddings if non-RoPE.
**Section 7 (LM Head)**: Handle tied vs untied embeddings. If the model has `lm_head_bias=True`, create a separate `nn.Parameter` for the bias (not inside `ColumnParallelLinear`) and add it to the logits in `forward()`. Map it in `load_weights` with the correct checkpoint key (e.g., `"lm_head_bias" → "lm_head.bias"`).

**Weight Loading**: Build complete checkpoint key mapping. Every `nn.Parameter` must have a mapping. Use `fused_qkv_weight_loader` for fused QKV weights. For fused QKV biases (1D tensors), create a custom bias loader since `fused_qkv_weight_loader` expects 2D slices. Attach `sharding_weight_loader` to TP-sharded params. Verify every mapping against the actual checkpoint (`model.safetensors.index.json` or `safe_open`) — do not assume the target follows the reference model's key conventions; a wrong transpose produces a model that runs and generates fluent-looking garbage.

**`load_weights_lite()` (CPU Compilation)**: During CPU Compilation (`VLLM_NEURON_CPU_COMPILE=1`) the model is instantiated on the `meta` device and `load_weights_lite()` is called instead of `load_weights()`. Implement it if the checkpoint has tensors that must be baked into the compiled graph as compile-time constants (e.g., FP8 dequant scale tensors) — read just those tensors on CPU, leave everything else on meta. If the model has no such requirement, the base-class no-op default is fine.

Rename ALL classes with the model's PascalCase prefix (e.g., `LlamaRMSNorm` → `YiRMSNorm`).

### Step 5: Register the Model

Edit `vllm_neuron/model/registry.py`:
1. Add import: `from .MODEL_NAME import PascalNameForCausalLM`
2. Add tuple to `get_models()`: `("HFArchitectureString", PascalNameForCausalLM),`

**CRITICAL: The HF architecture string must EXACTLY match the `architectures` field from config.json, including capitalization.** Example:
- HF config: `"architectures": ["PhiMoEForCausalLM"]`
- Registry: `("PhiMoEForCausalLM", PhiMoEForCausalLM)` ← correct
- Registry: `("PhimoeForCausalLM", PhiMoEForCausalLM)` ← WRONG, model won't load

Always verify with:
```bash
python3 -c "from transformers import AutoConfig; c = AutoConfig.from_pretrained('HF_MODEL_ID'); print(c.architectures)"
```

### Step 6: Generate Example & Docs

1. Create `examples/vllm_neuron/models/MODEL_NAME/run.py` from `templates/run.py.template`
2. Create `examples/vllm_neuron/models/MODEL_NAME/results/results.md` from `templates/results.md.template`
3. Create `vllm_neuron/model/MODEL_NAME/README.md` per the model bringup guide (`doc/vllm_neuron/source/design/framework/model_bringup.md`, Step 10). It must include an architecture parameter table, key differences from the reference model, and a feature status table (TP/SP/DP/EP, Eagle3, FP8 KV cache, etc. with ✅/❌/N/A and notes) — every parallelism block that was kept, removed, or deferred must be accounted for.

### Step 7: Self-Review

Before reporting completion, verify:
- [ ] Every `nn.Parameter` in model.py has a corresponding key in `load_weights` mappings
- [ ] Bias shapes are `[1, size]` (2D) for any param passed to `NF.attention_decode` (`bias_qkv`, `bias_out`)
- [ ] No `.to(device)` calls in the forward path (tensors are already on device)
- [ ] No `.item()` calls in the forward path (breaks torch.compile)
- [ ] RoPE inv_freq is computed lazily in `forward()`, not in `__init__()` (avoids meta tensor)
- [ ] Class names are consistent across config.py, factory.py, __init__.py, model.py
- [ ] `__init__.py` exports the factory's ForCausalLM (not the model's — they have the same name)
- [ ] Registry uses the exact HF architecture string from config.json (case-sensitive)
- [ ] `from_configs` classmethod properly maps all model-specific config fields
- [ ] Shared modules (`NF.*`, weight loaders, TP layers) are imported, not reimplemented
- [ ] For MoE models: router gate weights are float32, not bf16
- [ ] Normalization type matches HF source (read forward(), don't trust class name)
- [ ] **Any framework-fit adaptation that changes the model's OUTPUT is recorded as a
      profiling watch-item.** When porting into (or adapting a pre-ported bundle to) a
      tree that has diverged from the bundle's original design, note every deviation —
      especially anything that changes what crosses the device↔host boundary or moves an
      op on/off device (pooling, sampling, normalization, returning full `[T,H]` hidden
      states vs a pooled/reduced output). These are locally-correct integration choices
      that frequently become the *binding* performance cost later (see the callout below).

**Additional EP checks (MoE models with Expert Parallelism only):**
- [ ] ALL collectives use a single group (sp_group) — no mixed group sizes in the NEFF
- [ ] `tp_group` used only for weight sizing, `sp_group` for all collective ops
- [ ] O_proj and dense MLP down_proj weights scaled by `1/ep_degree` after loading
- [ ] MoE expert weights NOT scaled (each EP partition has different experts)
- [ ] Expert weight loading filtered by EP rank (`range(local_start, local_start + num_local)`)
- [ ] Router gate weight is replicated (full copy on every rank), not EP-sharded
- [ ] Embedding and LM head use sp_group.world_size for vocab sharding
- [ ] run.py uses `tensor_parallel_size=world_size`, `enable_expert_parallel=True`, `ep_degree` in neuron_config

Print summary: files created/modified, line counts, and any warnings.

> **Integration adaptations are a first-class source of downstream perf bugs — flag them,
> don't bury them.** When the target tree has diverged from the bundle's original design,
> the locally-correct adaptation you make to fit it can become the binding performance
> cost in the optimization phase. Real incident (`generic-encoder-only-model`): the bundle
> pooled on-device, but the target tree already had a CPU-side `DispatchPooler`, so the
> port was adapted to return full `[T,H]` hidden states and pool on host. That adaptation
> was invisible in CPU equivalence (math identical) and in the device trace
> (`nc_exec_running`), but forced a 4MB device→host `nrt_tensor_read` per request (~2ms)
> that pushed the E2E latency over SLA. The fix was to move pooling back on-device. Lesson:
> any adaptation that changes the model's output shape or moves an op host-side must be
> listed in the completion summary as an explicit **"profiling watch-item"** so the
> optimization phase checks it first — the integration and optimization phases look
> independent but are not.

---

## Phase C: Validation

### Step 8: Smoke Test

Run the example script:
```bash
# Standard (non-EP) models:
NEURON_SKIP_EFA_AFFINITY=1 python examples/vllm_neuron/models/MODEL_NAME/run.py

# EP models (requires VLLM_NEURON_SWITCH_CC for contiguous collective topology):
NEURON_SKIP_EFA_AFFINITY=1 VLLM_NEURON_SWITCH_CC=1 python examples/vllm_neuron/models/MODEL_NAME/run.py
```

Note: `NEURON_SKIP_EFA_AFFINITY=1` is needed on trn2 instances where the PCI topology doesn't match the hardcoded BDF-to-EFA mapping. Safe for single-node TP.

Check for:
- Successful weight loading (no missing key errors)
- Successful compilation (no NKI shape mismatch errors)
- Reasonable generated text (not garbage or degenerate repetition)

**Diagnosing common failures:**

| Symptom | Likely Cause | Fix |
|---|---|---|
| `Cannot copy out of meta tensor` | RoPE or other tensor computed in `__init__` | Move computation to `forward()`, compute lazily |
| `Bias shape must be [1, I], got (N,)` | 1D bias passed to NKI decode megakernel | `.unsqueeze(0)` before passing to `NF.attention_decode` |
| `unimplemented _copy_from xla:0 neuron:N` | `.to(device)` call in forward path | Remove `.to(device)`, tensors are already on device |
| `Unsupported Tensor.item()` | `.item()` call in forward path | Use static values or tensor ops |
| `Checkpoint key(s) not found` | Weight mapping mismatch | Check `load_weights` mappings vs checkpoint keys |
| `size mismatch for lm_head.bias` | lm_head bias not mapped correctly | Use separate `nn.Parameter` + explicit mapping |
| `nrt_tensor_allocate status=4` | HBM out of memory | Increase TP size |
| `No EFA device found` | PCI topology mismatch | `NEURON_SKIP_EFA_AFFINITY=1` |
| Degenerate output (`the the the...`) | Decode path bug (bias, KV cache, or norm) | Test prefill only (max_tokens=1), then debug decode |
| Registry `AttributeError: no attribute 'from_configs'` | Architecture string mismatch in registry | Verify exact HF architecture string |

**EP-specific failures (MoE models with Expert Parallelism):**
- `NEFF Warmup failed with status 1006` / DGE scatter/gather out-of-bound → Mixed collective group sizes in one NEFF. Grep the FX graph dump for different `replica_groups` sizes. Fix: unify ALL collectives to one group (sp_group).
- OOM during weight loading → Expert weights not filtered by EP rank. Check that `load_weights` only maps `range(local_expert_start, local_expert_start + num_local_experts)`.
- Garbage output but no crash → Check O_proj/down_proj scaling (missing `div_(ep_degree)`?). Check router gate is replicated (NOT EP-sharded). Check shared expert division by ep_degree after collective.
- Compilation OOM/timeout → With many experts in a loop, the compiler unrolls all iterations. For production, switch to `NF.moe_cte` (prefill) and `NF.moe_block_tkg` (decode) kernels.
- Run EP models with: `NEURON_SKIP_EFA_AFFINITY=1 VLLM_NEURON_SWITCH_CC=1 python run.py`

Update `results/results.md` with offline inference results.

### Step 9: Online Serving & APC

**Before running at TP>1 or with multiple replicas, read the runtime feasibility gates
below — they are real crash modes on trn2, not theoretical, and cost multiple hours to
rediscover if hit blind.** See
the `vllm-neuron-parity:experimental-neuron-parallelism-planner` skill's `references/tp-feasibility-and-kv-replication.md`
§6 for the full detail; summarized here for convenience:

- **`NEURON_VISIBLE_DEVICES` must list exactly `tensor_parallel_size` entries** — one ID
  per logical core (trn2.48xlarge = 32 logical cores at LNC=2). Fewer/more entries than
  `tensor_parallel_size` fails at worker init with
  `AssertionError: len(neuron_visible_devices) == num_local_ranks` — this is **not**
  evidence of a lower hardware core-count ceiling, it's a request/device-list size
  mismatch. Don't infer a smaller max-TP from this error.
- **Device index must equal rank** — a device window must be zero-based in its own
  process (`0..TP-1`). Two independent TP replicas cannot both use non-zero-based windows
  in the same launch (e.g. replica A on `0-3`, replica B on `12-15` fails with
  `RuntimeError: Invalid device index ... must match the rank`). A second replica CAN use
  a contiguous non-zero block like `16-31` as long as it's zero-based *within its own
  process* — this is how DP=2×TP=16 is actually deployed (two separate processes, each
  internally addressing its slice as local ranks 0..15).
- **The device set must form one valid contiguous collective mesh** — a set spanning two
  disjoint physical rings/NUMA nodes (check `neuron-ls` adjacency) crashes all ranks with
  `encd_mesh_add_wr_barrier: Assertion 'event->evt_type == EVT_SYNC' failed`. Pick a
  topologically contiguous block, don't just pick any N device IDs that add up to
  `tensor_parallel_size`.
- **Never force LNC=1 (or any `_NOMODULAR` variant) at `tensor_parallel_size > 1`.**
  NEFFs compiled with `--logical-nc-config=1` are runtime-incompatible with the default
  LNC=2 collective topology TP>1 needs (`Failed to load collectives for model` at
  warmup). Those flags are TP=1-only workarounds for all-PyTorch/no-NKI-kernel graphs;
  drop them the moment `tensor_parallel_size` goes above 1 — the compile error they work
  around (`NCC_IXRO002`) is specific to TP=1 graphs with no collectives to guide the
  compiler's modular-flow partitioner.

Start the vLLM API server:
```bash
NEURON_SKIP_EFA_AFFINITY=1 python3 -m vllm.entrypoints.openai.api_server \
    --model HF_MODEL_ID \
    --tensor-parallel-size TP_SIZE \
    --max-model-len 256
```

Test battery:
1. Basic completion: `POST /v1/completions` with "The capital of France is"
2. Batch: 4 prompts in one request
3. Streaming: `stream=true`, verify SSE chunks
4. Counting: "1 2 3 4 5 ", verify continuation

Performance: 10 requests, 64 tokens each, measure latency and throughput.

APC: Restart with `--enable-prefix-caching`, re-run all tests.

Update `results/results.md` with online serving results.

### Step 10: Logit Validation

Generate the canonical logit validation test from `test/vllm_neuron/model/templates/test_logits.py.template` and run it.

1. Create the test directory following the current layout convention: `test/vllm_neuron/model/MODEL_NAME/bf16/e2e/` (precision tier, then `e2e/`) — see existing tests like `test/vllm_neuron/model/qwen3/bf16/e2e/test_logits.py` for the pattern.
2. Generate `test/vllm_neuron/model/MODEL_NAME/bf16/e2e/test_logits.py` from the template, filling in all `{{...}}` variables based on the model's architecture (hidden_size, TP sizes, batch sizes, HF model ID, etc.). Reference existing tests in `test/vllm_neuron/model/` for examples of how other models fill in these variables.
3. Run the sanity tests first (they're fast):
```bash
NEURON_SKIP_EFA_AFFINITY=1 pytest test/vllm_neuron/model/MODEL_NAME/bf16/e2e/test_logits.py -m "offline_serving" -v --timeout=3600
```

4. If sanity tests pass, run the online serving test at a small seq_len:
```bash
NEURON_SKIP_EFA_AFFINITY=1 pytest test/vllm_neuron/model/MODEL_NAME/bf16/e2e/test_logits.py -m "online_serving and seq256" -v --timeout=7200
```

**Interpreting results:** The logit validation compares Neuron output logits against a HuggingFace CPU reference at various top-k levels (5, 50, 1000, all). Results are reported as sigma values:
- **< 3 sigma**: Excellent — within normal numerical noise
- **3-5 sigma**: Acceptable — minor numerical differences, usually from bf16 quantization
- **5-10 sigma**: Investigate — may indicate a real issue but could also be model-specific
- **> 10 sigma**: Likely a bug in the port

**IMPORTANT:** If logit validation fails, do NOT treat it as a hard blocker. Report the sigma values and which top-k levels failed, explain what they mean, and continue to completion. The user can decide whether the accuracy is acceptable for their use case. Some models naturally have higher variance at certain top-k levels.

### Step 11: Deep Equivalence Validation

After logit validation, run the full equivalence pipeline for rigorous component-level and E2E verification. This step is **always run** — it is part of the standard porting pipeline.

**Invoke the `vllm-neuron-parity:neuron-framework-equivalence` skill** to verify the port. The equivalence framework supports multiple serving stacks via adapters — **use the vLLM-Neuron adapter** by passing `--target-stack vllm_neuron` to every stage script that accepts it (`run_stage0.py`, `run_stage1.py`, `run_stage5.py`, `run_stage7.py`, `run_teacher_forced_comparison.py`). The adapter handles vLLM-specific concerns: distributed init, `from_configs()` instantiation, weight mapping with transpositions/QKV fusion, attn_metadata construction, and the `vllm.LLM` API. (The adapter is also auto-detected from `vllm_neuron` imports in the modeling file, but pass it explicitly to be safe.)

Provide the equivalence skill these inputs, mapped from this port:

```
target_stack: vllm_neuron
SOURCE_MODEL_PATH: {HF_MODEL_ID weights path}
COMPILED_MODEL_PATH: {compiled model path from Step 8, if available}
TARGET_MODELING_FILE: vllm_neuron/model/MODEL_NAME/model.py
TARGET_CONFIG_CLASS: {PascalName}Config  (from config.py)
TARGET_INNER_CLASS: {PascalName}Model  (backbone class in model.py)
TARGET_CAUSAL_CLASS: {PascalName}ForCausalLM  (from model.py)
VENV: {active venv path}
EXP_DIR: agent_artifacts/equiv_{MODEL_NAME}
TP_SIZE: {recommended TP from Step 1e}
```

The equivalence skill runs the 8-stage pipeline:
- Stage 0: Build model trees, component mapping
- Stage 2: Component-level R-ratio tests (per submodule)
- Stages 3-4: Fault localization and debugging (if failures found)
- Stages 5+6: Teacher-forced E2E comparison (R-ratio, cosine, KL)
- Step 6: Generate EQUIVALENCE_REPORT.md

For vLLM-Neuron-specific behaviors (weight transpositions, forward signature, TP detection, KV cache shapes), the equivalence skill's `references/vllm-neuron-adaptation.md` is the authority — defer to it if anything conflicts.

**Interpreting results:**
- All R < 1.2 and E2E passes → port is verified at component level
- Component failures found → equivalence report includes patches showing what's wrong
- Use the patches as a guide to fix the actual model code in `vllm_neuron/model/MODEL_NAME/model.py`

---

## Completion

Print final status:
```
## Port Complete: MODEL_NAME (HF_MODEL_ID)

Phase A: Research     ✓
Phase B: Code Gen     ✓ (N files, M lines)
Phase C: Validation   ✓/✗

Files created:
  vllm_neuron/model/MODEL_NAME/config.py
  vllm_neuron/model/MODEL_NAME/factory.py
  vllm_neuron/model/MODEL_NAME/__init__.py
  vllm_neuron/model/MODEL_NAME/model.py
  vllm_neuron/model/MODEL_NAME/README.md
  vllm_neuron/model/registry.py (modified)
  examples/vllm_neuron/models/MODEL_NAME/run.py
  examples/vllm_neuron/models/MODEL_NAME/results/results.md
  test/vllm_neuron/model/MODEL_NAME/bf16/e2e/test_logits.py
```
