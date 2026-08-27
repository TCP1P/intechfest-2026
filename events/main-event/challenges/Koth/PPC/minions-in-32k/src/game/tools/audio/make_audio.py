"""Procedural audio for minions-in-16k -> assets/audio/*.wav.

Everything is synthesized (numpy) or spoken by the Windows TTS engine via
WSL interop — no sampled/copyrighted material, safe to embed in the
distributed client. 22050 Hz mono 16-bit wav (bevy `wav` feature).

The music is an original chiptune funk loop standing in for licensed
music; swapping assets/audio/music.wav for any other wav keeps working.
"""

from __future__ import annotations

import math
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 22050
OUT = Path(__file__).resolve().parents[2] / "assets" / "audio"
RNG = np.random.default_rng(16000)  # deterministic output


def write_wav(name: str, x: np.ndarray, peak: float = 0.85):
    m = np.max(np.abs(x))
    if m > 0:
        x = x / m * peak
    # 2 ms edge fades kill clicks (and loop seams for the loops)
    n = int(SR * 0.002)
    if len(x) > 2 * n:
        x[:n] *= np.linspace(0, 1, n)
        x[-n:] *= np.linspace(1, 0, n)
    data = (x * 32767).astype(np.int16)
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(data.tobytes())
    print(f"  {name}.wav  {len(x) / SR:.2f}s")


def t(dur: float) -> np.ndarray:
    return np.arange(int(SR * dur)) / SR


def sine(freq, dur):
    return np.sin(2 * math.pi * freq * t(dur))


def sweep(f0, f1, dur, shape="exp"):
    """Phase-correct frequency sweep."""
    tt = t(dur)
    if shape == "exp":
        f = f0 * (f1 / f0) ** (tt / dur)
    else:
        f = f0 + (f1 - f0) * tt / dur
    phase = 2 * math.pi * np.cumsum(f) / SR
    return np.sin(phase), phase


def square_phase(phase, duty=0.5):
    return np.where((phase / (2 * math.pi)) % 1.0 < duty, 1.0, -1.0)


def square(freq, dur, duty=0.5):
    return square_phase(2 * math.pi * freq * t(dur), duty)


def triangle(freq, dur):
    tt = t(dur)
    return 2 * np.abs(2 * ((tt * freq) % 1.0) - 1) - 1


def noise(dur):
    return RNG.uniform(-1, 1, int(SR * dur))


def lowpass(x, fc):
    a = math.exp(-2 * math.pi * fc / SR)
    y = np.empty_like(x)
    acc = 0.0
    b = 1 - a
    for i, v in enumerate(x):
        acc = a * acc + b * v
        y[i] = acc
    return y


def env_exp(dur, k):
    return np.exp(-k * t(dur))


def fit(x, n):
    return x[:n] if len(x) >= n else np.pad(x, (0, n - len(x)))


# ----------------------------------------------------------------- sfx

def sfx_rail():
    dur = 0.32
    body, phase = sweep(2400, 160, dur)
    x = (0.6 * body + 0.4 * square_phase(phase)) * env_exp(dur, 11)
    x += fit(lowpass(noise(0.04), 4000) * env_exp(0.04, 60), len(x))
    return x


def sfx_shotgun():
    dur = 0.38
    n = lowpass(noise(dur), 1100) * env_exp(dur, 9)
    thump, _ = sweep(150, 45, 0.22)
    x = n + 0.9 * fit(thump * env_exp(0.22, 12), len(n))
    return x


def sfx_rocket():
    dur = 0.55
    tt = t(dur)
    whoosh = lowpass(noise(dur), 600) * (tt / dur) ** 0.5 * env_exp(dur, 3)
    ign = lowpass(noise(dur), 2500) * env_exp(dur, 18)
    rumble = sine(75, dur) * env_exp(dur, 5)
    return whoosh + 0.5 * ign + 0.6 * rumble


def sfx_explosion():
    dur = 0.95
    brown = np.cumsum(noise(dur))
    brown /= np.max(np.abs(brown))
    x = lowpass(brown, 700) * env_exp(dur, 4.2)
    drop, _ = sweep(130, 38, 0.45)
    x += 0.8 * fit(drop * env_exp(0.45, 7), len(x))
    x += 0.5 * fit(lowpass(noise(0.12), 3000) * env_exp(0.12, 25), len(x))
    return x


def sfx_flame():
    dur = 0.22
    gate = (RNG.uniform(0, 1, int(SR * dur)) > 0.4).astype(float)
    crackle = lowpass(noise(dur) * (0.6 + 0.4 * gate), 1900)
    soft = lowpass(noise(dur), 280)
    return (crackle - 0.5 * soft) * (0.4 + 0.6 * env_exp(dur, 4))


def sfx_melee():
    dur = 0.18
    body, _ = sweep(500, 2600, dur, "lin")
    swish = lowpass(noise(dur), 2200) * np.sin(math.pi * t(dur) / dur) ** 2
    return swish + 0.25 * body * env_exp(dur, 14)


