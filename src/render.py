"""pastel news card, 1080×1350 (4:5).

layout (modelled on the reference set):
  wordmark, centred
  serif headline, centred — opening lines sit on pastel highlight bars
  optional "— attribution" for quotes, right-aligned italic
  hero: a cut-out subject on a pastel gradient, or a full-bleed scene fading up into the page
  inset circle with a context image, white ring
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .common import CFG, ROOT

W, H = 1080, 1350
FONTS = ROOT / "fonts"
ASSETS = ROOT / "assets"


# ── fonts / colour helpers ────────────────────────────────────
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = {"serif": "YoungSerif-Regular.ttf", "sans": "Inter-Medium.otf",
            "sans_semi": "Inter-SemiBold.otf", "italic": "Inter-Italic.otf",
            "body": "Inter-Regular.otf"}[name]
    return ImageFont.truetype(str(FONTS / path), size)


def _hex(c: str, a: int = 255) -> tuple:
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (a,)


def _mix(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)


def palette_for(category: str) -> dict:
    p = CFG["palettes"].get(category) or CFG["palettes"]["default"]
    return {"page": _hex(p[0]), "wash": _hex(p[1]), "bar": _hex(p[2]), "ink": _hex(p[3])}


# ── text layout ───────────────────────────────────────────────
def _wrap(words: list[str], font, width: int) -> list[list[str]]:
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


def _balanced(words: list[str], font, width: int) -> list[list[str]]:
    """same number of lines, but as even as possible — no one-word orphan on the last line."""
    lines = _wrap(words, font, width)
    n, w = len(lines), width
    while n > 1 and w > 300:
        trial = _wrap(words, font, w - 10)
        if len(trial) != n:
            break
        lines, w = trial, w - 10
    return lines


def _fit_headline(text: str, width: int, max_lines: int, top: int = 66) -> tuple:
    words = text.split()
    for size in range(top, 40, -2):
        f = _font("serif", size)
        lines = _wrap(words, f, width)
        if len(lines) <= max_lines:
            return f, _balanced(words, f, width)
    f = _font("serif", 42)
    lines = _wrap(words, f, width)[:max_lines]
    lines[-1][-1] = lines[-1][-1].rstrip(".,;:") + "…"
    return f, lines


# ── image helpers ─────────────────────────────────────────────
def _gradient(top: tuple, bottom: tuple, start: int) -> Image.Image:
    g = Image.new("RGBA", (W, H), top)
    d = ImageDraw.Draw(g)
    for y in range(start, H):
        d.line([(0, y), (W, y)], fill=_mix(top, bottom, (y - start) / (H - start)))
    return g


def _pastelise(img: Image.Image, tint: tuple, amount: float = 0.18) -> Image.Image:
    """soften saturation + contrast a touch and lean toward the palette so every post sits in one grid."""
    img = img.convert("RGB")
    img = ImageEnhance.Color(img).enhance(0.85)
    img = ImageEnhance.Contrast(img).enhance(0.94)
    return Image.blend(img, Image.new("RGB", img.size, tint[:3]), amount)


def _circle(img: Image.Image, d: int, ring: int = 8) -> Image.Image:
    img = ImageOps.fit(img.convert("RGB"), (d, d), Image.LANCZOS)
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, d - 1, d - 1], fill=255)
    out = Image.new("RGBA", (d + 2 * ring, d + 2 * ring), (0, 0, 0, 0))
    ImageDraw.Draw(out).ellipse([0, 0, d + 2 * ring - 1, d + 2 * ring - 1], fill=(255, 255, 255, 255))
    out.paste(img, (ring, ring), mask)
    return out


def _shadow(layer: Image.Image, blur: int = 18, alpha: int = 60, offset=(0, 10)) -> Image.Image:
    a = layer.split()[-1].point(lambda v: min(v, alpha))
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    sh.putalpha(a)
    pad = blur * 3
    big = Image.new("RGBA", (layer.width + 2 * pad, layer.height + 2 * pad), (0, 0, 0, 0))
    big.paste(sh, (pad + offset[0], pad + offset[1]))
    return big.filter(ImageFilter.GaussianBlur(blur))


def _wordmark(card: Image.Image, ink: tuple) -> int:
    """logo from assets/logo.png if present, else a two-line text wordmark from config."""
    logo = ASSETS / "logo.png"
    if logo.exists():
        lg = Image.open(logo).convert("RGBA")
        lg.thumbnail((300, 70))
        card.alpha_composite(lg, ((W - lg.width) // 2, 42))
        return 42 + lg.height
    l1, l2 = CFG["page"].get("wordmark", ["your", "Page"])
    d = ImageDraw.Draw(card)
    d.text((W // 2, 62), l1, font=_font("sans", 30), fill=ink, anchor="mm")
    d.text((W // 2, 98), l2, font=_font("serif", 36), fill=ink, anchor="mm")
    return 120


# ── main ──────────────────────────────────────────────────────
def _render_stack(copy: dict, category: str, hero: dict | None, inset: Image.Image | None, out_path: Path) -> Path:
    """
    copy  : headline, highlight_words (int), attribution (str|None)
    hero  : {"kind": "cutout", "image": RGBA}  person/object already cut out
            {"kind": "scene",  "image": RGB}   full photo/illustration
            None                               typographic card
    inset : context image for the circle (optional)
    """
    pal = palette_for(category)
    page, wash, bar, ink = pal["page"], pal["wash"], pal["bar"], pal["ink"]
    card = _gradient(page, wash, 420)

    y = _wordmark(card, ink) + 52

    # headline
    has_ex = bool(copy.get("explainer"))
    max_lines = (3 if has_ex else 4) if hero else 6
    hf, lines = _fit_headline(copy["headline"], 920, max_lines,
                              (58 if has_ex else 66) if hero else 84)
    lh = int(hf.size * 1.12)
    if not hero:   # typographic card: centre the headline block in the page
        y = max(y, (H - len(lines) * (lh + 6)) // 2 - 40)
    hl_words = int(copy.get("highlight_words") or 0)
    d = ImageDraw.Draw(card)
    used = 0
    for i, line in enumerate(lines):
        text = " ".join(line)
        tw = hf.getlength(text)
        x0 = (W - tw) / 2
        highlighted = used < hl_words and i < 2
        if highlighted:
            d.rectangle([x0 - 18, y + 2, x0 + tw + 18, y + lh + 2], fill=bar)
        d.text((W / 2, y + lh / 2 + 2), text, font=hf, fill=ink, anchor="mm")
        used += len(line)
        y += lh + (10 if highlighted else 2)

    if copy.get("attribution"):
        y += 14
        d.text((W - 100, y), f"— {copy['attribution']}", font=_font("italic", 26),
               fill=_hex("#000000", 170)[:3] + (170,), anchor="ra")
        y += 34

    # explainer: 3-4 plain lines under the headline
    if has_ex:
        y += 18
        ef = _font("body", 29)
        elines = _balanced(copy["explainer"].split(), ef, 880)
        if len(elines) > 4:
            elines = elines[:4]
            elines[-1][-1] = elines[-1][-1].rstrip(".,;:") + "…"
        elh = int(ef.size * 1.45)
        for line in elines:
            d.text((W / 2, y + elh / 2), " ".join(line), font=ef, fill=ink[:3] + (205,), anchor="mm")
            y += elh
        y += 6

    img_top = max(y + 30, 430)

    # hero
    if hero and hero["kind"] == "scene":
        region_h = H - img_top + 40
        scene = ImageOps.fit(_pastelise(hero["image"], wash, 0.12), (W, region_h), Image.LANCZOS,
                             centering=(0.5, 0.35)).convert("RGBA")
        fade = Image.new("L", (W, region_h), 255)
        fd = ImageDraw.Draw(fade)
        for k in range(170):
            fd.line([(0, k), (W, k)], fill=int(255 * (k / 170) ** 1.3))
        scene.putalpha(fade)
        card.alpha_composite(scene, (0, img_top - 40))
    elif hero and hero["kind"] == "cutout":
        cut = hero["image"].convert("RGBA")
        bbox = cut.getbbox()
        if bbox:
            cut = cut.crop(bbox)
        target_h = H - img_top + 20
        scale = min(target_h / cut.height, 900 / cut.width)
        cut = cut.resize((int(cut.width * scale), int(cut.height * scale)), Image.LANCZOS)
        rgb = _pastelise(cut, wash, 0.06)
        rgb.putalpha(cut.split()[-1])
        cx = int(W * (0.44 if inset is not None else 0.5))
        pos = (cx - rgb.width // 2, H - rgb.height + 20)
        card.alpha_composite(rgb, pos)
    else:
        # typographic card: soft blobs so the bottom isn't empty
        deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dd = ImageDraw.Draw(deco)
        dd.ellipse([W - 300, -140, W + 160, 300], fill=bar[:3] + (110,))
        dd.ellipse([-200, H - 380, 260, H + 80], fill=bar[:3] + (110,))
        card.alpha_composite(deco.filter(ImageFilter.GaussianBlur(3)))
        card = Image.alpha_composite(card, Image.new("RGBA", (W, H), (0, 0, 0, 0)))

    # inset circle
    if inset is not None and hero is not None:
        dia = 300
        circ = _circle(_pastelise(inset, wash, 0.08), dia)
        cx, cy = W - 100 - circ.width // 2, img_top + 210
        sh = _shadow(circ)
        card.alpha_composite(sh, (cx - sh.width // 2, cy - sh.height // 2))
        card.alpha_composite(circ, (cx - circ.width // 2, cy - circ.height // 2))

    # handle, small, bottom-left on a soft chip so it reads on any image
    hdl = CFG["page"]["handle"]
    hfont = _font("sans_semi", 24)
    tw = int(hfont.getlength(hdl))
    chip = Image.new("RGBA", (tw + 36, 46), (0, 0, 0, 0))
    ImageDraw.Draw(chip).rounded_rectangle([0, 0, tw + 35, 45], 23, fill=page[:3] + (225,))
    card.alpha_composite(chip, (40, H - 86))
    ImageDraw.Draw(card).text((58, H - 63), hdl, font=hfont, fill=ink, anchor="lm")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.convert("RGB").save(out_path, "JPEG", quality=93, optimize=True)
    return out_path


# ── shared bits for the other layouts ─────────────────────────
def _headline_left(card, copy, x, y, width, max_lines, top, ink, bar) -> int:
    """left-aligned serif headline; opening words on bars. returns y after the block."""
    hf, lines = _fit_headline(copy["headline"], width, max_lines, top)
    lh = int(hf.size * 1.14)
    d = ImageDraw.Draw(card)
    used, hl = 0, int(copy.get("highlight_words") or 0)
    for i, line in enumerate(lines):
        n = len(line)
        # bar covers only the highlighted words on this line
        k = max(0, min(n, hl - used)) if i < 3 else 0
        if k:
            seg = " ".join(line[:k])
            d.rectangle([x - 12, y + 4, x + hf.getlength(seg) + 12, y + lh + 2], fill=bar)
        d.text((x, y + lh / 2 + 2), " ".join(line), font=hf, fill=ink, anchor="lm")
        used += n
        y += lh + 6
    return y


def _pill(card, text, x, y, fill, ink, font=None) -> int:
    f = font or _font("sans_semi", 24)
    w = int(f.getlength(text))
    ImageDraw.Draw(card).rounded_rectangle([x, y, x + w + 40, y + 48], 24, fill=fill)
    ImageDraw.Draw(card).text((x + 20, y + 24), text, font=f, fill=ink, anchor="lm")
    return x + w + 40


def _rounded(img: Image.Image, r: int) -> Image.Image:
    img = img.convert("RGBA")
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, img.width - 1, img.height - 1], r, fill=255)
    img.putalpha(m)
    return img


def _region_label(category: str) -> str:
    return {"us": "UNITED STATES", "europe": "EUROPE", "middle_east": "MIDDLE EAST"}.get(category, category.upper())


def _hero_image(hero, wash):
    if not hero:
        return None
    im = hero["image"]
    if hero["kind"] == "cutout":                 # put the cut-out on a soft pastel backdrop
        bg = Image.new("RGBA", im.size, wash)
        bg.alpha_composite(im.convert("RGBA"))
        return bg.convert("RGB")
    return _pastelise(im, wash, 0.10)


# ── layout B: framed ──────────────────────────────────────────
def _render_framed(copy, category, hero, inset, out_path):
    pal = palette_for(category)
    page, wash, bar, ink = pal["page"], pal["wash"], pal["bar"], pal["ink"]
    card = Image.new("RGBA", (W, H), _mix(page, wash, 0.45))
    M = 64
    _pill(card, _region_label(category), M, 58, bar, ink)
    l1, l2 = CFG["page"].get("wordmark", ["your", "Page"])
    ImageDraw.Draw(card).text((W - M, 82), f"{l1} {l2}", font=_font("serif", 30), fill=ink, anchor="rm")

    y = _headline_left(card, copy, M + 4, 150, W - 2 * M, 4, 64, ink, bar)
    if copy.get("attribution"):
        ImageDraw.Draw(card).text((M, y + 6), f"— {copy['attribution']}", font=_font("italic", 26), fill=ink)
        y += 44
    top = y + 28
    img = _hero_image(hero, wash)
    if img is not None:
        box = (W - 2 * M, H - top - M)
        photo = _rounded(ImageOps.fit(img, box, Image.LANCZOS, centering=(0.5, 0.45)), 34)
        sh = _shadow(photo, 22, 50, (0, 12))
        card.alpha_composite(sh, (M - 66, top - 66))
        card.alpha_composite(photo, (M, top))
        if inset is not None:
            c = _circle(_pastelise(inset, wash, 0.06), 250, 10)
            px, py = W - M - c.width + 30, H - M - c.height + 30
            card.alpha_composite(_shadow(c), (px - 54, py - 54))
            card.alpha_composite(c, (px, py))
        # handle chip on the photo
        f = _font("sans_semi", 24)
        hdl = CFG["page"]["handle"]
        _pill(card, hdl, M + 24, H - M - 72, page[:3] + (235,), ink, f)
    card.convert("RGB").save(out_path, "JPEG", quality=93, optimize=True)
    return out_path


# ── layout C: split ───────────────────────────────────────────
def _render_split(copy, category, hero, inset, out_path):
    pal = palette_for(category)
    page, wash, bar, ink = pal["page"], pal["wash"], pal["bar"], pal["ink"]
    card = Image.new("RGBA", (W, H), page)
    split = 820
    img = _hero_image(hero, wash)
    if img is not None:
        card.alpha_composite(ImageOps.fit(img, (W, split), Image.LANCZOS, centering=(0.5, 0.5)).convert("RGBA"))
    # pastel panel with a soft top edge
    panel = Image.new("RGBA", (W, H - split + 40), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle([0, 0, W, H], 44, fill=_mix(page, wash, 0.35))
    card.alpha_composite(panel, (0, split - 40))
    # wordmark chip top-left, region pill top-right
    l1, l2 = CFG["page"].get("wordmark", ["your", "Page"])
    _pill(card, f"{l1} {l2}", 48, 48, page[:3] + (235,), ink, _font("serif", 26))
    lab = _region_label(category)
    lw = int(_font("sans_semi", 22).getlength(lab)) + 40
    _pill(card, lab, W - 48 - lw, 48, bar[:3] + (240,), ink, _font("sans_semi", 22))
    if inset is not None and img is not None:
        c = _circle(_pastelise(inset, wash, 0.06), 210, 9)
        px, py = W - 64 - c.width, split - 40 - c.height // 2 - 10
        card.alpha_composite(_shadow(c), (px - 54, py - 54))
        card.alpha_composite(c, (px, py))
    y = _headline_left(card, copy, 72, split + 30, W - 2 * 72 - (150 if inset is not None else 0), 4, 64, ink, bar)
    if copy.get("attribution"):
        ImageDraw.Draw(card).text((72, y + 4), f"— {copy['attribution']}", font=_font("italic", 24), fill=ink)
    ImageDraw.Draw(card).text((72, H - 52), CFG["page"]["handle"], font=_font("sans_semi", 22),
                              fill=ink[:3] + (170,), anchor="ls")
    card.convert("RGB").save(out_path, "JPEG", quality=93, optimize=True)
    return out_path


from . import cinema  # noqa: E402

LAYOUTS = {"stack": _render_stack, "framed": _render_framed, "split": _render_split,
           "khabar": cinema.render_khabar, "cinema": cinema.render_cinema, "cinema_bottom": cinema.render_cinema_bottom, "poster": cinema.render_poster, "glass": cinema.render_glass, "jist": cinema.render_jist}


def render(copy: dict, category: str, hero: dict | None, inset: Image.Image | None,
           out_path: Path, layout: str | None = None) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    name = layout or CFG.get("card", {}).get("layout", "stack")
    return LAYOUTS.get(name, _render_stack)(copy, category, hero, inset, out_path)
