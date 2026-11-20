from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.deps import get_session
from app import growth as growth_mod
from app.config import settings
from app.llm import llm_configured
from app.models import (Account, AccountSnapshot, Approval, ContentItem, Conversation, EngagementEvent, FunnelRule,
                        FunnelSend, Lead, Message, Post, aware, now)
from app.prefs import get_settings

router = APIRouter()


async def overview(session) -> dict:
    by_status = dict((await session.execute(select(Post.status, func.count()).group_by(Post.status))).all())
    decisions = dict((await session.execute(
        select(Approval.decision, func.count()).group_by(Approval.decision))).all())
    decided = decisions.get("approved", 0) + decisions.get("rejected", 0)
    avg = await session.scalar(select(func.avg(Post.relevance_score)).where(
        Post.status.in_(["pending", "approved", "rejected", "published"])))
    languages = dict((await session.execute(select(Post.language, func.count())
                                            .where(Post.language.is_not(None)).group_by(Post.language))).all())
    skips = (await session.execute(select(Post.skip_reason, func.count()).where(Post.status == "skipped")
                                   .group_by(Post.skip_reason).order_by(func.count().desc()).limit(5))).all()
    published = await session.scalar(select(func.count()).select_from(EngagementEvent)
                                     .where(EngagementEvent.status == "published"))
    accounts = [{"id": a.id, "platform": a.platform, "handle": a.handle, "status": a.status,
                 "paused_reason": a.paused_reason} for a in await session.scalars(select(Account))]
    return {
        "counts_by_status": by_status,
        "approval_rate": round(decisions.get("approved", 0) / decided, 3) if decided else 0,
        "avg_relevance": round(avg, 3) if avg is not None else None,
        "languages": languages,
        "top_skip_reasons": [{"reason": r, "count": n} for r, n in skips],
        "published": published,
        "accounts": accounts,
    }


async def weekly(session, weeks: int = 8) -> list[dict]:
    # ponytail: in-Python bucketing for SQLite/PG portability; move to date_trunc SQL if rows > ~100k
    since = datetime.now(UTC) - timedelta(days=weeks * 7)

    def wk(dt: datetime) -> str:
        y, w, _ = dt.isocalendar()
        return f"{y}-W{w:02d}"

    rows: dict[str, Counter] = {}
    def add(dt, key):
        rows.setdefault(wk(dt), Counter())[key] += 1

    for created, status in (await session.execute(select(Post.created_at, Post.status).where(Post.created_at >= since))).all():
        add(created, "discovered")
        if status not in ("new", "skipped"):
            add(created, "queued")
    for created, decision in (await session.execute(
            select(Approval.created_at, Approval.decision).where(Approval.created_at >= since))).all():
        add(created, decision)
    for (created,) in (await session.execute(select(EngagementEvent.created_at).where(
            EngagementEvent.created_at >= since, EngagementEvent.status == "published"))).all():
        add(created, "published")

    keys = ("discovered", "queued", "approved", "rejected", "published")
    return [{"week": w, **{k: c[k] for k in keys}} for w, c in sorted(rows.items())]


@router.get("/analytics/overview")
async def get_overview(session=Depends(get_session)):
    return await overview(session)


@router.get("/analytics/weekly")
async def get_weekly(weeks: int = 8, session=Depends(get_session)):
    return await weekly(session, min(max(weeks, 1), 52))


# ---------------------------------------------------------------- v2: growth, content, funnels, leads, today


def _er(interactions, reach):
    return round(interactions / reach, 4) if reach else None


@router.get("/analytics/growth")
async def growth_series(days: int = 90, session=Depends(get_session)):
    since = growth_mod.local_today() - timedelta(days=min(max(days, 7), 365))
    rows = await session.scalars(select(AccountSnapshot).where(AccountSnapshot.day >= since).order_by(AccountSnapshot.day))
    return [{"day": r.day, "followers": r.followers, "reach": r.metrics.get("reach"), "views": r.metrics.get("views"),
             "accounts_engaged": r.metrics.get("accounts_engaged"), "total_interactions": r.metrics.get("total_interactions"),
             "er": _er(r.metrics.get("total_interactions") or 0, r.metrics.get("reach"))} for r in rows]


@router.get("/analytics/content")
async def content_stats(session=Depends(get_session)):
    items = []
    for i in await session.scalars(select(ContentItem).where(ContentItem.status == "published")
                                   .order_by(ContentItem.published_at.desc())):
        m = i.insights or {}
        interactions = sum(m.get(k) or 0 for k in ("likes", "comments", "saved", "shares"))
        items.append({"id": i.id, "format": i.format, "pillar": i.pillar, "post_type": i.post_type, "angle": i.angle,
                      "published_at": i.published_at, "permalink": i.permalink, "reach": m.get("reach"),
                      "views": m.get("views"), "likes": m.get("likes"), "comments": m.get("comments"),
                      "saved": m.get("saved"), "shares": m.get("shares"), "er": _er(interactions, m.get("reach"))})

    def by(key):
        groups: dict[str, list] = {}
        for x in items:
            groups.setdefault(x[key], []).append(x)
        out = []
        for k, xs in groups.items():
            reach = [x["reach"] for x in xs if x["reach"] is not None]
            ers = [x["er"] for x in xs if x["er"] is not None]
            out.append({"key": k, "posts": len(xs), "avg_reach": round(sum(reach) / len(reach)) if reach else None,
                        "avg_er": round(sum(ers) / len(ers), 4) if ers else None})
        return sorted(out, key=lambda g: -(g["avg_reach"] or 0))
    return {"items": items, "by_pillar": by("pillar"), "by_format": by("format")}


