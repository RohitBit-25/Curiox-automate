"""Small-business leads (AI audit + pitch, sent by hand) and the daily manual engagement round.

Other accounts are only ever READ here (Business Discovery). Comments, follows and first DMs are done by hand.
"""
import logging
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, true
from sqlalchemy.exc import IntegrityError

from app import craft, pipeline
from app.config import settings
from app.content import creator_system
from app.models import Account, BrandKnowledge, Creator, DailyTask, Lead, Post, audit, now
from app.platforms import PlatformError, get_adapter
from app.prefs import get_settings

log = logging.getLogger(__name__)
# Business Discovery needs instagram_manage_insights; without it Meta says #10 / #200.
NO_PERMISSION = {10, 200}
FOLLOW_DAILY = 15  # ponytail: fixed; Instagram flags new accounts that follow much faster than this
HANDLE_RE = re.compile(r"^[a-z0-9._]{1,30}$")
NOT_HANDLES = {"p", "reel", "reels", "tv", "explore", "stories"}


def parse_handles(text: str) -> list[str]:
    """Handles from pasted text: @names, bare names or instagram.com links, separated by spaces/commas/lines."""
    out = []
    for tok in re.split(r"[\s,;]+", text):
        tok = tok.strip().rstrip("/")
        if "instagram.com/" in tok:
            tok = tok.split("instagram.com/", 1)[1].split("/")[0].split("?")[0]
        h = tok.lstrip("@").lower()
        if HANDLE_RE.match(h) and h not in NOT_HANDLES and h not in out:
            out.append(h)
    return out


async def _adapter(session, adapter_factory=None):
    a = await session.scalar(select(Account).where(Account.status == "active").order_by(Account.id))
    return (adapter_factory or get_adapter)(a.platform, a.access_token) if a else None


def _shortcode(permalink: str) -> str | None:
    m = re.search(r"/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", permalink or "")
    return m.group(1) if m else None


async def fetch_creator_posts(session, adapter_factory=None, days: int = 7, per_creator: int = 2,
                              limit: int = 50) -> dict:
    """Pull creators' recent posts (Business Discovery) into the engagement queue, to be drafted by the AI."""
    adapter = await _adapter(session, adapter_factory)
    if adapter is None:
        return {"status": "no_account", "new_post_ids": [], "failed": []}
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (await session.execute(select(Creator.id, Creator.handle).where(Creator.active)
                                  .order_by(Creator.last_engaged_at.asc().nulls_first()).limit(limit))).all()
    new_ids, failed = [], []
    for creator_id, handle in rows:
        try:
            bd = await adapter.business_discovery(handle, per_creator + 1)
        except PlatformError as e:
            if e.code in NO_PERMISSION:
                return {"status": "no_permission", "error": str(e), "new_post_ids": [], "failed": []}
            failed.append(handle)  # personal/private account, renamed, or a transient error
            log.warning("business discovery failed for @%s: %s", handle, e)
            continue
        c = await session.get(Creator, creator_id)
        c.display_name = c.display_name or bd.get("name")
        for m in bd["media"][:per_creator]:
            code, caption = _shortcode(m.get("permalink")), (m.get("caption") or "").strip()
            ts = datetime.strptime(m["timestamp"], "%Y-%m-%dT%H:%M:%S%z") if m.get("timestamp") else None
            if not code or len(caption) < 10 or (ts and ts < since):
                continue
            if await session.scalar(select(Post.id).where(Post.platform == "instagram", Post.external_id == code)):
                continue
            p = Post(platform="instagram", source="manual_link", external_id=code, url=m["permalink"],
                     author_handle=handle, creator_id=creator_id, text=caption[:5000], published_at=ts)
            session.add(p)
            await session.flush()
            new_ids.append(p.id)
    audit(session, "system", "creator_posts_fetched", "creator", None, new=len(new_ids), failed=failed)
    await session.commit()
    return {"status": "ok", "new_post_ids": new_ids, "failed": failed}


