"""Comment-to-DM funnels and the auto-reply gate for comments on our own posts."""
import random
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import craft, pipeline, workers
from app.config import settings
from app.models import AUTO_ACTOR, Account, Approval, FunnelRule, FunnelSend, Post, audit, aware, now
from app.platforms import PlatformError, get_adapter
from app.prefs import get_settings
from app.rules import normalize

COMMENT_AUTO = {"question", "support", "substance"}
DM_AUTO = {"question", "support", "keyword"}
DECISION_SCHEMA = pipeline._obj({
    "bucket": {"type": "string", "enum": ["lead", "substance", "question", "support", "critic", "noise", "sensitive"]},
    "pick": {"type": "integer"}, "safe_to_auto": pipeline.BOOL, "confidence": pipeline.NUM, "reason": pipeline.STR})


def match_funnel(text: str, media_id: str | None, rules: list[FunnelRule]) -> FunnelRule | None:
    """Whole-word keyword match. Post-specific rules win over all-posts rules."""
    words = normalize(text).split()
    for r in sorted(rules, key=lambda r: (r.media_id is None, r.id)):
        if not r.active or (r.media_id and r.media_id != media_id):
            continue
        for kw in r.keywords:
            k = normalize(kw).split()
            if k and any(words[i:i + len(k)] == k for i in range(len(words) - len(k) + 1)):
                return r
    return None


async def run_funnel(session, post: Post, rule: FunnelRule, account: Account | None, adapter_factory=None) -> str:
    s = await get_settings(session)
    sent_hour = await session.scalar(select(func.count()).select_from(FunnelSend).where(
        FunnelSend.status == "sent", FunnelSend.created_at >= now() - timedelta(hours=1)))
    commented_at = aware(post.published_at or post.created_at)
    reason = ("automation_paused" if settings.AUTOMATION_PAUSED else
              "account_paused" if account is None or account.status != "active" else
              "no_commenter" if not post.author_id or post.author_id == account.external_user_id else
              "window_expired" if now() - commented_at > timedelta(days=7) else
              "hourly_cap" if sent_hour >= s["cap_funnel_dms_per_hour"] else None)
    if reason:
        audit(session, "system", "funnel_skipped", "post", post.id, rule_id=rule.id, reason=reason)
        await session.commit()
        return reason

    post_id, rule_id = post.id, rule.id  # rollback expires ORM objects; keep plain ids
    send = FunnelSend(rule_id=rule_id, post_id=post_id, comment_id=post.external_id,
                      commenter_id=post.author_id, status="sending")
    session.add(send)
    try:
        await session.commit()  # the unique (rule, commenter) + comment_id constraints are the lock
    except IntegrityError:
        await session.rollback()
        audit(session, "system", "funnel_duplicate", "post", post_id, rule_id=rule_id)
        await session.commit()
        return "duplicate"

    adapter = (adapter_factory or get_adapter)(account.platform, account.access_token)
    try:
        send.message_id = await adapter.private_reply(account.external_user_id, post.external_id, rule.dm_text)
    except PlatformError as e:
        send.status, send.error = "failed", str(e)
        audit(session, "system", "funnel_failed", "post", post.id, rule_id=rule.id, error=str(e))
        if e.pause:
            account.status, account.paused_reason = "paused", str(e)
            audit(session, "system", "account_paused", "account", account.id, reason=str(e), code=e.code)
        await session.commit()
        return "failed"

    if rule.public_replies:
        recent = set(await session.scalars(select(FunnelSend.public_reply_text).where(
            FunnelSend.rule_id == rule.id, FunnelSend.id != send.id).order_by(FunnelSend.id.desc()).limit(10)))
        text = next((v for v in rule.public_replies if v not in recent), random.choice(rule.public_replies))
        try:
            send.public_reply_id = await adapter.reply_to_comment(post.external_id, text)
            send.public_reply_text = text
        except PlatformError as e:  # the DM already went; the public reply is a nice-to-have
            send.error = f"public reply: {e}"
    send.status = "sent"
    post.status = "funnel"
    audit(session, "system", "funnel_sent", "post", post.id, rule_id=rule.id, commenter_id=post.author_id)
    await session.commit()
    return "sent"


def auto_block_reason(*, mode, paused, account_active, bucket, allowed, safe, confidence, min_conf,
                      needs_review, warnings, flags, tells_verdict, sent_last_hour, cap,
                      same_target_24h, target_cap) -> str | None:
    """Every condition must hold for an automated public reply. First failure wins."""
    if mode != "auto":
        return "mode_not_auto"
    if paused:
        return "automation_paused"
    if not account_active:
        return "account_paused"
    if bucket not in allowed:
        return f"bucket_{bucket}"
    if not safe:
        return "model_unsure"
    if confidence < min_conf:
        return "low_confidence"
    if needs_review:
        return "needs_human_review"
    if warnings:
        return "factuality_warnings"
    if flags:
        return "quality_flags"  # ANY quality flag, not just the hard ones
    if tells_verdict == "FLAGGED":
        return "ai_tells"
    if sent_last_hour >= cap:
        return "hourly_cap"
    if same_target_24h >= target_cap:
        return "target_cap"
    return None


