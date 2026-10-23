"""higgsfield image generation (1K) via the higgsfield cloud api.

auth:   Authorization: Key <HF_API_KEY_ID>:<HF_API_KEY_SECRET>
flow:   POST https://api.higgsfield.ai/<model path>  →  {request_id, status_url}
        poll status_url until completed → image url(s) → download

model path and body fields live in config.yaml (higgsfield:) so you can switch
models or rename a field without touching code.
"""
from __future__ import annotations

import io
import time

import requests
from PIL import Image

from .common import CFG, env, log

BASE = "https://api.higgsfield.ai"
DONE = {"completed", "succeeded", "success", "finished", "done"}
FAILED = {"failed", "error", "nsfw", "canceled", "cancelled", "rejected"}


_AUTH_STYLE = None   # remembers which auth worked: "key" | "hf"


def _headers(style: str | None = None) -> dict:
    style = style or _AUTH_STYLE or "key"
    kid, sec = env("HF_API_KEY_ID").strip(), env("HF_API_KEY_SECRET").strip()
    base = {"Content-Type": "application/json", "Accept": "application/json"}
    if style == "hf":          # older platform header style
        return {**base, "hf-api-key": kid, "hf-secret": sec}
    return {**base, "Authorization": f"Key {kid}:{sec}"}


def _find_image_url(obj) -> str | None:
    """walk any response shape and return the first http(s) url that looks like an image."""
    if isinstance(obj, str):
        if obj.startswith("http") and any(x in obj.lower() for x in (".png", ".jpg", ".jpeg", ".webp", "image", "cdn")):
            return obj
        return None
    if isinstance(obj, dict):
        for k in ("images", "image", "output", "outputs", "result", "results", "url", "raw", "min"):
            if k in obj:
                u = _find_image_url(obj[k])
                if u:
                    return u
        for v in obj.values():
            u = _find_image_url(v)
            if u:
                return u
    if isinstance(obj, list):
        for v in obj:
            u = _find_image_url(v)
            if u:
                return u
    return None


def generate(prompt: str, aspect_ratio: str = "4:5", timeout_s: int = 240) -> Image.Image | None:
    cfg = CFG["higgsfield"]
    body = dict(cfg.get("extra_params") or {})
    body[cfg.get("prompt_field", "prompt")] = f"{prompt}. {cfg['style_suffix']}"
    body[cfg.get("aspect_field", "aspect_ratio")] = aspect_ratio
    body[cfg.get("resolution_field", "resolution")] = cfg.get("resolution", "1K")

    global _AUTH_STYLE
    url = f"{BASE}/{cfg['model_path'].strip('/')}"
    r = None
    for style in ([_AUTH_STYLE] if _AUTH_STYLE else ["key", "hf"]):
        r = requests.post(url, json=body, headers=_headers(style), timeout=60)
        if r.status_code not in (401, 403):
            _AUTH_STYLE = style
            break
    if r.status_code >= 400:
        log.warning("higgsfield submit %s: %s  (check HF_API_KEY_ID / HF_API_KEY_SECRET in github secrets"
                    " — they come from cloud.higgsfield.ai → API keys)", r.status_code, r.text[:300])
        return None
    sub = r.json()
    status_url = sub.get("status_url") or f"{BASE}/requests/{sub.get('request_id')}/status"

    t0 = time.time()
    while time.time() - t0 < timeout_s:
        time.sleep(4)
        s = requests.get(status_url, headers=_headers(), timeout=30).json()
        state = str(s.get("status", "")).lower()
        if state in FAILED:
            log.warning("higgsfield %s: %s", state, str(s)[:300])
            return None
        if state in DONE or (state not in {"queued", "in_progress", "processing", "running", "pending"}
                             and _find_image_url(s)):
            url = _find_image_url(s)
            if not url:
                log.warning("higgsfield done but no image url: %s", str(s)[:300])
                return None
            img = requests.get(url, timeout=60)
            return Image.open(io.BytesIO(img.content)).convert("RGB")
    log.warning("higgsfield timed out after %ss", timeout_s)
    return None
