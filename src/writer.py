"""copy: card headline + dek, caption, hashtags, alt text, image brief."""
from __future__ import annotations

import json

from .common import llm_json, load_tuning

SYSTEM = """You write for WSE News, a Facebook card-news page in the style of India's biggest card pages
(like Jist): people-led, plain, direct, factual. No hype words ("shocking", "breaking", "you won't
believe"), no emojis on the card. Every fact you write must appear in the source material given to
you — never add numbers, names, quotes or causes that aren't there. If sources disagree, use the
cautious version. Neutral on politics: describe what happened, not who is right. Attribute claims:
"X says", "X claims", "a viral post shows" — never state an unverified claim as fact."""

PROMPT = """Write the post for this story. Cover ONLY this one story — the outlet material may include
unrelated headlines from the same feed; ignore anything not about this exact event.

STORY: {title}
WHAT OUTLETS ARE REPORTING: {context}
OUTLETS: {outlets}
CATEGORY: {category}   REGION: {region}
TRENDING SEARCH (if any): {trend}
HOUSE RULES LEARNED FROM PAST PERFORMANCE:
{guidance}

The card is a full-bleed photo of the person with the headline at the bottom in 3 short lines,
each on its own box; the middle line (the hook) is on a red box. Write in English.

HEADLINE PATTERN (copy this exactly in spirit):
- lead with the person: "Name Says / Recalls / Reveals / Admits / Claims / Shares / Calls / Slams..."
  e.g. "Keerthy Suresh Says She Was Told To Avoid OTT As A Theatrical Star"
       "Puneet Issar Says He Lost Work For 6 Years After Amitabh Bachchan's Accident"
       "Delhi Man, 34, Says He Earns ₹80,000 A Month But Has No Savings"
       "Developer Says New Job Brings A PIP Within The First Month"
       "Prominent MAGA Voices Question Their Trump Support As Divisions Surface"
- for an ordinary person: describe them ("Texas Nurse", "London Commuter, 29", "Woman In Viral TikTok")
- the surprising detail (number, twist, admission) goes in the middle of the sentence
- 10-16 words, one sentence, no full stop, no quote marks, digits for numbers

Return JSON:
{{
 "headline": "follows the pattern above",
 "highlight_words": "integer — the hook: count words from the start of the headline up to the END of
                     the surprising detail (it is printed on the red box)",
 "attribution": null,
 "explainer": "1 sentence of background (not printed on the card; used for alt text)",
 "caption": "exactly 3 short sentences, a plain factual summary: who, what was said or happened, and
             one line of context or what's next. no question, no emojis, no hashtags.",
 "hashtags": ["exactly 5 tags without #: the person's name, the show/team/company/place, the topic,
              and one broad tag like EntertainmentNews, SportsNews, ViralNews or WorldNews. no party tags"],
 "alt_text": "one sentence describing the card for screen readers",
 "image": {{
   "subject_type": "person whenever ANY named public figure appears in the material above.
                    scene only if no named public figure is mentioned at all.",
   "person_name": "the single most relevant named public figure, as their exact Wikipedia article title.
                   must be named in the material above — never guess who is involved. null if none.",
   "scene_prompt": "a photo prompt used when there is no named person: an anonymous ordinary person who
                    matches the story (age, job, setting), face clearly visible, close or medium shot,
                    mid-moment — e.g. 'a tired software developer in his 30s at a desk at night, laptop
                    glow', 'a young woman at an airport gate looking at her phone'. fictional and generic:
                    NEVER a real, named or identifiable person, no uniforms implying a real official,
                    no text in image.",
   "inset_prompt": "one object or place that explains the story at a glance. same rules.",
   "stock_query": "2-4 word stock-photo search as last resort"
 }}
}}"""


def write(story: dict) -> dict:
    t = load_tuning()
    guidance = "\n".join(f"- {g}" for g in t["copy_guidance"]) or "- (none yet)"
    copy = llm_json(SYSTEM, PROMPT.format(
        title=story["title"], context=story["summary"][:900],
        outlets=", ".join(story["outlets"][:8]), category=story["category"],
        region="/".join(story["regions"]), trend=story.get("trend_query") or "none",
        guidance=guidance, hw=t["headline_max_words"]))
    try:
        copy["highlight_words"] = int(copy.get("highlight_words") or 6)
    except (TypeError, ValueError):
        copy["highlight_words"] = 6

    tags = [h.strip().lstrip("#").replace(" ", "") for h in copy.get("hashtags", [])]
    tags = [h for h in tags if h and h.lower() not in {b.lower() for b in t["hashtag_blocklist"]}]
    for b in t["hashtag_boost"]:
        if b not in tags and len(tags) < 15:
            tags.append(b)
    copy["hashtags"] = tags[:15]
    return copy


def compose_caption(copy: dict, story: dict, credits: list[str]) -> str:
    srcs = ", ".join(story["outlets"][:2])
    parts = [copy["caption"].strip(), "", f"via {srcs}" + (" · " + " · ".join(credits) if credits else "")]
    parts += ["", " ".join("#" + h for h in copy["hashtags"])]
    cap = "\n".join(parts)
    return cap[:2150]   # instagram caption limit is 2,200 chars


def facebook_caption(caption: str, copy: dict) -> str:
    """facebook: summary + credit line, then the 5 hashtags (jist pattern)."""
    body = caption.split("\n\n#")[0]
    tags = copy.get("hashtags", [])[:5]
    return body + ("\n\n" + " ".join("#" + t for t in tags) if tags else "")


def dump(copy: dict) -> str:
    return json.dumps(copy, ensure_ascii=False, indent=2)
