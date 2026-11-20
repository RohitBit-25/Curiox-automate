"""The only code path that writes to a platform. Re-validates everything independently of the API layer."""
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models import AUTO_ACTOR, Account, Approval, EngagementEvent, Post, audit
from app.platforms import ADAPTERS, PlatformError, get_adapter
from app.prefs import get_settings
from app.rules import fingerprint


def check_publishable(post: Post, approval: Approval | None, account: Account | None,
                      *, already_published: bool, auto_allowed: bool = False) -> str | None:
    """Returns a block reason, or None if publishing is allowed."""
    if not settings.HUMAN_APPROVAL_REQUIRED or settings.THIRD_PARTY_AUTO_COMMENT:
        return "unsafe_config"
    if approval is None or approval.decision != "approved" or approval.post_id != post.id:
        return "not_approved"
    if post.source != "own_comment":
        return "third_party_never_auto_published"
    if approval.actor == AUTO_ACTOR and not auto_allowed:
        return "automation_disabled"
    if account is None or post.account_id != account.id:
        return "not_own_account"
    if account.status != "active":
        return "account_paused"
    if post.platform not in ADAPTERS:
        return "no_adapter"
    if already_published:
        return "duplicate_text"
    if not approval.final_text or len(approval.final_text) > 2200:
        return "invalid_length"
    return None


async def publish(session, approval_id: int, adapter_factory=None) -> str:
    adapter_factory = adapter_factory or get_adapter
    approval = await session.get(Approval, approval_id)
    if approval is None:
        return "not_approved"
    post = await session.get(Post, approval.post_id)
    account = await session.get(Account, post.account_id) if post.account_id else None
    fp = fingerprint(approval.final_text or "")
    already = await session.scalar(select(EngagementEvent.id).where(
        EngagementEvent.fingerprint == fp, EngagementEvent.status != "failed"))

    auto_allowed = (await get_settings(session))["mode_comments"] == "auto" and not settings.AUTOMATION_PAUSED
    if reason := check_publishable(post, approval, account, already_published=bool(already), auto_allowed=auto_allowed):
        audit(session, "system", "publish_blocked", "post", post.id, reason=reason)
        if post.status == "approved":  # don't clobber an already-published post on a re-run
            post.status, post.skip_reason = "failed", reason
        await session.commit()
        return reason

    # This row is the "never twice" lock (unique approval_id + live fingerprint). A crash after this
    # commit leaves a 'publishing' row, so the reply is never resent automatically. That is the safe side.
    event = EngagementEvent(approval_id=approval.id, post_id=post.id, platform=post.platform,
                            kind="published", status="publishing", fingerprint=fp)
    session.add(event)
    post_id = post.id  # rollback expires ORM objects; keep the plain id
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        audit(session, "system", "publish_duplicate", "post", post_id)
        await session.commit()
        return "duplicate"

    try:
        ext_id = await adapter_factory(post.platform, account.access_token).reply_to_comment(
            post.external_id, approval.final_text)
    except PlatformError as e:
        event.status, event.error = "failed", str(e)
        post.status, post.skip_reason = "failed", f"publish_error: {e}"
        audit(session, "system", "publish_failed", "post", post.id, error=str(e), code=e.code)
        if e.pause:
            account.status, account.paused_reason = "paused", str(e)
            audit(session, "system", "account_paused", "account", account.id, reason=str(e), code=e.code)
        await session.commit()
        return "failed"

    event.status, event.external_comment_id = "published", ext_id
    post.status = "published"
    audit(session, "system", "published", "post", post.id, external_comment_id=ext_id)
    await session.commit()
    return "published"
