"""Content calendar, editor, media uploads, and the pure craft scorer."""
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import content as c
from app import craft, workers
from app.api.automation import require_llm
from app.deps import current_user, get_session
from app.models import ContentItem, audit, now

router = APIRouter()
Format = Literal["reel", "carousel", "image", "story"]


async def load(session, item_id: int) -> ContentItem:
    item = await session.get(ContentItem, item_id, populate_existing=True)
    if item is None:
        raise HTTPException(404, "content item not found")
    return item


class NewItem(BaseModel):
    format: Format
    pillar: str = Field(min_length=1, max_length=100)
    angle: str = Field(default="", max_length=2000)
    scheduled_at: datetime | None = None
    caption: str = Field(default="", max_length=2200)
    post_type: Literal["proof", "teach", "opinion", "story", "offer"] = "teach"
    goal: Literal["saves", "shares", "comments", "follows"] = "saves"


@router.post("/content")
async def create_item(body: NewItem, user=Depends(current_user), session=Depends(get_session)):
    """Manual item (works without an LLM key)."""
    when = body.scheduled_at
    week = c.monday((when.astimezone(c.TZ()) if when else datetime.now(c.TZ())).date())
    item = ContentItem(**body.model_dump(), week_start=week,
                       status="drafted" if body.caption or body.format == "story" else "idea")
    session.add(item)
    await session.flush()
    audit(session, user, "content_created", "content", item.id)
    await session.commit()
    return c.content_dict(item)


class PlanBody(BaseModel):
    week_start: date
    brief: str = Field(default="", max_length=5000)
    count: int = Field(default=5, ge=3, le=7)


@router.post("/content/plan", status_code=202, dependencies=[Depends(require_llm)])
async def plan(body: PlanBody, user=Depends(current_user)):
    if body.week_start.weekday() != 0:
        raise HTTPException(422, "week_start must be a Monday")
    workers.enqueue_plan(body.week_start.isoformat(), body.brief, body.count, user)
    return {"queued": True}


@router.get("/content")
async def list_items(week_start: date | None = None, status: str | None = None, limit: int = 200,
                     session=Depends(get_session)):
    q = select(ContentItem).order_by(ContentItem.scheduled_at.asc().nulls_last(), ContentItem.id).limit(min(limit, 500))
    if week_start:
        q = q.where(ContentItem.week_start == week_start)
    if status:
        q = q.where(ContentItem.status == status)
    return [c.content_dict(i) for i in await session.scalars(q)]


@router.get("/content/{item_id}")
async def get_item(item_id: int, session=Depends(get_session)):
    return c.content_dict(await load(session, item_id))


class PatchItem(BaseModel):
    format: Format | None = None
    pillar: str | None = Field(None, min_length=1, max_length=100)
    angle: str | None = Field(None, max_length=2000)
    scheduled_at: datetime | None = None
    caption: str | None = Field(None, max_length=2200)
    hashtags: list[str] | None = Field(None, max_length=10)


@router.patch("/content/{item_id}")
async def patch_item(item_id: int, body: PatchItem, user=Depends(current_user), session=Depends(get_session)):
    item = await load(session, item_id)
    if item.status not in c.EDITABLE:
        raise HTTPException(409, f"item is {item.status}; it can't be edited now")
    changes = body.model_dump(exclude_unset=True)
    if "hashtags" in changes:
        changes["hashtags"] = [t.strip().lstrip("#") for t in changes["hashtags"] if t.strip()]
    if "scheduled_at" in changes and changes["scheduled_at"]:
        changes["week_start"] = c.monday(changes["scheduled_at"].astimezone(c.TZ()).date())
    for k, v in changes.items():
        setattr(item, k, v)
    if item.status == "approved" and {"caption", "hashtags", "format"} & changes.keys():
        item.status, item.approved_by, item.approved_at = "drafted", None, None
        audit(session, user, "content_unapproved", "content", item.id)
    if item.status == "idea" and item.caption:
        item.status = "drafted"
    audit(session, user, "content_edited", "content", item.id, fields=sorted(changes))
    await session.commit()
    return c.content_dict(item)


