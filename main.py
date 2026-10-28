"""newsbot — run one step.

  python main.py post        # called at each slot: discover → score → write → image → render → publish
  python main.py post --now  # ignore the slot schedule (manual run)
  python main.py analyze     # the 72h review (no-op if not due; --force to run anyway)
  python main.py report      # print the last review
  python main.py check       # setup check: shows the facebook page + instagram account the token sees
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

from src import alerts, analyst, facebook, imagery, instagram, scorer, sources, writer
from src.common import CFG, OUT, db, load_tuning, log, now, now_local
from src.render import render


def current_slot(force: bool) -> str | None:
    slots = CFG["posting"]["slots"]
    t = now_local()
    mins = t.hour * 60 + t.minute
    def dist(s):                                   # minutes apart on a 24h clock (handles midnight)
        d = abs(int(s[:2]) * 60 + int(s[3:]) - mins)
        return min(d, 1440 - d)
    best = min(slots, key=dist)
    tol = CFG["posting"].get("slot_tolerance_minutes", 50)
    if force or dist(best) <= tol:                 # github cron can start late
        return best
    return None


def slot_active(slot: str) -> bool:
    """when the analyst lowers posts/day, the weakest slots go quiet first."""
    t = load_tuning()
    n = t["posts_per_day"] or CFG["posting"]["max_posts_per_day"]
    n = max(CFG["posting"].get("min_posts_per_day", 1), min(n, CFG["posting"]["max_posts_per_day"]))
    ranked = sorted(CFG["posting"]["slots"], key=lambda s: t["slot_weights"].get(s, 1.0), reverse=True)
    return slot in ranked[:n]


def post(force: bool = False, preview: bool = False) -> None:
    slot = current_slot(force)
    if not slot:
        log.info("no slot right now")
        return
    if not force and not slot_active(slot):
        log.info("slot %s paused by tuning", slot)
        return
    con = db()
    last = con.execute("select created_at from posts where status='posted' order by id desc limit 1").fetchone()
    if last and not force and (now() - datetime.fromisoformat(last["created_at"])).total_seconds() < \
            CFG["posting"]["min_gap_minutes"] * 60:
        log.info("too soon after last post")
        return

    if not preview and post_queued(con, slot):
        return

    stories = sources.discover()
    scored = scorer.score(stories)
    story = scorer.pick(scored)
    if not story:
        con.execute("insert into posts (created_at, slot, status, note) values (?,?,?,?)",
                    (now().isoformat(), slot, "skipped_low_score", "nothing cleared the bar"))
        con.commit()
        return

    copy = writer.write(story)
    t = load_tuning()
    img = imagery.build(copy.get("image", {}), t["image_style"])
    caption = writer.compose_caption(copy, story, img["credits"])

    stamp = now_local().strftime("%Y%m%d_%H%M")
    path = render(copy, story["category"], img["hero"], img["inset"], OUT / f"{stamp}_{story['key']}.jpg")
    (OUT / f"{stamp}_{story['key']}.txt").write_text(caption)
    signals = {k: story.get(k, 0) for k in ("llm_virality", "coverage", "trend_match", "reddit_heat", "freshness")}
    row = (now().isoformat(), slot, story["key"], story["title"], story["category"],
           story["regions"][0] if story["regions"] else "other", copy["headline"], caption,
           " ".join(copy["hashtags"]), img["source"], story["category"], story["final"],
           json.dumps({**signals, "explore": story.get("explore", False)}))

    if CFG["mode"] != "auto" or preview:
        con.execute("""insert into posts (created_at, slot, story_key, title, category, region, headline,
                       caption, hashtags, image_source, palette, score, signals, status, note)
                       values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row + ("dry_run", str(path)))
        con.commit()
        prev = OUT.parent / "previews"
        prev.mkdir(exist_ok=True)
        import shutil
        shutil.copy(path, prev / path.name)
        (prev / (path.stem + ".txt")).write_text(caption)
        for old in sorted(prev.glob("*.jpg"))[:-20]:        # keep the last 20
            old.unlink(); (prev / (old.stem + ".txt")).unlink(missing_ok=True)
        log.info("dry run → %s", path)
        return

    try:
        if CFG["platform"] == "facebook":
            mid, link = facebook.publish(path, writer.facebook_caption(caption, copy))
            status, note = "posted", "facebook"
        else:
            url = instagram.host_image(path)
            mid, link = instagram.publish(url, caption, copy.get("alt_text", copy["headline"]))
            status, note = "posted", url
    except Exception as e:  # noqa: BLE001
        mid, link, status, note = None, None, "failed", str(e)[:500]
        log.error("publish failed: %s", e)
        low = str(e).lower()
        hint = ("The Facebook token or Page access was revoked — regenerate the system user token in "
                "Meta Business settings and update FB_ACCESS_TOKEN." if ("token" in low or "permission" in low
                or "oauth" in low or "session" in low) else "")
        alerts.raise_("Facebook post failed", f"{e}\n\n{hint}\n\nStory: {story['title']}")
    con.execute("""insert into posts (created_at, slot, story_key, title, category, region, headline,
                   caption, hashtags, image_source, palette, score, signals, ig_media_id, permalink,
                   status, note) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row + (mid, link, status, note))
    con.commit()


# ── manual queue: stories you asked for, posted at the right slot ──
QUEUE = OUT.parent / "state" / "queue.json"
REGION_HOURS = {            # utc hours when each audience is most active
    "us": set(range(13, 24)) | {0, 1},
    "europe": set(range(6, 22)),
    "middle_east": set(range(4, 20)),
}


def post_queued(con, slot: str) -> bool:
    if not QUEUE.exists():
        return False
    q = json.loads(QUEUE.read_text() or "[]")
    hour = int(slot[:2])
    for i, item in enumerate(q):
        if hour not in REGION_HOURS.get(item.get("region", "us"), set(range(24))):
            continue
        copy = item["copy"]
        img = imagery.build(item.get("image", {}), "photo")
        stamp = now_local().strftime("%Y%m%d_%H%M")
        path = render(copy, item["category"], img["hero"], img["inset"], OUT / f"{stamp}_queued.jpg")
        caption = item["caption"]
        if img["credits"] and img["credits"][0] not in caption:
            caption = caption.replace("\n\n#", "\n" + "\n".join(img["credits"]) + "\n\n#", 1)
        row = (now().isoformat(), slot, item.get("key", "queued"), item["title"], item["category"],
               item.get("region", "us"), copy["headline"], caption, "", img["source"], item["category"], 10.0,
               json.dumps({"queued": True}))
        try:
            if CFG["mode"] != "auto":
                raise RuntimeError("dry_run")
            mid, link = facebook.publish(path, writer.facebook_caption(caption, {"hashtags": item.get("hashtags", [])}))
            status, note = "posted", "queued"
        except Exception as e:  # noqa: BLE001
            mid, link, status, note = None, None, ("dry_run" if str(e) == "dry_run" else "failed"), str(e)[:300]
            if status == "failed":
                alerts.raise_("Facebook post failed (queued story)", f"{e}\n\nStory: {item['title']}")
        con.execute("""insert into posts (created_at, slot, story_key, title, category, region, headline,
                       caption, hashtags, image_source, palette, score, signals, ig_media_id, permalink,
                       status, note) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", row + (mid, link, status, note))
        con.commit()
        q.pop(i)
        QUEUE.write_text(json.dumps(q, indent=2, ensure_ascii=False))
        log.info("queued story %s → %s %s", item["title"], status, link or note)
        return True
    log.info("queue has %d item(s), none for this slot's audience", len(q))
    return False


def _run() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "post"
    force = "--now" in sys.argv or "--force" in sys.argv
    if cmd == "post":
        post(force)
    elif cmd == "analyze":
        md = analyst.run(force)
        if md:
            print(md)
    elif cmd == "check":
        import json as _j
        print(_j.dumps((facebook if CFG["platform"] == "facebook" else instagram).whoami(), indent=2))
        from src import higgsfield
        print("higgsfield:", higgsfield.auth_check())
    elif cmd == "preview":
        post(True, preview=True)
    elif cmd == "test-image":
        from src import higgsfield
        img = higgsfield.generate("a quiet European city square at dawn, wet cobblestones", "4:5")
        prev = OUT.parent / "previews"
        prev.mkdir(exist_ok=True)
        if img:
            img.save(prev / "test_image.jpg", quality=90)
            print("higgsfield OK", img.size)
        else:
            alerts.raise_("Higgsfield test image failed", "see run log", "error")
    elif cmd == "test-alert":
        alerts.raise_("Test alert", "This is a test. If you got this as an email, alerts work. Close this issue.", "warning")
    elif cmd == "report":
        reps = sorted((analyst.REPORTS).glob("*.md"))
        print(reps[-1].read_text() if reps else "no reports yet")
    else:
        print(__doc__)


def stale_check() -> None:
    """no successful post for 6h while live → something is silently wrong."""
    if CFG["mode"] != "auto":
        return
    con = db()
    last = con.execute("select created_at from posts where status='posted' order by id desc limit 1").fetchone()
    if last and (now() - datetime.fromisoformat(last["created_at"])).total_seconds() > 6 * 3600:
        alerts.raise_("No post in the last 6 hours",
                      f"Last successful post: {last['created_at']} UTC. Check recent runs, credits and the "
                      "Facebook token.")


def main() -> None:
    try:
        _run()
        if (sys.argv[1] if len(sys.argv) > 1 else "post") == "post":
            stale_check()
    except Exception as e:  # noqa: BLE001
        alerts.from_exception(" ".join(sys.argv[1:]) or "post", e)
    finally:
        alerts.flush()
    if alerts.has_errors():
        sys.exit(1)


if __name__ == "__main__":
    main()
