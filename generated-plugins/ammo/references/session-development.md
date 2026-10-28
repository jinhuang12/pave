# Campaign Development

Read this when a source change needs an environment check or an engine rebuild.
The user builds the engine before the campaign starts: the Pumice checkout (the engine root, `target.json` `engine.root`), its venv (`engine.venv`), and the release cargo build. `references/campaign-env.md` names both paths. A track worktree gets its own `.venv` from the worktree build hook. That venv sees the packages of the engine venv and imports `pumice` and `pumice_rust` from the worktree. Use that environment for all Python and cargo commands in the worktree.

## Check the environment

```bash
.venv/bin/python -c "import pumice, pumice_rust; print(pumice.__file__, pumice_rust.__file__)"
```

Both import paths must belong to the assigned worktree. Check again after resume or a worktree change.
Report a missing environment or import error to the lead. Agents do not create environments or install packages. Never `pip install torch` or `pip install vllm`: the engine venv holds the pinned versions, and `scripts/pumice_pins.py` checks them.

## Rebuild after a change

| Change | Action |
|---|---|
| Python only (`src/pumice/**`) | Use the editable installation; no build is needed. The edit is live at the next server launch. |
| Rust (`pumice-rust/src/**`), confdb (`pumice-rust/confdb/*.toml`), `build.rs`, or a `Cargo.toml` | Run `scripts/ammo pumice-stage-native.sh "$PWD/.venv/bin/python" "$PWD" --features "nixl-stub cmb"` from the worktree root. Neuron campaigns use `--features nixl-stub`. |
| `csrc/` (the prebuilt `pumice/_ops` libraries) | The worktree build stages the prebuilt `_ops` libraries of the engine and has no `_ops` build step. Do not start a `csrc/` track unless the lead approves a build path. The vLLM runner (gpt-oss on CUDA) does not load `_ops`. |

`pumice-stage-native.sh` runs `cargo build --release` for `pumice-rust/`, copies the new `pumice_rust` library into the venv, and restores `_ops`. Keep `CARGO_HOME`, `CARGO_TARGET_DIR`, `SCCACHE_DIR`, `RUSTC_WRAPPER`, and `RUSTUP_HOME` as they are set, unless the approved task changes them. `references/campaign-env.md` sets `CARGO_TARGET_DIR`. The other four come from your environment. `$CARGO_HOME/config.toml` carries the `[patch.crates-io]` entry for `amzn-mantle-cmb-api`; see the cmb-api note in `e2e-latency-guide.md` § The freshness gate.

## Validate the change

Run the tests that exercise the changed behavior. Add or repeat tests when a change, failure, or unresolved question requires them.
AMMO's required gates remain binding. Read `validation-defaults.md` for those gates and `gpu-pool.md` before GPU work.
Use `run_pumice_grpc_bench.py` for official campaign measurements. Generic benchmark commands do not replace its evidence contract.
