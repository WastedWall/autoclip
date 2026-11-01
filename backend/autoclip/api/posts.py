"""Post kits, SEO research, and the record of what was posted."""

from __future__ import annotations

import asyncio
import csv
import io
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import config, postkit, research
from ..db import store
from ..db.models import Post, new_id
from ..providers import ProviderError, build_provider
from .clips import clip_words
from .schemas import (
    PostIn,
    PostKitIn,
    PostKitStateOut,
    PostOut,
    PostResultsIn,
    PostRowOut,
    TopicIn,
    TopicOut,
    YouTubeResultOut,
)

router = APIRouter(prefix="/api", tags=["posts"])


async def _clip_and_job(clip_id: str):
    clip = await asyncio.to_thread(store.get_clip, clip_id)
    job = await asyncio.to_thread(store.get_job, clip.job_id) if clip else None
    if clip is None or job is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    return clip, job


async def _clip_text(clip_id: str) -> str:
    return " ".join(word.text for word in await clip_words(clip_id))


def _secret(name: str, label: str) -> str:
    key = config.get_secret(name)
    if not key:
        raise HTTPException(status_code=400, detail=f"Add your {label} key in Settings.")
    return key


async def _state(clip_id: str) -> PostKitStateOut:
    data = await asyncio.to_thread(store.get_post_kit, clip_id) or {}
    posts = await asyncio.to_thread(store.list_clip_posts, clip_id)
    return PostKitStateOut(
        report=data.get("report"),
        approaches=data.get("approaches", []),
        kit=data.get("kit"),
        posts=[PostOut.of(post) for post in posts],
    )


@router.get("/clips/{clip_id}/post-kit", response_model=PostKitStateOut)
async def get_post_kit(clip_id: str) -> PostKitStateOut:
    await _clip_and_job(clip_id)
    return await _state(clip_id)


