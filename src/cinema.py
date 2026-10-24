"""cinematic layouts: full-bleed image, text set over it.

  cinema  — bottom-left serif headline, highlight words in pastel italic, film grade + grain
  poster  — big condensed uppercase headline, centred, highlight words in pastel
  glass   — frosted pastel lower-third panel over the image, serif headline with pastel bar
"""
from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .common import CFG, ROOT

W, H = 1080, 1350
FONTS = ROOT / "fonts"
FACES = {"pop_xb": "Poppins-ExtraBold.ttf", "kalam": "Kalam-Bold.ttf",
         "inst": "InstrumentSerif-Regular.ttf", "inst_i": "InstrumentSerif-Italic.ttf",
         "anton": "Anton-Regular.ttf", "dmserif": "DMSerifDisplay-Regular.ttf",
         "body": "Inter-Regular.otf", "semi": "Inter-SemiBold.otf", "black": "Inter-Black.otf"}


def F(name: str, size: int):
    return ImageFont.truetype(str(FONTS / FACES[name]), size)


def _hex(c, a=255):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def _pal(cat):
    p = CFG["palettes"].get(cat) or CFG["palettes"]["default"]
    return {"page": _hex(p[0]), "wash": _hex(p[1]), "bar": _hex(p[2]), "ink": _hex(p[3])}


# ── image treatment ───────────────────────────────────────────
def _grade(img: Image.Image, tint, cy: float = 0.45) -> Image.Image:
    img = ImageOps.fit(img.convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, cy))
    img = ImageEnhance.Contrast(img).enhance(1.08)
    img = ImageEnhance.Color(img).enhance(0.9)
    img = Image.blend(img, Image.new("RGB", img.size, tint[:3]), 0.07)
    # vignette
    v = Image.new("L", (W, H), 0)
    ImageDraw.Draw(v).ellipse([-W * 0.25, -H * 0.15, W * 1.25, H * 1.15], fill=255)
    v = v.filter(ImageFilter.GaussianBlur(160))
    img = Image.composite(img, ImageEnhance.Brightness(img).enhance(0.72), v)
    # fine grain
    rnd = random.Random(7)
    noise = Image.frombytes("L", (W // 2, H // 2), bytes(rnd.randint(110, 146) for _ in range(W * H // 4)))
    noise = noise.resize((W, H)).convert("RGB")
    img = ImageChops.overlay(img, noise)
    return img.convert("RGBA")


def _shade(card, start: float, strength: int = 235, color=(8, 8, 12), top: bool = True):
    """bottom gradient for legibility (+ a light one at the top for the logo)."""
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(g)
    y0 = int(H * start)
    for y in range(y0, H):
        t = min(1.0, ((y - y0) / (H - y0)) ** 0.8 * 1.15)
        d.line([(0, y), (W, y)], fill=color + (int(strength * t),))
    if top:
        for y in range(0, 220):
            d.line([(0, y), (W, y)], fill=color + (int(120 * (1 - y / 220) ** 1.6),))
    card.alpha_composite(g)


# ── logo (bug), light variant for dark images ─────────────────
def bug(h: int, news_color=(255, 255, 255), box=(22, 22, 30), letters=(255, 255, 255)) -> Image.Image:
    ink = box
    f = F("black", int(h * 0.78))
    tw = f.getlength("WSE")
    pad = int(h * 0.28)
    nf = F("black", int(h * 0.5))
    w = int(tw + 2 * pad + h * 0.32 + nf.getlength("NEWS") + 4)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, tw + 2 * pad, h], fill=ink)
    d.text((pad + tw / 2, h / 2 + h * 0.02), "WSE", font=f, fill=letters, anchor="mm")
    x = tw + 2 * pad
    cols = [(170, 200, 240), (200, 186, 242), (248, 190, 150)]
    for i, c in enumerate(cols):
        d.rectangle([x, i * h / 3, x + int(h * 0.12), (i + 1) * h / 3], fill=c)
    d.text((x + int(h * 0.32), h / 2 + h * 0.02), "NEWS", font=nf, fill=news_color, anchor="lm")
    return im


