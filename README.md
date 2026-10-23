# newsbot

fully automated facebook news page (instagram supported too): finds stories with virality potential from the US, Europe and the Middle East,
writes the copy, builds a pastel card, publishes with caption + hashtags, and every 72 hours
reviews performance and retunes itself.

```
every slot (6x/day, UTC)                          every 72h
────────────────────────                          ─────────
google news (US/UK editions + europe & middle east searches) · trends · reddit
        ↓ claude tags region; anything outside US/Europe/ME dropped
                                                  pull insights for every post (reach, shares,
        ↓ cluster, need ≥2 outlets                saves, comments, follows) + account totals
claude: virality 0-10, risk 0-10, safety          → performance index per post
        ↓ blocked themes / rumours dropped        → compare by category, slot, region, image type,
blend signals × learned weights → pick 1             signal, vs previous window
        ↓                                         → bounded changes to state/tuning.json
claude: headline, highlight, caption, tags        → claude editor: copy rules, hashtag boost/block,
        ↓                                            headline length, image style, alerts
person story → wikimedia CC photo → cut-out       → report in reports/, emailed if smtp set
else        → higgsfield 1K scene                 → reach collapse = hard alert, no tuning
inset       → higgsfield 1K context image
        ↓
render 1080×1350 → facebook page (direct upload)
```

## the card

centred serif headline, opening words on a pastel highlight bar, optional `— attribution`
for quotes, hero image below (cut-out person or full-bleed scene fading into the page),
context image in a white-ringed circle. colours come from the story's category
(`palettes:` in config.yaml). put your logo at `assets/logo.png` or set `page.wordmark`.

## guardrails (hard-coded, the analyst can't touch them)

- story must be carried by ≥2 independent outlets and judged a confirmed fact — no rumours, leaks, polls
- blocked themes (communal violence, self-harm, sexual assault, sub judice guilt, etc.) never auto-post
- risk score above 3/10 → skipped
- copy may only use facts present in the source material; quotes only if the exact words are in it
- higgsfield is never asked to draw a real, identifiable person — named people only appear via
  licensed wikimedia photos (credited); names are scrubbed out of every generation prompt
- AI-generated visuals are disclosed in the caption
- analyst changes are capped at ±25% per cycle and need ≥8 matured posts; 20% of posts are exploration

## setup (facebook page)

### 1 · facebook (≈10 min)
1. Create the Facebook Page "WSE News" and add it to the WSE News business portfolio
   (business.facebook.com → Settings → Accounts → Pages).
2. developers.facebook.com → app **WSE Newsroom** → use case **Manage everything on your Page** →
   Permissions and features → Add: `pages_show_list, pages_read_engagement, pages_manage_posts,
   read_insights, business_management`.
3. business.facebook.com/settings → Users → **System users** → Add `wse-newsbot` (Admin) →
   Assign assets: the Page (full control) and the app → **Generate new token** → app WSE Newsroom →
   expiry **Never** → tick the five permissions above → copy it. This is `FB_ACCESS_TOKEN`.
4. The Page ID: open the Page → About → Page transparency (or Graph API Explorer: `me/accounts`
   with the token). This is `FB_PAGE_ID`.

The app can stay unpublished — it only posts to a Page your business owns.

### 2 · other keys (≈10 min)
- **Higgsfield**: cloud.higgsfield.ai → API keys → `HF_API_KEY_ID`, `HF_API_KEY_SECRET`
- **Anthropic**: console.anthropic.com → API keys → `ANTHROPIC_API_KEY`
- optional: `PEXELS_API_KEY` (fallback images), `SMTP_HOST/PORT/USER/PASS` (emailed reviews)
- no image hosting needed — cards upload straight to Facebook.

### 3 · github (≈5 min)
1. github.com → New repository → **private** → `wse-newsbot` → upload everything in this folder.
2. Settings → Secrets and variables → Actions → add: `FB_ACCESS_TOKEN, FB_PAGE_ID, HF_API_KEY_ID,
   HF_API_KEY_SECRET, ANTHROPIC_API_KEY`.
3. Settings → Actions → General → Workflow permissions → **Read and write**.

### 4 · go live
1. Actions → newsbot → Run workflow → `check` → should print the WSE News page.
2. Run workflow → `post` → in dry run it renders a card; download it from the run's artifacts.
3. After 2–3 days, edit `config.yaml` → `mode: "auto"`. It then posts 10–15 cards a day to the Page
   and reviews itself every 72h.

To switch to Instagram later: `platform: "instagram"` + the instagram secrets (see git history).

## files

```
config.yaml            hard settings (you edit)
state/tuning.json      learned settings (analyst edits)
state/newsbot.db       posts, insights, decisions, change log
reports/*.md           72h reviews
src/sources.py         discovery + heat signals
src/scorer.py          virality + safety scoring, story pick
src/writer.py          headline, caption, hashtags, image brief
src/imagery.py         wikimedia cut-out / higgsfield / stock fallback
src/higgsfield.py      higgsfield 1K generation
src/render.py          the card
src/instagram.py       hosting, publish, insights
src/analyst.py         72h review + retuning
main.py                entry point
```

## cost, roughly (6 posts/day)

claude ≈ 2 calls/post + 1 per review · higgsfield 2 images/post at 1K · everything else free tier.
