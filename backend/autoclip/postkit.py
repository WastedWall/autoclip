"""Post kits: the title and caption for posting a clip.

The local model reads the OpenAI report and suggests an approach per clip. The user
picks one and sets the keywords, then the model writes the title and caption. YouTube
results never reach it (see ``research``). The disclosure and required tags are added
here in code, so they can't be dropped or reworded by the model.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .providers import DetectionConfig, LLMProvider, ProviderError
from .providers.base import extract_json_object, load_prompt

PLATFORMS = ("tiktok", "instagram", "youtube")
APPROACHES = ("search", "curiosity", "opinion")

MAX_TITLE_CHARS = 100
MAX_HASHTAGS = 3


@dataclass
class PostKit:
    approach: str
    keywords: str
    title: str
    caption: str
    category: str = ""
    language: str = ""
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def hashtags(keywords: str) -> list[str]:
    """Turn the user's comma-separated keywords into a few hashtags."""
    tags = []
    for keyword in keywords.split(","):
        tag = re.sub(r"[^\w]", "", keyword, flags=re.UNICODE)
        if tag and f"#{tag}" not in tags:
            tags.append(f"#{tag}")
    return tags[:MAX_HASHTAGS]


def build_caption(text: str, *, brand: str, keywords: str, required_tags: str) -> str:
    """Assemble a caption with the disclosure first, as the campaign rules require."""
    tags = " ".join([*hashtags(keywords), required_tags]).strip()
    disclosure = f"#Ad Paid partnership with {brand}." if brand else ""
    return "\n\n".join(part for part in (disclosure, text.strip(), tags) if part)


async def suggest_topic(provider: LLMProvider, clip_text: str) -> str:
    """Name a neutral search topic for a clip. This is all that leaves the machine."""
    raw = await provider._complete(
        load_prompt("post_topic_v1"), clip_text, DetectionConfig(temperature=0.2)
    )
    try:
        topic = extract_json_object(raw).get("topic")
    except ValueError as exc:
        raise ProviderError("The model returned no topic.") from exc
    if not isinstance(topic, str) or not topic.strip():
        raise ProviderError("The model returned no topic.")
    return topic.strip()


async def suggest_approaches(
    provider: LLMProvider, *, clip_text: str, report: str
) -> list[dict[str, Any]]:
    """Fit the report to one clip: one suggestion per approach, in a fixed order."""
    user = f"SEO report:\n{report}\n\nClip transcript:\n{clip_text}"
    raw = await provider._complete(
        load_prompt("post_approaches_v1"), user, DetectionConfig(temperature=0.4)
    )
    try:
        items = extract_json_object(raw).get("approaches")
    except ValueError as exc:
        raise ProviderError("The model returned no approaches.") from exc
    if not isinstance(items, list):
        raise ProviderError("The model returned no approaches.")

    by_key = {item.get("key"): item for item in items if isinstance(item, dict)}
    suggestions = []
    for key in APPROACHES:
        item = by_key.get(key)
        if item is None or not isinstance(item.get("headline"), str):
            raise ProviderError("The model left out an approach.")
        keywords = item.get("keywords")
        suggestions.append(
            {
                "key": key,
                "headline": item["headline"].strip(),
                "keywords": [str(k) for k in keywords] if isinstance(keywords, list) else [],
                "reason": str(item.get("reason") or ""),
            }
        )
    return suggestions


async def write_kit(
    provider: LLMProvider,
    *,
    clip_text: str,
    brief: str,
    report: str,
    approach: str,
    keywords: str,
    brand: str,
    required_tags: str,
) -> PostKit:
    """Write a kit for the approach and keywords the user chose."""
    user = (
        f"Approach: {approach}\nKeywords: {keywords or 'none'}\n\n"
        f"Campaign brief:\n{brief or 'none'}\n\nSEO report:\n{report or 'none'}\n\n"
        f"Clip transcript:\n{clip_text}"
    )
    raw = await provider._complete(
        load_prompt("post_kit_v1"), user, DetectionConfig(temperature=0.4)
    )
    try:
        data = extract_json_object(raw)
    except ValueError as exc:
        raise ProviderError("The model returned no usable post kit.") from exc

    title, text = data.get("title"), data.get("text")
    if not isinstance(title, str) or not isinstance(text, str) or not title or not text:
        raise ProviderError("The model returned no usable post kit.")

    flags = data.get("flags")
    return PostKit(
        approach=approach,
        keywords=keywords,
        title=title.strip()[:MAX_TITLE_CHARS],
        caption=build_caption(text, brand=brand, keywords=keywords, required_tags=required_tags),
        category=str(data.get("category") or ""),
        language=str(data.get("language") or ""),
        flags=[str(flag) for flag in flags] if isinstance(flags, list) else [],
    )
