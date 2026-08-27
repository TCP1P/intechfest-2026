#!/usr/bin/env bash
# Cross-compile the Windows client from Linux/WSL using the
# x86_64-pc-windows-gnu target (mingw-w64). Build happens on ext4 and
# reuses the WSL-side cargo registry + target cache — no rsync to /mnt/c,
# no cargo.exe, no UNC-path slowdown.
#
# Prereqs:
#   sudo apt install mingw-w64
#   rustup target add x86_64-pc-windows-gnu

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="x86_64-pc-windows-gnu"
PKG_NAME="${PKG_NAME:-minions-in-32k-win}"
DIST_DIR="${ROOT}/dist"

echo "==> cargo build --release --target ${TARGET} -p client"
(cd "${ROOT}" && cargo build --release --target "${TARGET}" -p client)

EXE="${ROOT}/target/${TARGET}/release/client.exe"
if [[ ! -f "${EXE}" ]]; then
    echo "error: ${EXE} missing after build" >&2
    exit 1
fi

# Assets are embedded in the exe (bevy_embedded_assets); the bundle is the
# single binary. config.toml is deliberately NOT shipped.
echo "==> packaging ${DIST_DIR}/${PKG_NAME}.zip"
rm -rf "${DIST_DIR}/${PKG_NAME}" "${DIST_DIR}/${PKG_NAME}.zip"
mkdir -p "${DIST_DIR}/${PKG_NAME}"
cp "${EXE}" "${DIST_DIR}/${PKG_NAME}/"
(cd "${DIST_DIR}" && zip -qr "${PKG_NAME}.zip" "${PKG_NAME}/")

EXE_SIZE=$(du -h "${DIST_DIR}/${PKG_NAME}/client.exe" | cut -f1)
ZIP_SIZE=$(du -h "${DIST_DIR}/${PKG_NAME}.zip" | cut -f1)
echo "==> done"
echo "    ${DIST_DIR}/${PKG_NAME}/client.exe     ${EXE_SIZE}"
echo "    ${DIST_DIR}/${PKG_NAME}.zip             ${ZIP_SIZE}"
