# Host prep - smoke container on g6e-cdd

The smoke container runs on g6e-cdd on two idle L40S GPUs. Pumice is at the new pin and is built. The engine venv now has the vLLM stack that the new Pumice pins. gpt-oss-20b starts on one GPU and answers a request. Claude Code works. The container is idle and its GPUs are free.

Next action: rsync the plugin into `/home/jinhun/ammo-plugin-smoke/plugin/` and start the wave-2 smoke.

## 1. Container

| Item | Value |
|---|---|
| Name | `ammo-plugin-smoke` |
| Image | `ammo-server-pumice-cuda:latest` (`sha256:42b70733...`, built 2026-10-05) |
| Started | 2026-10-28 18:05 UTC |
| Entrypoint | `bash -c "sleep infinity"`, `--init` (tini reaps children) |
| GPUs | host GPUs 2 and 3 (container sees them as 0 and 1) |
| Ports | none published |
| Network | default bridge (outbound only) |
| Shared memory | 32 GiB |

GPU state before start (host `nvidia-smi --query-gpu=index,memory.used,utilization.gpu --format=csv`, 18:04 UTC):

```
0, 109 MiB, 0 %
1, 1 MiB, 0 %
2, 1 MiB, 0 %
3, 1 MiB, 0 %
```

No compute process ran on any GPU (`--query-compute-apps` was empty). The running server container `ammo-pumice-cuda-server` has all four GPUs in its device list, so its sessions can take GPUs 2 and 3 too. Docker does not stop that. GPU 0 and GPU 1 stay free for the server.

Mounts:

| Host | Container | Mode |
|---|---|---|
| `/home/jinhun/ammo-plugin-smoke/plugin` | `/mnt/ammo` | read-only |
| `/home/jinhun/ammo-plugin-smoke/logs` | `/mnt/logs` | read-write |
| `/data/hf` | `/hf` | read-only |
| `/home/jinhun/.cuda-eval-server/aws` | `/root/.aws` | read-only |

The last two mounts are the same host directories the server container uses.

Start command:

```bash
mkdir -p /home/jinhun/ammo-plugin-smoke/plugin /home/jinhun/ammo-plugin-smoke/logs
docker run -d --name ammo-plugin-smoke --init \
  --entrypoint bash \
  --gpus '"device=2,3"' \
  --shm-size 32g --ulimit nofile=65536:65536 \
  -v /home/jinhun/ammo-plugin-smoke/plugin:/mnt/ammo:ro \
  -v /home/jinhun/ammo-plugin-smoke/logs:/mnt/logs \
  -v /data/hf:/hf:ro \
  -v /home/jinhun/.cuda-eval-server/aws:/root/.aws:ro \
  -e AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials \
  -e AWS_CONFIG_FILE=/root/.aws/config \
  -e AWS_REGION=eu-west-1 \
  -e CLAUDE_CODE_USE_BEDROCK=1 \
  -e CLAUDE_CODE_USE_MANTLE=1 \
  -e "ANTHROPIC_DEFAULT_OPUS_MODEL=global.anthropic.claude-opus-5-5[1m]" \
  -e ANTHROPIC_DEFAULT_SONNET_MODEL=anthropic.claude-sonnet-5 \
  -e ANTHROPIC_DEFAULT_HAIKU_MODEL=global.anthropic.claude-haiku-4-5-20251001-v1:0 \
  -e IS_SANDBOX=1 \
  ammo-server-pumice-cuda:latest -c "sleep infinity"
```

## 2. Pumice source

The old checkout was `mainline` at the image's local sm80 commit on top of the old pin. Its remote is a bundle path that no longer exists in the container. The new commit is not a descendant of the old pin: the two lines meet at an earlier merge base.

