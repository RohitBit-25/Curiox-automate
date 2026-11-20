from sqlalchemy import select

from app.models import AuditLog
from tests.conftest import H


async def test_status_without_keys(client):
    r = await client.get("/api/status", headers=H)
    body = r.json()
    assert r.status_code == 200
    assert body["llm"]["configured"] is False
    assert body["instagram"]["connected"] is False
    assert body["publishing"]["configured"] is False
    assert "secret" not in r.text


async def test_patch_settings_audited(client, session):
    assert (await client.get("/api/settings", headers=H)).json()["mode_comments"] == "draft"
    r = await client.patch("/api/settings", headers=H, json={"mode_comments": "auto"})
    assert r.status_code == 200 and r.json()["mode_comments"] == "auto"
    row = await session.scalar(select(AuditLog).where(AuditLog.action == "settings_changed"))
    assert row.actor == "tester" and row.data == {"key": "mode_comments", "before": "draft", "after": "auto"}
    assert (await client.patch("/api/settings", headers=H, json={"mode_comments": "yolo"})).status_code == 422
    assert (await client.patch("/api/settings", headers=H, json={"auto_min_confidence": 0.2})).status_code == 422


async def test_voice_roundtrip(client):
    voice = (await client.get("/api/settings", headers=H)).json()["voice"]
    voice["who"] = "Builder shipping AI automations for small businesses"
    r = await client.patch("/api/settings", headers=H, json={"voice": voice})
    assert r.json()["voice"]["who"].startswith("Builder")
