"""every 72h: pull performance, work out what's working, retune the system within guardrails.

what it can change (state/tuning.json only):
  category weights · slot weights · region mix · posts per day · virality threshold
  signal weights · copy rules · hashtag boost/block lists · headline length · image style
what it can never change: config.yaml (safety rules, hard caps, palettes, mode).
"""
from __future__ import annotations

import json
import os
import smtplib
import statistics as st
from collections import defaultdict
from datetime import datetime, timedelta
from email.message import EmailMessage

from . import facebook, instagram
from .common import CFG as _CFG

ig = facebook if _CFG.get("platform") == "facebook" else instagram
from .common import CFG, ROOT, db, hours_ago, llm_json, load_tuning, log, now, save_tuning

REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)


# ── data ──────────────────────────────────────────────────────
def refresh_insights() -> None:
    con = db()
    rows = con.execute("select id, ig_media_id, created_at from posts where status='posted' "
                       "and created_at > ?", (hours_ago(24 * 10).isoformat(),)).fetchall()
    for r in rows:
        try:
            m = ig.media_insights(r["ig_media_id"])
        except Exception as e:  # noqa: BLE001
            log.warning("insights %s: %s", r["ig_media_id"], e)
            continue
        age = (now() - datetime.fromisoformat(r["created_at"])).total_seconds() / 3600
        con.execute("insert into insights values (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["id"], now().isoformat(), round(age, 1), m.get("reach", 0), m.get("views", 0),
                     m.get("likes", 0), m.get("comments", 0), m.get("shares", 0), m.get("saved", 0),
                     m.get("total_interactions", 0), m.get("follows", 0), m.get("profile_visits", 0)))
    try:
        until = int(now().timestamp())
        snap = ig.account_snapshot(until - 86400, until)
        con.execute("insert or replace into account_daily values (?,?,?,?,?)",
                    (now().date().isoformat(), snap.get("followers"), snap.get("reach"),
                     snap.get("views"), snap.get("total_interactions")))
    except Exception as e:  # noqa: BLE001
        log.warning("account snapshot: %s", e)
    con.commit()


def dataset(since_h: float, until_h: float = 0) -> list[dict]:
    """latest insight per post, for posts published in (now-since_h, now-until_h], aged ≥ 18h."""
    con = db()
    q = """select p.*, i.reach, i.views, i.likes, i.comments, i.shares, i.saved,
                  i.total_interactions, i.follows, i.profile_visits, i.age_hours
           from posts p join insights i on i.post_id = p.id
           where p.status='posted' and p.created_at > ? and p.created_at <= ?
             and i.fetched_at = (select max(fetched_at) from insights where post_id = p.id)
             and i.age_hours >= 18"""
    rows = [dict(r) for r in con.execute(q, (hours_ago(since_h).isoformat(), hours_ago(until_h).isoformat()))]
    for r in rows:
        reach = max(r["reach"] or 0, 1)
        r["er"] = (r["total_interactions"] or 0) / reach
        r["share_rate"] = (r["shares"] or 0) / reach
        r["save_rate"] = (r["saved"] or 0) / reach
        r["follow_rate"] = (r["follows"] or 0) / reach
        r["signals"] = json.loads(r["signals"] or "{}")
    _performance_index(rows)
    return rows


def _pct_rank(vals: list[float], v: float) -> float:
    return sum(1 for x in vals if x <= v) / len(vals) if vals else 0.5


def _performance_index(rows: list[dict]) -> None:
    """one number per post, 0–1. reach matters most; shares and saves (instagram) are what the feed rewards next."""
    if not rows:
        return
    if CFG.get("platform") == "facebook":      # no saves or follows per post on facebook
        cols = {"reach": 0.45, "share_rate": 0.35, "er": 0.2}
    else:
        cols = {"reach": 0.4, "share_rate": 0.25, "save_rate": 0.15, "er": 0.1, "follow_rate": 0.1}
    series = {c: [r[c] or 0 for r in rows] for c in cols}
    for r in rows:
        r["perf"] = round(sum(w * _pct_rank(series[c], r[c] or 0) for c, w in cols.items()), 3)


def group(rows: list[dict], key) -> dict:
    g = defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    return {k: {"n": len(v), "perf": round(st.mean(x["perf"] for x in v), 3),
                "reach": int(st.median(x["reach"] or 0 for x in v)),
                "share_rate": round(st.mean(x["share_rate"] for x in v), 4)} for k, v in g.items()}