| Step | Command | Wall |
|---|---|---|
| Bundle on the Mac (read-only on the checkout) | `git -C /Users/jinhun/AWSMantle/src/AWSMantlePumice bundle create /tmp/pumice-769c7529.bundle refs/remotes/origin/mainline ^668dbdc8450032686150c2d9c1124a082924bdaa` | under 5 s |
| Copy | `scp` to the host, then `docker cp` to `/tmp/pumice-769c7529.bundle` | under 5 s |
| Fetch | `git -C /workspace/pumice fetch /tmp/pumice-769c7529.bundle refs/remotes/origin/mainline:refs/remotes/pin/mainline-769c7529` | 0.6 s |
| Checkout | `git -C /workspace/pumice checkout --detach 769c752995b8a39b27df028176411d17c99ff441` | 0.1 s |

Bundle: 2 MB, sha256 `1a56eb41f7945ecf135c689f609e019d813177b09a585ab9751827c1cc132b9b`, same on the Mac and the host. The bundle needs only the merge base, which the old clone has.

Result:

- `git -C /workspace/pumice rev-parse HEAD` prints `769c752995b8a39b27df028176411d17c99ff441`. HEAD is detached.
- The branch `mainline` still points at the old local commit `9560f3dd2485ef8ed6ac29489b2fe6dfed806200`. It was not reset.
- The Mac checkout was not changed. It is still on `mainline` (ahead 2, behind 96).
- `Cargo.lock` is not tracked by Pumice. The old untracked lock stayed in place, and cargo reused it. A copy of it is at `/tmp/Cargo.lock.9d126713`.
- The image provenance files `.docker_commit`, `.docker_local_commit`, `.docker_version` still name the old pin. Nothing in the plugin reads them. They were left as they are.

## 3. sm80 patch

`git apply --check -v /usr/local/share/ammo/pumice-sm80-gpt-oss.patch` on the new commit: it applies cleanly, with no fuzz. The patch in the image is byte-identical to `ammo-server/docker/pumice-sm80-gpt-oss.patch` (sha256 `9ae42bf7...`).

The patch was applied to the working tree and not committed, so HEAD stays at the pin. The confdb TOML files are compiled into the Rust library, so the patch went in before the build. After the build, `git status --porcelain` shows ` M pumice-rust/confdb/gpt_oss.toml`.

Two effects to know:

1. A git worktree made from HEAD does not carry the patch. A track worktree that builds its own engine gets no `[sm80]` row for gpt-oss.
2. To undo: `git -C /workspace/pumice checkout -- pumice-rust/confdb/gpt_oss.toml`, then rebuild (section 4).

## 4. Build

Script: `/home/jinhun/ammo-plugin-smoke/logs/build-769c7529.sh` (in the container at `/mnt/logs/`). Full log: `/mnt/logs/build.log`. Step table: `/mnt/logs/build-steps.tsv`. The script sources `/etc/profile.d/ammo-rust.sh`, so `CARGO_HOME=/opt/cargo` and `PYO3_PYTHON=/opt/pumice-venv/bin/python`. `CARGO_TARGET_DIR` and `RUSTC_WRAPPER` are not set.

| # | Command | Exit | Wall |
|---|---|---|---|
| 1 | `git -C /workspace/pumice apply /usr/local/share/ammo/pumice-sm80-gpt-oss.patch` | 0 | 0 s |
| 2 | `cargo fetch --manifest-path /workspace/pumice/pumice-rust/Cargo.toml` | 0 | 1 s |
| 3 | `cargo build --release --manifest-path /workspace/pumice/pumice-rust/Cargo.toml --bin pumice-server --no-default-features --features "embedded cmb nixl-stub"` | 0 | 38 s |
| 4 | `cp -p /opt/pumice-venv/bin/pumice-server /root/pumice-server.base-wheel.bak` | 0 | 0 s |
| 5 | `install -m 0755 /workspace/pumice/target/release/pumice-server /opt/pumice-venv/bin/pumice-server` | 0 | 0 s |
| 6 | `rm -f /workspace/pumice/target/release/pumice-server` | 0 | 0 s |
| 7 | `/usr/local/bin/pumice-stage-native.sh /opt/pumice-venv/bin/python /workspace/pumice --features "nixl-stub cmb"` | 0 | 28 s |

Step 7 is the image's own staging script, with the same features the image build used. Inside it:

