"""discovery: pull candidate stories + heat signals from free public feeds.

- google news rss (us + uk editions per topic, plus europe / middle east searches) → stories, and how many outlets carry each
- google trends daily rss (IN, US)          → what people are searching right now
- reddit hot (selected subs)                → what people are discussing/upvoting
"""
from __future__ import annotations

import hashlib
import html
import re
from calendar import timegm
from datetime import datetime, timezone

import feedparser
import requests
from rapidfuzz import fuzz

from .common import CFG, UA, hours_ago, log

GN = "https://news.google.com/rss"


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(s or ""))).strip()


def _key(title: str) -> str:
    norm = re.sub(r"[^a-z0-9 ]", "", title.lower())
    return hashlib.sha1(" ".join(sorted(set(norm.split())))[:200].encode()).hexdigest()[:16]


def _published(entry) -> datetime | None:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(timegm(t), tz=timezone.utc) if t else None


def _gn_url(ed: dict, topic: str | None = None, query: str | None = None) -> str:
    from urllib.parse import quote_plus
    q = f"hl={ed['hl']}&gl={ed['gl']}&ceid={ed['ceid']}"
    if query:
        return f"{GN}/search?q={quote_plus(query + ' when:1d')}&{q}"
    if topic:
        return f"{GN}/headlines/section/topic/{topic}?{q}"
    return f"{GN}?{q}"


def _parse_feed(url: str, region_hint: str, topic: str, cutoff) -> list[dict]:
    try:
        feed = feedparser.parse(requests.get(url, headers=UA, timeout=20).content)
    except Exception as e:  # noqa: BLE001
        log.warning("gnews fail %s: %s", url[:90], e)
        return []
    out = []
    for e in feed.entries:
        pub = _published(e)
        if pub and pub < cutoff:
            continue
        raw_title = e.get("title", "")
        title, _, outlet = raw_title.rpartition(" - ")     # "Headline - Outlet"
        title = title or raw_title
        desc = e.get("summary", "")
        # google bundles related coverage as <li><a>…</a><font>Outlet</font></li>
        outlets = set(re.findall(r"<font[^>]*>([^<]+)</font>", desc)) | ({outlet} if outlet else set())
        related = [_strip(x) for x in re.findall(r"<a [^>]*>(.*?)</a>", desc)]
        out.append({
            "key": _key(title), "title": title.strip(), "summary": " | ".join(related[:5]),
            "link": e.get("link"), "published": pub.isoformat() if pub else None,
            "region": region_hint, "topic": topic,
            "outlets": sorted(o.strip() for o in outlets if o.strip()),
        })
    return out


def google_news() -> list[dict]:
    d = CFG["discovery"]
    cutoff = hours_ago(d["lookback_hours"])
    out = []
    for region, ed in d["editions"].items():
        for topic in [None] + d["topics"]:
            out += _parse_feed(_gn_url(ed, topic=topic), region, (topic or "top").lower(), cutoff)
    us_ed = d["editions"]["us"]
    for region, queries in d.get("searches", {}).items():
        ed = d.get("search_editions", {}).get(region, us_ed)
        for q in queries:
            out += _parse_feed(_gn_url(ed, query=q), region, "search", cutoff)
    log.info("google news: %d raw items", len(out))
    return out


def google_trends() -> list[dict]:
    out = []
    for geo in CFG["discovery"]["trends_geo"]:
        try:
            feed = feedparser.parse(requests.get(
                f"https://trends.google.com/trending/rss?geo={geo}", headers=UA, timeout=20).content)
        except Exception as e:  # noqa: BLE001
            log.warning("trends fail %s: %s", geo, e)
            continue
        for e in feed.entries:
            traffic = re.sub(r"[^0-9]", "", e.get("ht_approx_traffic", "") or "0") or "0"
            out.append({
                "query": e.get("title", ""),
                "traffic": int(traffic),
                "geo": geo,
                "news": _strip(e.get("ht_news_item_title", "")),
            })
    log.info("trends: %d queries", len(out))
    return out


def reddit_hot() -> list[dict]:
    out = []
    for sub in CFG["discovery"]["reddit_subs"]:
        try:
            r = requests.get(f"https://www.reddit.com/r/{sub}/hot.json?limit=40",
                             headers=UA, timeout=20)
            r.raise_for_status()
            for c in r.json()["data"]["children"]:
                p = c["data"]
                if p.get("stickied"):
                    continue
                age_h = max((datetime.now(timezone.utc).timestamp() - p["created_utc"]) / 3600, 0.5)
                out.append({"title": p["title"], "sub": sub, "score": p["score"],
                            "comments": p["num_comments"], "velocity": p["score"] / age_h})
        except Exception as e:  # noqa: BLE001
            log.warning("reddit fail %s: %s", sub, e)
    log.info("reddit: %d posts", len(out))
    return out


# ── merge into clusters with signals ──────────────────────────
def cluster(items: list[dict], threshold: int = 72) -> list[dict]:
    clusters: list[dict] = []
    for it in items:
        for c in clusters:
            if fuzz.token_set_ratio(it["title"], c["title"]) >= threshold:
                c["outlets"] = sorted(set(c["outlets"]) | set(it["outlets"]))
                c["topics"].add(it["topic"])
                c["regions"].add(it["region"])
                c["alt_titles"].append(it["title"])
                if it["summary"] and len(c["summary"]) < 600:
                    c["summary"] += " | " + it["summary"]
                if (it["published"] or "") > (c["published"] or ""):
                    c["published"] = it["published"]
                break
        else:
            clusters.append({**it, "topics": {it["topic"]}, "regions": {it["region"]},
                             "alt_titles": [it["title"]]})
    for c in clusters:
        c["topics"] = sorted(c["topics"])
        c["regions"] = sorted(c["regions"])
    return clusters


def attach_signals(stories: list[dict], trends: list[dict], reddit: list[dict]) -> list[dict]:
    max_vel = max([r["velocity"] for r in reddit] or [1])
    for s in stories:
        text = s["title"] + " " + s["summary"]
        # trend match: best fuzzy hit of any trending query inside the story text
        best_t, hit = 0.0, None
        for t in trends:
            if not t["query"]:
                continue
            r = max(fuzz.partial_ratio(t["query"].lower(), text.lower()),
                    fuzz.token_set_ratio(t["news"], s["title"]) if t["news"] else 0)
            if r >= 85:
                score = min(1.0, 0.5 + (t["traffic"] / 200000))
                if score > best_t:
                    best_t, hit = score, t["query"]
        s["trend_match"], s["trend_query"] = round(best_t, 3), hit
        # reddit heat: best matching post velocity, normalised
        best_r = 0.0
        for p in reddit:
            if fuzz.token_set_ratio(p["title"], s["title"]) >= 70:
                best_r = max(best_r, p["velocity"] / max_vel)
        s["reddit_heat"] = round(best_r, 3)
        s["coverage"] = round(min(1.0, len(s["outlets"]) / 8), 3)
        if s["published"]:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(s["published"])).total_seconds() / 3600
            s["freshness"] = round(max(0.0, 1 - age / CFG["discovery"]["lookback_hours"]), 3)
        else:
            s["freshness"] = 0.3
    return stories


def discover() -> list[dict]:
    items = google_news()
    stories = cluster(items)
    stories = attach_signals(stories, google_trends(), reddit_hot())
    min_src = CFG["discovery"]["min_independent_sources"]
    kept = [s for s in stories if len(s["outlets"]) >= min_src]
    log.info("clusters: %d, with ≥%d outlets: %d", len(stories), min_src, len(kept))
    return kept
