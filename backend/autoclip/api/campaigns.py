"""Campaign data and rules.

Reading a link only touches the one public page the user pasted. See
``autoclip.campaigns`` for why nothing here logs in or crawls.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from .. import campaigns
from ..campaigns import Campaign, CampaignDetails, CampaignError
from ..config import load as load_settings
from ..providers import ProviderError, build_provider
from .schemas import CampaignLinkIn, CampaignOut, CampaignReadingOut, CampaignRulesIn

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def _out(campaign: Campaign) -> CampaignOut:
    details = campaigns.read_details(campaign)
    return CampaignOut.of(campaign, campaigns.read_rules(campaign), details)


async def _saved(campaign_id: str) -> Campaign:
    campaign = await asyncio.to_thread(campaigns.load, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return campaign


@router.post("", response_model=CampaignOut)
async def read_campaign(payload: CampaignLinkIn) -> CampaignOut:
    try:
        campaign_id = campaigns.campaign_id(payload.link)
        html = await asyncio.to_thread(campaigns.fetch_page, campaign_id)
        campaign = campaigns.parse_campaign(html, campaign_id)
    except CampaignError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await asyncio.to_thread(campaigns.save, campaign)
    return _out(campaign)


@router.get("", response_model=list[CampaignOut])
async def list_campaigns() -> list[CampaignOut]:
    saved = await asyncio.to_thread(campaigns.list_saved)
    return [_out(campaign) for campaign in saved]


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(campaign_id: str) -> CampaignOut:
    return _out(await _saved(campaign_id))


@router.put("/{campaign_id}/rules", response_model=CampaignOut)
async def put_rules(campaign_id: str, payload: CampaignRulesIn) -> CampaignOut:
    campaign = await _saved(campaign_id)
    await asyncio.to_thread(campaigns.write_rules, campaign, payload.rules)
    details = CampaignDetails(payload.brand.strip(), payload.required_tags.strip())
    await asyncio.to_thread(campaigns.write_details, campaign, details)
    return _out(campaign)


@router.post("/{campaign_id}/reading", response_model=CampaignReadingOut)
async def read_rules_with_model(campaign_id: str) -> CampaignReadingOut:
    campaign = await _saved(campaign_id)
    rules = campaigns.read_rules(campaign)
    try:
        lines = await campaigns.reading(build_provider(settings=load_settings()), campaign, rules)
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CampaignReadingOut(lines=lines)
