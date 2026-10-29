"""synthesise a few original, royalty-free background beds for the reels (run once; output committed).
minimal news-style loop: soft kick, closed hat, plucked minor chords, sub bass. 120 bpm, 16 s."""
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "music"


def env(n, a=0.005, d=0.25):
    t = np.arange(n) / SR
    return np.minimum(t / a, 1) * np.exp(-t / d)


def tone(f, n, kind="saw"):
    t = np.arange(n) / SR
    if kind == "sine":
        return np.sin(2 * np.pi * f * t)
    # soft saw: few harmonics
    return sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 6))


def kick(n):
    t = np.arange(n) / SR
    f = 110 * np.exp(-t * 28) + 42
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def hat(n, rng):
    return rng.standard_normal(n) * np.exp(-np.arange(n) / SR * 60) * 0.25


def note(m):  # midi → hz
    return 440 * 2 ** ((m - 69) / 12)


def bed(prog, root, seed, bpm=120, bars=8):
    rng = np.random.default_rng(seed)
    beat = int(SR * 60 / bpm)
    total = beat * 4 * bars
    mix = np.zeros(total)
    for b in range(4 * bars):
        s = b * beat
        mix[s:s + beat] += kick(beat) * 0.9
        h = s + beat // 2
        mix[h:h + beat // 2] += hat(beat // 2, rng)
    for bar in range(bars):
        chord = prog[bar % len(prog)]
        for step in range(8):                       # 8th-note plucks
            s = bar * 4 * beat + step * beat // 2
            n = beat // 2
            m = root + chord[step % len(chord)] + (12 if step % 4 == 3 else 0)
            mix[s:s + n] += tone(note(m), n) * env(n, d=0.12) * 0.16
        s = bar * 4 * beat                          # sub bass, whole bar
        n = 4 * beat
        mix[s:s + n] += tone(note(root + chord[0] - 24), n, "sine") * env(n, a=0.02, d=1.6) * 0.5
    mix /= np.max(np.abs(mix)) + 1e-9
    fade = int(SR * 0.4)
    mix[:fade] *= np.linspace(0, 1, fade)
    mix[-fade:] *= np.linspace(1, 0, fade)
    return (mix * 0.7 * 32767).astype(np.int16)


BEDS = {
    "pulse": ([[0, 3, 7], [-4, 0, 3], [-7, -3, 0], [-2, 2, 5]], 57, 1),     # A minor-ish
    "drive": ([[0, 3, 7], [5, 8, 12], [3, 7, 10], [-2, 2, 5]], 52, 2),      # E
    "light": ([[0, 4, 7], [-3, 0, 4], [5, 9, 12], [-5, -1, 2]], 60, 3),     # C major-ish
}

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (prog, root, seed) in BEDS.items():
        pcm = bed(prog, root, seed)
        wav = OUT / f"{name}.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "128k",
                        str(OUT / f"{name}.m4a")], check=True)
        wav.unlink()
        print("wrote", name)
