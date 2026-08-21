#!/usr/bin/env bash
set -euo pipefail

trailer_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

exec "${trailer_dir}/remotion/render.sh" "$@"
