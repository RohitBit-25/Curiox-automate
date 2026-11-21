"""DM inbox: conversations, AI suggestions, human/auto sends, lead linking."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import inbox
from app.api.automation import require_llm
from app.deps import current_user, get_session
from app.models import Conversation, Lead, Message, audit, now

router = APIRouter()


def msg_dict(m: Message) -> dict:
    return {"id": m.id, "direction": m.direction, "text": m.text, "status": m.status, "actor": m.actor,
            "meta": m.meta, "error": m.error, "attachments": m.attachments, "created_at": m.created_at}


async def conv_dict(session, c: Conversation, full: bool = False) -> dict:
    msgs = list(await session.scalars(select(Message).where(Message.conversation_id == c.id).order_by(Message.id)))
    visible = [m for m in msgs if m.status != "discarded"]
    draft = next((m for m in reversed(msgs) if m.status == "draft"), None)
    lead = await session.get(Lead, c.lead_id) if c.lead_id else None
    out = {"id": c.id, "igsid": c.igsid, "username": c.username, "needs_reply": c.needs_reply,
           "last_message_at": c.last_message_at, "last_inbound_at": c.last_inbound_at,
           "window_open": inbox.window_open(c, now()),
           "lead": {"id": lead.id, "handle": lead.handle, "stage": lead.stage} if lead else None,
           "preview": (visible[-1].text[:120] if visible else ""), "draft_id": draft.id if draft else None}
    if full:
        out["messages"] = [msg_dict(m) for m in visible]
    return out


async def load(session, conv_id: int) -> Conversation:
    c = await session.get(Conversation, conv_id, populate_existing=True)
    if c is None:
        raise HTTPException(404, "conversation not found")
    return c


@router.get("/inbox/conversations")
async def conversations(filter: str = "all", session=Depends(get_session)):
    q = select(Conversation).order_by(Conversation.last_message_at.desc()).limit(200)
    if filter == "needs_reply":
        q = q.where(Conversation.needs_reply)
    return [await conv_dict(session, c) for c in await session.scalars(q)]


@router.get("/inbox/conversations/{conv_id}")
async def conversation(conv_id: int, session=Depends(get_session)):
    return await conv_dict(session, await load(session, conv_id), full=True)


@router.post("/inbox/conversations/{conv_id}/suggest", dependencies=[Depends(require_llm)])
async def suggest(conv_id: int, session=Depends(get_session)):
    from app.llm import get_llm
    await load(session, conv_id)
    await inbox.draft_dm_reply(session, conv_id, get_llm(), force=True)
    return await conv_dict(session, await load(session, conv_id), full=True)


class SendBody(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


async def _send(session, conv: Conversation, msg: Message, user: str, text: str) -> dict:
    # Written or edited by you = a sample of your real voice for future drafts (prefs.voice_samples)
    human = msg.meta is None or text.strip() != msg.text.strip()
    msg.meta = {**(msg.meta or {}), "human_written": human}
    msg.text, msg.status, msg.actor = text, "approved", user
    audit(session, user, "dm_approved", "conversation", conv.id, message_id=msg.id)
    await session.commit()
    result = await inbox.send_dm(session, msg.id)
    if result != "sent":
        raise HTTPException(409, {"message": "not sent", "reason": result})
    return await conv_dict(session, await load(session, conv.id), full=True)


@router.post("/inbox/messages/{message_id}/send")
async def send_draft(message_id: int, body: SendBody, user=Depends(current_user), session=Depends(get_session)):
    msg = await session.get(Message, message_id)
    if msg is None or msg.status != "draft":
        raise HTTPException(409, "not a pending draft")
    return await _send(session, await load(session, msg.conversation_id), msg, user, body.text)


@router.post("/inbox/conversations/{conv_id}/reply")
async def reply(conv_id: int, body: SendBody, user=Depends(current_user), session=Depends(get_session)):
    conv = await load(session, conv_id)
    msg = Message(conversation_id=conv.id, direction="out", text=body.text, status="draft")
    session.add(msg)
    await session.flush()
    return await _send(session, conv, msg, user, body.text)


@router.post("/inbox/messages/{message_id}/discard")
async def discard(message_id: int, user=Depends(current_user), session=Depends(get_session)):
    msg = await session.get(Message, message_id)
    if msg is None or msg.status != "draft":
        raise HTTPException(409, "not a pending draft")
    msg.status = "discarded"
    audit(session, user, "dm_discarded", "conversation", msg.conversation_id, message_id=msg.id)
    await session.commit()
    return {"ok": True}


class LinkBody(BaseModel):
    lead_id: int | None = None
    create: bool = False


@router.post("/inbox/conversations/{conv_id}/lead")
async def link(conv_id: int, body: LinkBody, user=Depends(current_user), session=Depends(get_session)):
    conv = await load(session, conv_id)
    if body.create:
        if not conv.username:
            raise HTTPException(409, "username unknown; add the lead manually")
        lead = await session.scalar(select(Lead).where(Lead.handle == conv.username.lower()))
        if lead is None:
            lead = Lead(handle=conv.username.lower(), stage="replied", notes="Created from an Instagram DM")
            session.add(lead)
            await session.flush()
        conv.lead_id = lead.id
    else:
        if body.lead_id is not None and await session.get(Lead, body.lead_id) is None:
            raise HTTPException(404, "lead not found")
        conv.lead_id = body.lead_id
    audit(session, user, "conversation_linked", "conversation", conv.id, lead_id=conv.lead_id)
    await session.commit()
    return await conv_dict(session, conv, full=True)
