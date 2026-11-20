"""Daily Instagram Insights snapshots (account + our published content)."""
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import settings
from app.models import Account, AccountSnapshot, ContentItem, now
from app.platforms import PlatformError, get_adapter


def local_today() -> date:
    return datetime.now(ZoneInfo(settings.TIMEZONE)).date()

log = logging.getLogger(__name__)
CORE = "reach,views,accounts_engaged,total_interactions,likes,comments,saves,shares,profile_links_taps"
# "Application does not have permission": the token lacks instagram_manage_insights (it can need App Review).
# Fall back to what instagram_basic allows: follower count, and likes/comments per post.
NO_PERMISSION = {10, 200}
MEDIA = {"FEED": "reach,likes,comments,saved,shares,views,total_interactions,profile_visits,follows",
         "REELS": "reach,likes,comments,saved,shares,views,total_interactions,ig_reels_avg_watch_time"}


def day_bounds(day: date) -> tuple[int, int]:
    tz = ZoneInfo(settings.TIMEZONE)
    start = datetime.combine(day, time(), tz)
    return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())


async def snapshot_account(session, account: Account, adapter, today: date) -> None:
    # ponytail: no metric backfill; metrics are as of the run (Insights can lag up to 48h, so we refresh 2 days)
    me = await adapter.me()
    ig = account.external_user_id
    for back in (1, 2):
        day = today - timedelta(days=back)
        since, until = day_bounds(day)
        try:
            metrics = await adapter.account_insights(ig, CORE, since, until)
        except PlatformError as e:
            if e.code not in NO_PERMISSION:
                raise
            metrics = {}  # no insights permission yet: still record followers below
        if metrics:
            try:  # not returned under 100 followers, and one bad metric fails a whole call
                metrics |= await adapter.account_insights(ig, "follows_and_unfollows", since, until)
            except PlatformError:
                pass
        row = await session.scalar(select(AccountSnapshot).where(
            AccountSnapshot.account_id == account.id, AccountSnapshot.day == day))
        if row is None:
            row = AccountSnapshot(account_id=account.id, day=day)
            session.add(row)
        row.metrics = metrics
        if back == 1:
            row.followers = row.followers if row.followers is not None else me.get("followers_count")
            row.media_count = row.media_count if row.media_count is not None else me.get("media_count")
    await session.commit()


async def media_insights(session, account: Account, adapter) -> int:
    items = await session.scalars(select(ContentItem).where(
        ContentItem.status == "published", ContentItem.format != "story", ContentItem.external_media_id.is_not(None),
        ContentItem.published_at >= now() - timedelta(days=30)))
    n = 0
    for item in items:
        try:
            try:
                item.insights = await adapter.media_insights(item.external_media_id,
                                                             MEDIA["REELS" if item.format == "reel" else "FEED"])
            except PlatformError as e:
                if e.code not in NO_PERMISSION:
                    raise
                item.insights = await adapter.media_counts(item.external_media_id)  # likes + comments only
            item.insights_at = now()
            n += 1
        except PlatformError as e:
            log.warning("media insights failed for content %s: %s", item.id, e)
    await session.commit()
    return n


async def insights_daily(session, adapter_factory=None) -> None:
    # Iterate ids, not ORM rows: a rollback expires loaded objects and async sessions can't lazy-reload.
    for account_id in list(await session.scalars(select(Account.id).where(Account.status == "active"))):
        account = await session.get(Account, account_id, populate_existing=True)
        adapter = (adapter_factory or get_adapter)(account.platform, account.access_token)
        try:
            await snapshot_account(session, account, adapter, local_today())
            await media_insights(session, account, adapter)
        except PlatformError as e:
            await session.rollback()
            log.warning("insights failed for account %s: %s", account_id, e)
