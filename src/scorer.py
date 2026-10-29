"""virality + safety scoring, and picking the one story to post now."""
from __future__ import annotations

import json
import random

from rapidfuzz import fuzz

from .common import CFG, db, hours_ago, llm_json, load_tuning, log, now

SYSTEM = """You are the editor of WSE News, a fast-growing English Facebook news page covering the
United States, Europe and the Middle East. The page follows the format of India's biggest card-news
pages (like Jist): mostly people-led stories, plus genuinely funny offbeat news from anywhere
(India and the rest of the world included) — a celebrity, athlete or public figure who says, recalls,
reveals or does something; viral human-interest stories (a worker, a traveller, a couple, a family,
someone's post that went viral about money, jobs, dating, travel, food); and a smaller share of the
biggest hard news. You judge which stories make people stop scrolling, and you are strict about safety
and accuracy. You never reward rumours, gossip without an on-record source, or outrage-bait."""

PROMPT = """Score each story below. For each, return an object:
{{
 "key": <story key>,
 "region": where the story happens or mainly matters: us | europe | middle_east | india | other
            (europe includes the UK; middle_east = gulf states, levant, iran, iraq, turkey, egypt.
             india, asia, africa, latin america etc. = other),
 "category": one of [funny, entertainment, sports, human_interest, us, europe, middle_east, business,
             technology, science, health]. funny = genuinely light, offbeat, laugh-out-loud real news (a goat
             that boarded a bus, a jugaad invention, a wedding mishap, a record nobody asked for, a pet's
             antics, a hilarious official notice) — harmless: nobody hurt, killed, humiliated, or mocked for
             poverty, religion, caste, disability or ethnicity. entertainment = film/tv/music/celebrity; sports = athletes/teams;
             human_interest = an ordinary person's story, a viral post or video, workplace/money/dating/
             travel/food stories, quirky real-life moments; region name = politics/general hard news,
 "virality": 0-10  (would a 18-45 facebook user in the US, Europe or the Middle East stop, react, comment
                    or share? highest for: a famous face saying or revealing something personal or
                    surprising, a relatable everyday story (salary, boss, rent, dating, travel mishap),
                    a viral moment, a big-name feud or tribute, money numbers people talk about.
                    lower for: dry policy, process news, markets, diplomacy without a human angle),
 "why": "<=15 words",
 "risk": 0-10  (defamation, graphic content, unverified claims, sectarian tension, privacy,
                prejudicing a trial. straight factual reporting of a conflict that major outlets
                confirm is NOT high risk by itself),
 "blocked_theme": null or the matching theme from this list: {blocked},
 "is_confirmed_fact": true/false — true if major outlets report it as fact, OR it is an on-record
                      statement or interview quote, official figure, court ruling, market move, a named
                      study/report, or a viral post/video that outlets report as such (attributed:
                      "a woman said in a viral TikTok..."). false only for rumours, anonymous leaks,
                      "sources say" gossip, unverified claims, speculation, or opinion pieces,
 "evergreen_minutes": how long this stays postable, estimate in minutes,
 "has_face": true if a named public figure is central to the story (subject, announcer, quoted),
 "duplicate_of_recent": true if this is the same event/story as anything in ALREADY POSTED below
                        (even with different wording or a new detail); false otherwise
}}
Return a JSON array, same order.

ALREADY POSTED (last 72h):
{posted}

STORIES:
{stories}"""


def _prescore(s: dict, w: dict) -> float:
    return (w["coverage"] * s["coverage"] + w["trend_match"] * s["trend_match"]
            + w["reddit_heat"] * s["reddit_heat"] + w["freshness"] * s["freshness"])


_STOP = {"The", "A", "An", "And", "Of", "In", "On", "For", "To", "As", "At", "By", "With", "After",
         "Says", "Said", "Over", "From", "His", "Her", "Their", "Is", "Are", "Was", "Be", "New", "US", "UK"}


def _names(text: str) -> set[str]:
    import re
    return {w for w in re.findall(r"\b[A-Z][a-zA-Z'\-]{2,}\b", text or "") if w not in _STOP}


def _recent_posts(con, hours: float = 72) -> list[str]:
    return [f"{r['title'] or ''} | {r['headline'] or ''}" for r in con.execute(
        "select title, headline from posts where created_at > ? and status in ('posted','dry_run')",
        (hours_ago(hours).isoformat(),))]


def _already_done(con, stories: list[dict]) -> list[dict]:
    recent = _recent_posts(con)
    recent_names = [_names(r.split(" | ")[0]) for r in _recent_posts(con, 24)]   # source titles: caps = names
    rejected = {r["story_key"] for r in con.execute(
        "select story_key from seen where decision='reject' and first_seen > ?",
        (hours_ago(24).isoformat(),))}
    keep = []
    for s in stories:
        if s["key"] in rejected:
            continue
        if any(fuzz.token_set_ratio(s["title"], t) >= 62 for t in recent):
            continue
        n = _names(s["title"])
        if any(len(n & rn) >= 2 for rn in recent_names):     # same people/places = same story
            continue
        keep.append(s)
    return keep