async def maybe_auto_approve(session, post_id: int, llm) -> str:
    post = await session.scalar(select(Post).options(selectinload(Post.suggestions), selectinload(Post.approval))
                                .where(Post.id == post_id).execution_options(populate_existing=True))
    if post is None or post.status != "pending" or not post.suggestions or post.source != "own_comment":
        return "not_eligible"
    s = await get_settings(session)
    d = await llm.json(pipeline.system_prompt(s["voice"]), pipeline.render(
        "auto_decision.txt", parent=post.parent_text or "(caption not available)", author=post.author_handle or "unknown",
        comment=post.text, suggestions="\n".join(f"{i}. {x.text}" for i, x in enumerate(post.suggestions, 1)),
    ), DECISION_SCHEMA, "low")
    if d["bucket"] == "noise":
        post.status, post.skip_reason = "skipped", "auto_noise"
        audit(session, "system", "skip", "post", post.id, reason="auto_noise")
        await session.commit()
        return "noise"

    pick = post.suggestions[min(max(int(d["pick"]) - 1, 0), len(post.suggestions) - 1)]
    account = await session.get(Account, post.account_id)
    gen = (post.analysis or {}).get("generation", {})
    hour_ago, day_ago = now() - timedelta(hours=1), now() - timedelta(days=1)
    sent_hour = await session.scalar(select(func.count()).select_from(Approval).where(
        Approval.actor == AUTO_ACTOR, Approval.created_at >= hour_ago))
    same_target = await session.scalar(select(func.count()).select_from(Approval).join(Post).where(
        Approval.actor == AUTO_ACTOR, Approval.created_at >= day_ago, Post.author_id == post.author_id))
    reason = auto_block_reason(
        mode=s["mode_comments"], paused=settings.AUTOMATION_PAUSED, account_active=bool(account and account.status == "active"),
        bucket=d["bucket"], allowed=COMMENT_AUTO, safe=d["safe_to_auto"], confidence=float(d["confidence"]),
        min_conf=s["auto_min_confidence"], needs_review=gen.get("needs_human_review", True),
        warnings=gen.get("factuality_warnings", []), flags=pick.quality_flags, tells_verdict=craft.ai_tells(pick.text)["verdict"],
        sent_last_hour=sent_hour, cap=s["cap_auto_comment_replies_per_hour"], same_target_24h=same_target, target_cap=3)
    if reason:
        post.analysis = {**(post.analysis or {}), "auto": {**d, "blocked": reason}}
        audit(session, "system", "auto_declined", "post", post.id, reason=reason, bucket=d["bucket"])
        await session.commit()
        return reason

    approval = Approval(post_id=post.id, suggestion_id=pick.id, decision="approved", final_text=pick.text,
                        actor=AUTO_ACTOR, reason=f"auto:{d['bucket']}:{float(d['confidence']):.2f}")
    session.add(approval)
    post.status = "approved"
    post.analysis = {**(post.analysis or {}), "auto": {**d, "blocked": None}}
    await session.flush()
    audit(session, "system", "auto_approved", "post", post.id, approval_id=approval.id, reason=d["reason"])
    await session.commit()
    workers.enqueue_publish(approval.id)  # the publisher re-checks everything
    return "approved"


async def handle_comment(session, post_id: int, llm=None, adapter_factory=None) -> str:
    """Entry point for every new post/comment: funnel -> mode -> AI pipeline -> optional auto gate."""
    post = await session.get(Post, post_id)
    if post is None or post.status != "new":
        return "noop"
    if post.source != "own_comment":  # third-party: v1 review pipeline only, never automated
        if llm:
            await pipeline.process_post(session, post_id, llm)
        return "pipeline"
    account = await session.get(Account, post.account_id)
    rules = list(await session.scalars(select(FunnelRule).where(FunnelRule.active)))
    if rule := match_funnel(post.text, post.parent_external_id, rules):
        post.status = "funnel"  # claimed before the network call; never falls through to an AI reply
        await session.commit()
        return await run_funnel(session, post, rule, account, adapter_factory)
    s = await get_settings(session)
    if s["mode_comments"] == "off":
        post.status, post.skip_reason = "skipped", "mode_off"
        audit(session, "system", "skip", "post", post.id, reason="mode_off")
        await session.commit()
        return "mode_off"
    if llm is None:
        return "llm_not_configured"  # stays 'new'; picked up again once a key is added
    await pipeline.process_post(session, post_id, llm)
    if s["mode_comments"] == "auto":
        return await maybe_auto_approve(session, post_id, llm)
    return "queued"
