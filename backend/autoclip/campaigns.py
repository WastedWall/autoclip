"""Campaign data and rules.

A pasted Content Rewards link is read once: that one public page, with no login.
Joining, reference files and posting stay with the user, because Whop's terms ban
automated access. Campaign rules are hidden until you join, so the user pastes
them in and they are stored next to a dated copy of the public data.

Page text is written by strangers. It is shown, saved and handed to a model as
data, and nothing in it decides what this code does.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from . import paths
from .providers import DetectionConfig, LLMProvider, ProviderError
from .providers.base import extract_json_object, load_prompt

log = logging.getLogger(__name__)

USER_AGENT = "VideoClipper/0.1"

_LINK = re.compile(r"contentrewards\.com/(?:c/)?(?:campaigns|discover)/([0-9a-f-]{36})")
_FLIGHT_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', re.DOTALL)
_BLOCKED_TOPICS = re.compile(
    r"\b(gambl\w*|casinos?|sportsbooks?|betting|crypto\w*|bitcoin|memecoins?)\b", re.IGNORECASE
)

#: From contentrewards.com/ftc-compliance, which applies to every campaign.
STANDING_RULES = [
    (
        "Disclose near the top of the caption on every platform: #Ad, Advertisement, "
        "Sponsored by [Brand] or Paid partnership with [Brand]."
    ),
    (
        "Not accepted: #Collab, #Sp, #Spon, a brand tag alone, a disclosure among hashtags, "
        "at the end of a long caption, only in comments, or behind 'more'."
    ),
    "Disclose at the beginning of the video too, and in thumbnails and overlays.",
    (
        "Instagram: Paid Partnership tag where applicable. TikTok: Paid Partnership or "
        "Sponsored Content toggle where available. YouTube: Branded content setting, under "
        "Show more, and a disclosure in the description."
    ),
    "A clip without a proper disclosure can be rejected, and its payment is lost.",
    "Never buy views or engagement. That is a permanent ban.",
]


class CampaignError(RuntimeError):
    """The link or the page could not be used."""


@dataclass
class Campaign:
    id: str
    name: str
    host: str
    status: str
    launched: str
    description: str
    payouts: list[dict]
    budget_cents: int
    spent_cents: int
    creators: int
    approved_clips: int
    total_views: int
    needs_application: bool
    rule_count: int
    requirement_count: int
    reference_count: int

    @property
    def locked_items(self) -> int:
        return self.rule_count + self.requirement_count + self.reference_count


@dataclass
class CampaignDetails:
    """What only the user can supply, used word for word in clips and captions."""

    brand: str = ""
    required_tags: str = ""


def campaign_id(link: str) -> str:
    match = _LINK.search(link)
    if not match:
        raise CampaignError("This is not a Content Rewards campaign link.")
    return match.group(1)


def fetch_page(campaign: str) -> str:
    url = f"https://contentrewards.com/c/campaigns/{campaign}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except OSError as exc:
        raise CampaignError(f"Could not read the campaign page: {exc}") from exc


def parse_campaign(html: str, expected_id: str) -> Campaign:
    """Pull the campaign record out of the data embedded in the page."""
    try:
        data = "".join(json.loads(f'"{chunk}"') for chunk in _FLIGHT_CHUNK.findall(html))
        counts_at = data.find('"briefCounts"')
        record_at = data.rfind('"data":{', 0, counts_at)
        if counts_at == -1 or record_at == -1:
            raise CampaignError("The page has no campaign data. Its layout may have changed.")
        record, _ = json.JSONDecoder().raw_decode(data[record_at + len('"data":') :])
    except ValueError as exc:
        raise CampaignError("The page data is unreadable. Its layout may have changed.") from exc

    if record.get("id") != expected_id:
        raise CampaignError("The page holds a different campaign. Its layout may have changed.")

    # Fields can be present but null, for example the launch date of an unlaunched
    # campaign, so every read falls back on falsy values rather than missing keys.
    config = record.get("configuration") or {}
    metrics = record.get("metrics") or {}
    counts = record.get("briefCounts") or {}
    return Campaign(
        id=record["id"],
        name=record.get("name") or "Unnamed campaign",
        host=(record.get("organization") or {}).get("name") or "Unknown",
        status=record.get("status") or "unknown",
        launched=(record.get("launchDate") or "")[:10],
        description=record.get("description") or "",
        payouts=[payout for payout in config.get("payoutModel") or [] if _is_payout(payout)],
        budget_cents=config.get("budget") or 0,
        spent_cents=metrics.get("budgetSpentCents") or 0,
        creators=metrics.get("creatorCount") or 0,
        approved_clips=metrics.get("approvedSubmissionCount") or 0,
        total_views=metrics.get("totalViews") or 0,
        needs_application=bool(record.get("requiresApplication")),
        rule_count=counts.get("contentRules") or 0,
        requirement_count=counts.get("creatorRequirements") or 0,
        reference_count=counts.get("references") or 0,
    )


def _is_payout(payout: dict) -> bool:
    numbers = (payout.get(key) for key in ("rate", "minPayout", "maxPayout"))
    return isinstance(payout.get("platform"), str) and all(isinstance(n, int) for n in numbers)


def views_needed(cents: int, rate_cents: int) -> int:
    return round(cents / rate_cents * 1000) if rate_cents else 0


def blocked_topics(campaign: Campaign, rules: str) -> list[str]:
    """Excluded topics named by a campaign. Gambling and crypto have no exceptions."""
    text = f"{campaign.name}\n{campaign.host}\n{campaign.description}\n{rules}"
    return sorted({match.group(0).lower() for match in _BLOCKED_TOPICS.finditer(text)})


def _folder(campaign: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-f-]{36}", campaign):
        return None
    return next(paths.campaigns_dir().glob(f"*-{campaign[:8]}"), None)


def _new_folder(campaign: Campaign) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", campaign.name.lower()).strip("-")[:50].strip("-")
    return paths.campaigns_dir() / f"{slug}-{campaign.id[:8]}"


def save(campaign: Campaign) -> None:
    """Store the latest data plus a dated copy, so old rules stay on record."""
    folder = _folder(campaign.id) or _new_folder(campaign)
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(asdict(campaign), indent=2)
    (folder / "campaign.json").write_text(text, encoding="utf-8")
    (folder / f"data-{date.today().isoformat()}.json").write_text(text, encoding="utf-8")


def load(campaign: str) -> Campaign | None:
    folder = _folder(campaign)
    if folder is None:
        return None
    return Campaign(**json.loads((folder / "campaign.json").read_text(encoding="utf-8")))


def list_saved() -> list[Campaign]:
    """Saved campaigns, most recently read first."""
    files = paths.campaigns_dir().glob("*/campaign.json")
    newest = sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)
    return [Campaign(**json.loads(path.read_text(encoding="utf-8"))) for path in newest]


def read_rules(campaign: Campaign) -> str:
    folder = _folder(campaign.id)
    path = folder / "rules.txt" if folder else None
    return path.read_text(encoding="utf-8").strip() if path and path.exists() else ""


def write_rules(campaign: Campaign, rules: str) -> None:
    folder = _folder(campaign.id) or _new_folder(campaign)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "rules.txt").write_text(f"{rules.strip()}\n", encoding="utf-8")


def read_details(campaign: Campaign) -> CampaignDetails:
    folder = _folder(campaign.id)
    path = folder / "details.json" if folder else None
    if not path or not path.exists():
        return CampaignDetails()
    return CampaignDetails(**json.loads(path.read_text(encoding="utf-8")))


def write_details(campaign: Campaign, details: CampaignDetails) -> None:
    folder = _folder(campaign.id) or _new_folder(campaign)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "details.json").write_text(json.dumps(asdict(details), indent=2), encoding="utf-8")


def build_brief(campaign: Campaign, rules: str) -> str:
    """The text clip selection gets. It holds the campaign's own words only."""
    return f"{campaign.name}\n\n{campaign.description}\n\n{rules}".strip()


async def reading(provider: LLMProvider, campaign: Campaign, rules: str) -> list[str]:
    """Ask the model for a short reading of the rules. The rules themselves always win."""
    config = DetectionConfig(temperature=0.2)
    raw = await provider._complete(
        load_prompt("campaign_reading_v1"), build_brief(campaign, rules), config
    )
    try:
        lines = extract_json_object(raw).get("lines")
    except ValueError as exc:
        raise ProviderError("The model returned no usable reading.") from exc
    if not isinstance(lines, list):
        raise ProviderError("The model returned no usable reading.")
    return list(dict.fromkeys(str(line) for line in lines))[:10]
