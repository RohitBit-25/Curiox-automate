"""End to end: submit link -> pipeline -> queue -> approve -> mark posted, plus auth/safety checks."""
import hashlib
import hmac
import json

import httpx
import pytest
from sqlalchemy import select, text

from app import pipeline
from app.config import Settings
from app.models import Account, AuditLog
from tests.conftest import H, FakeLLM


ANALYSIS = {
    "post_summary": "Developer replaced cron with Celery + Redis and asks about retries.",
    "detected_language": "en", "detected_tone": "practical, technical", "author_intent": "share a win",
    "topics": ["python", "background jobs"], "technical_concepts": ["Celery", "Redis", "idempotency"],
    "invites_discussion": True, "scores": {"topic": 0.9, "audience": 0.9, "conversation": 0.8, "quality": 0.8},
    "reject_reason": "none", "relevance_reason": "Core developer topic with an explicit question.",
}
SUGGESTIONS = [
    {"text": "Idempotent tasks plus a dead-letter queue made retries boring in the best way. Are you using acks_late?",
     "strategy": "question"},
    {"text": "Worth pairing retry_backoff with a jitter cap, otherwise a Redis blip can turn into a thundering herd.",
     "strategy": "insight"},
    {"text": "Follow me for more Celery tips!", "strategy": "insight"},  # dropped by quality control
]
GENERATION = {"no_comment": False, "no_comment_reason": "", "response_strategy": "question",
              "comment_suggestions": SUGGESTIONS, "factuality_warnings": [], "needs_human_review": False}


async def submit(client, shortcode="ABC123xyz"):
    return await client.post("/api/posts", headers=H, json={
        "url": f"https://www.instagram.com/p/{shortcode}/",
        "caption": "Replaced my cron jobs with Celery + Redis. Idempotent tasks or nothing. What do you use for retries?",
        "author_handle": "@SomeDev"})


async def test_full_flow(client, session):
    assert (await client.get("/api/opportunities")).status_code == 401
    bad = await client.post("/api/posts", headers=H, json={"url": "https://example.com/p/x", "caption": "a" * 20})
    assert bad.status_code == 422

    r = await submit(client)
    assert r.status_code == 200
    post_id = r.json()["id"]
    assert client.queued["process"] == [post_id]
    assert (await submit(client)).status_code == 409  # same post twice

    await pipeline.process_post(session, post_id, FakeLLM(ANALYSIS, GENERATION))
    [opp] = (await client.get("/api/opportunities", headers=H)).json()
    assert opp["id"] == post_id and len(opp["suggestions"]) == 2
    assert opp["relevance_score"] >= 0.65 and opp["relevance_reason"]

    sid = opp["suggestions"][1]["id"]
    edited = await client.post(f"/api/opportunities/{post_id}/edit", headers=H,
                               json={"suggestion_id": sid, "text": SUGGESTIONS[1]["text"] + " Ask me how."})
    assert edited.json()["suggestions"][1]["edited"] is True

    r = await client.post(f"/api/opportunities/{post_id}/approve", headers=H, json={"suggestion_id": sid})
    assert r.status_code == 200 and r.json()["approval"]["actor"] == "tester"
    assert client.queued["publish"] == []  # third-party post: never auto-published
    assert (await client.post(f"/api/opportunities/{post_id}/approve", headers=H,
                              json={"suggestion_id": sid})).status_code == 409

    assert (await client.post(f"/api/opportunities/{post_id}/mark-posted", headers=H)).status_code == 200
    assert (await client.post(f"/api/opportunities/{post_id}/mark-posted", headers=H)).status_code == 409

    # Second post with the same approved text -> blocked as duplicate at approve time
    r2 = await submit(client, "XYZ987")
    p2 = r2.json()["id"]
    gen2 = {**GENERATION, "comment_suggestions": [{"text": "A fresh angle on visibility timeouts.", "strategy": "insight"}]}
    await pipeline.process_post(session, p2, FakeLLM(ANALYSIS, gen2))
    opp2 = (await client.get(f"/api/opportunities/{p2}", headers=H)).json()
    s2 = opp2["suggestions"][0]["id"]
    await client.post(f"/api/opportunities/{p2}/edit", headers=H,
                      json={"suggestion_id": s2, "text": SUGGESTIONS[1]["text"] + " Ask me how."})
    r = await client.post(f"/api/opportunities/{p2}/approve", headers=H, json={"suggestion_id": s2})
    assert r.status_code == 422 and "duplicate" in r.json()["detail"]["issues"]

    actions = set(await session.scalars(select(AuditLog.action)))
    assert {"submit_link", "queued", "edit", "approve", "manual_posted"} <= actions

    overview = (await client.get("/api/analytics/overview", headers=H)).json()
    assert overview["published"] == 1 and overview["approval_rate"] == 1.0
    weekly = (await client.get("/api/analytics/weekly", headers=H)).json()
    assert weekly[-1]["discovered"] == 2 and weekly[-1]["published"] == 1


async def test_skips_engagement_bait(client, session):
    post_id = (await submit(client)).json()["id"]
    await pipeline.process_post(session, post_id, FakeLLM({**ANALYSIS, "reject_reason": "engagement_bait"}))
    [p] = (await client.get("/api/opportunities?status=skipped", headers=H)).json()
    assert p["skip_reason"] == "engagement_bait" and p["suggestions"] == []


async def test_token_encrypted_and_never_returned(client, session):
    session.add(Account(platform="instagram", handle="curiox", external_user_id="17841", access_token="secret-token"))
    await session.commit()
    raw = (await session.execute(text("select access_token from accounts"))).scalar()
    assert "secret-token" not in raw and raw.startswith("gAAAA")
    body = (await client.get("/api/accounts", headers=H)).text
    assert "access_token" not in body and "secret-token" not in body
    assert "secret" not in (await client.get("/api/config", headers=H)).text


async def test_webhook_handshake_and_signature(client, session):
    r = await client.get("/api/webhooks/instagram",
                         params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert r.status_code == 200 and r.text == "42"
    bad = await client.get("/api/webhooks/instagram",
                           params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert bad.status_code == 403

    session.add(Account(id=7, platform="instagram", handle="curiox", external_user_id="17841", access_token="t" * 12))
    await session.commit()
    body = json.dumps({"object": "instagram", "entry": [{"id": "17841", "time": 1763625600, "changes": [
        {"field": "comments", "value": {"id": "c-1", "text": "How do you handle retries?",
                                        "from": {"id": "999", "username": "fan"}, "media": {"id": "m-1"}}},
        {"field": "comments", "value": {"id": "c-2", "text": "mine", "from": {"id": "17841", "username": "curiox"},
                                        "media": {"id": "m-1"}}},
    ]}]}).encode()
    assert (await client.post("/api/webhooks/instagram", content=body,
                              headers={"X-Hub-Signature-256": "sha256=bad"})).status_code == 403
    sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    ok = await client.post("/api/webhooks/instagram", content=body, headers={"X-Hub-Signature-256": sig})
    assert ok.status_code == 200
    assert len(client.queued["process"]) == 1  # our own comment ignored


@pytest.mark.parametrize("override", [
    {"THIRD_PARTY_AUTO_COMMENT": True}, {"HUMAN_APPROVAL_REQUIRED": False}, {"DISCOVERY_MODE": "scrape"},
])
def test_unsafe_config_refuses_to_boot(override):
    with pytest.raises(ValueError, match="unsafe config"):
        Settings(**override)
