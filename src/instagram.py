"""image hosting + instagram graph api: publish, media insights, account insights."""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import requests

from .common import env, log

GRAPH = "https://graph.facebook.com"


def _v() -> str:
    return os.environ.get("IG_API_VERSION", "v23.0")


def _tok() -> str:
    return env("IG_ACCESS_TOKEN")


def _ig() -> str:
    return env("IG_USER_ID")


def _call(method: str, path: str, **params) -> dict:
    params["access_token"] = _tok()
    url = f"{GRAPH}/{_v()}/{path}"
    r = requests.request(method, url, params=params if method == "GET" else None,
                         data=params if method == "POST" else None, timeout=60)
    j = r.json()
    if "error" in j:
        raise RuntimeError(f"graph {path}: {j['error'].get('message')}")
    return j


# ── hosting: instagram pulls the image from a public url ──────
def host_image(path: Path) -> str:
    cloud, key, secret = env("CLOUDINARY_CLOUD_NAME"), env("CLOUDINARY_API_KEY"), env("CLOUDINARY_API_SECRET")
    ts = str(int(time.time()))
    folder = "newsbot"
    sig = hashlib.sha1(f"folder={folder}&timestamp={ts}{secret}".encode()).hexdigest()
    with open(path, "rb") as f:
        r = requests.post(f"https://api.cloudinary.com/v1_1/{cloud}/image/upload", timeout=60,
                          data={"api_key": key, "timestamp": ts, "folder": folder, "signature": sig},
                          files={"file": f}).json()
    if "secure_url" not in r:
        raise RuntimeError(f"cloudinary upload failed: {r}")
    return r["secure_url"]


# ── publishing ────────────────────────────────────────────────
def quota_left() -> int:
    j = _call("GET", f"{_ig()}/content_publishing_limit", fields="quota_usage,config")
    d = (j.get("data") or [{}])[0]
    total = d.get("config", {}).get("quota_total", 50)
    return total - d.get("quota_usage", 0)


def publish(image_url: str, caption: str, alt_text: str) -> tuple[str, str]:
    """returns (media_id, permalink)."""
    if quota_left() <= 1:
        raise RuntimeError("publishing quota nearly exhausted — skipping")
    c = _call("POST", f"{_ig()}/media", image_url=image_url, caption=caption, alt_text=alt_text[:1000])
    cid = c["id"]
    for _ in range(30):
        st = _call("GET", cid, fields="status_code").get("status_code")
        if st == "FINISHED":
            break
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {cid} status {st}")
        time.sleep(4)
    m = _call("POST", f"{_ig()}/media_publish", creation_id=cid)
    mid = m["id"]
    link = _call("GET", mid, fields="permalink").get("permalink", "")
    log.info("published %s %s", mid, link)
    return mid, link


# ── insights ──────────────────────────────────────────────────
MEDIA_METRICS = ["reach", "views", "likes", "comments", "shares", "saved",
                 "total_interactions", "follows", "profile_visits"]


def media_insights(media_id: str) -> dict:
    out: dict = {}
    metrics = list(MEDIA_METRICS)
    try:
        j = _call("GET", f"{media_id}/insights", metric=",".join(metrics))
    except RuntimeError as e:
        # some metrics aren't available on every media/version — fall back one by one
        log.warning("bulk insights failed (%s), retrying per metric", e)
        j = {"data": []}
        for m in metrics:
            try:
                j["data"] += _call("GET", f"{media_id}/insights", metric=m).get("data", [])
            except RuntimeError:
                pass
    for row in j.get("data", []):
        vals = row.get("values") or [{}]
        out[row["name"]] = vals[0].get("value", (row.get("total_value") or {}).get("value", 0))
    return out


def account_snapshot(since_ts: int, until_ts: int) -> dict:
    out = {"followers": _call("GET", _ig(), fields="followers_count").get("followers_count")}
    try:
        j = _call("GET", f"{_ig()}/insights", metric="reach,views,total_interactions",
                  period="day", metric_type="total_value", since=since_ts, until=until_ts)
        for row in j.get("data", []):
            out[row["name"]] = (row.get("total_value") or {}).get("value", 0)
    except RuntimeError as e:
        log.warning("account insights: %s", e)
    return out


# ── facebook page (same image + caption) ──────────────────────
def _page_token(page_id: str) -> str:
    """the system-user token can mint the page's own token."""
    return _call("GET", page_id, fields="access_token")["access_token"]


def publish_facebook(image_url: str, caption: str) -> str:
    page_id = env("FB_PAGE_ID")
    r = requests.post(f"{GRAPH}/{_v()}/{page_id}/photos", timeout=60, data={
        "url": image_url, "message": caption, "access_token": _page_token(page_id)}).json()
    if "error" in r:
        raise RuntimeError(f"facebook publish: {r['error'].get('message')}")
    log.info("facebook post %s", r.get("post_id") or r.get("id"))
    return r.get("post_id") or r.get("id")


def whoami() -> dict:
    """setup check: prints the page + instagram account the token can see."""
    return _call("GET", "me/accounts", fields="id,name,instagram_business_account{id,username}")
