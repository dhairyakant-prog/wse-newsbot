"""facebook page: publish the card, read post + page insights.

auth: one never-expiring system-user token (FB_ACCESS_TOKEN) → we fetch the page's own token from it.
the image is uploaded directly (multipart), so no image hosting is needed.

insight metric names have changed a few times on meta's side, so every metric is fetched
on its own and missing ones are skipped instead of failing the run.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

from .common import env, log

GRAPH = "https://graph.facebook.com"
_page_tok: str | None = None


def _v() -> str:
    return os.environ.get("FB_API_VERSION", "v23.0")


def _page() -> str:
    return env("FB_PAGE_ID")


def _sys_tok() -> str:
    return os.environ.get("FB_ACCESS_TOKEN") or env("IG_ACCESS_TOKEN")


def _get(path: str, token: str, **params) -> dict:
    params["access_token"] = token
    j = requests.get(f"{GRAPH}/{_v()}/{path}", params=params, timeout=60).json()
    if "error" in j:
        raise RuntimeError(f"graph {path}: {j['error'].get('message')}")
    return j


def page_token() -> str:
    global _page_tok
    if not _page_tok:
        _page_tok = _get(_page(), _sys_tok(), fields="access_token")["access_token"]
    return _page_tok


# ── publish ───────────────────────────────────────────────────
def publish(image_path: Path, caption: str) -> tuple[str, str]:
    """returns (post_id, permalink)."""
    with open(image_path, "rb") as f:
        r = requests.post(f"{GRAPH}/{_v()}/{_page()}/photos", timeout=120,
                          data={"message": caption, "published": "true", "access_token": page_token()},
                          files={"source": (image_path.name, f, "image/jpeg")}).json()
    if "error" in r:
        raise RuntimeError(f"facebook publish: {r['error'].get('message')}")
    post_id = r.get("post_id") or r.get("id")
    link = ""
    try:
        link = _get(post_id, page_token(), fields="permalink_url").get("permalink_url", "")
    except RuntimeError:
        pass
    log.info("facebook post %s %s", post_id, link)
    return post_id, link


# ── insights ──────────────────────────────────────────────────
REACH_METRICS = ["post_impressions_unique", "post_total_media_view_unique"]
VIEW_METRICS = ["post_media_view", "post_impressions"]


def _metric(obj: str, name: str, **extra) -> int | None:
    try:
        j = _get(f"{obj}/insights", page_token(), metric=name, **extra)
    except RuntimeError:
        return None
    for row in j.get("data", []):
        vals = row.get("values") or [{}]
        v = vals[-1].get("value")
        if isinstance(v, dict):
            v = sum(x for x in v.values() if isinstance(x, (int, float)))
        if isinstance(v, (int, float)):
            return int(v)
    return None


def _first(obj: str, names: list[str], **extra) -> int:
    for n in names:
        v = _metric(obj, n, **extra)
        if v is not None:
            return v
    return 0


def media_insights(post_id: str) -> dict:
    """same keys the analyst expects from any platform."""
    out = {"reach": _first(post_id, REACH_METRICS), "views": _first(post_id, VIEW_METRICS)}
    try:
        j = _get(post_id, page_token(),
                 fields="shares,reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)")
        out["likes"] = j.get("reactions", {}).get("summary", {}).get("total_count", 0)
        out["comments"] = j.get("comments", {}).get("summary", {}).get("total_count", 0)
        out["shares"] = (j.get("shares") or {}).get("count", 0)
    except RuntimeError as e:
        log.warning("post fields %s: %s", post_id, e)
    out["saved"] = 0                                    # facebook doesn't expose saves
    out["profile_visits"] = _first(post_id, ["post_clicks"])
    out["follows"] = 0
    out["total_interactions"] = out.get("likes", 0) + out.get("comments", 0) + out.get("shares", 0)
    if not out["reach"]:                                # metric unavailable → fall back to views
        out["reach"] = out["views"]
    return out


def account_snapshot(since_ts: int, until_ts: int) -> dict:
    j = _get(_page(), page_token(), fields="followers_count,fan_count")
    out = {"followers": j.get("followers_count") or j.get("fan_count")}
    out["reach"] = _first(_page(), ["page_impressions_unique", "page_total_media_view_unique"],
                          period="day", since=since_ts, until=until_ts)
    out["views"] = _first(_page(), ["page_media_view", "page_impressions"],
                          period="day", since=since_ts, until=until_ts)
    out["total_interactions"] = _first(_page(), ["page_post_engagements"],
                                       period="day", since=since_ts, until=until_ts)
    return out


def whoami() -> dict:
    j = _get(_page(), page_token(), fields="id,name,followers_count,link")
    return {"page": j}