@router.post("/content/{item_id}/draft", dependencies=[Depends(require_llm)])
async def draft(item_id: int, session=Depends(get_session)):
    from app.llm import get_llm
    item = await load(session, item_id)
    if item.status not in ("idea", "drafted", "failed"):
        raise HTTPException(409, f"item is {item.status}")
    await c.draft_item(session, item_id, get_llm())
    return c.content_dict(await load(session, item_id))


def _approve(session, item: ContentItem, user: str) -> list[str]:
    if item.status not in ("idea", "drafted"):
        return [f"item is {item.status}"]
    if problems := c.approval_problems(item):
        return problems
    item.status, item.approved_by, item.approved_at, item.error = "approved", user, now(), None
    audit(session, user, "content_approved", "content", item.id)
    return []


@router.post("/content/{item_id}/approve")
async def approve(item_id: int, user=Depends(current_user), session=Depends(get_session)):
    item = await load(session, item_id)
    if problems := _approve(session, item, user):
        raise HTTPException(422, {"message": "can't approve yet", "issues": problems})
    await session.commit()
    return c.content_dict(item)


class WeekBody(BaseModel):
    week_start: date


@router.post("/content/approve-week")
async def approve_week(body: WeekBody, user=Depends(current_user), session=Depends(get_session)):
    approved, skipped = [], []
    for item in await session.scalars(select(ContentItem).where(
            ContentItem.week_start == body.week_start, ContentItem.status.in_(["idea", "drafted"]))):
        problems = _approve(session, item, user)
        (skipped.append({"id": item.id, "reason": "; ".join(problems)}) if problems else approved.append(item.id))
    await session.commit()
    return {"approved": approved, "skipped": skipped}


@router.post("/content/{item_id}/retry")
async def retry(item_id: int, user=Depends(current_user), session=Depends(get_session)):
    item = await load(session, item_id)
    if item.status != "failed" or not item.approved_by:
        raise HTTPException(409, "only failed, previously approved items can be retried")
    item.status, item.container_id, item.container_created_at, item.error = "approved", None, None, None
    audit(session, user, "content_retry", "content", item.id)
    await session.commit()
    return c.content_dict(item)


@router.delete("/content/{item_id}")
async def delete(item_id: int, user=Depends(current_user), session=Depends(get_session)):
    item = await load(session, item_id)
    if item.status in ("processing", "publishing", "published"):
        raise HTTPException(409, f"item is {item.status}")
    await session.delete(item)
    audit(session, user, "content_deleted", "content", item_id, angle=item.angle)
    await session.commit()
    return {"ok": True}


@router.put("/content/{item_id}/media")
async def upload_media(item_id: int, request: Request, kind: Literal["image", "video"] = "image",
                       user=Depends(current_user), session=Depends(get_session)):
    item = await load(session, item_id)
    if item.status not in c.EDITABLE:
        raise HTTPException(409, f"item is {item.status}")
    name = await c.save_upload(request, item, kind)
    audit(session, user, "media_uploaded", "content", item.id, file=name)
    await session.commit()
    return c.content_dict(item)


@router.delete("/content/{item_id}/media/{name}")
async def delete_media(item_id: int, name: str, user=Depends(current_user), session=Depends(get_session)):
    item = await load(session, item_id)
    if item.status not in c.EDITABLE:
        raise HTTPException(409, f"item is {item.status}")
    item.media = [m for m in item.media if m["file"] != name]
    audit(session, user, "media_removed", "content", item.id, file=name)
    await session.commit()
    return c.content_dict(item)


class ScoreBody(BaseModel):
    hook: str | None = Field(None, max_length=500)
    caption: str | None = Field(None, max_length=2200)
    hashtags: list[dict] | None = None


@router.post("/tools/score")
async def score(body: ScoreBody):
    tags = body.hashtags or []
    return {
        "hook": craft.hook_score(body.hook) if body.hook else None,
        "classified": craft.classify_hook(body.hook) if body.hook else None,
        "caption": craft.caption_lint(body.caption, [t.get("tag", "") for t in tags]) if body.caption else None,
        "tells": craft.ai_tells(body.caption) if body.caption else None,
        "hashtags": craft.hashtag_issues(tags) if tags else [],
    }
