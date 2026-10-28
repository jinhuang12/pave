# pumice_pb

This directory holds the gRPC stubs that `run_pumice_grpc_bench.py` uses to talk to the Pumice server. AMMO owns these stubs. Do not edit the two generated files by hand. Generate them again.

## Source

- Repository: AWSMantlePumice, branch `mainline`.
- Commit: `9d126713c03dcc88883b85c3d3a857b89982c957`.
- File: `pumice-rust/proto/pumice_engine.proto`. The last commit that changed it is `1a1bcdbfe1929f00e19e24277b10dd9042a5413b`.
- SHA-256 of the file: `9b4dbe3de02378403d6d7a231fdc2389a4f6d341b5cb452caa48f2b14ee29f88`.

The Rust proto declares `prompt_tokens_cached` (tag 6 in `GenerateStreamChunk`, tag 7 in `GenerateComplete`). The Python copy in `src/pumice/grpc/pumice_engine.proto` comments that field out. That is why AMMO does not use the stubs from the installed `pumice` wheel.

## Generator versions

- `grpcio-tools==1.80.0`, `grpcio==1.80.0`, `protobuf==6.31.1` (`libprotoc 31.1`).
- These are the versions that made the stubs shipped in the Pumice wheel. The generated code needs `protobuf>=6.31.1` and `grpcio>=1.80.0` at run time. The CUDA base lock has `protobuf==6.33.6` and `grpcio==1.83.1`, so it satisfies both.
- Do not use a newer `grpcio-tools`. Newer gencode raises an error at import when the run-time `protobuf` or `grpcio` is older than the gencode.

## Generate command

Run these commands from the ammo-server repo root. Use a scratch venv outside the repo.

```bash
uv venv ~/scratch/pb2-venv --python 3.12
uv pip install --python ~/scratch/pb2-venv/bin/python "grpcio-tools==1.80.0" "grpcio==1.80.0" "protobuf==6.31.1"
PROTO_DIR=/Users/jinhun/AWSMantle/src/AWSMantlePumice/pumice-rust/proto
OUT_DIR=ai_cli_session/_ammo_shared/scripts/pumice_pb
~/scratch/pb2-venv/bin/python -m grpc_tools.protoc -I"$PROTO_DIR" --python_out="$OUT_DIR" --grpc_python_out="$OUT_DIR" "$PROTO_DIR/pumice_engine.proto"
sed -i '' 's/^import pumice_engine_pb2 as pumice__engine__pb2$/from . import pumice_engine_pb2 as pumice__engine__pb2/' "$OUT_DIR/pumice_engine_pb2_grpc.py"
python scripts/render_ammo_variants.py
```

The `sed` step makes the import in `pumice_engine_pb2_grpc.py` relative, so the stubs load as the `pumice_pb` package. On Linux, use `sed -i` without `''`.

## Check

```bash
~/scratch/pb2-venv/bin/python -c "import sys; sys.path.insert(0, 'ai_cli_session/_ammo_shared/scripts'); from pumice_pb import pumice_engine_pb2 as pb2; print(pb2.GenerateComplete.DESCRIPTOR.fields_by_name['prompt_tokens_cached'].number)"
```

The command prints `7`.
