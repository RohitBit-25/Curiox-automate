from collections import Counter
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.deps import get_session
from app.models import Account, Approval, EngagementEvent, Post

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
