"""Celery app, beat schedule, tasks. `celery -A app.workers worker|beat`."""
import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from celery import Celery
from celery.schedules import crontab
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app import automation, content, inbox, publisher
from app.config import settings
from app.discovery import discover_account
from app.llm import get_llm, llm_configured
from app.models import Account, Post, audit
from app.platforms import PlatformError, get_adapter

logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs, which carry access_token

celery = Celery("curiox", broker=settings.REDIS_URL)
celery.conf.timezone = settings.TIMEZONE
celery.conf.beat_schedule = {
    "discover": {"task": "discover", "schedule": crontab(minute="*/30")},
    "refresh_tokens": {"task": "refresh_tokens", "schedule": crontab(hour=3, minute=0)},
    "weekly_report": {"task": "weekly_report", "schedule": crontab(day_of_week="mon", hour=9, minute=0)},
    "publish_due": {"task": "publish_due", "schedule": crontab()},  # every minute
}

# asyncio.run makes a new loop per task, so pooled connections can't be shared across tasks.
_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
_Session = async_sessionmaker(_engine, expire_on_commit=False)


def run(fn, *args):
    async def go():
        async with _Session() as s:
            return await fn(s, *args)
    return asyncio.run(go())


@celery.task(name="process_post", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_post_task(post_id: int):
    run(automation.handle_comment, post_id, get_llm() if llm_configured() else None)


@celery.task(name="publish")  # deliberately NO autoretry: a failed publish waits for a human
def publish_task(approval_id: int):
    return run(publisher.publish, approval_id)


async def _pause(session, account: Account, e: PlatformError):
    if e.pause:
        account.status, account.paused_reason = "paused", str(e)
        audit(session, "system", "account_paused", "account", account.id, reason=str(e), code=e.code)
    else:
        audit(session, "system", "platform_error", "account", account.id, error=str(e), code=e.code)
    await session.commit()


async def _discover(session):
    for account in await session.scalars(select(Account).where(Account.status == "active")):
        try:
            for post_id in await discover_account(session, account, get_adapter(account.platform, account.access_token)):
                enqueue_process(post_id)
        except PlatformError as e:
            await session.rollback()
            await _pause(session, account, e)
    # Re-enqueue posts stuck in 'new' (e.g. a lost Redis message). process_post is idempotent.
    stale = datetime.now(UTC) - timedelta(minutes=10)
    q = select(Post.id).where(Post.status == "new", Post.created_at < stale)
    if not llm_configured():  # without an LLM only funnels can act, and those only apply to our own comments
        q = q.where(Post.source == "own_comment")
    for post_id in await session.scalars(q):
        enqueue_process(post_id)


async def _refresh_tokens(session):
    soon = datetime.now(UTC) + timedelta(days=15)
    q = select(Account).where(Account.status == "active",
                              or_(Account.token_expires_at.is_(None), Account.token_expires_at < soon))
    for account in await session.scalars(q):
        try:
            token, expires_in = await get_adapter(account.platform, account.access_token).refresh_token()
        except PlatformError as e:
            await _pause(session, account, e)
            continue
        account.access_token = token
        account.token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
        audit(session, "system", "token_refreshed", "account", account.id)
        await session.commit()


async def _weekly_report(session):
    from app.api.analytics import overview, weekly
    audit(session, "system", "weekly_report", "report", None,
          week=await weekly(session, 1), overview=await overview(session))
    await session.commit()


@celery.task(name="discover")
def discover_task():
    run(_discover)


@celery.task(name="refresh_tokens")
def refresh_tokens_task():
    run(_refresh_tokens)


@celery.task(name="weekly_report")
def weekly_report_task():
    run(_weekly_report)


@celery.task(name="plan_week", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def plan_week_task(week_start_iso: str, brief: str, count: int, user: str):
    for item_id in run(content.plan_week, date.fromisoformat(week_start_iso), brief, count, user, get_llm()):
        enqueue_draft(item_id)


@celery.task(name="draft_item", autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def draft_item_task(item_id: int):
    run(content.draft_item, item_id, get_llm())


@celery.task(name="publish_due")  # NO autoretry: the next tick (one minute later) is the retry
def publish_due_task():
    return run(content.publish_tick)


@celery.task(name="dm_reply", autoretry_for=(Exception,), retry_backoff=True, max_retries=2)
def dm_reply_task(conv_id: int):
    run(inbox.draft_dm_reply, conv_id, get_llm() if llm_configured() else None)


@celery.task(name="send_dm")  # NO autoretry: a failed send waits for a human
def send_dm_task(message_id: int):
    return run(inbox.send_dm, message_id)


def enqueue_dm(conv_id: int) -> None:
    dm_reply_task.delay(conv_id)


def enqueue_send_dm(message_id: int) -> None:
    send_dm_task.delay(message_id)


def enqueue_plan(week_start_iso: str, brief: str, count: int, user: str) -> None:
    plan_week_task.delay(week_start_iso, brief, count, user)


def enqueue_draft(item_id: int) -> None:
    draft_item_task.delay(item_id)


def enqueue_process(post_id: int) -> None:
    process_post_task.delay(post_id)


def enqueue_publish(approval_id: int) -> None:
    publish_task.delay(approval_id)
