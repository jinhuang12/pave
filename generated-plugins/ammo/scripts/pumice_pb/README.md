# pumice_pb

This directory holds the gRPC stubs that `run_pumice_grpc_bench.py` uses to talk to the Pumice server. AMMO owns these stubs. Do not edit the two generated files by hand. Generate them again.

## Source

- Repository: AWSMantlePumice, branch `mainline`.
- Commit: `769c752995b8a39b27df028176411d17c99ff441`. This is `PUMICE_COMMIT` in `scripts/pumice_pins.py`.
- File: `pumice-rust/proto/pumice_engine.proto`. The last commit that changed it is `1a1bcdbfe1929f00e19e24277b10dd9042a5413b`.
- SHA-256 of the file: `9b4dbe3de02378403d6d7a231fdc2389a4f6d341b5cb452caa48f2b14ee29f88`.

The Rust proto declares `prompt_tokens_cached` (tag 6 in `GenerateStreamChunk`, tag 7 in `GenerateComplete`). The Python copy in `src/pumice/grpc/pumice_engine.proto` comments that field out. That is why AMMO does not use the stubs from the installed `pumice` wheel.

## Proto check

The stubs were made from the proto at the earlier pin `9d126713c03dcc88883b85c3d3a857b89982c957`. The proto did not change between that commit and the current pin, so the stubs were not generated again. Run these commands in a Pumice checkout that has both commits:

```bash
git diff --exit-code 9d126713 769c7529 -- pumice-rust/proto src/pumice/grpc; echo "exit=$?"
git log --oneline 9d126713..769c7529 -- pumice-rust/proto src/pumice/grpc | wc -l
git show 769c7529:pumice-rust/proto/pumice_engine.proto | shasum -a 256
```

The output was:

```
exit=0
       0
9b4dbe3de02378403d6d7a231fdc2389a4f6d341b5cb452caa48f2b14ee29f88  -
```

The diff is empty, no commit in the range touches the proto, and the SHA-256 matches the value above. When the pin moves again, run the same commands with the new commit. Generate the stubs again only when the diff is not empty.

## Generator versions

- `grpcio-tools==1.80.0`, `grpcio==1.80.0`, `protobuf==6.31.1` (`libprotoc 31.1`).
- These are the versions that made the stubs shipped in the Pumice wheel. The generated code needs `protobuf>=6.31.1` and `grpcio>=1.80.0` at run time.
- Pumice at the pinned commit does not pin `protobuf` or `grpcio`. The engine venv gets them from other packages. Preflight imports `pumice_engine_pb2`, so a `protobuf` that is older than the gencode fails the `proto stubs` check.
- Do not use a newer `grpcio-tools`. Newer gencode raises an error at import when the run-time `protobuf` or `grpcio` is older than the gencode.

## Generate command

Run these commands from the plugin root. Use a scratch venv outside the plugin. Set `PROTO_DIR` to the proto dir of your Pumice checkout.

```bash
uv venv ~/scratch/pb2-venv --python 3.12
uv pip install --python ~/scratch/pb2-venv/bin/python "grpcio-tools==1.80.0" "grpcio==1.80.0" "protobuf==6.31.1"
PROTO_DIR=<pumice-checkout>/pumice-rust/proto
OUT_DIR=scripts/pumice_pb
~/scratch/pb2-venv/bin/python -m grpc_tools.protoc -I"$PROTO_DIR" --python_out="$OUT_DIR" --grpc_python_out="$OUT_DIR" "$PROTO_DIR/pumice_engine.proto"
sed -i '' 's/^import pumice_engine_pb2 as pumice__engine__pb2$/from . import pumice_engine_pb2 as pumice__engine__pb2/' "$OUT_DIR/pumice_engine_pb2_grpc.py"
```

The `sed` step makes the import in `pumice_engine_pb2_grpc.py` relative, so the stubs load as the `pumice_pb` package. On Linux, use `sed -i` without `''`.

## Check

Run this command from the plugin root:

```bash
~/scratch/pb2-venv/bin/python -c "import sys; sys.path.insert(0, 'scripts'); from pumice_pb import pumice_engine_pb2 as pb2; print(pb2.GenerateComplete.DESCRIPTOR.fields_by_name['prompt_tokens_cached'].number)"
```

The command prints `7`.
