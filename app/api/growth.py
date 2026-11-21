"""Leads CRM and the daily engagement round."""
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import growth, workers
from app.llm import llm_configured
from app.api.automation import require_llm
from app.api.opportunities import LOAD, serialize
from app.api.sources import creator_dict, norm_handle
from app.deps import current_user, get_session
from app.models import Conversation, Creator, DailyTask, Lead, Post, audit, now
from app.prefs import get_settings

router = APIRouter()
STAGES = ("new", "researched", "contacted", "replied", "call", "won", "lost")


async def lead_dict(session, lead: Lead) -> dict:
    conv = await session.scalar(select(Conversation.id).where(Conversation.lead_id == lead.id))
    return {c.name: getattr(lead, c.name) for c in Lead.__table__.columns} | {"conversation_id": conv}


async def load_lead(session, lead_id: int) -> Lead:
    lead = await session.get(Lead, lead_id, populate_existing=True)
    if lead is None:
        raise HTTPException(404, "lead not found")
    return lead


class LeadBody(BaseModel):
    handle: str = Field(min_length=1, max_length=100)
    name: str | None = Field(None, max_length=200)
    niche: str | None = Field(None, max_length=100)
    website: str | None = Field(None, max_length=500)
    notes: str | None = Field(None, max_length=5000)
    profile_text: str | None = Field(None, max_length=20000)


class LeadPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    niche: str | None = Field(None, max_length=100)
    website: str | None = Field(None, max_length=500)
    notes: str | None = Field(None, max_length=5000)
    profile_text: str | None = Field(None, max_length=20000)
    pitch: str | None = Field(None, max_length=2000)


@router.get("/leads")
async def list_leads(session=Depends(get_session)):
    return [await lead_dict(session, x) for x in await session.scalars(select(Lead).order_by(Lead.updated_at.desc()))]


@router.post("/leads")
async def add_lead(body: LeadBody, user=Depends(current_user), session=Depends(get_session)):
    lead = Lead(**(body.model_dump() | {"handle": norm_handle(body.handle)}))
    session.add(lead)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "lead already exists")
    audit(session, user, "lead_added", "lead", lead.id, handle=lead.handle)
    await session.commit()
    return await lead_dict(session, lead)


class LeadBulkBody(BaseModel):
    handles: str = Field(min_length=1, max_length=20000)
    niche: str | None = Field(None, max_length=100)


@router.post("/leads/bulk")
async def add_leads_bulk(body: LeadBulkBody, user=Depends(current_user), session=Depends(get_session)):
    """Add many businesses at once; each is researched (profile read + pitch drafted) in the background."""
    handles = growth.parse_handles(body.handles)[:100]
    existing = set(await session.scalars(select(Lead.handle).where(Lead.handle.in_(handles))))
    leads = [Lead(handle=h, niche=body.niche) for h in handles if h not in existing]
    session.add_all(leads)
    audit(session, user, "leads_bulk_added", "lead", None, added=len(leads))
    await session.commit()
    if llm_configured():
        for lead in leads:
            workers.enqueue_research(lead.id)
    return {"added": [x.handle for x in leads], "skipped": sorted(existing), "researching": llm_configured()}


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: int, session=Depends(get_session)):
    return await lead_dict(session, await load_lead(session, lead_id))


@router.patch("/leads/{lead_id}")
async def patch_lead(lead_id: int, body: LeadPatch, user=Depends(current_user), session=Depends(get_session)):
    lead = await load_lead(session, lead_id)
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(lead, k, v)
    audit(session, user, "lead_edited", "lead", lead.id, fields=sorted(changes))
    await session.commit()
    return await lead_dict(session, lead)


@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: int, user=Depends(current_user), session=Depends(get_session)):
    lead = await load_lead(session, lead_id)
    await session.delete(lead)
    audit(session, user, "lead_deleted", "lead", lead_id, handle=lead.handle)
    await session.commit()
    return {"ok": True}


