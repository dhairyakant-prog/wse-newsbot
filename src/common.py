"""shared plumbing: config, state db, tuning file, llm calls, time."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
OUT = ROOT / "out"
STATE.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("newsbot")

UA = {"User-Agent": "Mozilla/5.0 (newsbot; +https://instagram.com)"}


# ── config ────────────────────────────────────────────────────
def load_config() -> dict:
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


CFG = load_config()
TZ = ZoneInfo(CFG["page"].get("timezone", "UTC"))


def now() -> datetime:
    return datetime.now(timezone.utc)


def now_local() -> datetime:
    return now().astimezone(TZ)


# ── tuning (the only thing the analyst may change) ────────────
DEFAULT_TUNING = {
    "category_weights": {k: 1.0 for k in
                         ["us", "europe", "middle_east", "business", "technology",
                          "entertainment", "sports", "science", "health"]},
    "region_mix": dict(CFG["coverage"]["target_mix"]),
    "slot_weights": {},                     # "08:30": 1.0 …  (empty = all equal)
    "posts_per_day": CFG["posting"].get("start_posts_per_day"),
    "virality_threshold": 4.5,              # 0–10, final blended score needed to post
    "signal_weights": {                     # how the final score blends signals
        "llm_virality": 0.5, "coverage": 0.2, "trend_match": 0.2,
        "reddit_heat": 0.0, "freshness": 0.1},
    "copy_guidance": [],                    # short learned rules for the writer
    "hashtag_blocklist": [],
    "hashtag_boost": [],
    "headline_max_words": 22,
    "image_style": "photo",                 # photo | illustration-led | text-only
    "updated_at": None,
}


def load_tuning() -> dict:
    p = STATE / "tuning.json"
    if p.exists():
        t = json.loads(p.read_text())
        merged = json.loads(json.dumps(DEFAULT_TUNING))
        merged.update(t)
        return merged
    return json.loads(json.dumps(DEFAULT_TUNING))


def save_tuning(t: dict) -> None:
    t["updated_at"] = now().isoformat()
    (STATE / "tuning.json").write_text(json.dumps(t, indent=2, ensure_ascii=False))


# ── state db ──────────────────────────────────────────────────
SCHEMA = """
create table if not exists posts (
  id integer primary key,
  created_at text, slot text, story_key text, title text, category text, region text,
  headline text, caption text, hashtags text, image_source text, palette text,
  score real, signals text, ig_media_id text, permalink text, status text, note text
);
create table if not exists seen (
  story_key text primary key, title text, first_seen text, decision text, reason text
);
create table if not exists insights (
  post_id integer, fetched_at text, age_hours real,
  reach integer, views integer, likes integer, comments integer, shares integer,
  saved integer, total_interactions integer, follows integer, profile_visits integer
);
create table if not exists account_daily (
  day text primary key, followers integer, reach integer, views integer,
  total_interactions integer
);
create table if not exists changes (
  id integer primary key, at text, field text, old text, new text, reason text
);
"""


def db() -> sqlite3.Connection:
    con = sqlite3.connect(STATE / "newsbot.db")
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


# ── llm ───────────────────────────────────────────────────────
_client = None


def llm(system: str, user: str, max_tokens: int | None = None) -> str:
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()          # ANTHROPIC_API_KEY from env
    r = _client.messages.create(
        model=CFG["llm"]["model"],
        max_tokens=max_tokens or CFG["llm"]["max_tokens"],
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def _parse_json_loose(raw: str):
    """accept: a json value, a value wrapped in prose/code fences, or several top-level
    objects in a row (returned as a list)."""
    raw = re.sub(r"```(?:json)?", "", raw)
    dec = json.JSONDecoder()
    vals, i = [], 0
    while i < len(raw):
        j = min([k for k in (raw.find("{", i), raw.find("[", i)) if k != -1], default=-1)
        if j == -1:
            break
        try:
            v, end = dec.raw_decode(raw, j)
        except json.JSONDecodeError:
            i = j + 1
            continue
        vals.append(v)
        i = end
    if not vals:
        raise ValueError(f"no json in llm output: {raw[:300]}")
    if len(vals) == 1:
        return vals[0]
    flat = []
    for v in vals:
        flat.extend(v if isinstance(v, list) else [v])
    return flat


def llm_json(system: str, user: str, max_tokens: int | None = None):
    """ask for json; tolerate fences, chatter and object-per-line output; retry once."""
    sys2 = system + "\n\nReturn ONLY valid JSON. No prose, no code fences."
    last = None
    for _ in range(2):
        raw = llm(sys2, user, max_tokens)
        try:
            return _parse_json_loose(raw)
        except ValueError as e:
            last = e
            log.warning("llm json parse failed, retrying: %s", e)
    raise last


def env(name: str, required: bool = True) -> str | None:
    v = os.environ.get(name)
    if required and not v:
        raise RuntimeError(f"missing env var {name}")
    return v


def hours_ago(h: float) -> datetime:
    return now() - timedelta(hours=h)