def sfx_melee_hit():
    dur = 0.16
    thunk, _ = sweep(220, 60, dur)
    x = thunk * env_exp(dur, 18)
    x += 0.6 * fit(lowpass(noise(0.05), 2500) * env_exp(0.05, 50), len(x))
    return x


def sfx_hurt():
    dur = 0.16
    body, phase = sweep(190, 110, dur)
    return (0.5 * body + 0.5 * square_phase(phase, 0.3)) * env_exp(dur, 16)


def sfx_death():
    dur = 0.7
    body, phase = sweep(240, 70, dur)
    x = (0.45 * body + 0.4 * square_phase(phase, 0.4)) * env_exp(dur, 5)
    x += 0.3 * lowpass(noise(dur), 500) * env_exp(dur, 6)
    return x


def sfx_pickup():
    a = sine(880, 0.09) * env_exp(0.09, 18)
    b = sine(1318.5, 0.14) * env_exp(0.14, 14)
    return np.concatenate([a, b])


def sfx_hit_confirm():
    """Q3-style shooter-side hitsound: short bright tick, full volume
    regardless of range (pain grunts attenuate; this never does)."""
    a = (0.6 * sine(1245, 0.05) + 0.4 * triangle(1245, 0.05)) * env_exp(0.05, 30)
    b = (0.6 * sine(1865, 0.07) + 0.4 * triangle(1865, 0.07)) * env_exp(0.07, 26)
    return np.concatenate([a, b])


def sfx_kill():
    a = (0.7 * sine(1567, 0.10) + 0.3 * square(1567, 0.10)) * env_exp(0.10, 22)
    b = (0.7 * sine(2093, 0.12) + 0.3 * square(2093, 0.12)) * env_exp(0.12, 20)
    return np.concatenate([a, b])


def sfx_spawn():
    dur = 0.28
    body, _ = sweep(300, 950, dur)
    return body * np.sin(math.pi * t(dur) / dur)


def sfx_lava_loop():
    dur = 4.0
    brown = np.cumsum(noise(dur))
    brown /= np.max(np.abs(brown))
    x = lowpass(brown, 220) * 0.8
    for _ in range(14):  # bubble pops
        at = RNG.uniform(0.05, dur - 0.1)
        f0 = RNG.uniform(120, 240)
        pop, _ = sweep(f0, f0 * 0.55, 0.07)
        pop *= env_exp(0.07, 30)
        i = int(at * SR)
        x[i:i + len(pop)] += 0.5 * pop
    # crossfade tail into head so it loops seamlessly
    n = int(SR * 0.25)
    fade = np.linspace(0, 1, n)
    x[:n] = x[:n] * fade + x[-n:] * (1 - fade)
    return x[: len(x) - n]


# --------------------------------------------------------------- music

NOTE = {n: 440.0 * 2 ** ((i - 9) / 12) for i, n in enumerate(
    ["C", "Cs", "D", "Ds", "E", "F", "Fs", "G", "Gs", "A", "As", "B"])}


def freq(name: str, octave: int) -> float:
    return NOTE[name] * 2 ** (octave - 4)


def music():
    """Original 8-bar banana-funk chiptune loop, 116 BPM."""
    bpm = 116
    eighth = 60 / bpm / 2
    bars = 8
    total = int(bars * 8 * eighth * SR)
    mix = np.zeros(total)

    def put(x, bar, e, gain):
        i = int((bar * 8 + e) * eighth * SR)
        x = x * gain
        end = min(i + len(x), total)
        mix[i:end] += x[: end - i]

    def pluck(f, dur, wave_fn=square, k=6.0, duty=0.5):
        if wave_fn is square:
            x = square(f, dur, duty)
        else:
            x = wave_fn(f, dur)
        return x * env_exp(dur, k)

    # F  F  Bb C | Dm Bb C  F
    prog = ["F", "F", "As", "C", "D", "As", "C", "F"]
    minor = {"D"}
    bass_oct = {"F": 2, "As": 2, "C": 2, "D": 2}

    for bar, root in enumerate(prog):
        fb = freq(root, bass_oct[root])
        fifth = fb * 1.5
        # bass groove: R . 5 R  8 . 5 b7
        for e, f, g in [(0, fb, 1.0), (2, fifth, 0.8), (3, fb, 0.9),
                        (4, fb * 2, 0.9), (6, fifth, 0.8), (7, fb * 16 / 9, 0.7)]:
            put(pluck(f, eighth * 0.9, triangle, 3.5), bar, e, 0.9 * g)
        # offbeat chord stabs
        third = fb * (2 ** (3 / 12) if root in minor else 2 ** (4 / 12))
        for e in (1, 5):
            for cf in (fb * 2, third * 2, fifth * 2):
                put(pluck(cf, eighth * 0.55, square, 14.0, 0.3), bar, e, 0.16)
        # drums
        for e in (0, 4):
            kick, _ = sweep(110, 40, 0.12)
            put(kick * env_exp(0.12, 22), bar, e, 1.0)
        if bar % 2 == 1:
            kick, _ = sweep(110, 40, 0.10)
            put(kick * env_exp(0.10, 25), bar, 7, 0.7)
        for e in (2, 6):
            sn = lowpass(noise(0.12), 3500) * env_exp(0.12, 18)
            put(sn, bar, e, 0.5)
        for e in range(8):
            ht = lowpass(noise(0.04), 8000) * env_exp(0.04, 50)
            put(ht, bar, e, 0.22 if e % 2 else 0.12)

    # lead riff over 2-bar phrases (F major pentatonic, bouncy)
    riff = [
        (0, 0, "A", 4, 1), (0, 1, "C", 5, 1), (0, 2, "D", 5, 1), (0, 4, "F", 5, 2),
        (0, 7, "D", 5, 1),
        (1, 0, "C", 5, 1), (1, 2, "A", 4, 1), (1, 4, "G", 4, 2), (1, 6, "A", 4, 2),
    ]
    for phrase in range(4):
        b0 = phrase * 2
        for dbar, e, n, o, ln in riff:
            f = freq(n, o)
            if phrase == 3 and dbar == 1:  # turnaround: lift the last bar
                f *= 2 ** (2 / 12)
            x = pluck(f, eighth * ln * 0.92, square, 4.5, 0.4)
            x += 0.4 * pluck(f * 2.0, eighth * ln * 0.92, square, 7.0, 0.4)
            put(x, b0 + dbar, e, 0.30)

    return np.tanh(mix * 1.2)


