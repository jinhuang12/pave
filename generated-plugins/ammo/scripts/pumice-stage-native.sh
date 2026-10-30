#!/usr/bin/env bash
#
# pumice-stage-native.sh <venv-python> <src-dir> [cargo feature flags...]
#
# Rebuild pumice-rust from source and stage its native extensions into an
# editable Pumice checkout. An editable "pip install -e ." never compiles
# the `_ops` CUDA kernels and removes the wheel's staged `.so` files, so
# this script rescues them first, then puts them back.
#
# Run it through the plugin launcher, from the worktree root:
#   scripts/ammo pumice-stage-native.sh "$PWD/.venv/bin/python" "$PWD" --features "nixl-stub cmb"
# The launcher fills the campaign env (AMMO_ENGINE_ROOT and the others)
# before it runs this script with bash.
#
# Steps:
#   1. Rescue pumice/_ops/*.so, pumice_rust*.so and bin/pumice-server from
#      the venv, if the wheel is installed there. Otherwise take _ops from
#      <engine root>/src/pumice/_ops/*.so. A previous run on the engine venv
#      staged them into that tree. A fresh track worktree has no _ops .so
#      files, because git ignores *.so. The engine root is AMMO_ENGINE_ROOT
#      when it is set, else the parent of `git -C <src-dir> rev-parse
#      --git-common-dir` (the main checkout of the worktree's repository).
#   2. "uv pip install --python <venv-python> -e <src-dir> --no-deps"
#   3. "cargo build --release --manifest-path <src-dir>/pumice-rust/Cargo.toml"
#      with any extra cargo flags appended. PYO3_PYTHON defaults to
#      <venv-python>, so the extension is built for the venv's interpreter.
#   4. Copy <target>/release/libpumice_rust.so to the venv's site-packages,
#      where <target> is CARGO_TARGET_DIR, else the workspace target dir
#      <src-dir>/target, else <src-dir>/pumice-rust/target,
#      named pumice_rust<EXT_SUFFIX>. The site-packages dir comes from the
#      venv python (sysconfig purelib), so any Python version works.
#   5. Restore the rescued _ops set into <src-dir>/src/pumice/_ops/, and
#      put bin/pumice-server back into the venv if step 2 removed it.
#
# CARGO_TARGET_DIR, CARGO_HOME, RUSTC_WRAPPER and PYO3_PYTHON are honored
# from the environment if set.
#
# Final check: importing pumice and pumice_rust with <venv-python> resolves
# both to <src-dir>, and <src-dir>/src/pumice/_ops has at least one .so.

set -euo pipefail

# Find cargo when it is not on PATH. A container image can ship a profile
# file with the cargo PATH plus PYO3_PYTHON; profile.d is only sourced by
# login shells, so source it here. Else try CARGO_HOME (default ~/.cargo),
# then the shared toolchain location /opt/cargo.
if ! command -v cargo > /dev/null 2>&1; then
    if [ -r /etc/profile.d/ammo-rust.sh ]; then
        # shellcheck source=/dev/null
        . /etc/profile.d/ammo-rust.sh
    elif [ -x "${CARGO_HOME:-$HOME/.cargo}/bin/cargo" ]; then
        export PATH="${CARGO_HOME:-$HOME/.cargo}/bin:${PATH}"
    elif [ -x /opt/cargo/bin/cargo ]; then
        export CARGO_HOME="${CARGO_HOME:-/opt/cargo}"
        export RUSTUP_HOME="${RUSTUP_HOME:-/opt/rustup}"
        export PATH="/opt/cargo/bin:${PATH}"
    fi
fi

usage() {
    echo "Usage: $0 <venv-python> <src-dir> [cargo feature flags...]" >&2
    echo "  <venv-python>  Path to the python binary in the target venv." >&2
    echo "  <src-dir>      Editable Pumice checkout to build and stage." >&2
    exit 1
}

if [ "$#" -lt 2 ]; then
    usage
fi

VENV_PYTHON="$1"
SRC_DIR="$2"
shift 2
CARGO_FLAGS=("$@")

if [ ! -x "$VENV_PYTHON" ]; then
    echo "pumice-stage-native.sh: $VENV_PYTHON is not an executable file" >&2
    exit 1
fi

if [ ! -d "$SRC_DIR" ]; then
    echo "pumice-stage-native.sh: $SRC_DIR is not a directory" >&2
    exit 1
fi

