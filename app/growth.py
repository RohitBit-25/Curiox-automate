"""Small-business leads (AI audit + pitch, sent by hand) and the daily manual engagement round."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, true
from sqlalchemy.exc import IntegrityError

from app import craft, pipeline
from app.config import settings
from app.content import creator_system
from app.models import BrandKnowledge, Creator, DailyTask, Lead, Post, audit
from app.prefs import get_settings

LEAD_SCHEMA = pipeline._obj({
    "summary": pipeline.STR,
    "rubric": {"type": "array", "items": pipeline._obj({
        "id": {"type": "string", "enum": [i["id"] for i in craft.RUBRIC["items"]]}, "score": pipeline.NUM, "note": pipeline.STR})},
    "problems": {"type": "array", "items": pipeline._obj({
        "problem": pipeline.STR, "evidence": pipeline.STR, "curiox_solution": pipeline.STR,
        "impact": {"type": "string", "enum": ["high", "medium", "low"]}})},
    "best_angle": pipeline.STR, "pitch": pipeline.STR, "followups": pipeline.STRS,
    "confidence": pipeline.NUM, "insufficient_info": pipeline.BOOL})


async def research_lead(session, lead_id: int, llm) -> Lead | None:
    lead = await session.get(Lead, lead_id)
    if lead is None:
        return None
    voice = (await get_settings(session))["voice"]
    kn = [r for r in await session.scalars(select(BrandKnowledge))
          if r.kind == "profile" or (r.kind == "project" and r.verified)]
    d = await llm.json(creator_system(voice), pipeline.render(
        "lead_audit.txt", offer=voice.get("offer") or "(not set: software, automation and AI for small businesses)",
        knowledge="\n".join(f"- [{r.kind}] {r.title}: {r.content}" for r in kn) or "none",
        handle=lead.handle, name=lead.name or "unknown", niche=lead.niche or "unknown", website=lead.website or "none",
        notes=lead.notes or "none", profile=lead.profile_text or "(nothing pasted)", rubric=craft.rubric_for_prompt(),
    ), LEAD_SCHEMA, "medium")
    lead.research = d
    lead.rubric_total = craft.rubric_total(d["rubric"])
    lead.pitch = craft.scrub(d["pitch"])
    lead.followups = [craft.scrub(f) for f in d["followups"][:2]]
    if lead.stage == "new":
        lead.stage = "researched"
    audit(session, "system", "lead_researched", "lead", lead.id, rubric_total=lead.rubric_total)
    await session.commit()
    return lead


def local_today() -> date:
    return datetime.now(ZoneInfo(settings.TIMEZONE)).date()


async def build_today(session, day: date, target: int) -> None:
    """Pick today's round once: top pending third-party opportunities, then least-recently engaged creators."""
    if await session.scalar(select(func.count()).select_from(DailyTask).where(DailyTask.day == day)):
        return
    opps = (await session.execute(select(Post.id, Post.creator_id).where(
        Post.source == "manual_link", Post.status.in_(["pending", "approved"]))
        .order_by(Post.relevance_score.desc().nulls_last()).limit(target // 2))).all()
    used = {c for _, c in opps if c}
    creators = list(await session.scalars(select(Creator.id).where(
        Creator.active, Creator.id.not_in(used) if used else true())
        .order_by(Creator.last_engaged_at.asc().nulls_first(), Creator.handle).limit(target - len(opps))))
    session.add_all([DailyTask(day=day, kind="opportunity", post_id=p) for p, _ in opps] +
                    [DailyTask(day=day, kind="creator", creator_id=c) for c in creators])
    try:
        await session.commit()
    except IntegrityError:  # a concurrent request built it first
        await session.rollback()


def streak(done_by_day: dict[date, int], today: date, min_done: int) -> int:
    """Consecutive days hitting min_done. Today counts only once it's hit; until then yesterday anchors it."""
    d = today if done_by_day.get(today, 0) >= min_done else today - timedelta(days=1)
    n = 0
    while done_by_day.get(d, 0) >= min_done:
        n += 1
        d -= timedelta(days=1)
    return n


async def engagement_stats(session, today: date, target: int) -> dict:
    rows = (await session.execute(select(DailyTask.day, func.count()).where(
        DailyTask.done_at.is_not(None), DailyTask.day >= today - timedelta(days=60)).group_by(DailyTask.day))).all()
    done = dict(rows)
    return {"done": done.get(today, 0), "target": target, "streak": streak(done, today, max(1, target // 2))}
