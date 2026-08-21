"""Fetch the real audio set -> assets/audio/ (overwrites the synth set).

Sources (event is invite-only; licensing call made by the operator):
- music.ogg   one loop iteration cut from the Minion Rush theme
              (youtube ubercGXxkPg, "Game Theme Extended"), found by
              envelope autocorrelation, end crossfaded into the start.
- Quake sfx   shareware quake106.zip from id's ftp mirror; pak0.pak
              parsed directly (PACK header, 64-byte dirents).
- announcer   Halo Reach lines + UT first blood from myinstants.

`make_audio.py` remains the from-scratch synth fallback for every file.
Needs: yt-dlp, ffmpeg, 7z, numpy.
"""

from __future__ import annotations

import struct
import subprocess
import urllib.request
import wave
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[2] / "assets" / "audio"
WORK = Path("/tmp/minions_audio_fetch")
SR = 22050

QUAKE_ZIP = "https://ftp.gwdg.de/pub/misc/ftp.idsoftware.com/idstuff/quake/quake106.zip"
MUSIC_URL = "https://www.youtube.com/watch?v=ubercGXxkPg"

QUAKE_MAP = {
    "rail": "sound/weapons/guncock.wav",
    "shotgun": "sound/weapons/shotgn2.wav",
    "rocket": "sound/weapons/sgun1.wav",
    "explosion": "sound/weapons/r_exp3.wav",
    "melee": "sound/weapons/ax1.wav",
    "melee_hit": "sound/player/axhit2.wav",
    "hurt": "sound/player/pain1.wav",
    "death": "sound/player/death1.wav",
    "pickup": "sound/weapons/pkup.wav",
    "spawn": "sound/misc/r_tele1.wav",
    "lava": "sound/ambience/fire1.wav",
}

HALO_MAP = {
    "first_blood": "firstblood.mp3",  # UT announcer; halo has no line for it
    "double_kill": "halo-reach-double-kill.mp3",
    "triple_kill": "halo-reach-triple-kill.mp3",
    "overkill": "halo-reach-overkill.mp3",
    "killing_spree": "halo-reach-killing-spree.mp3",
    "rampage": "halo-reach-rampage.mp3",
    "slayer": "halo-reach-slayer-voice.mp3",
}


def run(*cmd, **kw):
    subprocess.run(cmd, check=True, capture_output=True, **kw)


def decode(path: Path, sr: int = SR, channels: int = 1) -> np.ndarray:
    p = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", str(path), "-f", "s16le",
         "-ar", str(sr), "-ac", str(channels), "-"],
        check=True, capture_output=True)
    x = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float64) / 32768.0
    return x.reshape(-1, channels) if channels > 1 else x


def write_norm(name: str, x: np.ndarray, peak: float = 0.85):
    m = np.max(np.abs(x))
    if m > 0:
        x = x / m * peak
    n = int(SR * 0.002)
    if x.ndim == 1 and len(x) > 2 * n:
        x = x.copy()
        x[:n] *= np.linspace(0, 1, n)
        x[-n:] *= np.linspace(1, 0, n)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1 if x.ndim == 1 else x.shape[1])
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype(np.int16).tobytes())
    print(f"  {name}.wav  {len(x) / SR:.2f}s")


def quake():
    z = WORK / "quake106.zip"
    if not z.exists():
        print("downloading quake106.zip ...")
        urllib.request.urlretrieve(QUAKE_ZIP, z)
    ext = WORK / "q106"
    pak_path = ext / "ID1" / "PAK0.PAK"
    if not pak_path.exists():
        run("unzip", "-o", "-q", str(z), "-d", str(WORK / "qzip"))
        run("7z", "x", "-y", str(WORK / "qzip" / "resource.1"), f"-o{ext}")
    pak = pak_path.read_bytes()
    off, size = struct.unpack("<ii", pak[4:12])
    entries = {}
    for i in range(size // 64):
        e = pak[off + i * 64: off + (i + 1) * 64]
        name = e[:56].split(b"\0")[0].decode()
        fo, fs = struct.unpack("<ii", e[56:64])
        entries[name] = (fo, fs)
    for out_name, src in QUAKE_MAP.items():
        fo, fs = entries[src]
        raw = WORK / "q.wav"
        raw.write_bytes(pak[fo:fo + fs])
        x = decode(raw)
        if out_name == "lava":
            # ambience loop: keep seam, just crossfade edges a touch
            n = int(SR * 0.15)
            fade = np.linspace(0, 1, n)
            x = x.copy()
            x[:n] = x[:n] * fade + x[-n:] * (1 - fade)
            x = x[:-n]
        write_norm(out_name, x)


def halo():
    for out_name, f in HALO_MAP.items():
        dst = WORK / f
        if not dst.exists():
            req = urllib.request.Request(
                f"https://www.myinstants.com/media/sounds/{f}",
                headers={"User-Agent": "Mozilla/5.0"})
            dst.write_bytes(urllib.request.urlopen(req).read())
        write_norm(out_name, decode(dst), peak=0.92)


def music():
    full = WORK / "music_full.wav"
    if not full.exists():
        print("downloading music ...")
        run("yt-dlp", "-x", "--audio-format", "wav", "-o", str(full),
            MUSIC_URL)
    # find the loop period of the "extended" video by autocorrelating a
    # 50 Hz loudness envelope
    mono = decode(full, sr=11025)
    hop = 11025 // 50
    env = np.abs(mono[: len(mono) // hop * hop]).reshape(-1, hop).mean(axis=1)
    cen = env - env.mean()
    ac = np.correlate(cen, cen, "full")[len(cen) - 1:]
    lo, hi = 30 * 50, 300 * 50
    period = (lo + int(np.argmax(ac[lo:hi]))) / 50.0
    # skip the one-time lead-in (the video opens with ~2 s of silence) —
    # looping back into dead air read as the music "stopping"
    ref = np.median(env[len(env) // 4: len(env) // 2])
    onset = int(np.argmax(env > 0.3 * ref)) / 50.0 + 0.5
    print(f"  loop period ~{period:.1f}s, start at {onset:.2f}s")

    st = decode(full, sr=44100, channels=2)
    a = int(onset * 44100)
    n_loop = int(period * 44100)
    n_fade = int(0.5 * 44100)
    y = st[a:a + n_loop].copy()
    w = np.linspace(0, 1, n_fade)[:, None]
    y[-n_fade:] = y[-n_fade:] * (1 - w) + st[a + n_loop:a + n_loop + n_fade] * w
    tmp = WORK / "music_loop.wav"
    with wave.open(str(tmp), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(44100)
        f.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())
    run("ffmpeg", "-y", "-v", "quiet", "-i", str(tmp), "-q:a", "4",
        str(OUT / "music.ogg"))
    legacy = OUT / "music.wav"
    if legacy.exists():
        legacy.unlink()
    print(f"  music.ogg  {period:.1f}s loop")


def main():
    WORK.mkdir(exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"-> {OUT}")
    quake()
    halo()
    music()


if __name__ == "__main__":
    main()