def _corr(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 5 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return 0.0
    return st.correlation(xs, ys)


# ── deterministic, bounded tuning ─────────────────────────────
def _nudge(cur: float, rel: float, cap: float, lo: float, hi: float) -> float:
    delta = max(-cap, min(cap, rel))
    return round(max(lo, min(hi, cur * (1 + delta))), 3)


def tune(rows: list[dict], prev: list[dict], t: dict, changes: list) -> dict:
    cap = CFG["analyst"]["max_weight_change"]
    overall = st.mean(r["perf"] for r in rows)

    def record(field, old, new, why):
        if old != new:
            changes.append({"field": field, "old": old, "new": new, "reason": why})

    # categories (exploration posts included — that's the point of exploring)
    for cat, g in group(rows, lambda r: r["category"]).items():
        if g["n"] < 2:
            continue
        old = t["category_weights"].get(cat, 1.0)
        new = _nudge(old, (g["perf"] - overall) * 1.5, cap, 0.4, 2.0)
        record(f"category_weights.{cat}", old, new, f"perf {g['perf']} vs avg {overall:.2f} (n={g['n']})")
        t["category_weights"][cat] = new

    # time slots
    for slot, g in group(rows, lambda r: r["slot"]).items():
        if g["n"] < 2 or not slot:
            continue
        old = t["slot_weights"].get(slot, 1.0)
        new = _nudge(old, (g["perf"] - overall) * 1.5, cap, 0.3, 2.0)
        record(f"slot_weights.{slot}", old, new, f"perf {g['perf']} (n={g['n']})")
        t["slot_weights"][slot] = new

    # region mix
    reg = group(rows, lambda r: r["region"])
    mix = dict(t["region_mix"])
    if sum(1 for r in mix if reg.get(r, {}).get("n", 0) >= 3) >= 2:
        for r in mix:
            if reg.get(r, {}).get("n", 0) >= 3:
                step = max(-0.05, min(0.05, (reg[r]["perf"] - overall) * 0.2))
                mix[r] = max(0.15, min(0.6, mix[r] + step))      # every region keeps a floor
        tot = sum(mix.values())
        mix = {r: round(v / tot, 3) for r, v in mix.items()}
        record("region_mix", t["region_mix"], mix,
               " · ".join(f"{r} {reg[r]['perf']} (n={reg[r]['n']})" for r in mix if r in reg))
        t["region_mix"] = mix

    # signal weights: lean toward signals that actually predicted performance
    sw = t["signal_weights"]
    perf = [r["perf"] for r in rows]
    corrs = {s: _corr([r["signals"].get(s, 0) for r in rows], perf) for s in sw}
    if len(rows) >= 12:
        new_sw = {s: (0.0 if sw[s] == 0 else max(0.05, sw[s] * (1 + max(-cap, min(cap, corrs[s]))))) for s in sw}
        tot = sum(new_sw.values())
        new_sw = {s: round(v / tot, 3) for s, v in new_sw.items()}
        record("signal_weights", sw, new_sw, f"correlations {json.dumps({k: round(v, 2) for k, v in corrs.items()})}")
        t["signal_weights"] = new_sw

    # volume: are we diluting reach by posting too much?
    max_ppd = CFG["posting"]["max_posts_per_day"]
    ppd = t["posts_per_day"] or max_ppd
    if prev:
        reach_now = st.median(r["reach"] or 0 for r in rows)
        reach_prev = st.median(r["reach"] or 0 for r in prev) or 1
        change = reach_now / reach_prev - 1
        new_ppd = ppd
        if change < -0.25 and len(rows) > len(prev):
            new_ppd = max(CFG["posting"].get("min_posts_per_day", 3), ppd - 2)
        elif change > 0.15:
            new_ppd = min(max_ppd, ppd + 1)
        record("posts_per_day", ppd, new_ppd, f"median reach per post {change:+.0%} vs previous window")
        t["posts_per_day"] = new_ppd

    # threshold: if the bar is filtering out too many slots, lower it; if quality dropped, raise it
    con = db()
    skipped = con.execute("select count(*) c from posts where status='skipped_low_score' and created_at > ?",
                          (hours_ago(72).isoformat(),)).fetchone()["c"]
    old = t["virality_threshold"]
    if skipped > len(rows):
        new = round(max(3.0, old - 0.3), 2)
    elif overall < 0.45 and len(rows) >= CFG["analyst"]["min_posts_for_decisions"]:
        new = round(min(8.0, old + 0.2), 2)
    else:
        new = old
    record("virality_threshold", old, new, f"{skipped} slots skipped, avg perf {overall:.2f}")
    t["virality_threshold"] = new
    return t


# ── editorial read via llm ────────────────────────────────────
EDITOR = """You are the growth editor for a Facebook news page (pastel cards, US + Europe + Middle East news).
You read performance data and set short, concrete house rules. You don't chase outrage or
misinformation, and you never relax accuracy or neutrality for reach."""

EDITOR_PROMPT = """Last {hours}h performance. perf is a 0-1 index (on facebook: reach 45%, share rate 35%, engagement 20%), ranked within this window.

WINDOW SUMMARY: {summary}
BY CATEGORY: {by_cat}
BY REGION: {by_reg}
BY SLOT (UTC): {by_slot}
BY IMAGE SOURCE: {by_img}
PREVIOUS WINDOW: {prev}

TOP POSTS:
{top}

BOTTOM POSTS:
{bottom}

CURRENT HOUSE RULES: {rules}
CURRENT HASHTAG BOOST: {boost}   BLOCK: {block}
CURRENT HEADLINE MAX WORDS: {hw}   IMAGE STYLE: {style}

Return JSON:
{{
 "diagnosis": ["3-5 plain sentences on what is and isn't working, with numbers"],
 "copy_guidance": ["max 6 short rules for the copywriter, replacing current ones. specific, testable."],
 "hashtag_boost": ["max 5 tags (no #) that keep appearing on top posts"],
 "hashtag_block": ["tags (no #) that keep appearing on bottom posts and look like dead weight"],
 "headline_max_words": integer 12-28,
 "image_style": "photo | illustration-led | text-only",
 "alerts": ["anything a human must look at — e.g. sudden reach collapse (possible restriction),
            a post with unusually many comments (possible controversy), repeated topic fatigue. empty if none."]
}}"""


def _brief(r: dict) -> str:
    return (f"- [{r['category']}/{r['region']}/{r['slot']}] \"{r['headline']}\" | reach {r['reach']} "
            f"shares {r['shares']} saves {r['saved']} comments {r['comments']} perf {r['perf']} "
            f"| tags: {r['hashtags'][:120]}")


def editorial(rows: list[dict], prev: list[dict], t: dict, changes: list) -> tuple[list, list]:
    ranked = sorted(rows, key=lambda r: r["perf"], reverse=True)
    summary = {"posts": len(rows), "median_reach": int(st.median(r["reach"] or 0 for r in rows)),
               "avg_er": round(st.mean(r["er"] for r in rows), 4),
               "total_follows": sum(r["follows"] or 0 for r in rows)}
    prev_s = ({"posts": len(prev), "median_reach": int(st.median(r["reach"] or 0 for r in prev)),
               "avg_er": round(st.mean(r["er"] for r in prev), 4)} if prev else "none")
    j = llm_json(EDITOR, EDITOR_PROMPT.format(
        hours=CFG["analyst"]["every_hours"], summary=summary, prev=prev_s,
        by_cat=group(rows, lambda r: r["category"]), by_slot=group(rows, lambda r: r["slot"]), by_reg=group(rows, lambda r: r["region"]),
        by_img=group(rows, lambda r: r["image_source"]),
        top="\n".join(_brief(r) for r in ranked[:5]), bottom="\n".join(_brief(r) for r in ranked[-5:]),
        rules=t["copy_guidance"], boost=t["hashtag_boost"], block=t["hashtag_blocklist"],
        hw=t["headline_max_words"], style=t["image_style"]))

    def setf(field, new, why="editor review"):
        if t[field] != new:
            changes.append({"field": field, "old": t[field], "new": new, "reason": why})
            t[field] = new

    setf("copy_guidance", [g for g in j.get("copy_guidance", [])][:6])
    setf("hashtag_boost", [h.lstrip("#") for h in j.get("hashtag_boost", [])][:5])
    setf("hashtag_blocklist", sorted(set(t["hashtag_blocklist"]) |
                                     {h.lstrip("#") for h in j.get("hashtag_block", [])})[-40:])
    hw = j.get("headline_max_words", t["headline_max_words"])
    if isinstance(hw, int):
        setf("headline_max_words", max(12, min(28, hw)))
    if j.get("image_style") in ("photo", "illustration-led", "text-only"):
        setf("image_style", j["image_style"])
    return j.get("diagnosis", []), j.get("alerts", [])


# ── report ────────────────────────────────────────────────────
def write_report(rows, diagnosis, alerts, changes, applied: bool) -> str:
    d = now().astimezone().strftime("%Y-%m-%d %H:%M")
    lines = [f"# newsbot review — {d}", ""]
    if alerts:
        lines += ["## needs your eyes", *[f"- {a}" for a in alerts], ""]
    lines += ["## what happened", *[f"- {x}" for x in diagnosis], ""]
    if rows:
        lines += ["## top 5", *[f"- {r['headline']} — reach {r['reach']}, shares {r['shares']}, "
                                f"comments {r['comments']} ({r['permalink']})"
                                for r in sorted(rows, key=lambda r: r['perf'], reverse=True)[:5]], ""]
    lines += [f"## changes {'applied' if applied else 'proposed (not enough data yet — nothing changed)'}"]
    lines += [f"- **{c['field']}**: {json.dumps(c['old'], ensure_ascii=False)} → "
              f"{json.dumps(c['new'], ensure_ascii=False)}  _({c['reason']})_" for c in changes] or ["- none"]
    md = "\n".join(lines)
    (REPORTS / f"{now().strftime('%Y-%m-%d_%H%M')}.md").write_text(md)
    return md


def _email(md: str, alerts: list) -> None:
    to = CFG["analyst"].get("notify_email")
    host = os.environ.get("SMTP_HOST")
    if not (to and host):
        return
    msg = EmailMessage()
    msg["Subject"] = ("⚠ " if alerts else "") + "newsbot 72h review"
    msg["From"] = os.environ.get("SMTP_USER")
    msg["To"] = to
    msg.set_content(md)
    with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT", 465))) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)


def due() -> bool:
    t = load_tuning()
    last = t.get("last_review")
    if not last:
        return True
    return now() - datetime.fromisoformat(last) >= timedelta(hours=CFG["analyst"]["every_hours"] - 1)


def run(force: bool = False) -> str | None:
    if not force and not due():
        log.info("analyst: not due yet")
        return None
    refresh_insights()
    hours = CFG["analyst"]["every_hours"]
    rows = dataset(hours + 18, 0)          # +18h so the oldest posts in the window have matured
    prev = dataset(2 * hours + 18, hours + 18)
    t = load_tuning()
    changes: list = []
    if not rows:
        md = write_report([], ["no matured posts in this window yet."], [], [], False)
        t["last_review"] = now().isoformat()
        save_tuning(t)
        return md

    enough = len(rows) >= CFG["analyst"]["min_posts_for_decisions"]
    proposed = json.loads(json.dumps(t))
    proposed = tune(rows, prev, proposed, changes)
    diagnosis, alerts = editorial(rows, prev, proposed, changes)

    # hard alert: reach collapse usually means a restriction or a content problem, not a tuning problem
    if prev:
        drop = st.median(r["reach"] or 0 for r in rows) / max(st.median(r["reach"] or 0 for r in prev), 1)
        if drop < 0.4:
            alerts.insert(0, f"median reach fell to {drop:.0%} of the previous window — check account status "
                             "in meta business suite (page quality, restrictions, community standards flags) before anything else.")

    final = proposed if enough else t
    final["last_review"] = now().isoformat()
    save_tuning(final)
    if enough:
        con = db()
        for c in changes:
            con.execute("insert into changes (at, field, old, new, reason) values (?,?,?,?,?)",
                        (now().isoformat(), c["field"], json.dumps(c["old"], ensure_ascii=False),
                         json.dumps(c["new"], ensure_ascii=False), c["reason"]))
        con.commit()
    md = write_report(rows, diagnosis, alerts, changes, enough)
    from . import alerts as _al
    for a in alerts:
        _al.raise_("72h review: needs your attention", a, "warning")
    try:
        _email(md, alerts)
    except Exception as e:  # noqa: BLE001
        log.warning("email failed: %s", e)
    log.info("analyst done: %d posts, %d changes, applied=%s", len(rows), len(changes), enough)
    return md
