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

from src import analyst, facebook, imagery, instagram, scorer, sources, writer
from src.common import CFG, OUT, db, load_tuning, log, now, now_local
from src.render import render


def current_slot(force: bool) -> str | None:
    slots = CFG["posting"]["slots"]
    t = now_local()
    mins = t.hour * 60 + t.minute
    best = min(slots, key=lambda s: abs(int(s[:2]) * 60 + int(s[3:]) - mins))
    if force or abs(int(best[:2]) * 60 + int(best[3:]) - mins) <= 25:
        return best
    return None


def slot_active(slot: str) -> bool:
    """when the analyst lowers posts/day, the weakest slots go quiet first."""
    t = load_tuning()
    n = t["posts_per_day"] or CFG["posting"]["max_posts_per_day"]
    n = max(CFG["posting"].get("min_posts_per_day", 1), min(n, CFG["posting"]["max_posts_per_day"]))
    ranked = sorted(CFG["posting"]["slots"], key=lambda s: t["slot_weights"].get(s, 1.0), reverse=True)
    return slot in ranked[:n]


def post(force: bool = False) -> None:
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

    if CFG["mode"] != "auto":
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
    con.execute("""insert into posts (created_at, slot, story_key, title, category, region, headline,
                   caption, hashtags, image_source, palette, score, signals, ig_media_id, permalink,
                   status, note) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row + (mid, link, status, note))
    con.commit()


def main() -> None:
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
    elif cmd == "report":
        reps = sorted((analyst.REPORTS).glob("*.md"))
        print(reps[-1].read_text() if reps else "no reports yet")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
