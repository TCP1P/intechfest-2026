# audio

Two generators, same output filenames in `assets/audio/`:

- `python3 fetch_real.py` — the shipped set: Minion Rush theme (one
  loop iteration auto-cut from the extended OST, -> `music.ogg`),
  Quake shareware pak0 weapon/impact sounds, Halo Reach announcer
  lines (+ UT first blood). Needs yt-dlp, ffmpeg, 7z. This set is for
  the invite-only event only — not redistribution-safe.
- `python3 make_audio.py` — fully synthesized fallback (numpy synth +
  Windows TTS announcer via WSL interop). No sampled material; use
  this set if a build ever goes public.

`flame.wav` and `kill.wav` are always synthesized (no good source
equivalent). The client loads `music.ogg` + `<name>.wav` — swapping
any file and rebuilding is all it takes.