def score(stories: list[dict]) -> list[dict]:
    t = load_tuning()
    w = t["signal_weights"]
    con = db()
    stories = _already_done(con, stories)
    stories.sort(key=lambda s: _prescore(s, w), reverse=True)
    funny_pool = [s for s in stories if set(s.get("regions") or []) & {"india", "world"}]
    shortlist = stories[:20]
    shortlist += [s for s in funny_pool if s not in shortlist][:8]     # offbeat stories always get a look
    if not shortlist:
        return []

    payload = [{"key": s["key"], "title": s["title"], "context": s["summary"][:400],
                "outlets": s["outlets"][:8], "region": s["regions"],
                "trending_search": s.get("trend_query")} for s in shortlist]
    judged = llm_json(SYSTEM, PROMPT.format(
        blocked=json.dumps(CFG["safety"]["blocked_themes"]),
        posted="\n".join("- " + p for p in _recent_posts(con)[-80:]) or "- (none)",
        stories=json.dumps(payload, ensure_ascii=False, indent=1)), max_tokens=6000)
    by_key = {j["key"]: j for j in judged if isinstance(j, dict) and "key" in j}

    out = []
    for s in shortlist:
        j = by_key.get(s["key"])
        if not j:
            continue
        reg = j.get("region", "other")
        if reg in CFG["coverage"]["regions"] or j.get("category") == "funny":
            s["regions"] = [reg]
        s.update(category=j.get("category", reg), llm_virality=j.get("virality", 0) / 10,
                 why=j.get("why"), risk=j.get("risk", 10), blocked=j.get("blocked_theme"),
                 confirmed=j.get("is_confirmed_fact", False))
        reason = None
        if j.get("duplicate_of_recent"):
            reason = "duplicate of a recent post"
        elif len(s["outlets"]) < CFG["discovery"]["min_independent_sources"] and s["category"] != "funny":
            reason = "single source"
        elif reg not in CFG["coverage"]["regions"] and not (
                s["category"] == "funny" and CFG["coverage"].get("funny_any_region")):
            reason = f"out of coverage ({reg})"
        elif s["blocked"]:
            reason = f"blocked theme: {s['blocked']}"
        elif s["risk"] > CFG["safety"]["max_risk_score"]:
            reason = f"risk {s['risk']}"
        elif not s["confirmed"]:
            reason = "not a confirmed fact"
        base = (w["llm_virality"] * s["llm_virality"] + _prescore(s, w))       # 0–1
        cat_w = t["category_weights"].get(s["category"], 1.0)
        face_w = 1.15 if j.get("has_face") else 1.0          # cards with a real face perform better
        s["final"] = round(10 * base * cat_w * face_w, 2)
        s["reject_reason"] = reason
        con.execute("insert or replace into seen values (?,?,?,?,?)",
                    (s["key"], s["title"], now().isoformat(),
                     "reject" if reason else "candidate", reason))
        out.append(s)
    con.commit()
    out.sort(key=lambda s: s["final"], reverse=True)
    return out


def _fmt(category: str | None) -> str:
    if category in ("entertainment", "sports"):
        return "celebrity"
    if category == "human_interest":
        return "human_interest"
    if category == "funny":
        return "funny"
    return "news"


def pick(scored: list[dict]) -> dict | None:
    t = load_tuning()
    ok = [s for s in scored if not s["reject_reason"]]
    if not ok:
        return None

    # region balance: nudge toward whichever covered region is furthest behind its target share today
    con = db()
    today = [r["region"] for r in con.execute(
        "select region from posts where created_at > ? and status in ('posted','dry_run')",
        (hours_ago(24).isoformat(),))]
    want = t["region_mix"]
    if today:
        gap = {r: want.get(r, 0) - today.count(r) / len(today) for r in want}
        lagging = max(gap, key=gap.get)
        for s in ok:
            if lagging in s["regions"]:
                s["final"] *= 1.10
    # format balance (jist pattern): celebrity / human-interest / hard news
    fmix = CFG.get("format_mix")
    cats = [r["category"] for r in con.execute(
        "select category from posts where created_at > ? and status in ('posted','dry_run')",
        (hours_ago(24).isoformat(),))]
    if fmix:
        done = [_fmt(c) for c in cats]
        n = max(1, len(done))
        gap = {f: fmix[f] - done.count(f) / n for f in fmix}
        for s in ok:
            s["final"] *= 1 + max(0.0, gap.get(_fmt(s.get("category")), 0)) * 0.8
    ok.sort(key=lambda s: s["final"], reverse=True)

    explore = random.random() < CFG["analyst"]["exploration_share"]
    if explore and len(ok) > 2:
        choice = random.choice(ok[1:min(len(ok), 6)])
        choice["explore"] = True
        log.info("exploration pick: %s", choice["title"])
        return choice

    best = ok[0]
    if best["final"] < t["virality_threshold"]:
        log.info("best story %.2f below threshold %.2f — skipping slot", best["final"], t["virality_threshold"])
        return None
    best["explore"] = False
    return best