@router.post("/leads/{lead_id}/research", dependencies=[Depends(require_llm)])
async def research(lead_id: int, session=Depends(get_session)):
    from app.llm import get_llm
    await load_lead(session, lead_id)
    await growth.research_lead(session, lead_id, get_llm("brain"))
    return await lead_dict(session, await load_lead(session, lead_id))


class StageBody(BaseModel):
    stage: Literal[STAGES]


@router.post("/leads/{lead_id}/stage")
async def set_stage(lead_id: int, body: StageBody, user=Depends(current_user), session=Depends(get_session)):
    lead = await load_lead(session, lead_id)
    before, lead.stage = lead.stage, body.stage
    audit(session, user, "lead_stage", "lead", lead.id, before=before, after=body.stage)
    await session.commit()
    return await lead_dict(session, lead)


@router.post("/leads/{lead_id}/contacted")
async def contacted(lead_id: int, user=Depends(current_user), session=Depends(get_session)):
    lead = await load_lead(session, lead_id)
    lead.stage, lead.contacted_at, lead.next_followup_at = "contacted", now(), now() + timedelta(days=3)
    audit(session, user, "lead_contacted", "lead", lead.id)
    await session.commit()
    return await lead_dict(session, lead)


@router.post("/leads/{lead_id}/followup-sent")
async def followup_sent(lead_id: int, user=Depends(current_user), session=Depends(get_session)):
    lead = await load_lead(session, lead_id)
    lead.followups_sent += 1
    lead.next_followup_at = now() + timedelta(days=4) if lead.followups_sent < len(lead.followups) else None
    audit(session, user, "lead_followup_sent", "lead", lead.id, n=lead.followups_sent)
    await session.commit()
    return await lead_dict(session, lead)


# ---------------------------------------------------------------- daily engagement round

@router.get("/engagement/today")
async def engagement_today(session=Depends(get_session)):
    target = (await get_settings(session))["engagement_daily_target"]
    day = growth.local_today()
    await growth.build_today(session, day, target)
    tasks = list(await session.scalars(select(DailyTask).where(DailyTask.day == day).order_by(DailyTask.id)))
    posts = {p.id: p for p in await session.scalars(select(Post).options(*LOAD).where(
        Post.id.in_([t.post_id for t in tasks if t.post_id])))}
    creators = {c.id: c for c in await session.scalars(select(Creator).where(
        Creator.id.in_([t.creator_id for t in tasks if t.creator_id])))}
    return {"day": day, **await growth.engagement_stats(session, day, target), "tasks": [
        {"id": t.id, "kind": t.kind, "done_at": t.done_at,
         "creator": creator_dict(creators[t.creator_id]) if t.creator_id in creators else None,
         "opportunity": serialize(posts[t.post_id]) if t.post_id in posts else None}
        for t in tasks]}


@router.post("/engagement/tasks/{task_id}/done")
async def task_done(task_id: int, user=Depends(current_user), session=Depends(get_session)):
    task = await session.get(DailyTask, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    if task.done_at is None:
        task.done_at = now()
        if task.creator_id and (c := await session.get(Creator, task.creator_id)):
            c.last_engaged_at = now()
        audit(session, user, "engagement_done", "task", task.id, kind=task.kind)
        await session.commit()
    return {"ok": True, "done_at": task.done_at}



@router.post("/engagement/fetch")
async def fetch_creator_posts(session=Depends(get_session)):
    """Read creators' latest posts now (also runs every morning at 6:30). Drafts follow in the background."""
    r = await workers._fetch_creators(session)
    return {k: v for k, v in r.items() if k != "new_post_ids"} | {"new": len(r["new_post_ids"])}


@router.get("/engagement/follows")
async def follows(session=Depends(get_session)):
    return await growth.follow_list(session, growth.local_today())


class FollowBody(BaseModel):
    action: Literal["followed", "followed_back", "no_follow_back"]


@router.post("/engagement/follows/{kind}/{item_id}")
async def mark_follow(kind: Literal["creator", "lead"], item_id: int, body: FollowBody,
                      user=Depends(current_user), session=Depends(get_session)):
    if await growth.mark_follow(session, user, kind, item_id, body.action) is None:
        raise HTTPException(404, f"{kind} not found")
    return {"ok": True}