@router.get("/analytics/funnels")
async def funnel_stats(session=Depends(get_session)):
    since = now() - timedelta(days=30)
    out = []
    for r in await session.scalars(select(FunnelRule).order_by(FunnelRule.id)):
        sends = list(await session.scalars(select(FunnelSend).where(FunnelSend.rule_id == r.id,
                                                                    FunnelSend.created_at >= since)))
        days = Counter(aware(s.created_at).date().isoformat() for s in sends if s.status == "sent")
        out.append({"rule_id": r.id, "name": r.name, "active": r.active,
                    "sent_30d": sum(s.status == "sent" for s in sends),
                    "failed_30d": sum(s.status == "failed" for s in sends),
                    "by_day": [{"day": d, "n": n} for d, n in sorted(days.items())]})
    return out


@router.get("/analytics/leads")
async def lead_stats(session=Depends(get_session)):
    return dict((await session.execute(select(Lead.stage, func.count()).group_by(Lead.stage))).all())


async def _kpis(session) -> dict:
    snaps = list(await session.scalars(select(AccountSnapshot).order_by(AccountSnapshot.day.desc()).limit(30)))
    with_f = [s for s in snaps if s.followers is not None]
    latest = with_f[0] if with_f else None
    week_ago = next((s for s in with_f if latest and (latest.day - s.day).days >= 7), None)
    last7 = [s for s in snaps if latest and (latest.day - s.day).days < 7] if latest else snaps[:7]
    reach = sum(s.metrics.get("reach") or 0 for s in last7)
    inter = sum(s.metrics.get("total_interactions") or 0 for s in last7)
    return {"followers": latest.followers if latest else None,
            "followers_delta_7d": latest.followers - week_ago.followers if latest and week_ago else None,
            "reach_7d": reach if last7 else None, "er_7d": _er(inter, reach),
            "followers_spark": [s.followers for s in reversed(with_f)]}


@router.get("/today")
async def today(session=Depends(get_session)):
    from app.api.automation import get_account
    s = await get_settings(session)
    t, day = now(), growth_mod.local_today()
    account = await get_account(session)
    approved_waiting = await session.scalar(select(func.count()).select_from(ContentItem).where(
        ContentItem.status == "approved"))
    scheduled = list(await session.scalars(select(ContentItem).where(
        ContentItem.scheduled_at >= t - timedelta(hours=6), ContentItem.scheduled_at <= t + timedelta(hours=48),
        ContentItem.status.in_(["idea", "drafted", "approved", "processing", "publishing"]))
        .order_by(ContentItem.scheduled_at)))
    failed = list(await session.scalars(select(ContentItem).where(ContentItem.status == "failed")))

    alerts = []
    def alert(level, code, text, href="#settings"):
        alerts.append({"level": level, "code": code, "text": text, "href": href})
    if settings.AUTOMATION_PAUSED:
        alert("warn", "automation_paused", "Automation is paused (AUTOMATION_PAUSED=true).")
    if not llm_configured():
        alert("warn", "llm_not_configured", "AI is not configured. Add ANTHROPIC_API_KEY to .env.")
    if account is None:
        alert("warn", "instagram_not_connected", "Instagram is not connected.")
    elif account.status != "active":
        alert("bad", "account_paused", f"@{account.handle} is paused: {account.paused_reason}")
    elif account.token_expires_at and aware(account.token_expires_at) - t < timedelta(days=7):
        alert("warn", "token_expiring", "The Instagram token expires within 7 days.")
    if not (settings.IG_APP_SECRET and settings.IG_WEBHOOK_VERIFY_TOKEN):
        alert("info", "webhooks_not_configured", "Webhooks are not configured, so DMs and funnels won't fire.")
    if approved_waiting and not settings.PUBLIC_BASE_URL.startswith("https://"):
        alert("bad", "publishing_not_configured", "Approved posts can't publish until PUBLIC_BASE_URL is an https URL.")
    for i in failed:
        alert("bad", f"content_failed:{i.id}", f"A {i.format} failed to publish: {i.error}", f"#content/{i.id}")
    for i in scheduled:
        if not i.media and aware(i.scheduled_at) - t < timedelta(hours=24):
            alert("warn", f"needs_media:{i.id}", f"{i.format.title()} due soon has no media.", f"#content/{i.id}")

    return {
        "approvals": {
            "comments": await session.scalar(select(func.count()).select_from(Post).where(Post.status == "pending")),
            "content": await session.scalar(select(func.count()).select_from(ContentItem).where(
                ContentItem.status.in_(["idea", "drafted"]))),
            "dms": await session.scalar(select(func.count()).select_from(Message).where(Message.status == "draft"))},
        "engagement": await growth_mod.engagement_stats(session, day, s["engagement_daily_target"]),
        "scheduled": [{"id": i.id, "format": i.format, "pillar": i.pillar, "angle": i.angle, "status": i.status,
                       "scheduled_at": i.scheduled_at, "has_media": bool(i.media)} for i in scheduled],
        "inbox": {"needs_reply": await session.scalar(select(func.count()).select_from(Conversation).where(
            Conversation.needs_reply))},
        "followups_due": [{"id": x.id, "handle": x.handle, "stage": x.stage,
                           "text": (x.followups or [""])[min(x.followups_sent, len(x.followups or [""]) - 1)]}
                          for x in await session.scalars(select(Lead).where(
                              Lead.next_followup_at.is_not(None), Lead.next_followup_at <= t,
                              Lead.stage.in_(["contacted", "replied"])))],
        "alerts": alerts,
        "kpis": await _kpis(session),
        "modes": {"comments": s["mode_comments"], "dms": s["mode_dms"]},
    }
