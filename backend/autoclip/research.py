"""SEO research for the user to read.

Google's and YouTube's terms decide the shape of this module. Grounded Gemini
results must be shown unchanged with their search suggestions and must not be
analysed, and YouTube search results must not be modified or used to derive new
data. So results go straight to the page, are never stored, and never reach a
model. The user reads them, picks an approach and types the keywords they want,
and only that choice goes to the local model.

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

GEMINI_MODEL = "gemini-3.5-flash-lite"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"
YOUTUBE_URL = "https://www.googleapis.com/youtube/v3"
YOUTUBE_RESULTS = 8
REQUEST_TIMEOUT_S = 120


class ResearchError(RuntimeError):
    """A research service could not answer."""


@dataclass
class WebSource:
    title: str
    url: str


@dataclass
class GeminiReport:
    text: str
    #: Google's search suggestions, shown as supplied, as its terms require.
    suggestions_html: str = ""
    sources: list[WebSource] = field(default_factory=list)


@dataclass
class YouTubeResult:
    title: str
    channel: str
    views: int
    url: str


def _call(
    url: str,
    api_key: str,
    body: dict[str, Any] | None = None,
    *,
    quota_message: str = "The daily quota ran out.",
) -> dict[str, Any]:
    # The key goes in a header, so it can't end up in a logged URL or an error.
    headers = {"X-Goog-Api-Key": api_key, "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data, headers)
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ResearchError(quota_message) from exc
        raise ResearchError(f"The service refused the request ({exc.code}).") from exc
    except (OSError, ValueError) as exc:
        raise ResearchError("The service could not be reached.") from exc


def gemini_report(topic: str, api_key: str) -> GeminiReport:
    """Ask Gemini, with live Google Search, for an SEO report on a topic."""
    prompt = load_prompt("seo_report_v1").replace("{topic}", topic)
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    answer = _call(
        f"{GEMINI_URL}/{GEMINI_MODEL}:generateContent",
        api_key,
        body,
        quota_message="The quota ran out. Live Google Search needs billing on your project.",
    )

    candidates = answer.get("candidates") or [{}]
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise ResearchError("Gemini returned no report.")

    grounding = candidates[0].get("groundingMetadata") or {}
    chunks = [chunk.get("web") or {} for chunk in grounding.get("groundingChunks") or []]
    return GeminiReport(
        text=text,
        suggestions_html=(grounding.get("searchEntryPoint") or {}).get("renderedContent", ""),
        sources=[
            WebSource(title=chunk.get("title") or chunk["uri"], url=chunk["uri"])
            for chunk in chunks
            if chunk.get("uri")
        ],
    )


def youtube_results(topic: str, api_key: str) -> list[YouTubeResult]:
    """Short videos YouTube returns for a topic, in YouTube's own order."""
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
    items = _call(f"{YOUTUBE_URL}/search?{query}", api_key).get("items") or []
    items = [item for item in items if item.get("id", {}).get("videoId") and item.get("snippet")]
    ids = [item["id"]["videoId"] for item in items]
    if not ids:
        return []

    stats_query = urllib.parse.urlencode({"part": "statistics", "id": ",".join(ids)})
    stats = _call(f"{YOUTUBE_URL}/videos?{stats_query}", api_key).get("items") or []
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
