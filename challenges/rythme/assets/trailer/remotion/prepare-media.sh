#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
trailer_dir="$(cd -- "${project_dir}/.." && pwd)"
challenge_root="$(cd -- "${project_dir}/../../.." && pwd)"
public_dir="${project_dir}/public"

if [[ $# -ne 2 ]]; then
    printf 'Usage: %s <native-menu-capture.mkv> <gameplay-capture.mkv>\n' "${0##*/}" >&2
    exit 2
fi

menu_capture_path="$1"
gameplay_capture_path="$2"
beatmap_archive="${challenge_root}/src/shared/beatmaps/city-girl-ji-euns-sunset.osz"

for required_file in "${menu_capture_path}" "${gameplay_capture_path}" "${beatmap_archive}"; do
    if [[ ! -f "${required_file}" ]]; then
        printf 'Missing required file: %s\n' "${required_file}" >&2
        exit 1
    fi
done

mkdir -p -- "${public_dir}/sfx"

encode_clip() {
    local input_path="$1"
    local start_time="$2"
    local duration="$3"
    local output_path="$4"

    ffmpeg -y -v error \
        -ss "${start_time}" \
        -i "${input_path}" \
        -t "${duration}" \
        -an \
        -c:v libx264 \
        -preset fast \
        -crf 16 \
        -pix_fmt yuv420p \
        -movflags +faststart \
        "${output_path}"
}

encode_clip "${menu_capture_path}" 6.40 10.20 "${public_dir}/menu.mp4"
encode_clip "${gameplay_capture_path}" 24.00 2.40 "${public_dir}/lobby.mp4"
encode_clip "${gameplay_capture_path}" 31.40 2.80 "${public_dir}/reveal.mp4"
encode_clip "${gameplay_capture_path}" 35.80 11.80 "${public_dir}/play.mp4"

unzip -p -- "${beatmap_archive}" source-audio.mp3 >"${public_dir}/beatmap.mp3"

for sound_name in \
    Intro-welcome.mp3 \
    Menu-button-hover.wav \
    Menu-osu-logo-select.wav \
    Menu-osu-logo-swoosh.wav \
    Menu-button-play-select.wav \
    Menu-button-default-select.wav \
    Multiplayer-player-joined.wav \
    Multiplayer-countdown-tick.wav \
    Multiplayer-countdown-warn-final.wav \
    UI-ruleset-select-mania.wav \
    Gameplay-Argon-normal-hitnormal.wav; do
    install -m 0644 \
        "${trailer_dir}/sounds/${sound_name}" \
        "${public_dir}/sfx/${sound_name}"
done

printf 'Prepared Remotion media in %s\n' "${public_dir}"
