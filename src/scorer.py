"""virality + safety scoring, and picking the one story to post now."""
from __future__ import annotations

import json
import random

from rapidfuzz import fuzz

from .common import CFG, db, hours_ago, llm_json, load_tuning, log, now

SYSTEM = """You are the news editor of a fast-growing English Instagram news page covering
the United States, Europe and the Middle East, for readers in those regions. You judge which stories will travel on Instagram, and you are
strict about safety and accuracy. You never reward outrage-bait or rumours."""

PROMPT = """Score each story below. For each, return an object:
{{
 "key": <story key>,
 "region": where the story happens or mainly matters: us | europe | middle_east | other
            (europe includes the UK; middle_east = gulf states, levant, iran, iraq, turkey, egypt.
             india, asia, africa, latin america etc. = other),
 "category": one of [us, europe, middle_east, business, technology, entertainment, sports, science, health]
             (use the region name for politics/general news, a topic name when the topic is the point),
 "virality": 0-10  (would an 18-40 instagram user in the US, Europe or the Middle East stop, share
                    or save this? weigh: surprise, relevance to daily life, emotional pull,
                    conversation value, visual potential, celebrity, money impact),
 "why": "<=15 words",
 "risk": 0-10  (defamation, graphic content, unverified claims, sectarian tension, privacy,
                prejudicing a trial. straight factual reporting of a conflict that major outlets
                confirm is NOT high risk by itself),
 "blocked_theme": null or the matching theme from this list: {blocked},
 "is_confirmed_fact": true/false — true if major outlets report it as fact, OR it is an on-record
                      statement, official figure, court ruling, market move, or a named study/report
                      (attributed). false only for rumours, anonymous leaks, unverified claims, speculation,
                      or opinion pieces,
 "evergreen_minutes": how long this stays postable, estimate in minutes
}}
Return a JSON array, same order.

STORIES:
{stories}"""


def _prescore(s: dict, w: dict) -> float:
    return (w["coverage"] * s["coverage"] + w["trend_match"] * s["trend_match"]
            + w["reddit_heat"] * s["reddit_heat"] + w["freshness"] * s["freshness"])


def _already_done(con, stories: list[dict]) -> list[dict]:
    recent = [r["title"] for r in con.execute(
        "select title from posts where created_at > ?", (hours_ago(72).isoformat(),))]
    rejected = {r["story_key"] for r in con.execute(
        "select story_key from seen where decision='reject' and first_seen > ?",
        (hours_ago(24).isoformat(),))}
    keep = []
    for s in stories:
        if s["key"] in rejected:
            continue
        if any(fuzz.token_set_ratio(s["title"], t) >= 70 for t in recent):
            continue
        keep.append(s)
    return keep


def score(stories: list[dict]) -> list[dict]:
    t = load_tuning()
    w = t["signal_weights"]
    con = db()
    stories = _already_done(con, stories)
    stories.sort(key=lambda s: _prescore(s, w), reverse=True)
    shortlist = stories[:25]
    if not shortlist:
        return []

    payload = [{"key": s["key"], "title": s["title"], "context": s["summary"][:400],
                "outlets": s["outlets"][:8], "region": s["regions"],
                "trending_search": s.get("trend_query")} for s in shortlist]
    judged = llm_json(SYSTEM, PROMPT.format(
        blocked=json.dumps(CFG["safety"]["blocked_themes"]),
        stories=json.dumps(payload, ensure_ascii=False, indent=1)), max_tokens=6000)
    by_key = {j["key"]: j for j in judged if isinstance(j, dict) and "key" in j}

    out = []
    for s in shortlist:
        j = by_key.get(s["key"])
        if not j:
            continue
        reg = j.get("region", "other")
        if reg in CFG["coverage"]["regions"]:
            s["regions"] = [reg]
        s.update(category=j.get("category", reg), llm_virality=j.get("virality", 0) / 10,
                 why=j.get("why"), risk=j.get("risk", 10), blocked=j.get("blocked_theme"),
                 confirmed=j.get("is_confirmed_fact", False))
        reason = None
        if reg not in CFG["coverage"]["regions"]:
            reason = f"out of coverage ({reg})"
        elif s["blocked"]:
            reason = f"blocked theme: {s['blocked']}"
        elif s["risk"] > CFG["safety"]["max_risk_score"]:
            reason = f"risk {s['risk']}"
        elif not s["confirmed"]:
            reason = "not a confirmed fact"
        base = (w["llm_virality"] * s["llm_virality"] + _prescore(s, w))       # 0–1
        cat_w = t["category_weights"].get(s["category"], 1.0)
        s["final"] = round(10 * base * cat_w, 2)
        s["reject_reason"] = reason
        con.execute("insert or replace into seen values (?,?,?,?,?)",
                    (s["key"], s["title"], now().isoformat(),
                     "reject" if reason else "candidate", reason))
        out.append(s)
    con.commit()
    out.sort(key=lambda s: s["final"], reverse=True)
    return out


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
