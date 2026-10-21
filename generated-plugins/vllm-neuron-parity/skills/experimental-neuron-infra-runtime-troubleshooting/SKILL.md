---
name: experimental-neuron-infra-runtime-troubleshooting
description: Diagnose Neuron device errors, compilation failures, OOM, runtime crashes, and performance issues on Trainium/Inferentia.
version: 1.0.0
tags: [skill, neuron, troubleshooting, debugging, errors, trainium, inferentia]
---

# Neuron Troubleshooting

## Overview
Systematic diagnosis for Neuron SDK issues on Trainium and Inferentia hardware.

## Diagnostic Commands

### Device health
```bash
neuron-ls                              # List devices and status
neuron-top                             # Real-time utilization
neuron-top -n 1                        # Single snapshot
dmesg | grep -i neuron                 # Kernel-level errors
cat /opt/aws/neuron/bin/neuron-ls -v   # Verbose device info
```

### Runtime info
```bash
pip list | grep neuron                 # Installed Neuron packages
python -c "import torch_neuronx; print(torch_neuronx.__version__)"
dpkg -l | grep neuron                  # System packages
```

### Process debugging
```bash
fuser /dev/neuron*                     # Who's using devices
ps aux | grep nrt                      # Neuron runtime processes
```

## Common Issues

### Device not found
1. Check `neuron-ls` — are devices visible?
2. Check `lspci | grep -i neuron` — is hardware present?
3. Check `modprobe neuron` — is DKMS driver loaded?
4. In containers: ensure `--device /dev/neuronN` is passed

### Compilation failures
1. Check `NEURON_CC_FLAGS` for conflicting flags
2. Try `--retry_failed_compilation`
3. Check compiler version matches runtime: `neuronx-cc --version`
4. Look at compiler logs: `NEURON_FRAMEWORK_DEBUG=1`

### Performance issues
1. Run `neuron-profile` to capture execution trace
2. Check for excessive host-device transfers
3. Verify BF16 is used (FP32 is 2x slower on NeuronCores)
4. Check `neuron-top` for low utilization (indicates host bottleneck)

### Distributed training hangs
1. Verify all ranks started: check `RANK`, `WORLD_SIZE`, `MASTER_ADDR`
2. Check `NEURON_RT_ROOT_COMM_ID` is set correctly
3. Try `NEURON_RT_EXEC_TIMEOUT=600` to increase timeout
4. Check for asymmetric collective calls (rank mismatch)

## Environment Debug Template
```bash
echo "=== Neuron Environment ==="
neuron-ls 2>/dev/null || echo "neuron-ls not found"
pip list 2>/dev/null | grep -E "neuron|torch|nki"
echo "NEURON_RT_VISIBLE_CORES=${NEURON_RT_VISIBLE_CORES:-unset}"
echo "NEURON_RT_NUM_CORES=${NEURON_RT_NUM_CORES:-unset}"
echo "NEURON_CC_FLAGS=${NEURON_CC_FLAGS:-unset}"
python -c "import torch; print(f'PyTorch {torch.__version__}')" 2>/dev/null
python -c "import torch_neuronx; print(f'TorchNeuronX {torch_neuronx.__version__}')" 2>/dev/null
```
