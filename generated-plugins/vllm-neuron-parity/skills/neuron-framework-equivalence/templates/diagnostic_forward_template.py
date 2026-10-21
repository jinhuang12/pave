"""
diagnostic_forward_template.py — Component-level intermediate capture for TP debugging.

Captures intermediate outputs at each stage of the forward pass (embedding,
attention, MoE, logits) so you can compare TP=1 vs TP=N and pinpoint the
first component that diverges.

Also captures key weights and patch verification flags to detect issues like
patches not being applied in mp.spawn workers.

Adapt placeholders (<model>, <Model>, etc.) to your model.
"""
import torch


def diagnostic_forward(inner_model, input_ids, dtype):
    """Run forward pass capturing intermediate outputs at each stage.

    Returns a dict of {name: tensor} where each tensor is float32 on CPU.
    Compare the dict from TP=1 against TP=N to find the first divergent point.
    """
    results = {}

    # ---------------------------------------------------------------
    # 1. Capture key weights (verify sharding correctness)
    # ---------------------------------------------------------------
    layer = inner_model.layers[0]
    results["w_input_ln"] = layer.input_layernorm.weight.detach().float().cpu().clone()
    results["w_embed"] = inner_model.embed_tokens.weight.detach().float().cpu().clone()

    # Attention weights (will differ at TP>1 due to sharding — that's expected)
    qkv = layer.self_attn.qkv_proj
    results["w_q"] = qkv.q_proj.weight.detach().float().cpu().clone()
    results["w_q_bias"] = (
        qkv.q_proj.bias.detach().float().cpu().clone()
        if qkv.q_proj.bias is not None
        else torch.tensor([0.0])
    )

    # ---------------------------------------------------------------
    # 2. Capture patch verification flag
    # ---------------------------------------------------------------
    # Adapt the module and attribute name to your model's patch markers
    import modeling_model  # noqa: adapt import

    results["patched_flag"] = torch.tensor(
        [1.0 if getattr(modeling_model, "_rmsnorm_patched", False) else 0.0]
    )

    # ---------------------------------------------------------------
    # 3. Run forward with intermediate captures
    # ---------------------------------------------------------------
    batch_size, seq_len = input_ids.shape
    position_ids = torch.arange(seq_len, dtype=torch.long).unsqueeze(0)
    causal_mask = torch.tril(torch.ones(seq_len, seq_len, dtype=torch.bool))
    causal_mask = causal_mask.unsqueeze(0).unsqueeze(0).expand(
        batch_size, 1, seq_len, seq_len
    )

    with torch.no_grad():
        hs = inner_model.embed_tokens(input_ids)
        results["embed"] = hs.float().cpu().clone()

        for i, layer in enumerate(inner_model.layers):
            # Pre-attention layernorm
            residual = hs
            normed = layer.input_layernorm(hs)
            results[f"layer{i}_pre_attn_norm"] = normed.float().cpu().clone()

            # Attention
            attn_out = layer.self_attn(
                normed,
                attention_mask=causal_mask,
                position_ids=position_ids,
                past_key_value=None,
                use_cache=False,
            )
            attn_hs = attn_out[0]
            results[f"layer{i}_attn_out"] = attn_hs.float().cpu().clone()

            # Post-attention residual
            hs = residual + attn_hs
            results[f"layer{i}_post_attn_residual"] = hs.float().cpu().clone()

            # Post-attention layernorm (MoE / MLP input)
            residual = hs
            mlp_input = layer.post_attention_layernorm(hs)
            results[f"layer{i}_mlp_input"] = mlp_input.float().cpu().clone()

            # MLP / MoE forward
            mlp_out = layer.mlp(mlp_input)
            # MoE returns (output, router_scores); dense MLP returns just output
            if isinstance(mlp_out, tuple):
                mlp_hs, router_scores = mlp_out
                results[f"layer{i}_router_scores"] = router_scores.float().cpu().clone()
            else:
                mlp_hs = mlp_out
            results[f"layer{i}_mlp_out"] = mlp_hs.float().cpu().clone()

            # Post-MLP residual
            hs = residual + mlp_hs
            results[f"layer{i}_post_mlp_residual"] = hs.float().cpu().clone()

        # Final norm + lm_head
        hs = inner_model.norm(hs)
        results["final_norm"] = hs.float().cpu().clone()
        logits = inner_model.lm_head(hs)
        results["logits"] = logits.float().cpu().clone()

    return results


def compare_diagnostics(ref, tgt, tp_degree):
    """Compare TP=1 (ref) vs TP=N (tgt) diagnostic outputs.

    Prints a component-by-component comparison with verdict:
    - OK:   rel_fro < 1e-3
    - WARN: 1e-3 <= rel_fro < 1e-1
    - FAIL: rel_fro >= 1e-1

    The first checkpoint that shows FAIL localizes the bug.
    """
    print(f"\n{'='*60}")
    print(f"  Component-level comparison: TP=1 vs TP={tp_degree}")
    print(f"{'='*60}")

    # Order matters: check from earliest to latest
    checkpoint_order = [
        "w_input_ln", "w_embed", "patched_flag",
        "w_q", "w_q_bias",
        "embed",
    ]
    # Add per-layer checkpoints
    num_layers = sum(1 for k in ref if k.startswith("layer") and k.endswith("_pre_attn_norm"))
    for i in range(num_layers):
        checkpoint_order.extend([
            f"layer{i}_pre_attn_norm",
            f"layer{i}_attn_out",
            f"layer{i}_post_attn_residual",
            f"layer{i}_mlp_input",
            f"layer{i}_router_scores",
            f"layer{i}_mlp_out",
            f"layer{i}_post_mlp_residual",
        ])
    checkpoint_order.extend(["final_norm", "logits"])

    for name in checkpoint_order:
        if name not in ref or name not in tgt:
            continue
        r, t = ref[name], tgt[name]
        if r.shape != t.shape:
            print(f"  {name:35s} SHAPE MISMATCH ref={r.shape} tgt={t.shape}")
            continue
        diff = r - t
        rel = torch.norm(diff) / (torch.norm(r) + 1e-12)
        mx = diff.abs().max().item()
        status = "OK" if rel < 1e-3 else ("WARN" if rel < 1e-1 else "FAIL")
        print(f"  {name:35s} rel_fro={rel:.6e}  max_diff={mx:.6e}  [{status}]")

    print()
