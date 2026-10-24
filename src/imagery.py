"""pick the hero + inset images for a story.

named person  → wikimedia CC photo (credited) → cut out with rembg → "cutout" hero
anything else → higgsfield 1K scene → "scene" hero
inset circle  → higgsfield 1K context image (1:1)
fallbacks     → pexels / unsplash stock, then a typographic card
"""
from __future__ import annotations

import io
import os
import re

import requests
from PIL import Image

from . import higgsfield
from .common import CFG, UA, log

OK_LICENSES = re.compile(r"^(cc0|cc by|cc-by|public domain|pd)", re.I)
_rembg = None


def _download(url: str) -> Image.Image | None:
    try:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        return img if min(img.size) >= 400 else None
    except Exception as e:  # noqa: BLE001
        log.warning("download failed %s: %s", url[:80], e)
        return None


def wikimedia_person(name: str) -> tuple[Image.Image, str] | None:
    if not name:
        return None
    r = requests.get("https://en.wikipedia.org/w/api.php", headers=UA, timeout=20, params={
        "action": "query", "format": "json", "redirects": 1, "prop": "pageimages",
        "piprop": "original|name", "titles": name}).json()
    page = next(iter(r.get("query", {}).get("pages", {}).values()), {})
    fname, src = page.get("pageimage"), page.get("original", {}).get("source")
    if not (fname and src):
        return None
    meta = requests.get("https://commons.wikimedia.org/w/api.php", headers=UA, timeout=20, params={
        "action": "query", "format": "json", "prop": "imageinfo", "iiprop": "extmetadata",
        "titles": f"File:{fname}"}).json()
    info = next(iter(meta.get("query", {}).get("pages", {}).values()), {})
    ext = (info.get("imageinfo") or [{}])[0].get("extmetadata", {})
    lic = re.sub("<[^>]+>", "", ext.get("LicenseShortName", {}).get("value", ""))
    artist = re.sub("<[^>]+>", "", ext.get("Artist", {}).get("value", "")).strip()
    if not OK_LICENSES.match(lic):
        log.info("wikimedia %s skipped: license '%s'", fname, lic)
        return None
    img = _download(src)
    return (img, f"{artist or 'Wikimedia Commons'} / {lic}") if img else None


def cutout(img: Image.Image) -> Image.Image | None:
    global _rembg
    try:
        from rembg import new_session, remove
        _rembg = _rembg or new_session("u2net_human_seg")
        out = remove(img, session=_rembg)
        a = out.split()[-1]
        # reject bad cut-outs: almost nothing kept, or almost everything kept
        cover = sum(a.histogram()[128:]) / (a.width * a.height)
        return out if 0.08 < cover < 0.9 else None
    except Exception as e:  # noqa: BLE001
        log.warning("cutout failed: %s", e)
        return None


def stock(query: str) -> tuple[Image.Image, str] | None:
    for p in CFG["images"].get("stock_fallback", []):
        try:
            if p == "pexels" and os.environ.get("PEXELS_API_KEY"):
                j = requests.get("https://api.pexels.com/v1/search", timeout=20,
                                 headers={"Authorization": os.environ["PEXELS_API_KEY"]},
                                 params={"query": query, "per_page": 6}).json()
                for ph in j.get("photos", []):
                    img = _download(ph["src"]["large2x"])
                    if img:
                        return img, f"{ph['photographer']} / Pexels"
            if p == "unsplash" and os.environ.get("UNSPLASH_ACCESS_KEY"):
                key = os.environ["UNSPLASH_ACCESS_KEY"]
                j = requests.get("https://api.unsplash.com/search/photos", timeout=20,
                                 params={"query": query, "per_page": 6, "client_id": key}).json()
                for ph in j.get("results", []):
                    img = _download(ph["urls"]["regular"])
                    if img:
                        requests.get(ph["links"]["download_location"], params={"client_id": key}, timeout=10)
                        return img, f"{ph['user']['name']} / Unsplash"
        except Exception as e:  # noqa: BLE001
            log.warning("%s failed: %s", p, e)
    return None


def _scrub(prompt: str, names: list[str]) -> str:
    """belt-and-braces: never send a real person's name to the generator."""
    for n in names:
        for part in [n] + n.split():
            if len(part) > 3:
                prompt = re.sub(re.escape(part), "a person", prompt, flags=re.I)
    return prompt


def build(brief: dict, style: str = "photo") -> dict:
    """returns {hero, inset, credits[], source} ready for render()."""
    res = {"hero": None, "inset": None, "credits": [], "source": "none"}
    if style == "text-only":
        return res
    person = brief.get("person_name") or None
    names = [person] if person else []

    if person:
        got = wikimedia_person(person)
        if got:
            cut = cutout(got[0])
            if cut is not None:
                res.update(hero={"kind": "cutout", "image": cut, "original": got[0]}, source="wikimedia")
            else:   # keep the real photo even if the cut-out fails — a face beats no face
                res.update(hero={"kind": "photo", "image": got[0], "original": got[0]}, source="wikimedia")
            res["credits"].append(f"photo: {got[1]}")

    if res["hero"] is None and brief.get("scene_prompt"):
        prompt = _scrub(brief["scene_prompt"], names)
        prompt += (". candid editorial photograph of a fictional, non-identifiable ordinary person,"
                   " face clearly visible and expressive")
        if CFG.get("card", {}).get("layout") in ("cinema", "cinema_bottom", "poster", "khabar"):
            prompt += (". person in the lower half of the frame; upper half is calm sky,"
                       " wall or soft background as negative space")
        img = higgsfield.generate(prompt, "4:5")
        if img:
            res.update(hero={"kind": "scene", "image": img}, source="higgsfield")
            res["credits"].append("visual: AI-generated")
        else:
            got = stock(brief.get("stock_query") or "")
            if got:
                res.update(hero={"kind": "scene", "image": got[0]}, source="stock")
                res["credits"].append(f"photo: {got[1]}")

    if CFG.get("card", {}).get("inset", False) and res["hero"] is not None and brief.get("inset_prompt"):
        res["inset"] = higgsfield.generate(_scrub(brief["inset_prompt"], names), "1:1")
        if res["inset"] is not None and "visual: AI-generated" not in res["credits"]:
            res["credits"].append("inset: AI-generated")
    return res