# ------------------------------------------------------------ announcer

LINES = [
    ("first_blood", "First Blood!"),
    ("double_kill", "Double Kill!"),
    ("triple_kill", "Triple Kill!"),
    ("overkill", "Overkill!"),
    ("killing_spree", "Killing Spree!"),
    ("rampage", "Rampage!"),
    ("slayer", "Slayer."),
]


def announcer():
    """Windows TTS via WSL interop, then pitch-down + slap echo so it
    reads as an arena announcer instead of a desktop assistant."""
    tmp = Path("/mnt/c/Windows/Temp/minions_tts")
    if not Path("/mnt/c/Windows").exists():
        print("  (no Windows interop — skipping announcer lines)")
        return
    tmp.mkdir(exist_ok=True)
    ps = ["Add-Type -AssemblyName System.Speech",
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer",
          "$s.SelectVoiceByHints('Male')", "$s.Rate = -1"]
    for name, text in LINES:
        ps.append(f"$s.SetOutputToWaveFile('C:\\Windows\\Temp\\minions_tts\\{name}.wav')")
        ps.append(f"$s.Speak('{text}')")
    ps.append("$s.Dispose()")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", "; ".join(ps)],
                   check=True, capture_output=True, timeout=120)

    for name, _ in LINES:
        with wave.open(str(tmp / f"{name}.wav"), "rb") as w:
            sr_in = w.getframerate()
            raw = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            if w.getnchannels() == 2:
                raw = raw.reshape(-1, 2).mean(axis=1)
        x = raw.astype(np.float64) / 32768.0
        # resample to SR with a 0.93 pitch drop folded in
        ratio = (SR / sr_in) / 0.93
        x = np.interp(np.arange(int(len(x) * ratio)) / ratio,
                      np.arange(len(x)), x)
        # slap echo for arena feel
        d = int(SR * 0.10)
        y = np.pad(x, (0, 2 * d))
        y[d:d + len(x)] += 0.40 * x
        y[2 * d:2 * d + len(x)] += 0.18 * x
        write_wav(name, y, peak=0.92)


def main():
    print(f"-> {OUT}")
    write_wav("rail", sfx_rail())
    write_wav("shotgun", sfx_shotgun())
    write_wav("rocket", sfx_rocket())
    write_wav("explosion", sfx_explosion())
    write_wav("flame", sfx_flame(), peak=0.5)
    write_wav("melee", sfx_melee())
    write_wav("melee_hit", sfx_melee_hit())
    write_wav("hurt", sfx_hurt())
    write_wav("death", sfx_death())
    write_wav("pickup", sfx_pickup())
    write_wav("kill", sfx_kill())
    write_wav("hit_confirm", sfx_hit_confirm())
    write_wav("spawn", sfx_spawn())
    write_wav("lava", sfx_lava_loop())
    # the client loads music.ogg (fetch_real.py ships the real track);
    # encode the synth fallback to the same name
    write_wav("music_tmp", music(), peak=0.7)
    subprocess.run(["ffmpeg", "-y", "-v", "quiet", "-i", str(OUT / "music_tmp.wav"),
                    "-q:a", "4", str(OUT / "music.ogg")], check=True)
    (OUT / "music_tmp.wav").unlink()
    print("  music.ogg (synth fallback)")
    announcer()


if __name__ == "__main__":
    main()
