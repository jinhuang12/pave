"""
Test NN: {COMPONENT_NAME} equivalence.
Source: {SOURCE_CLASS}  (from the source/reference model)
Target: {TARGET_CLASS}  (from the target/ported model — the ACTUAL class used in CPU mode)

IMPORTANT: ref_fp32 and ref_bf16 use the SOURCE model's class.
           target_bf16 uses the TARGET port's class.
           These may be DIFFERENT classes (e.g., Olmo2RMSNorm vs LlamaRMSNorm).
           Do NOT use the same class for both — that tests nothing.

Three-tensor method:
  ref_fp32:    HF module, FP32 weights (ground truth)
  ref_bf16:    HF module, BF16 weights (precision baseline)
  target_bf16: Neuron module, BF16 weights (target under test)

  R = ||target - ref_fp32||_F / (||ref_bf16 - ref_fp32||_F + ε)
  PASS if R < 1.2
"""
import torch
import torch.nn as nn
from conftest import (
    HIDDEN_SIZE, BS, SEQ_LEN, TOLERANCE_RATIO,
    compare_3tensors, check_3tensor_result,
)


def test_{component_name}():
    # Import BOTH implementations — source and target must be DIFFERENT classes
    # from transformers.models.xxx import SourceModule  # e.g., Olmo2RMSNorm
    # from modeling_xxx import TargetModule              # e.g., LlamaRMSNorm (what the port actually uses)

    torch.manual_seed(42)

    # Generate shared random FP32 weights
    weight_fp32 = torch.randn(OUT_FEATURES, IN_FEATURES)

    # --- ref_fp32: SOURCE model's class, FP32 weights ---
    ref_fp32 = SourceModule(config)
    ref_fp32.weight.data.copy_(weight_fp32)

    # --- ref_bf16: SOURCE model's class, BF16 weights ---
    ref_bf16 = SourceModule(config)
    ref_bf16.weight = nn.Parameter(weight_fp32.to(torch.bfloat16))

    # --- target_bf16: TARGET PORT's class, BF16 weights ---
    # This MUST be the class the port actually uses (check modeling file)
    target_bf16 = NeuronModule(neuron_config)
    # CRITICAL: Use nn.Parameter replacement for ColumnParallelLinear
    target_bf16.weight = nn.Parameter(weight_fp32.to(torch.bfloat16))
    target_bf16.eval()  # REQUIRED for pad=True modules

    # Generate input
    x = torch.randn(BS, SEQ_LEN, IN_FEATURES)

    with torch.no_grad():
        out1 = ref_fp32(x.float()).float()
        out2 = ref_bf16(x.to(torch.bfloat16)).float()

        # Handle tuple outputs from Neuron modules
        out3_raw = target_bf16(x.to(torch.bfloat16))
        out3 = out3_raw[0].float() if isinstance(out3_raw, tuple) else out3_raw.float()

    # Align shapes if needed (e.g., vocab padding)
    # min_dim = min(out1.shape[-1], out3.shape[-1])
    # out1, out2, out3 = out1[..., :min_dim], out2[..., :min_dim], out3[..., :min_dim]

    result = compare_3tensors(out1, out2, out3)
    assert check_3tensor_result(result, "{COMPONENT_NAME}", TOLERANCE_RATIO)