# ── text helpers ──────────────────────────────────────────────
def _wrap(words, font, width):
    lines, cur = [], []
    for w in words:
        if cur and font.getlength(" ".join(cur + [w])) > width:
            lines.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(cur)
    return lines


def _balanced(words, font, width):
    lines = _wrap(words, font, width)
    n, w = len(lines), width
    while n > 1 and w > 300:
        t = _wrap(words, font, w - 10)
        if len(t) != n:
            break
        lines, w = t, w - 10
    return lines


def _fit(text, face, width, max_lines, sizes, upper=False):
    words = (text.upper() if upper else text).split()
    for s in sizes:
        f = F(face, s)
        if len(_wrap(words, f, width)) <= max_lines:
            return f, _balanced(words, f, width)
    f = F(face, sizes[-1])
    lines = _wrap(words, f, width)[:max_lines]
    lines[-1][-1] = lines[-1][-1].rstrip(".,;:") + "…"
    return f, lines


def _label(cat):
    return {"us": "UNITED STATES", "europe": "EUROPE", "middle_east": "MIDDLE EAST"}.get(cat, cat.upper())


def _circle(img, d, ring=7):
    img = ImageOps.fit(img.convert("RGB"), (d, d), Image.LANCZOS)
    m = Image.new("L", (d, d), 0)
    ImageDraw.Draw(m).ellipse([0, 0, d - 1, d - 1], fill=255)
    out = Image.new("RGBA", (d + 2 * ring, d + 2 * ring), (0, 0, 0, 0))
    ImageDraw.Draw(out).ellipse([0, 0, d + 2 * ring - 1, d + 2 * ring - 1], fill=(255, 255, 255, 255))
    out.paste(img, (ring, ring), m)
    sh = Image.new("RGBA", (out.width + 80, out.height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([40, 52, out.width + 40, out.height + 52], fill=(0, 0, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(16))
    sh.alpha_composite(out, (40, 40))
    return sh


def _fit_sentences(text, font, width, lines_max):
    """drop trailing sentences until the text fits; never cut mid-sentence if avoidable."""
    import re as _re
    sents = _re.split(r"(?<=[.!?])\s+", text.strip())
    while len(sents) > 1 and len(_wrap(" ".join(sents).split(), font, width)) > lines_max:
        sents.pop()
    return " ".join(sents)


def _explainer(d, text, x, y, width, color, align="left", size=28, lines_max=4):
    ef = F("body", size)
    text = _fit_sentences(text, ef, width, lines_max)
    lines = _balanced(text.split(), ef, width)
    if len(lines) > lines_max:
        lines = lines[:lines_max]
        lines[-1][-1] = lines[-1][-1].rstrip(".,;:") + "…"
    lh = int(size * 1.48)
    for ln in lines:
        t = " ".join(ln)
        if align == "center":
            d.text((W / 2, y), t, font=ef, fill=color, anchor="ma")
        else:
            d.text((x, y), t, font=ef, fill=color)
        y += lh
    return y


def _measure_explainer(text, width, size=28, lines_max=4):
    if not text:
        return 0
    text = _fit_sentences(text, F("body", size), width, lines_max)
    n = min(lines_max, len(_balanced(text.split(), F("body", size), width)))
    return n * int(size * 1.48)


def _words_with_highlight(lines, hl):
    """yield (line_words, [bool per word]) marking the first `hl` words."""
    i = 0
    for ln in lines:
        yield ln, [(i + k) < hl for k in range(len(ln))]
        i += len(ln)


def _draw_mixed(d, x, y, words, flags, f_norm, f_hl, c_norm, c_hl, anchor_center=False):
    space = f_norm.getlength(" ")
    widths = [(f_hl if fl else f_norm).getlength(w) for w, fl in zip(words, flags)]
    total = sum(widths) + space * (len(words) - 1)
    cx = (W - total) / 2 if anchor_center else x
    for w, fl, wd in zip(words, flags, widths):
        d.text((cx, y), w, font=f_hl if fl else f_norm, fill=c_hl if fl else c_norm, anchor="ls")
        cx += wd + space


def _handle(d, x, y, color, anchor="rs"):
    d.text((x, y), CFG["page"]["handle"], font=F("semi", 22), fill=color, anchor=anchor)


# ── layout: cinema ────────────────────────────────────────────
def render_cinema_bottom(copy, cat, hero, inset, out: Path):
    p = _pal(cat)
    card = _grade(hero["image"], p["wash"]) if hero else Image.new("RGBA", (W, H), (20, 20, 26))
    _shade(card, 0.26, 245)
    d = ImageDraw.Draw(card)
    M = 72
    lg = bug(54)
    card.alpha_composite(lg, (M, 60))

    ex_h = _measure_explainer(copy.get("explainer"), W - 2 * M)
    fn, lines = _fit(copy["headline"], "inst", W - 2 * M, 3, range(96, 58, -2))
    fi = F("inst_i", fn.size)
    lh = int(fn.size * 1.02)
    attr_h = 44 if copy.get("attribution") else 0
    block = 40 + len(lines) * lh + attr_h + 26 + ex_h
    y = H - 96 - block

    # kicker
    kf = F("semi", 22)
    d.rectangle([M, y + 6, M + 10, y + 16], fill=p["bar"])
    d.text((M + 22, y + 11), _label(cat), font=kf, fill=p["bar"], anchor="lm")
    y += 40
    hl = int(copy.get("highlight_words") or 0)
    for ln, flags in _words_with_highlight(lines, hl):
        y += lh
        _draw_mixed(d, M, y - int(lh * 0.18), ln, flags, fn, fi, (255, 255, 255), p["bar"])
    if copy.get("attribution"):
        d.text((M, y + 14), f"— {copy['attribution']}", font=F("body", 24), fill=(255, 255, 255, 190))
        y += attr_h
    y += 22
    d.line([M, y, M + 90, y], fill=p["bar"], width=3)
    y += 22
    if copy.get("explainer"):
        _explainer(d, copy["explainer"], M, y, W - 2 * M, (255, 255, 255, 215))
    _handle(d, W - M, H - 52, (255, 255, 255, 170))

    if inset is not None:
        c = _circle(inset, 210)
        card.alpha_composite(c, (W - M - c.width + 40, 30))
    card.convert("RGB").save(out, "JPEG", quality=93, optimize=True)
    return out


# ── layout: poster ────────────────────────────────────────────
def render_poster(copy, cat, hero, inset, out: Path):
    p = _pal(cat)
    card = _grade(hero["image"], p["wash"]) if hero else Image.new("RGBA", (W, H), (20, 20, 26))
    _shade(card, 0.2, 248)
    d = ImageDraw.Draw(card)
    lg = bug(50)
    card.alpha_composite(lg, ((W - lg.width) // 2, 58))

    M = 64
    ex_h = _measure_explainer(copy.get("explainer"), W - 2 * 110, 27)
    fn, lines = _fit(copy["headline"], "anton", W - 2 * M, 4, range(104, 60, -2), upper=True)
    lh = int(fn.size * 1.06)
    attr_h = 40 if copy.get("attribution") else 0
    block = 46 + len(lines) * lh + attr_h + 28 + ex_h
    y = H - 92 - block

    kf = F("semi", 22)
    lab = _label(cat)
    tw = kf.getlength(lab) + 40
    d.rounded_rectangle([(W - tw) / 2, y, (W + tw) / 2, y + 38], 19, fill=p["bar"])
    d.text((W / 2, y + 19), lab, font=kf, fill=p["ink"], anchor="mm")
    y += 46
    hl = int(copy.get("highlight_words") or 0)
    for ln, flags in _words_with_highlight(lines, hl):
        y += lh
        _draw_mixed(d, 0, y - int(lh * 0.1), ln, flags, fn, fn, (255, 255, 255), p["bar"], anchor_center=True)
    if copy.get("attribution"):
        d.text((W / 2, y + 12), f"— {copy['attribution']}", font=F("body", 24), fill=(255, 255, 255, 190), anchor="ma")
        y += attr_h
    y += 28
    if copy.get("explainer"):
        _explainer(d, copy["explainer"], 0, y, W - 2 * 110, (255, 255, 255, 210), "center", 27)
    _handle(d, W / 2, H - 46, (255, 255, 255, 150), "ms")

    if inset is not None:
        c = _circle(inset, 190)
        card.alpha_composite(c, (W - c.width - 10, 150))
    card.convert("RGB").save(out, "JPEG", quality=93, optimize=True)
    return out


# ── layout: glass ─────────────────────────────────────────────
def render_glass(copy, cat, hero, inset, out: Path):
    p = _pal(cat)
    card = _grade(hero["image"], p["wash"]) if hero else Image.new("RGBA", (W, H), p["wash"])
    _shade(card, 0.55, 90, top=True)
    M, PAD = 40, 48
    inner = W - 2 * M - 2 * PAD

    ex_h = _measure_explainer(copy.get("explainer"), inner, 27)
    fn, lines = _fit(copy["headline"], "dmserif", inner, 3, range(66, 44, -2))
    lh = int(fn.size * 1.16)
    attr_h = 38 if copy.get("attribution") else 0
    ph = PAD + 40 + len(lines) * (lh + 6) + attr_h + 20 + ex_h + PAD
    top = H - M - ph

    # frosted panel: blur what's behind, wash it pastel
    box = (M, top, W - M, H - M)
    region = card.crop(box).filter(ImageFilter.GaussianBlur(26))
    region = Image.alpha_composite(region, Image.new("RGBA", region.size, p["page"][:3] + (205,)))
    m = Image.new("L", region.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, region.width - 1, region.height - 1], 34, fill=255)
    sh = Image.new("RGBA", (region.width + 80, region.height + 80), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([40, 52, region.width + 40, region.height + 52], 34, fill=(0, 0, 0, 70))
    card.alpha_composite(sh.filter(ImageFilter.GaussianBlur(20)), (M - 40, top - 40))
    card.paste(region, box[:2], m)

    d = ImageDraw.Draw(card)
    x, y = M + PAD, top + PAD
    kf = F("semi", 21)
    lab = _label(cat)
    tw = kf.getlength(lab) + 32
    d.rounded_rectangle([x, y, x + tw, y + 34], 17, fill=p["bar"])
    d.text((x + 16, y + 17), lab, font=kf, fill=p["ink"], anchor="lm")
    y += 50
    hl = int(copy.get("highlight_words") or 0)
    used = 0
    for ln in lines:
        k = max(0, min(len(ln), hl - used))
        if k:
            seg = " ".join(ln[:k])
            d.rectangle([x - 8, y + 4, x + fn.getlength(seg) + 8, y + lh + 2], fill=p["bar"])
        d.text((x, y + lh / 2 + 3), " ".join(ln), font=fn, fill=p["ink"], anchor="lm")
        used += len(ln)
        y += lh + 6
    if copy.get("attribution"):
        d.text((x, y + 4), f"— {copy['attribution']}", font=F("body", 23), fill=p["ink"][:3] + (180,))
        y += attr_h
    y += 16
    if copy.get("explainer"):
        _explainer(d, copy["explainer"], x, y, inner, p["ink"][:3] + (215,), "left", 27)

    lg = bug(50, news_color=(255, 255, 255))
    card.alpha_composite(lg, (M + 12, 52))
    _handle(d, W - M - 12, 86, (255, 255, 255, 220))
    if inset is not None:
        c = _circle(inset, 200)
        card.alpha_composite(c, (W - M - c.width + 30, top - c.height + 70))
    card.convert("RGB").save(out, "JPEG", quality=93, optimize=True)
    return out


# ── person photo: face always below the text ──────────────────
def _person_stage(img: Image.Image, p: dict, text_end: int) -> Image.Image:
    """dark tinted backdrop + the person bottom-aligned so the head starts below the text block."""
    tint = p["bar"][:3]
    bg = Image.new("RGB", (W, H), (14, 14, 20))
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse([W * 0.1, H * 0.45, W * 0.9, H * 1.15], fill=110)
    glow = glow.filter(ImageFilter.GaussianBlur(140))
    bg = Image.composite(Image.new("RGB", (W, H), tuple(int(c * 0.55) for c in tint)), bg, glow)
    card = bg.convert("RGBA")
    im = img.convert("RGBA")
    if im.getbbox():
        im = im.crop(im.getbbox())
    top = max(text_end + 10, int(H * 0.40))                # head starts here at the latest
    avail_h = H - top
    scale = min(avail_h / im.height, (W * 0.98) / im.width)
    im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
    rgb = ImageEnhance.Contrast(ImageEnhance.Color(im.convert("RGB")).enhance(0.92)).enhance(1.05)
    rgb = rgb.convert("RGBA")
    alpha = im.split()[-1]
    if alpha.getextrema()[0] > 200:                         # no cut-out: feather the rectangle edges
        f = Image.new("L", im.size, 255)
        fd = ImageDraw.Draw(f)
        e = max(20, im.width // 7)
        for k in range(e):
            v = int(255 * (k / e) ** 1.6)
            fd.rectangle([k, k, im.width - 1 - k, im.height + e], outline=v)
        alpha = ImageChops.multiply(alpha, f.filter(ImageFilter.GaussianBlur(6)))
    rgb.putalpha(alpha)
    card.alpha_composite(rgb, ((W - rgb.width) // 2, H - rgb.height))
    return card


# ── layout: cinema (text on top) ──────────────────────────────
def render_cinema(copy, cat, hero, inset, out: Path):
    p = _pal(cat)
    M = 72
    ex_h = _measure_explainer(copy.get("explainer"), W - 2 * M)
    fn, lines = _fit(copy["headline"], "inst", W - 2 * M, 4, range(96, 52, -2))
    lh0 = int(fn.size * 1.02)
    attr_h0 = 44 if copy.get("attribution") else 0
    text_end = 150 + 40 + len(lines) * lh0 + attr_h0 + 44 + ex_h
    if hero and hero["kind"] == "cutout":
        card = _person_stage(hero["image"], p, text_end)
    else:
        card = _grade(hero["image"], p["wash"], 0.7) if hero else Image.new("RGBA", (W, H), (20, 20, 26))
    fi = F("inst_i", fn.size)
    lh = int(fn.size * 1.02)
    attr_h = 44 if copy.get("attribution") else 0
    block_end = 150 + 40 + len(lines) * lh + attr_h + 44 + ex_h

    # dark from the top, fading out below the text block
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(g)
    solid, fade_to = block_end - 40, min(H - 200, block_end + 300)
    for y in range(0, fade_to):
        a = 222 if y < solid else int(222 * (1 - (y - solid) / (fade_to - solid)) ** 1.5)
        gd.line([(0, y), (W, y)], fill=(8, 8, 12, a))
    for y in range(H - 160, H):                       # light floor for the handle
        gd.line([(0, y), (W, y)], fill=(8, 8, 12, int(120 * ((y - H + 160) / 160) ** 1.4)))
    card.alpha_composite(g)
    d = ImageDraw.Draw(card)

    card.alpha_composite(bug(54, box=(255, 255, 255), letters=(22, 22, 30)), (M, 60))
    y = 150
    kf = F("semi", 22)
    d.rectangle([M, y + 6, M + 10, y + 16], fill=p["bar"])
    d.text((M + 22, y + 11), _label(cat), font=kf, fill=p["bar"], anchor="lm")
    y += 40
    hl = int(copy.get("highlight_words") or 0)
    for ln, flags in _words_with_highlight(lines, hl):
        y += lh
        _draw_mixed(d, M, y - int(lh * 0.18), ln, flags, fn, fi, (255, 255, 255), p["bar"])
    if copy.get("attribution"):
        d.text((M, y + 14), f"— {copy['attribution']}", font=F("body", 24), fill=(255, 255, 255, 190))
        y += attr_h
    y += 22
    d.line([M, y, M + 90, y], fill=p["bar"], width=3)
    y += 22
    if copy.get("explainer"):
        _explainer(d, copy["explainer"], M, y, W - 2 * M, (255, 255, 255, 215))
    _handle(d, W - M, H - 52, (255, 255, 255, 190))
    if inset is not None:
        c = _circle(inset, 210)
        card.alpha_composite(c, (W - M - c.width + 40, H - c.height - 60))
    card.convert("RGB").save(out, "JPEG", quality=93, optimize=True)
    return out


# ── layout: khabar (khabargaon-style) ─────────────────────────
#   light airy top, logo centred, short bold headline (often a quote),
#   handwritten "– name / role", big real photo of the person filling the bottom.
def _soft_bg(img: Image.Image, light=(240, 242, 246)) -> Image.Image:
    bg = ImageOps.fit(img.convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, 0.35))
    bg = bg.filter(ImageFilter.GaussianBlur(22))
    return Image.blend(bg, Image.new("RGB", (W, H), light), 0.45).convert("RGBA")


def _top_light(card: Image.Image, until: int, light=(244, 245, 248)):
    g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(g)
    solid = int(until * 0.72)
    for y in range(until):
        a = 238 if y < solid else int(238 * (1 - (y - solid) / (until - solid)) ** 1.4)
        d.line([(0, y), (W, y)], fill=light + (a,))
    card.alpha_composite(g)


def render_khabar(copy, cat, hero, inset, out: Path):
    ink = (17, 17, 22)
    light = (244, 245, 248)
    M = 70
    quote = bool(copy.get("attribution"))
    text = copy["headline"].strip()
    if quote:
        text = "‘" + text.strip("\"'‘’“” ") + "’"
    fn, lines = _fit(text, "pop_xb", W - 2 * M, 3, range(62, 40, -2))
    lh = int(fn.size * 1.22)
    head_top = 200
    attr_h = 110 if quote else 0
    text_end = head_top + len(lines) * lh + attr_h + 20

    # background + photo
    if hero and hero["kind"] in ("cutout", "photo"):
        orig = hero.get("original") or hero["image"]
        card = _soft_bg(orig, light)
        _top_light(card, text_end + 160, light)
        person = hero["image"].convert("RGBA")
        if hero["kind"] == "photo":                     # no cut-out: fade the photo's top and sides
            f = Image.new("L", person.size, 255)
            fd = ImageDraw.Draw(f)
            ew, eh = max(30, person.width // 6), max(60, person.height // 4)
            for k in range(eh):
                fd.line([(0, k), (person.width, k)], fill=int(255 * (k / eh) ** 1.5))
            fx = Image.new("L", person.size, 255)
            fxd = ImageDraw.Draw(fx)
            for k in range(ew):
                v = int(255 * (k / ew) ** 1.5)
                fxd.line([(k, 0), (k, person.height)], fill=v)
                fxd.line([(person.width - 1 - k, 0), (person.width - 1 - k, person.height)], fill=v)
            person.putalpha(ImageChops.multiply(f, fx))
        if person.getbbox():
            person = person.crop(person.getbbox())
        top = max(text_end - 30, int(H * 0.36))
        scale = min((H - top) / person.height, (W * 1.05) / person.width)
        person = person.resize((max(1, int(person.width * scale)), max(1, int(person.height * scale))), Image.LANCZOS)
        card.alpha_composite(person, ((W - person.width) // 2, H - person.height))
    elif hero:                                          # ai scene: full bleed, light top
        card = ImageOps.fit(hero["image"].convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, 0.6)).convert("RGBA")
        _top_light(card, text_end + 220, light)
    else:
        card = Image.new("RGBA", (W, H), light + (255,))

    d = ImageDraw.Draw(card)
    lg = bug(60, news_color=(17, 17, 22))
    card.alpha_composite(lg, ((W - lg.width) // 2, 64))

    y = head_top
    for ln in lines:
        d.text((W / 2, y + lh / 2), " ".join(ln), font=fn, fill=ink, anchor="mm")
        y += lh
    if quote:
        name, _, role = copy["attribution"].partition(",")
        af = F("kalam", 38)
        d.text((W - M, y + 20), f"– {name.strip()}", font=af, fill=ink, anchor="ra")
        if role.strip():
            d.text((W - M, y + 64), role.strip(), font=af, fill=ink, anchor="ra")
    card.convert("RGB").save(out, "JPEG", quality=93, optimize=True)
    return out
