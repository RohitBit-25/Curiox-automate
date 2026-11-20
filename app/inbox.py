"""DM inbox: ingest messaging webhooks, draft replies, send inside Instagram's 24h window, link leads."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app import automation, craft, pipeline, workers  # module refs: automation <-> workers <-> inbox cycle
from app.config import settings
from app.content import creator_system
from app.models import (AUTO_ACTOR, Account, BrandKnowledge, Conversation, FunnelRule, FunnelSend, Lead, Message,
                        audit, aware, now)
from app.platforms import PlatformError, get_adapter
from app.prefs import get_settings
from app.rules import fingerprint

WINDOW = timedelta(hours=24)
DM_SCHEMA = pipeline._obj({
    "category": {"type": "string", "enum": ["keyword", "lead", "question", "support", "complaint", "spam", "sensitive", "other"]},
    "reply": pipeline.STR, "confidence": pipeline.NUM, "needs_human": pipeline.BOOL, "needs_human_reason": pipeline.STR,
    "lead_signal": pipeline.BOOL, "lead_summary": pipeline.STR})


def window_open(conv: Conversation, now_: datetime) -> bool:
    return conv.last_inbound_at is not None and now_ - aware(conv.last_inbound_at) <= WINDOW


async def ingest_messaging(session, account: Account, ev: dict) -> int | None:
    """Store one webhook messaging event. Returns the conversation id if it now needs a reply."""
    msg = ev.get("message")
    if not msg or msg.get("is_deleted"):
        return None
    echo = bool(msg.get("is_echo"))
    other = str(ev["recipient"]["id"] if echo else ev["sender"]["id"])
    if other == account.external_user_id or await session.scalar(select(Message.id).where(Message.mid == msg["mid"])):
        return None  # webhook retries, and echoes of messages we sent through the API
    ts = datetime.fromtimestamp(ev["timestamp"] / 1000, UTC) if ev.get("timestamp") else now()
    conv = await session.scalar(select(Conversation).where(Conversation.account_id == account.id,
                                                           Conversation.igsid == other))
    if conv is None:
        conv = Conversation(account_id=account.id, igsid=other, last_message_at=ts)
        session.add(conv)
        await session.flush()
    session.add(Message(conversation_id=conv.id, mid=msg["mid"], direction="out" if echo else "in",
                        text=msg.get("text", ""), status="sent" if echo else "received",
                        attachments=[(a.get("payload") or {}).get("url") for a in msg.get("attachments", [])],
                        actor="instagram_app" if echo else None, created_at=ts))
    conv.last_message_at = ts
    if echo:  # a human answered from the Instagram app: drop pending drafts
        conv.needs_reply = False
        await session.execute(update(Message).where(Message.conversation_id == conv.id, Message.status == "draft")
                              .values(status="discarded"))
        return None
    conv.last_inbound_at, conv.needs_reply = ts, True
    return conv.id


async def link_lead(session, conv: Conversation) -> None:
    if conv.lead_id or not conv.username:
        return
    lead = await session.scalar(select(Lead).where(Lead.handle == conv.username.lower()))
    if lead is None:
        return
    conv.lead_id = lead.id
    if lead.stage == "contacted":
        lead.stage = "replied"
        audit(session, "system", "lead_replied", "lead", lead.id, conversation_id=conv.id)


def dm_flags(text: str) -> list[str]:
    return ["empty"] if not text.strip() else ["too_long"] if len(text.encode()) > 1000 else []


async def draft_dm_reply(session, conv_id: int, llm=None, adapter_factory=None, force: bool = False) -> Message | None:
    conv = await session.get(Conversation, conv_id, populate_existing=True)
    if conv is None:
        return None
    account = await session.get(Account, conv.account_id)
    history = list(await session.scalars(select(Message).where(
        Message.conversation_id == conv.id, Message.status.in_(["received", "sent"])).order_by(Message.id)))
    if not force and (not conv.needs_reply or not history or history[-1].direction != "in"):
        return None

    if conv.username is None and account is not None:
        try:
            conv.username = ((await (adapter_factory or get_adapter)(account.platform, account.access_token)
                              .user_profile(conv.igsid)).get("username") or None)
        except PlatformError:
            pass  # best effort; the inbox shows the IG id instead
    await link_lead(session, conv)
    await session.commit()

    s = await get_settings(session)
    if llm is None or (s["mode_dms"] == "off" and not force):
        return None

    last_in = next((m.text for m in reversed(history) if m.direction == "in"), "")
    rows = list(await session.scalars(select(BrandKnowledge)))
    kn = pipeline.pick_knowledge(rows, last_in.lower().split())
    lead = await session.get(Lead, conv.lead_id) if conv.lead_id else None
    fsend = await session.scalar(select(FunnelSend).where(FunnelSend.commenter_id == conv.igsid)
                                 .order_by(FunnelSend.id.desc()))
    rule = await session.get(FunnelRule, fsend.rule_id) if fsend else None
    left = WINDOW - (now() - aware(conv.last_inbound_at)) if conv.last_inbound_at else timedelta(0)

    d = await llm.json(creator_system(s["voice"]), pipeline.render(
        "dm_reply.txt", window_left=f"{max(left.total_seconds(), 0) / 3600:.0f}h",
        who=f"@{conv.username}" if conv.username else "unknown (Instagram id only)",
        lead_block=(f"Linked lead: @{lead.handle}, stage {lead.stage}, niche {lead.niche or '?'}, "
                    f"best angle: {(lead.research or {}).get('best_angle', 'n/a')}") if lead else "",
        funnel_block=f'They commented keyword "{rule.keywords[0]}" and received: "{rule.dm_text}"' if rule else "",
        knowledge="\n".join(f"- [{r.kind}] {r.title}: {r.content}" for r in kn) or "none",
        history="\n".join(f"{'them' if m.direction == 'in' else 'us'}: {m.text}" for m in history[-20:]),
    ), DM_SCHEMA, "low")
    text = craft.scrub(d["reply"])

    await session.execute(update(Message).where(Message.conversation_id == conv.id, Message.status == "draft")
                          .values(status="discarded"))
    draft = Message(conversation_id=conv.id, direction="out", text=text, status="draft", meta=d)
    session.add(draft)
    await session.flush()

    if s["mode_dms"] == "auto" and not force:
        hour_ago, day_ago = now() - timedelta(hours=1), now() - timedelta(days=1)
        sent_hour = await session.scalar(select(func.count()).select_from(Message).where(
            Message.actor == AUTO_ACTOR, Message.status == "sent", Message.created_at >= hour_ago))
        same = await session.scalar(select(func.count()).select_from(Message).where(
            Message.conversation_id == conv.id, Message.actor == AUTO_ACTOR, Message.created_at >= day_ago))
        reason = automation.auto_block_reason(
            mode="auto", paused=settings.AUTOMATION_PAUSED, account_active=bool(account and account.status == "active"),
            bucket=d["category"], allowed=automation.DM_AUTO, safe=not d["needs_human"], confidence=float(d["confidence"]),
            min_conf=s["auto_min_confidence"], needs_review=d["needs_human"], warnings=[], flags=dm_flags(text),
            tells_verdict=craft.ai_tells(text)["verdict"] if len(text.split()) > 25 else "PASS",
            sent_last_hour=sent_hour, cap=s["cap_auto_dms_per_hour"], same_target_24h=same, target_cap=5)
        if reason is None:
            draft.status, draft.actor = "approved", AUTO_ACTOR
            audit(session, "system", "dm_auto_approved", "conversation", conv.id, message_id=draft.id)
            await session.commit()
            workers.enqueue_send_dm(draft.id)
            return draft
        draft.meta = {**d, "blocked": reason}
    audit(session, "system", "dm_drafted", "conversation", conv.id, message_id=draft.id, category=d["category"])
    await session.commit()
    return draft


def check_dm_sendable(msg: Message, conv: Conversation, account: Account | None, *,
                      now_: datetime, auto_allowed: bool, dup: bool) -> str | None:
    if msg.direction != "out" or msg.status != "approved":
        return "not_approved"
    if account is None or account.status != "active":
        return "account_paused"
    if not window_open(conv, now_):
        return "outside_window"
    if msg.actor == AUTO_ACTOR and not auto_allowed:
        return "automation_disabled"
    if dm_flags(msg.text):
        return "invalid_length"
    if dup:
        return "duplicate_text"
    return None


async def send_dm(session, message_id: int, adapter_factory=None) -> str:
    msg = await session.get(Message, message_id, populate_existing=True)
    if msg is None:
        return "missing"
    conv = await session.get(Conversation, msg.conversation_id)
    account = await session.get(Account, conv.account_id)
    s = await get_settings(session)
    fp = fingerprint(msg.text)
    recent = await session.scalars(select(Message.text).where(
        Message.conversation_id == conv.id, Message.status == "sent", Message.direction == "out",
        Message.created_at >= now() - WINDOW))
    dup = any(fingerprint(t) == fp for t in recent)
    if reason := check_dm_sendable(msg, conv, account, now_=now(), dup=dup,
                                   auto_allowed=s["mode_dms"] == "auto" and not settings.AUTOMATION_PAUSED):
        if msg.status == "approved":
            msg.status, msg.error = "failed", reason
        audit(session, "system", "dm_blocked", "conversation", conv.id, message_id=msg.id, reason=reason)
        await session.commit()
        return reason

    r = await session.execute(update(Message).where(Message.id == msg.id, Message.status == "approved")
                              .values(status="sending").execution_options(synchronize_session=False))
    await session.commit()
    if r.rowcount != 1:
        return "taken"  # another worker is sending it
    await session.refresh(msg)
    try:
        mid = await (adapter_factory or get_adapter)(account.platform, account.access_token).send_message(
            account.external_user_id, conv.igsid, msg.text)
    except PlatformError as e:
        msg.status, msg.error = "failed", str(e)
        audit(session, "system", "dm_failed", "conversation", conv.id, message_id=msg.id, error=str(e))
        if e.pause:
            account.status, account.paused_reason = "paused", str(e)
            audit(session, "system", "account_paused", "account", account.id, reason=str(e), code=e.code)
        await session.commit()
        return "failed"
    msg.mid, msg.status, msg.created_at = mid or None, "sent", now()
    conv.needs_reply, conv.last_message_at = False, now()
    audit(session, msg.actor or "system", "dm_sent", "conversation", conv.id, message_id=msg.id)
    await session.commit()
    return "sent"