- `uv pip install --python /opt/pumice-venv/bin/python -e /workspace/pumice --no-deps`: about 3 s.
- `cargo build --release --manifest-path /workspace/pumice/pumice-rust/Cargo.toml --features nixl-stub cmb`: 24 s.
- It copies `target/release/libpumice_rust.so` into site-packages and puts back the 11 `_ops` kernel files.

Target dir reuse: yes. Pumice is a cargo workspace, so the target dir is `/workspace/pumice/target`, which the image build left warm (1.1 GB before, 1.2 GB after). Step 3 compiled `amzn-pumice-rust` plus the pyo3 crates and `numpy`, because the `embedded` feature changes pyo3's features. Step 7 compiled only `amzn-pumice-rust`. No other dependency crate compiled. `/workspace/cargo-target` is empty and was not used. The registry under `/opt/cargo` had every crate (`Cargo.toml` did not change between the two pins), and `cargo fetch` downloaded nothing.

`pumice-server` binary:

- Final path: `/opt/pumice-venv/bin/pumice-server`, built from the new commit (step 3; 33 MB, 2026-10-28 18:10 UTC).
- The image's old binary came from the base wheel. It is saved at `/root/pumice-server.base-wheel.bak`.
- Step 6 removes the binary from `target/release`. Reason: the AMMO bench runner picks `<repo>/target/release/pumice-server` first, else `python <repo>/rust_grpc_server.py`. Track worktrees build only the library, so they use `rust_grpc_server.py`. With no binary in the main checkout, the baseline uses the same launcher as the tracks.

Library: the staged `site-packages/pumice_rust.cpython-312-x86_64-linux-gnu.so` and `/workspace/pumice/target/release/libpumice_rust.so` have the same sha256 (`c8f834c2...`). So the module that Python imports is the new build.

Stale vLLM extension removed. The base image installed `pumice_vllm_ext` as its own package (`pumice-vllm-ext 0.1.0`, an older copy). From the new commit on, Pumice ships the same package inside its main package, from `extensions/pumice_vllm_ext`. The old copy in site-packages shadowed the new one. The new tree has 5 more patches and a `models/` directory that the old copy lacks. Command: `uv pip uninstall --python /opt/pumice-venv/bin/python pumice-vllm-ext` (under 1 s). After it, `import pumice_vllm_ext` resolves to `/workspace/pumice/extensions/pumice_vllm_ext/`, and the vLLM and Pumice plugin entry points come from the Pumice package only. At that point the engine venv differed from the image snapshot `/etc/ammo-engine-freeze.0` by that one line. Section 6 changed more. A copy of the freeze before the removal is at `/mnt/logs/freeze-before-ext-uninstall.txt`.

## 5. Import check

Run again after section 6, with the same output:

```
$ /opt/pumice-venv/bin/python -c "import pumice, pumice_rust; from pumice import config; print(pumice.__file__, pumice_rust.__file__, config.REQUIRED_VLLM_VERSION)"
/workspace/pumice/src/pumice/__init__.py /opt/pumice-venv/lib64/python3.12/site-packages/pumice_rust.cpython-312-x86_64-linux-gnu.so 0.32.0
```

`pumice` resolves inside `/workspace/pumice`. `pumice_rust` resolves to the staged copy in site-packages. That is the image's staging design (`pumice-stage-native.sh` step 4), and the file is byte-identical to the new build in `/workspace/pumice/target/release`.

## 6. Engine venv upgrade to the vLLM stack of the new pin

Team-lead approved this step after the first launch check failed. It changes only `/opt/pumice-venv` inside this container. The image, the server container and the host are not changed.

### 6.1 Why

The first launch check ran on the image's vLLM 0.30.0. It failed before the model loaded:

```
TypeError: EngineArgs.__init__() got an unexpected keyword argument 'enable_mamba_shared_prefix_checkpoint'
  File "/workspace/pumice/src/pumice/vllm_adapter/bootstrap.py", line 1139, in build_vllm_config
```

