#!/usr/bin/env bash
# Build the browser client: wasm32 + wasm-bindgen bundle for the backend's
# /wasm route. WebTransport is an unstable web-sys API, hence the cfg flag.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/dist/wasm-pkg"

echo "==> cargo build --release --target wasm32-unknown-unknown -p client"
(cd "${ROOT}" && RUSTFLAGS='--cfg web_sys_unstable_apis --cfg getrandom_backend="wasm_js"' \
    cargo build --release --target wasm32-unknown-unknown -p client)

echo "==> wasm-bindgen"
rm -rf "${OUT}"
wasm-bindgen --target web --no-typescript --out-dir "${OUT}" --out-name client \
    "${ROOT}/target/wasm32-unknown-unknown/release/client.wasm"
du -h "${OUT}/client_bg.wasm"
