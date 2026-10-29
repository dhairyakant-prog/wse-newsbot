"""turn a story into a 7-second vertical reel (1080x1920) in the jist style:
slow push-in on the face photo, headline boxes pop in line by line, logo top-left, music bed.
reels are what facebook recommends to people who don't follow the page yet."""
from __future__ import annotations

import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

from .cinema import F, _balanced, _face_box, _title_case, bug
from .common import ROOT, log

RW, RH, FPS, SECS = 1080, 1920, 30, 7
SAFE_BOTTOM = 430            # facebook's caption + buttons cover the bottom of a reel
MUSIC = ROOT / "assets" / "music"


def _base(hero: dict | None) -> Image.Image:
    """photo at 1.12x the frame so the push-in has room; face at ~34% height."""
    big_w, big_h = int(RW * 1.12), int(RH * 1.12)
    if not hero:
        return Image.new("RGB", (big_w, big_h), (28, 28, 34))
    img = (hero.get("original") or hero["image"]).convert("RGB")
    fb = _face_box(img, hero["image"] if hero.get("kind") == "cutout" else None)
    cover = max(big_w / img.width, big_h / img.height)
    if fb:
        sc = max(cover, min(380 / fb[3], 2.6))
        below = img.height - (fb[1] + fb[3] / 2)
        sc = min(max(sc, big_h * 0.62 / max(1, below)), 2.8)
        fcx, fcy = (fb[0] + fb[2] / 2) * sc, (fb[1] + fb[3] / 2) * sc
        x0 = int(min(max(fcx - big_w / 2, 0), img.width * sc - big_w))
        y0 = int(min(max(fcy - big_h * 0.34, 0), img.height * sc - big_h))
    else:
        sc = cover
        x0, y0 = int((img.width * sc - big_w) / 2), int((img.height * sc - big_h) * 0.3)
    big = img.resize((max(big_w, int(img.width * sc)), max(big_h, int(img.height * sc))), Image.LANCZOS)
    return ImageEnhance.Contrast(big.crop((x0, y0, x0 + big_w, y0 + big_h))).enhance(1.05)


def _overlay(copy: dict):
    """static layers: gradients + logo, and the headline boxes as separate line images."""
    red, white, black = (226, 27, 35), (255, 255, 255), (10, 10, 12)
    words = _title_case(copy["headline"].strip().strip("\"'‘’“” ")).split()
    for size in range(88, 50, -2):
        f = F("pop_xb", size)
        lines = _balanced(words, f, RW - 130)
        if len(lines) <= 4:
            break
    lh = int(f.size * 1.24)
    y_top = RH - SAFE_BOTTOM - len(lines) * lh

    hl = int(copy.get("highlight_words") or 0)
    red_i, seen = 1, 0
    for i, ln in enumerate(lines):
        seen += len(ln)
        if hl and seen >= hl:
            red_i = i
            break
    red_i = min(max(red_i, 1), len(lines) - 2) if len(lines) >= 3 else min(red_i, len(lines) - 1)

    shade = Image.new("RGBA", (RW, RH), (0, 0, 0, 0))
    d = ImageDraw.Draw(shade)
    g0 = max(int(RH * 0.40), y_top - 320)
    for k in range(g0, RH):
        t = min(1.0, (k - g0) / max(1, (y_top + 60) - g0))
        d.line([(0, k), (RW, k)], fill=(0, 0, 0, int(235 * t * t * (3 - 2 * t))))
    for k in range(260):
        d.line([(0, k), (RW, k)], fill=(0, 0, 0, int(120 * (1 - k / 260) ** 2)))
    lg = bug(64)
    shade.alpha_composite(lg, (54, 120))

    boxes = []
    pad = int(f.size * 0.2)
    y = y_top
    for i, ln in enumerate(lines):
        t = " ".join(ln)
        tw = int(f.getlength(t))
        im = Image.new("RGBA", (tw + 2 * pad, lh - 6), (0, 0, 0, 0))
        dd = ImageDraw.Draw(im)
        dd.rectangle([0, 0, im.width, im.height], fill=red if i == red_i else white)
        dd.text((im.width / 2, im.height / 2 + 1), t, font=f, fill=white if i == red_i else black, anchor="mm")
        boxes.append((im, ((RW - im.width) // 2, y + 2)))
        y += lh
    return shade, boxes


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def render_reel(copy: dict, hero: dict | None, out: Path) -> Path:
    base = _base(hero)
    shade, boxes = _overlay(copy)
    n = FPS * SECS
    music = sorted(MUSIC.glob("*.m4a"))
    track = random.choice(music) if music else None
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{RW}x{RH}", "-r", str(FPS), "-i", "-"]
    if track:
        cmd += ["-i", str(track), "-filter_complex", "[1:a]volume=0.55,afade=t=out:st=6.2:d=0.8[a]",
                "-map", "0:v", "-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-t", str(SECS), str(out)]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    bw, bh = base.size
    for i in range(n):
        t = i / n
        z = 1.0 + 0.10 * t                                  # slow push-in
        cw, ch = int(bw / z), int(bh / z)
        x0, y0 = (bw - cw) // 2, int((bh - ch) * 0.4)
        fr = base.crop((x0, y0, x0 + cw, y0 + ch)).resize((RW, RH), Image.BILINEAR).convert("RGBA")
        fr.alpha_composite(shade)
        sec = i / FPS
        for k, (im, (x, y)) in enumerate(boxes):
            a = _ease((sec - 0.35 - 0.28 * k) / 0.35)
            if a <= 0:
                continue
            dy = int((1 - a) * 40)
            if a < 1:
                im2 = im.copy()
                im2.putalpha(im.split()[-1].point(lambda v, a=a: int(v * a)))
            else:
                im2 = im
            fr.alpha_composite(im2, (x, y + dy))
        p.stdin.write(fr.convert("RGB").tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError("ffmpeg failed rendering the reel")
    log.info("reel → %s (%s)", out, track.name if track else "no music")
    return out