Pumice only warns on a version mismatch. The failure is the adapter passing a vLLM 0.32 argument. Log: `/mnt/logs/launch-check-vllm-0.30.0.log`.

### 6.2 What the new pin declares

From `/workspace/pumice/pyproject.toml` at the pin. Comment lines are left out.

```toml
dependencies = [
    "transformers==5.19.0",
    ...
]

[project.optional-dependencies]
cuda = [
    "torch==2.13.0",
    "torchaudio==2.11.0",
    "torchvision==0.28.0",
    "triton>=3.7.0,<3.8",
    "nvidia-cuda-cccl==13.0.85",
    "nvidia-cuda-crt==13.0.88",
    "nvidia-cuda-nvcc==13.0.88",
    "nvidia-cuda-runtime==13.0.96",
    "nvidia-nvvm==13.0.88",
    "boto3>=1.42,<2",
    "pyOpenSSL>=25.3,<26",
    "nvidia-cutlass-dsl[cu13]==4.8.0",
    "quack-kernels==0.6.5",
    "tokenspeed-mla==0.2.15; platform_system == 'Linux'",
    "humming-kernels[cu13]==0.1.17",
    "apache-tvm-ffi==0.1.11",
    "tilelang==0.1.12",
    "flashinfer-python==0.7.0.post1",
    "flashinfer-jit-cache==0.7.0.post1",
    "flashinfer-cubin @ https://github.com/flashinfer-ai/flashinfer/releases/download/v0.7.0.post1/flashinfer_cubin-0.7.0.post1-py3-none-any.whl",
    "vllm==0.32.0",
    "soundfile>=0.12",
    "librosa>=0.10",
]
vllm-runner = [
    "vllm==0.32.0",
    "soundfile>=0.12",
    "librosa>=0.10",
]
```

`vllm-runner` is a subset of `cuda`, so the install names `[cuda]` only.

### 6.3 Recipe

Script: `/mnt/logs/venv-upgrade-vllm-0.32.0.sh`. Log: `/mnt/logs/venv-upgrade.log`. Step table: `/mnt/logs/venv-upgrade-steps.tsv`. The recipe is the install step of the Pumice base image Dockerfile, with the release vLLM wheel and the Pumice source tree instead of the Pumice wheel.

| # | Command | Exit | Wall |
|---|---|---|---|
| 0 | `curl -fsSL --retry 5 --retry-all-errors -o /tmp/vllm-0.32.0/vllm-0.32.0-cp38-abi3-manylinux_2_28_x86_64.whl https://github.com/vllm-project/vllm/releases/download/v0.32.0/vllm-0.32.0-cp38-abi3-manylinux_2_28_x86_64.whl` | 0 | 1 s |
| 1 | `sha256sum -c` against `5e2ecdc5b0baf72921c288486417ca82778456a448e4fe60b62ee43c0dfc8611` (the digest GitHub lists for the release asset) | 0 | 1 s |
| 2 | `env -u UV_CONSTRAINT uv pip install --python /opt/pumice-venv/bin/python --index-strategy unsafe-best-match --extra-index-url https://download.pytorch.org/whl/cu130 --extra-index-url https://flashinfer.ai/whl/cu130 /tmp/vllm-0.32.0/vllm-0.32.0-cp38-abi3-manylinux_2_28_x86_64.whl -e "/workspace/pumice[cuda]"` | 0 | 139 s |
| 3 | `/usr/local/bin/pumice-stage-native.sh /opt/pumice-venv/bin/python /workspace/pumice --features "nixl-stub cmb"` | 0 | 4 s |

A dry run of step 2 (`--dry-run`, 20 s) came first and showed the same change list. Step 2 resolved 224 packages: 18 out, 26 in, 26 downloaded.

Order: the subtask asked for this upgrade before the build. It ran after the build in section 4, so step 3 re-ran the staging script. That redoes the editable install on the new venv and re-stages the library. Cargo had nothing to compile (0.3 s), because the Rust build does not depend on vLLM. The staged library hash did not change.

