"""SEO research: a live OpenAI report, and YouTube results for the user to read.

The OpenAI report is the customer's own output, so Qwen may read it. It must never
become training material for a model, and its citations must stay visible and
clickable wherever it is shown.

YouTube's rules are stricter: search results must not be modified or used to
create derived data. So they go straight to the page, are never stored, and never
reach a model.

Only a short search topic leaves this machine. Transcripts, campaign rules and
account details do not.
"""

from __future__ import annotations

import html
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .providers.base import load_prompt

log = logging.getLogger(__name__)

OPENAI_URL = "https://api.openai.com/v1/responses"
OPENAI_MODEL = "gpt-5-nano"
#: Each search costs money, so a report stops after this many.
MAX_SEARCHES = 5
#: Includes the model's thinking, which can use all of it at the default effort.
MAX_REPORT_TOKENS = 6000

YOUTUBE_URL = "https://www.googleapis.com/youtube/v3"
YOUTUBE_RESULTS = 8
REQUEST_TIMEOUT_S = 180


class ResearchError(RuntimeError):
    """A research service could not answer."""


@dataclass
class Citation:
    title: str
    url: str
    #: The span of the report text the citation replaces when shown as a link.
    start: int
    end: int


@dataclass
class Report:
    text: str
    citations: list[Citation] = field(default_factory=list)


@dataclass
class YouTubeResult:
    title: str
    channel: str
    views: int
    url: str


def _call(
    url: str,
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
    *,
    quota_message: str = "The daily quota ran out.",
) -> dict[str, Any]:
    # Keys go in a header, so they can't end up in a logged URL or an error.
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data, {"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # YouTube reports a used-up quota as a 403, not a 429.
        detail = exc.fp.read().decode("utf-8", "replace") if exc.fp else ""
        if exc.code == 429 or "quotaExceeded" in detail:
            raise ResearchError(quota_message) from exc
        raise ResearchError(f"The service refused the request ({exc.code}).") from exc
    except (OSError, ValueError) as exc:
        raise ResearchError("The service could not be reached.") from exc


def openai_report(topic: str, api_key: str) -> Report:
    """Ask OpenAI, with live web search, for an SEO report on a topic."""
    answer = _call(
        OPENAI_URL,
        {"Authorization": f"Bearer {api_key}"},
        {
            "model": OPENAI_MODEL,
            "input": load_prompt("seo_report_v1").replace("{topic}", topic),
            "tools": [{"type": "web_search"}],
            "max_tool_calls": MAX_SEARCHES,
            "max_output_tokens": MAX_REPORT_TOKENS,
            "reasoning": {"effort": "low"},
        },
        quota_message="The OpenAI quota ran out. Check the credit on your API account.",
    )

    text = ""
    citations: list[Citation] = []
    for item in answer.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") != "output_text":
                continue
            offset = len(text)
            text += part.get("text", "")
            citations += [
                Citation(
                    title=note.get("title") or note["url"],
                    url=note["url"],
                    start=offset + note.get("start_index", 0),
                    end=offset + note.get("end_index", 0),
                )
                for note in part.get("annotations") or []
                if note.get("type") == "url_citation" and note.get("url")
            ]

    if not text.strip():
        raise ResearchError("OpenAI returned no report.")
    return Report(text=text, citations=citations)


def youtube_results(topic: str, api_key: str) -> list[YouTubeResult]:
    """Short videos YouTube returns for a topic, in YouTube's own order."""
    headers = {"X-Goog-Api-Key": api_key}
    query = urllib.parse.urlencode(
        {
            "part": "snippet",
            "q": topic,
            "type": "video",
            "videoDuration": "short",
            "maxResults": YOUTUBE_RESULTS,
            "relevanceLanguage": "en",
        }
    )
    items = _call(f"{YOUTUBE_URL}/search?{query}", headers).get("items") or []
    items = [item for item in items if item.get("id", {}).get("videoId") and item.get("snippet")]
    ids = [item["id"]["videoId"] for item in items]
    if not ids:
        return []

    stats_query = urllib.parse.urlencode({"part": "statistics", "id": ",".join(ids)})
    stats = _call(f"{YOUTUBE_URL}/videos?{stats_query}", headers).get("items") or []
    views = {item["id"]: int(item.get("statistics", {}).get("viewCount", 0)) for item in stats}

    return [
        YouTubeResult(
            title=html.unescape(item["snippet"].get("title", "")),
            channel=html.unescape(item["snippet"].get("channelTitle", "")),
            views=views.get(item["id"]["videoId"], 0),
            url=f"https://www.youtube.com/watch?v={item['id']['videoId']}",
        )
        for item in items
    ]
