import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import inbox
from app.models import AUTO_ACTOR, Account, Conversation, Lead, Message
from app.prefs import SettingsPatch, save_settings
from tests.conftest import FakeAdapter, FakeLLM

DM = {"category": "question", "reply": "Yes, it works with Celery 5. Retries use exponential backoff by default.",
      "confidence": 0.92, "needs_human": False, "needs_human_reason": "", "lead_signal": False, "lead_summary": ""}


def signed(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    return raw, {"X-Hub-Signature-256": "sha256=" + hmac.new(b"app-secret", raw, hashlib.sha256).hexdigest()}


def event(mid="m1", sender="555", text="does this work with celery 5?", echo=False, ms=None):
    ms = ms or int(datetime.now(UTC).timestamp() * 1000)
    msg = {"mid": mid, "text": text, **({"is_echo": True} if echo else {})}
    a, b = ("17841", sender) if echo else (sender, "17841")
    return {"object": "instagram", "entry": [{"id": "17841", "time": ms // 1000, "messaging": [
        {"sender": {"id": a}, "recipient": {"id": b}, "timestamp": ms, "message": msg}]}]}


async def account(session):
    session.add(Account(id=1, platform="instagram", handle="curiox.os", external_user_id="17841", access_token="t" * 12))
    await session.commit()


async def test_webhook_ingest_dedupe_and_echo(client, session):
    await account(session)
    raw, h = signed(event())
    assert (await client.post("/api/webhooks/instagram", content=raw, headers=h)).status_code == 200
    assert (await client.post("/api/webhooks/instagram", content=raw, headers=h)).status_code == 200  # retry
    assert await session.scalar(select(func.count()).select_from(Conversation)) == 1
    assert await session.scalar(select(func.count()).select_from(Message)) == 1
    assert client.queued["dm"] == [1]

    conv = await session.get(Conversation, 1)
    session.add(Message(conversation_id=1, direction="out", text="draft", status="draft"))
    await session.commit()
    raw, h = signed(event(mid="m2", text="answered from the app", echo=True))
    await client.post("/api/webhooks/instagram", content=raw, headers=h)
    await session.refresh(conv)
    assert conv.needs_reply is False
    statuses = set(await session.scalars(select(Message.status)))
    assert statuses == {"received", "sent", "discarded"}


async def seed_conv(session, *, inbound_hours_ago=1, username=None):
    await account(session)
    t = datetime.now(UTC) - timedelta(hours=inbound_hours_ago)
    conv = Conversation(account_id=1, igsid="555", username=username, last_inbound_at=t, last_message_at=t, needs_reply=True)
    session.add(conv)
    await session.flush()
    session.add(Message(conversation_id=conv.id, mid="m1", direction="in", text="does this work with celery 5?",
                        status="received", created_at=t))
    await session.commit()
    return conv


async def test_draft_then_auto_send_once(session, monkeypatch):
    queued = []
    monkeypatch.setattr(inbox.workers, "enqueue_send_dm", queued.append)
    conv = await seed_conv(session)
    draft = await inbox.draft_dm_reply(session, conv.id, FakeLLM(DM), adapter_factory=FakeAdapter())
    assert draft.status == "draft" and queued == []

    await save_settings(session, "tester", SettingsPatch(mode_dms="auto"))
    draft = await inbox.draft_dm_reply(session, conv.id, FakeLLM(DM), adapter_factory=FakeAdapter())
    assert draft.status == "approved" and draft.actor == AUTO_ACTOR and queued == [draft.id]

    fake = FakeAdapter()
    assert await inbox.send_dm(session, draft.id, fake) == "sent"
    assert await inbox.send_dm(session, draft.id, fake) == "not_approved"
    assert [c[0] for c in fake.calls] == ["send_message"]


async def test_auto_dm_blocked_for_leads(session, monkeypatch):
    monkeypatch.setattr(inbox.workers, "enqueue_send_dm", lambda _: pytest.fail("must not auto-send"))
    await save_settings(session, "tester", SettingsPatch(mode_dms="auto"))
    conv = await seed_conv(session)
    draft = await inbox.draft_dm_reply(session, conv.id, FakeLLM({**DM, "category": "lead"}), adapter_factory=FakeAdapter())
    assert draft.status == "draft" and draft.meta["blocked"] == "bucket_lead"


NOW = datetime(2026, 1, 2, 12, tzinfo=UTC)


@pytest.mark.parametrize("hours,status,actor,auto,dup,reason", [
    (1, "approved", "tester", False, False, None),
    (25, "approved", "tester", False, False, "outside_window"),
    (1, "draft", "tester", False, False, "not_approved"),
    (1, "approved", AUTO_ACTOR, False, False, "automation_disabled"),
    (1, "approved", AUTO_ACTOR, True, False, None),
    (1, "approved", "tester", False, True, "duplicate_text"),
])
def test_check_dm_sendable(hours, status, actor, auto, dup, reason):
    conv = Conversation(last_inbound_at=NOW - timedelta(hours=hours))
    msg = Message(direction="out", status=status, actor=actor, text="hello")
    assert inbox.check_dm_sendable(msg, conv, Account(status="active"), now_=NOW, auto_allowed=auto, dup=dup) == reason


async def test_profile_links_lead_and_marks_replied(session):
    session.add(Lead(handle="acme", stage="contacted"))
    await session.commit()
    conv = await seed_conv(session)
    await inbox.draft_dm_reply(session, conv.id, llm=None, adapter_factory=FakeAdapter(profile={"username": "acme"}))
    await session.refresh(conv)
    lead = await session.scalar(select(Lead))
    assert conv.username == "acme" and conv.lead_id == lead.id and lead.stage == "replied"


async def test_human_reply_endpoint_respects_window(client, session, monkeypatch):
    conv = await seed_conv(session, inbound_hours_ago=30)
    r = await client.post(f"/api/inbox/conversations/{conv.id}/reply", headers={"Authorization": "Bearer t0ken"},
                          json={"text": "hi"})
    assert r.status_code == 409 and r.json()["detail"]["reason"] == "outside_window"
