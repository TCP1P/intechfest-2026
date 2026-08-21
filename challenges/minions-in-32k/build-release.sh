#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME="${ROOT}/game"
RELEASE="${ROOT}/release"
ARCHIVE="game_minions-in-32k.zip"
WINDOWS_TARGET="x86_64-pc-windows-gnu"
RUST_TOOLCHAIN="1.96.0"

mkdir -p "${RELEASE}"
BUILD="$(mktemp -d)"
PUBLISH="$(mktemp -d "${RELEASE}/.publish.XXXXXX")"
cleanup() {
    rm -rf -- "${BUILD}" "${PUBLISH}"
}
trap cleanup EXIT

mkdir -p "${BUILD}/handout" "${BUILD}/source/minions-in-32k-source"

echo "[*] Ensuring the Windows Rust target is installed"
rustup target add --toolchain "${RUST_TOOLCHAIN}" "${WINDOWS_TARGET}"

echo "[*] Building the native Linux client"
cargo "+${RUST_TOOLCHAIN}" build --manifest-path "${GAME}/Cargo.toml" --locked --release -p client
install -m 0755 "${GAME}/target/release/client" "${BUILD}/handout/minions-in-32k"
strip --strip-unneeded "${BUILD}/handout/minions-in-32k"

echo "[*] Cross-compiling the native Windows client"
cargo "+${RUST_TOOLCHAIN}" build --manifest-path "${GAME}/Cargo.toml" --locked --release \
    --target "${WINDOWS_TARGET}" -p client
install -m 0644 "${GAME}/target/${WINDOWS_TARGET}/release/client.exe" \
    "${BUILD}/handout/minions-in-32k.exe"
x86_64-w64-mingw32-strip --strip-unneeded "${BUILD}/handout/minions-in-32k.exe"

echo "[*] Assembling exact corresponding source"
SOURCE="${BUILD}/source/minions-in-32k-source"
rsync -a \
    --exclude target \
    --exclude dist \
    --exclude 'replays/***' \
    --exclude crates/backend \
    --exclude crates/headless-client \
    --exclude crates/latency-proxy \
    --exclude crates/replay \
    --exclude crates/server \
    --exclude crates/spectator \
    --exclude deploy \
    --exclude fuzz \
    "${GAME}/" "${SOURCE}/game/"
mkdir -p "${SOURCE}/game/replays"
touch "${SOURCE}/game/replays/.gitkeep"
cp "${ROOT}/challenge/SOURCE-README.md" "${SOURCE}/README.md"
cp "${ROOT}/challenge/NOTICE.txt" "${SOURCE}/NOTICE.txt"
(
    cd "${BUILD}/source"
    zip -X -q -9 -r "${BUILD}/handout/CORRESPONDING-SOURCE.zip" minions-in-32k-source
)

cp "${ROOT}/challenge/CLIENT-README.txt" "${BUILD}/handout/README.txt"
cp "${ROOT}/challenge/NOTICE.txt" "${BUILD}/handout/NOTICE.txt"
cp "${GAME}/LICENSE" "${BUILD}/handout/LICENSE-AGPL-3.0.txt"
cp "${GAME}/LICENSE.content.md" "${BUILD}/handout/LICENSE-CONTENT-CC-BY-NC-SA-4.0.md"

echo "[*] Creating ${ARCHIVE}"
(
    cd "${BUILD}/handout"
    zip -X -q -9 "${PUBLISH}/${ARCHIVE}" \
        minions-in-32k.exe \
        minions-in-32k \
        README.txt \
        NOTICE.txt \
        LICENSE-AGPL-3.0.txt \
        LICENSE-CONTENT-CC-BY-NC-SA-4.0.md \
        CORRESPONDING-SOURCE.zip
)

if unzip -tqq "${PUBLISH}/${ARCHIVE}" >/dev/null; then
    echo "[+] Archive integrity verified"
else
    echo "[-] Archive integrity check failed" >&2
    exit 1
fi
(
    cd "${PUBLISH}"
    sha256sum "${ARCHIVE}" > SHA256SUMS
)

mv -f -- "${PUBLISH}/${ARCHIVE}" "${RELEASE}/${ARCHIVE}"
mv -f -- "${PUBLISH}/SHA256SUMS" "${RELEASE}/SHA256SUMS"

echo "[+] Player handout built"
ls -lh "${RELEASE}/${ARCHIVE}"
unzip -l "${RELEASE}/${ARCHIVE}"
