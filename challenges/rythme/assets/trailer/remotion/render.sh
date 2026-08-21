#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
trailer_dir="$(cd -- "${project_dir}/.." && pwd)"

if [[ $# -lt 2 ]]; then
    printf 'Usage: %s <native-menu-capture.mkv> <gameplay-capture.mkv> [output.mp4]\n' "${0##*/}" >&2
    exit 2
fi

menu_capture_path="$(realpath -- "$1")"
gameplay_capture_path="$(realpath -- "$2")"
output_path="$(realpath -m -- "${3:-${trailer_dir}/rythme-teaser.mp4}")"

"${project_dir}/prepare-media.sh" "${menu_capture_path}" "${gameplay_capture_path}"

cd "${project_dir}"

if [[ ! -d node_modules ]]; then
    npm ci
fi

npx remotion render \
    src/index.ts \
    RythmeTrailer \
    "${output_path}" \
    --codec=h264 \
    --audio-codec=aac \
    --audio-bitrate=192K \
    --crf=17 \
    --pixel-format=yuv420p \
    --concurrency=50% \
    --overwrite

printf 'Rendered %s with Remotion\n' "${output_path}"