async def enrich_lead(session, lead: Lead, adapter_factory=None) -> bool:
    """Fill an empty lead profile from their public business profile. False if it can't be read."""
    adapter = await _adapter(session, adapter_factory)
    if adapter is None:
        return False
    try:
        bd = await adapter.business_discovery(lead.handle, 6)
    except PlatformError as e:
        log.info("can't read @%s: %s", lead.handle, e)
        return False
    lead.name, lead.website = lead.name or bd.get("name"), lead.website or bd.get("website")
    posts = "\n".join(f"- {(m.get('caption') or '').strip()[:300]}" for m in bd["media"] if m.get("caption"))
    lead.profile_text = (f"Bio: {bd.get('biography') or '(empty)'}\nWebsite: {bd.get('website') or 'none'}\n"
                         f"Followers: {bd.get('followers_count')}, posts: {bd.get('media_count')}\n"
                         f"Recent captions:\n{posts or '(none)'}")
    return True

LEAD_SCHEMA = pipeline._obj({
    "summary": pipeline.STR,
    "rubric": {"type": "array", "items": pipeline._obj({
        "id": {"type": "string", "enum": [i["id"] for i in craft.RUBRIC["items"]]}, "score": pipeline.NUM, "note": pipeline.STR})},
    "problems": {"type": "array", "items": pipeline._obj({
        "problem": pipeline.STR, "evidence": pipeline.STR, "curiox_solution": pipeline.STR,
        "impact": {"type": "string", "enum": ["high", "medium", "low"]}})},
    "best_angle": pipeline.STR, "pitch": pipeline.STR, "followups": pipeline.STRS,
    "confidence": pipeline.NUM, "insufficient_info": pipeline.BOOL})


async def research_lead(session, lead_id: int, llm, adapter_factory=None) -> Lead | None:
    lead = await session.get(Lead, lead_id)
    if lead is None:
        return None
    if not lead.profile_text:
        await enrich_lead(session, lead, adapter_factory)
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


# ---------------------------------------------------------------- daily follow list (you follow by hand)

def _follow_row(kind: str, x) -> dict:
    return {"kind": kind, "id": x.id, "handle": x.handle, "name": x.display_name if kind == "creator" else x.name,
            "followed_at": x.followed_at, "followed_back": x.followed_back}


async def follow_list(session, today: date) -> dict:
    start = datetime.combine(today, datetime.min.time(), ZoneInfo(settings.TIMEZONE))
    followed_today = sum([await session.scalar(select(func.count()).select_from(M).where(M.followed_at >= start))
                          for M in (Creator, Lead)])
    room = max(0, FOLLOW_DAILY - followed_today)
    creators = list(await session.scalars(select(Creator).where(Creator.active, Creator.followed_at.is_(None))
                                          .order_by(Creator.created_at).limit(room)))
    leads = list(await session.scalars(select(Lead).where(Lead.stage != "lost", Lead.followed_at.is_(None))
                                       .order_by(Lead.rubric_total.desc().nulls_last(), Lead.created_at)
                                       .limit(room - len(creators))))
    due = now() - timedelta(days=3)  # give people a few days to follow back
    check = [(k, x) for k, M in (("creator", Creator), ("lead", Lead))
             for x in await session.scalars(select(M).where(M.followed_at <= due, M.followed_back.is_(None)))]
    back = [await session.scalar(select(func.count()).select_from(M).where(M.followed_back.is_(True)))
            for M in (Creator, Lead)]
    total = [await session.scalar(select(func.count()).select_from(M).where(M.followed_back.is_not(None)))
             for M in (Creator, Lead)]
    return {"daily_limit": FOLLOW_DAILY, "followed_today": followed_today,
            "to_follow": [_follow_row("creator", c) for c in creators] + [_follow_row("lead", x) for x in leads],
            "check_back": [_follow_row(k, x) for k, x in check],
            "followed_back": sum(back), "checked": sum(total)}


async def mark_follow(session, user: str, kind: str, id: int, action: str):
    x = await session.get(Creator if kind == "creator" else Lead, id)
    if x is None:
        return None
    if action == "followed":
        x.followed_at, x.followed_back = x.followed_at or now(), None
    else:
        x.followed_back = action == "followed_back"
    audit(session, user, f"follow_{action}", kind, id, handle=x.handle)
    await session.commit()
    return x