@router.post("/clips/{clip_id}/topic", response_model=TopicOut)
async def suggest_topic(clip_id: str) -> TopicOut:
    await _clip_and_job(clip_id)
    try:
        provider = build_provider(settings=config.load())
        return TopicOut(topic=await postkit.suggest_topic(provider, await _clip_text(clip_id)))
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/clips/{clip_id}/research", response_model=PostKitStateOut)
async def research_clip(clip_id: str, payload: TopicIn) -> PostKitStateOut:
    """Get a live report from OpenAI, then let Qwen fit it to this clip."""
    await _clip_and_job(clip_id)
    key = _secret(config.OPENAI_RESEARCH_KEY, "OpenAI research")
    try:
        report = await asyncio.to_thread(research.openai_report, payload.topic.strip(), key)
        approaches = await postkit.suggest_approaches(
            build_provider(settings=config.load()),
            clip_text=await _clip_text(clip_id),
            report=report.text,
        )
    except (research.ResearchError, ProviderError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    data = await asyncio.to_thread(store.get_post_kit, clip_id) or {}
    updated = data | {"report": asdict(report), "approaches": approaches}
    await asyncio.to_thread(store.save_post_kit, clip_id, updated)
    return await _state(clip_id)


@router.post("/clips/{clip_id}/post-kit", response_model=PostKitStateOut)
async def write_post_kit(clip_id: str, payload: PostKitIn) -> PostKitStateOut:
    """Write a kit from the approach and keywords the user chose."""
    _, job = await _clip_and_job(clip_id)
    clips = job.settings.get("clips", {})
    data = await asyncio.to_thread(store.get_post_kit, clip_id) or {}
    try:
        kit = await postkit.write_kit(
            build_provider(settings=config.load()),
            clip_text=await _clip_text(clip_id),
            brief=clips.get("brief", ""),
            report=(data.get("report") or {}).get("text", ""),
            approach=payload.approach,
            keywords=payload.keywords.strip(),
            brand=clips.get("disclosure_brand", ""),
            required_tags=clips.get("required_tags", ""),
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await asyncio.to_thread(store.save_post_kit, clip_id, data | {"kit": kit.to_dict()})
    return await _state(clip_id)


@router.post("/research/youtube", response_model=list[YouTubeResultOut])
async def youtube_research(payload: TopicIn) -> list[YouTubeResultOut]:
    """YouTube's results for the user to read. They are never stored or sent to a model."""
    key = _secret(config.YOUTUBE_KEY, "YouTube")
    try:
        results = await asyncio.to_thread(research.youtube_results, payload.topic.strip(), key)
    except research.ResearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return [YouTubeResultOut(**asdict(result)) for result in results]


@router.post("/clips/{clip_id}/reveal", status_code=204)
async def reveal_file(clip_id: str) -> None:
    """Show the clip's latest export in Explorer, ready to drag into an upload page."""
    await _clip_and_job(clip_id)
    exports = await asyncio.to_thread(store.list_exports, clip_id)
    existing = [Path(record.path) for record in exports if Path(record.path).exists()]
    if not existing:
        raise HTTPException(status_code=409, detail="Export this clip first.")
    if sys.platform != "win32":
        raise HTTPException(status_code=400, detail=f"The file is at {existing[-1]}.")
    subprocess.Popen(["explorer", f"/select,{existing[-1]}"])


@router.put("/clips/{clip_id}/posts/{platform}", response_model=PostOut)
async def mark_posted(clip_id: str, platform: str, payload: PostIn) -> PostOut:
    await _clip_and_job(clip_id)
    if platform not in postkit.PLATFORMS:
        raise HTTPException(status_code=404, detail="Unknown platform.")

    post = Post(
        id=new_id(),
        clip_id=clip_id,
        platform=platform,
        url=payload.url.strip(),
        approach=payload.approach,
        title=payload.title,
        caption=payload.caption,
    )
    return PostOut.of(await asyncio.to_thread(store.save_post, post))


async def _rows() -> list[PostRowOut]:
    """One row per platform for every clip the user kept or posted."""
    clips = await asyncio.to_thread(store.list_postable_clips)
    rows: list[PostRowOut] = []
    for clip in clips:
        posts = await asyncio.to_thread(store.list_clip_posts, clip.id)
        by_platform = {post.platform: post for post in posts}
        for platform in postkit.PLATFORMS:
            post = by_platform.get(platform)
            rows.append(
                PostRowOut(
                    clip_id=clip.id,
                    job_id=clip.job_id,
                    clip_title=clip.title,
                    platform=platform,
                    post=PostOut.of(post) if post else None,
                )
            )
    return rows


@router.get("/posts", response_model=list[PostRowOut])
async def list_posts() -> list[PostRowOut]:
    return await _rows()


@router.patch("/posts/{post_id}", response_model=PostOut)
async def update_post(post_id: str, payload: PostResultsIn) -> PostOut:
    post = await asyncio.to_thread(store.get_post, post_id)
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found.")

    await asyncio.to_thread(
        store.update_post_results,
        post_id,
        views_48h=payload.views_48h,
        views_7d=payload.views_7d,
        submitted=payload.submitted,
    )
    updated = await asyncio.to_thread(store.get_post, post_id)
    return PostOut.of(updated or post)


def _cell(value: object) -> object:
    # A leading = or + would run as a formula when the file opens in a spreadsheet.
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{value}"
    return value


@router.get("/posts.csv")
async def download_posts() -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["clip", "platform", "approach", "url", "posted_at", "views_48h", "views_7d", "submitted"]
    )
    for row in await _rows():
        post = row.post
        if post:
            writer.writerow(
                [
                    _cell(value)
                    for value in (
                        row.clip_title,
                        row.platform,
                        post.approach,
                        post.url,
                        post.posted_at,
                        post.views_48h,
                        post.views_7d,
                        post.submitted,
                    )
                ]
            )
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="posts.csv"'},
    )
