"""problem alerts → github issue (github emails the repo owner instantly) + failed run.

any module calls alerts.raise_(title, detail, severity). at the end of a run, flush():
  - one github issue per problem type; if an open one exists, it gets a comment instead
    (so you get an email per occurrence without a pile of duplicate issues)
  - severity "error" makes the run fail, which also triggers github's own failure email
"""
from __future__ import annotations

import os
import traceback

import requests

from .common import log, now

_problems: list[dict] = []


def raise_(title: str, detail: str = "", severity: str = "error") -> None:
    log.error("ALERT [%s] %s — %s", severity, title, detail[:300])
    if not any(p["title"] == title for p in _problems):
        _problems.append({"title": title, "detail": detail, "severity": severity})


def from_exception(where: str, e: BaseException) -> None:
    msg = str(e)
    low = msg.lower()
    if "credit balance" in low or "billing" in low or "insufficient" in low:
        raise_("Anthropic credits exhausted", f"Top up at console.anthropic.com → Billing.\n\n{msg}")
    elif "authentication" in low or "invalid x-api-key" in low:
        raise_("Anthropic API key rejected", f"Check ANTHROPIC_API_KEY in GitHub secrets.\n\n{msg}")
    else:
        raise_(f"Bot crashed in {where}", "".join(traceback.format_exception(e))[-3000:])


def has_errors() -> bool:
    return any(p["severity"] == "error" for p in _problems)


def _gh(method: str, path: str, **kw):
    tok, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (tok and repo):
        return None
    r = requests.request(method, f"https://api.github.com/repos/{repo}{path}", timeout=30,
                         headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}, **kw)
    return r.json() if r.content else None


def flush() -> None:
    if not _problems:
        return
    open("alerted", "w").write("1")          # tells the workflow the bot already reported it
    run = ""
    if os.environ.get("GITHUB_RUN_ID"):
        run = (f"\n\nRun log: {os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
               f"{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    try:
        open_issues = _gh("GET", "/issues", params={"state": "open", "labels": "bot-alert", "per_page": 50}) or []
    except Exception:  # noqa: BLE001
        open_issues = []
    for p in _problems:
        body = f"**{p['severity'].upper()}** · {now().strftime('%Y-%m-%d %H:%M UTC')}\n\n{p['detail']}{run}"
        title = f"⚠ {p['title']}"
        try:
            existing = next((i for i in open_issues if i.get("title") == title), None)
            if existing:
                _gh("POST", f"/issues/{existing['number']}/comments", json={"body": body})
            else:
                _gh("POST", "/issues", json={"title": title, "body": body + "\n\nClose this issue once fixed.",
                                             "labels": ["bot-alert"],
                                             "assignees": [os.environ.get("GITHUB_REPOSITORY_OWNER", "")]})
        except Exception as e:  # noqa: BLE001
            log.error("could not open alert issue: %s", e)