`UV_CONSTRAINT` is unset only for step 2. The container env still sets `UV_CONSTRAINT=/etc/pumice-runtime-constraints.txt`, and that file still has the old pins. A later `uv pip install` into the engine venv must use `env -u UV_CONSTRAINT`, or it will try to move packages back. The image snapshot `/etc/ammo-engine-freeze.0` no longer matches this venv.

### 6.4 Freeze, before and after

Full files: `/mnt/logs/freeze-before-vllm-upgrade.txt` and `/mnt/logs/freeze-after-vllm-upgrade.txt` (42 lines differ).

| Package | Before | After |
|---|---|---|
| torch | 2.13.0+cu130 | 2.13.0+cu130 |
| torchvision | 0.28.0+cu130 | 0.28.0+cu130 |
| torchaudio | 2.11.0+cu130 | 2.11.0+cu130 |
| triton | 3.7.1 | 3.7.1 |
| vllm | 0.30.0 | 0.32.0 |
| transformers | 5.14.1 | 5.19.0 |
| flashinfer-python | 0.6.18.post1 | 0.7.0.post1 |
| flashinfer-jit-cache | 0.6.18.post1+cu130 | 0.7.0.post1+cu130 |
| flashinfer-jit-cache-sm* | none | 6 per-arch wheels, 0.7.0.post1+cu130 |
| flashinfer-cubin | 0.6.18.post1 | 0.7.0.post1 |

Exact freeze lines:

```
before:
flashinfer-cubin @ https://github.com/flashinfer-ai/flashinfer/releases/download/v0.6.18.post1/flashinfer_cubin-0.6.18.post1-py3-none-any.whl
flashinfer-jit-cache==0.6.18.post1+cu130
flashinfer-python==0.6.18.post1
torch==2.13.0+cu130
transformers==5.14.1
triton==3.7.1
vllm @ file:///tmp/vllm-0.30.0-cp38-abi3-manylinux_2_28_x86_64.whl

after:
flashinfer-cubin @ https://github.com/flashinfer-ai/flashinfer/releases/download/v0.7.0.post1/flashinfer_cubin-0.7.0.post1-py3-none-any.whl
flashinfer-jit-cache==0.7.0.post1+cu130
flashinfer-jit-cache-sm100a==0.7.0.post1+cu130
flashinfer-jit-cache-sm103a==0.7.0.post1+cu130
flashinfer-jit-cache-sm120f==0.7.0.post1+cu130
flashinfer-jit-cache-sm80==0.7.0.post1+cu130
flashinfer-jit-cache-sm89==0.7.0.post1+cu130
flashinfer-jit-cache-sm90a==0.7.0.post1+cu130
flashinfer-python==0.7.0.post1
torch==2.13.0+cu130
transformers==5.19.0
triton==3.7.1
vllm @ file:///tmp/vllm-0.32.0/vllm-0.32.0-cp38-abi3-manylinux_2_28_x86_64.whl
```

The other changed packages: humming-kernels 0.1.17, instanttensor 0.2.2, nvidia-cudnn-frontend 1.32.0, nvidia-cutlass-dsl (and its 4 libs) 4.8.0, tokenizers 0.23.3, tokenspeed-mla 0.2.15, tokenspeed-triton 3.9.10.post20261008, xgrammar 0.2.8. New: nccl-extensions 0.1.0, oss-harmony 0.0.11.

Torch did not change, so the `_ops` kernels and the Rust build stay valid.

### 6.5 Checks after the upgrade

```
$ python -c "import vllm; print(vllm.__version__)"
0.32.0
$ python -c "from pumice import config; from pumice.vllm_adapter import bootstrap; print(config.REQUIRED_VLLM_VERSION, bootstrap.REQUIRED_VLLM_VERSION, bootstrap.check_vllm_available())"
0.32.0 0.32.0 0.32.0
```

The second command printed no warning. `bootstrap.check_vllm_available()` is the adapter's version check; it warns only when the installed vLLM differs from its pin.

### 6.6 Launch check

Script: `/mnt/logs/launch-check.sh`. Log: `/mnt/logs/launch-check.log`.

