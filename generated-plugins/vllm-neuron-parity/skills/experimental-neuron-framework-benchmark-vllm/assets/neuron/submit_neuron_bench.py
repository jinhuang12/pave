#!/usr/bin/env python3
"""Submit a vLLM-Neuron `vllm bench serve` run to Kaizen on Trainium.

Base64-encodes an in-container script that sets the bench env and runs the
in-image runner (bench_one_model.sh), then submits via `kaizen start-workload`.
Prints the full workload ID. HF_TOKEN is injected from env, not written to disk.

Usage:
  HF_TOKEN=<token> python3 submit_neuron_bench.py \
    --image <ecr-uri> --model-id allenai/OLMo-2-0425-1B --tp 8 \
    --instance trn2.48xlarge

Requires: kaizen CLI on PATH, kaizen-access profile, the event-matched driver RPM.
"""
import argparse, base64, os, re, subprocess, sys

p = argparse.ArgumentParser()
p.add_argument("--image", required=True, help="ECR URI of the model's bench image")
p.add_argument("--model-id", required=True, help="exact HF id, e.g. allenai/OLMo-2-0425-1B")
p.add_argument("--tp", type=int, default=8)
p.add_argument("--instance", default="trn2.48xlarge")
p.add_argument("--num-prompts", type=int, default=200)
p.add_argument("--seq-len", type=int, default=4096)
p.add_argument("--max-num-seqs", type=int, default=16)
p.add_argument("--runner", default="ondevice_scripts/bench_one_model.sh",
               help="path to the in-image runner")
p.add_argument("--driver", default="/tmp/driver/aws-neuronx-dkms-2.x.9497.0.noarch.rpm")
p.add_argument("--priority", default="high")
p.add_argument("--timeout", type=int, default=7200)
p.add_argument("--profile", default="kaizen-access")
args = p.parse_args()

hf = os.environ.get("HF_TOKEN")
if not hf:
    sys.exit("HF_TOKEN must be set in the environment")

inner = f"""#!/bin/bash
export PYTHONUNBUFFERED=1 VLLM_PLUGINS=neuron TOKENIZERS_PARALLELISM=false NEURON_SKIP_EFA_AFFINITY=1 HF_HUB_ENABLE_HF_TRANSFER=0
export VLLM_LOGGING_LEVEL=ERROR VLLM_CONFIGURE_LOGGING=0 NEURON_RT_LOG_LEVEL=ERROR NEURON_RT_LOG_LOCATION=console PYTHONWARNINGS=ignore TQDM_DISABLE=1
export HF_TOKEN='{hf}'
export MODEL_ID='{args.model_id}' TP={args.tp} NUM_PROMPTS={args.num_prompts} SEQ_LEN={args.seq_len} MAX_NUM_SEQS={args.max_num_seqs}
cd /workspace/plugin
bash {args.runner}
"""
enc = base64.b64encode(inner.encode()).decode()

cmd = [
    "kaizen", "start-workload", "--image", args.image,
    "--instanceType", args.instance, "--nodeCount", "1",
    "--command", f"echo {enc} | base64 -d | bash",
    "--neuron-driver-pkg-file", args.driver,
    "--priority", args.priority, "--timeout", str(args.timeout),
    "--profile", args.profile,
]
out = subprocess.run(cmd, capture_output=True, text=True)
m = re.search(r"workloadId:\s*(\S+)", out.stdout + out.stderr)
if m:
    print(m.group(1))
else:
    sys.exit("SUBMIT FAILED:\n" + (out.stdout + out.stderr)[-500:])