# The engine checkout that holds the compiled _ops kernels.
engine_root() {
    if [ -n "${AMMO_ENGINE_ROOT:-}" ]; then
        printf '%s\n' "$AMMO_ENGINE_ROOT"
        return 0
    fi
    local common
    common="$(git -C "$SRC_DIR" rev-parse --git-common-dir 2> /dev/null)" || return 0
    case "$common" in
        /*) ;;
        *) common="${SRC_DIR}/${common}" ;;
    esac
    (cd "${common}/.." 2> /dev/null && pwd) || true
}

VENV_BIN_DIR="$(cd "$(dirname "$VENV_PYTHON")" && pwd)"
SITE_PACKAGES="$("$VENV_PYTHON" -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
# OPS_DIR is only the restore destination. The fallback source is the
# engine root, because a fresh git worktree has no _ops .so files.
OPS_DIR="${SRC_DIR}/src/pumice/_ops"
ENGINE_ROOT="$(engine_root)"
FALLBACK_OPS=""
if [ -n "$ENGINE_ROOT" ]; then
    FALLBACK_OPS="${ENGINE_ROOT}/src/pumice/_ops"
fi

export PYO3_PYTHON="${PYO3_PYTHON:-$VENV_PYTHON}"

HOLD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/pumice-stage-native.XXXXXX")"
trap 'rm -rf "$HOLD_DIR"' EXIT

# Step 1: rescue the native artifacts before the editable install wipes them.
mkdir -p "${HOLD_DIR}/_ops"
RESCUED_FROM_VENV=0
if compgen -G "${SITE_PACKAGES}/pumice/_ops/*.so" > /dev/null 2>&1; then
    echo "pumice-stage-native.sh: rescuing native artifacts from the venv"
    cp "${SITE_PACKAGES}"/pumice/_ops/*.so "${HOLD_DIR}/_ops/"
    RESCUED_FROM_VENV=1
    if compgen -G "${SITE_PACKAGES}/pumice_rust*" > /dev/null 2>&1; then
        cp "${SITE_PACKAGES}"/pumice_rust* "${HOLD_DIR}/"
    fi
elif [ -n "$FALLBACK_OPS" ]; then
    echo "pumice-stage-native.sh: wheel not installed in the venv, falling back to ${FALLBACK_OPS}"
    if compgen -G "${FALLBACK_OPS}/*.so" > /dev/null 2>&1; then
        cp "${FALLBACK_OPS}"/*.so "${HOLD_DIR}/_ops/"
    fi
else
    echo "pumice-stage-native.sh: wheel not installed in the venv and no engine root found;" \
        "set AMMO_ENGINE_ROOT or run from a git worktree of the engine" >&2
fi
# The editable install removes the wheel's pumice-server. Keep a copy.
if [ -f "${VENV_BIN_DIR}/pumice-server" ]; then
    cp -p "${VENV_BIN_DIR}/pumice-server" "${HOLD_DIR}/pumice-server"
fi

# Step 2: editable install, no dependency resolution (the venv already has
# the pinned dependency versions).
echo "pumice-stage-native.sh: uv pip install -e ${SRC_DIR}"
uv pip install --python "$VENV_PYTHON" -e "$SRC_DIR" --no-deps

# Step 3: build pumice-rust. CARGO_TARGET_DIR, CARGO_HOME, RUSTC_WRAPPER and
# PYO3_PYTHON pass through from the environment when set.
echo "pumice-stage-native.sh: cargo build --release"
cargo build --release \
    --manifest-path "${SRC_DIR}/pumice-rust/Cargo.toml" \
    ${CARGO_FLAGS[@]+"${CARGO_FLAGS[@]}"}

# Step 4: stage the compiled extension into site-packages under the name
# Python expects for the current interpreter's ABI.
# Pumice is a cargo workspace (the root Cargo.toml lists pumice-rust as a
# member), so without CARGO_TARGET_DIR cargo writes to <src-dir>/target,
# not <src-dir>/pumice-rust/target. Check the workspace path first and keep
# the per-crate path for a non-workspace checkout.
if [ -n "${CARGO_TARGET_DIR:-}" ]; then
    RELEASE_DIR="${CARGO_TARGET_DIR}/release"
else
    RELEASE_DIR=""
    for candidate in "${SRC_DIR}/target/release" "${SRC_DIR}/pumice-rust/target/release"; do
        if [ -f "${candidate}/libpumice_rust.so" ]; then
            RELEASE_DIR="$candidate"
            break
        fi
    done
    if [ -z "$RELEASE_DIR" ]; then
        echo "pumice-stage-native.sh: libpumice_rust.so not found in ${SRC_DIR}/target/release or ${SRC_DIR}/pumice-rust/target/release" >&2
        exit 1
    fi
fi

EXT_SUFFIX="$("$VENV_PYTHON" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')"
mkdir -p "$SITE_PACKAGES"
cp "${RELEASE_DIR}/libpumice_rust.so" "${SITE_PACKAGES}/pumice_rust${EXT_SUFFIX}"

# Step 5: restore the rescued _ops set into the editable source tree.
mkdir -p "$OPS_DIR"
if compgen -G "${HOLD_DIR}/_ops/*.so" > /dev/null 2>&1; then
    cp "${HOLD_DIR}"/_ops/*.so "$OPS_DIR/"
fi
# Put pumice-server back. A cargo build with the "embedded" feature makes a
# fresh binary; use it in that case. Otherwise restore the rescued copy.
if [[ " ${CARGO_FLAGS[*]:-} " == *embedded* ]] && [ -x "${RELEASE_DIR}/pumice-server" ]; then
    cp "${RELEASE_DIR}/pumice-server" "${VENV_BIN_DIR}/pumice-server"
    chmod 0755 "${VENV_BIN_DIR}/pumice-server"
elif [ -f "${HOLD_DIR}/pumice-server" ] && [ ! -f "${VENV_BIN_DIR}/pumice-server" ]; then
    cp -p "${HOLD_DIR}/pumice-server" "${VENV_BIN_DIR}/pumice-server"
fi
if [ "$RESCUED_FROM_VENV" -eq 1 ]; then
    echo "pumice-stage-native.sh: artifacts were rescued from the wheel in the venv"
fi

# Final check: both modules resolve to the editable source tree, and _ops
# has at least one compiled kernel.
"$VENV_PYTHON" -c "
import pumice
import pumice_rust
print(pumice.__file__)
print(pumice_rust.__file__)
"

OPS_COUNT="$(find "$OPS_DIR" -maxdepth 1 -name '*.so' | wc -l | tr -d ' ')"
if [ "$OPS_COUNT" -eq 0 ]; then
    echo "pumice-stage-native.sh: no .so files in ${OPS_DIR} after staging" \
        "(engine root: ${ENGINE_ROOT:-none}; set AMMO_ENGINE_ROOT to the engine checkout)" >&2
    exit 1
fi
echo "pumice-stage-native.sh: staged ${OPS_COUNT} _ops .so file(s) into ${OPS_DIR}"

echo "pumice-stage-native.sh: done"