```bash
cd /workspace/pumice
CUDA_VISIBLE_DEVICES=0 timeout 1500 python rust_grpc_server.py --model /hf/gpt-oss-20b --port 50051 --max-model-len 4096 --tensor-parallel-size 1
```

Result: the server started and served. Timeline from the log:

| Phase | Time |
|---|---|
| Process start to model init | about 35 s |
| Weight load (3 shards, 12.8 GiB) | 97 s |
| torch.compile (cold cache) | 19 s |
| CUDA graph capture | 9 s |
| Start to ready | about 3 min |

- Runner: vLLM V2 model runner. Attention: TRITON_ATTN. MoE: MARLIN MXFP4. KV cache: 25 GiB.
- The log shows no confdb resolution line, so this record does not claim which confdb row picked these backends.
- `pumice_vllm_ext: 0 extension(s) ACTIVE`. gpt-oss asks for none.
- GPU memory on host GPU 2 while up: 41.7 GB.
- Request: `printf "What is 2+3? Answer with one number.\n" | python chat_client.py --model-path /hf/gpt-oss-20b --server localhost:50051 --temperature 0 --max-tokens 64`. Answer: `5` (92 prompt tokens, 46 completion tokens, finish=stop).
- The server was stopped after the request. Host GPU 2 went back to 1 MiB. The container runs only `sleep infinity` now.

Warm caches left for the smoke: `/root/.cache/vllm` (29 MB, torch.compile and AOT artifacts for this model and shape). The page cache on the host also holds the weights for now.

## 7. Model weights

- Path: `/hf/gpt-oss-20b` in the container (host `/data/hf/gpt-oss-20b`, read-only). It is a plain model directory, not an HF hub cache.
- Size: 39 GB on disk. The three safetensors shards are 13.8 GB. The rest is the `original/` and `metal/` copies.
- No download was needed. The image has no `HF_HOME`, and `/root/.cache/huggingface` does not exist.

## 8. Claude Code

- `claude --version` prints `2.1.281 (Claude Code)`.
- Mechanism: Bedrock through Mantle. Env: `CLAUDE_CODE_USE_BEDROCK=1`, `CLAUDE_CODE_USE_MANTLE=1`, `AWS_REGION=eu-west-1`, plus the three `ANTHROPIC_DEFAULT_*_MODEL` aliases. These values come from the session template `ai_cli_session/.claude/settings.local.json`.
- Credentials: static keys in the AWS shared credentials file `/root/.aws/credentials`, profile `default` (host `/home/jinhun/.cuda-eval-server/aws`, read-only). Key values: <redacted>.
- `IS_SANDBOX=1` is set, so `--dangerously-skip-permissions` works as root.
- The image managed settings `/etc/claude-code/managed-settings.json` set `defaultMode: bypassPermissions`.
- Live check: `claude -p "Reply with the single word OK"` returned `OK` with `--model haiku`, `--model sonnet` and `--model opus`. The check used a throwaway `CLAUDE_CONFIG_DIR=/tmp/claude-cred-check`, deleted after. `/root/.claude` and `/root/.claude.json` do not exist yet, so the clean-room install starts empty.

## 9. Traps for the wave-2 smoke

1. `UV_CONSTRAINT` still names the old lock file (section 6.3). Install into the engine venv only with `env -u UV_CONSTRAINT`.
2. The image sets `PYTHONPATH=/app`. With it, `import shared, app` resolves to the ammo-server tree (`/app/shared/__init__.py`, `/app/app.py`). Run the smoke with `PYTHONPATH` unset. Otherwise a leftover server import in the plugin passes here and fails on a real host.
3. `claude -p` reads the rest of stdin when stdin is a pipe. A smoke script that pipes commands into `bash` must give each `claude` call `< /dev/null`.

The scratch bundle on the Mac (`/tmp/pumice-769c7529.bundle`) was deleted after the review. The host copy stays at `/home/jinhun/ammo-plugin-smoke/pumice-769c7529.bundle`.
