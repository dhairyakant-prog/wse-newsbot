"""copy: card headline + dek, caption, hashtags, alt text, image brief."""
from __future__ import annotations

import json

from .common import llm_json, load_tuning

SYSTEM = """You write for a clean, calm social news page (WSE News). Voice: plain, direct, factual,
no hype words (no "shocking", "breaking", "you won't believe", no emojis on the card).
Short sentences. Every fact you write must appear in the source material given to you —
never add numbers, names, quotes or causes that aren't there. If sources disagree, use the
cautious version. Neutral on politics: describe what happened, not who is right."""

PROMPT = """Write the post for this story. Cover ONLY this one story — the outlet material may include
unrelated headlines from the same feed; ignore anything not about this exact event.

STORY: {title}
WHAT OUTLETS ARE REPORTING: {context}
OUTLETS: {outlets}
CATEGORY: {category}   REGION: {region}
TRENDING SEARCH (if any): {trend}
HOUSE RULES LEARNED FROM PAST PERFORMANCE:
{guidance}

The card is a serif headline, centred, with its opening words on a highlight bar,
then a short explainer underneath, over a photo. Write in English.

Return JSON:
{{
 "headline": "the card text. 12-{hw} words. one or two plain sentences that carry the whole news.
              if a named person said something notable AND their exact words appear in the material
              above, you may use it as a quote in double quotes; otherwise never use quote marks.",
 "explainer": "2-3 sentences, 35-50 words, printed under the headline. add what the headline does NOT
               say: background, why it matters, or what happens next. never repeat a fact, number,
               name or date already in the headline. flowing sentences, not a list of fragments.
               only facts from the material above.",
 "highlight_words": "integer — how many opening words go on the highlight bar (the hook — usually 2-5 words; they get the accent colour)",
 "attribution": "for a quote: 'Name, role' (e.g. 'Emmanuel Macron, President of France'); else null",
 "caption": "3-5 short lines. line 1 restates the news. then 2-3 lines of context.
             last line a genuine question to invite comments. no hashtags. at most one emoji.",
 "hashtags": ["8-12 tags without #: 2 broad, 4-6 topic-specific, 2-3 long-tail. no party tags"],
 "alt_text": "one sentence describing the card for screen readers",
 "image": {{
   "subject_type": "person if the story centres on one named public figure, else scene",
   "person_name": "their exact Wikipedia article title, else null",
   "scene_prompt": "a photo prompt for the MAIN image when there is no person, or as backup:
                    a concrete, literal scene that fits the story without implying anything false
                    (e.g. 'container ships waiting outside a Gulf port at dusk'). NEVER name or
                    describe a real identifiable person. no text in image.",
   "inset_prompt": "a photo prompt for the small context circle: one object or place that explains the
                    story at a glance (e.g. 'a petrol pump price board in a European town'). same rules.",
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
    srcs = ", ".join(story["outlets"][:3])
    parts = [copy["caption"].strip(), "", f"source: {srcs}", *credits]
    parts += ["", " ".join("#" + h for h in copy["hashtags"])]
    cap = "\n".join(parts)
    return cap[:2150]   # instagram caption limit is 2,200 chars


def facebook_caption(caption: str, copy: dict) -> str:
    """facebook: same text, hashtags trimmed to the 3 most specific ones."""
    body = caption.split("\n\n#")[0]
    tags = copy.get("hashtags", [])[2:5]
    return body + ("\n\n" + " ".join("#" + t for t in tags) if tags else "")


def dump(copy: dict) -> str:
    return json.dumps(copy, ensure_ascii=False, indent=2)
