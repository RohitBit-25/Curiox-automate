from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import growth
from app.models import Creator, Lead, Post
from tests.conftest import H, FakeLLM

AUDIT = {
    "summary": "A bakery taking orders by DM.",
    "rubric": [{"id": "name_field", "score": 99, "note": "x"}, {"id": "bio_first_line", "score": 5, "note": "y"}],
    "problems": [{"problem": "Orders by DM", "evidence": "'DM to order'", "curiox_solution": "Order form + auto-reply",
                  "impact": "high"}],
    "best_angle": "Orders by DM", "pitch": "Saw your “DM to order” pinned post​. Quick idea: ...",
    "followups": ["f1", "f2", "f3"], "confidence": 0.8, "insufficient_info": False,
}


async def test_research_lead(session):
    session.add(Lead(handle="sweetcrumbs", profile_text="DM to order. Open Tue-Sun."))
    await session.commit()
    lead = await growth.research_lead(session, 1, FakeLLM(AUDIT))
    assert lead.rubric_total == 12 + 5  # name_field clamped to its 12 points
    assert lead.stage == "researched" and lead.followups == ["f1", "f2"]
    assert "​" not in lead.pitch and '"DM to order"' in lead.pitch


async def test_lead_crud_and_followups(client):
    lead = (await client.post("/api/leads", headers=H, json={"handle": "@SweetCrumbs", "niche": "bakery"})).json()
    assert lead["handle"] == "sweetcrumbs"
    assert (await client.post("/api/leads", headers=H, json={"handle": "sweetcrumbs"})).status_code == 409
    r = (await client.post(f"/api/leads/{lead['id']}/contacted", headers=H)).json()
    due = datetime.fromisoformat(r["next_followup_at"]).replace(tzinfo=UTC)
    assert r["stage"] == "contacted" and timedelta(days=2.9) < due - datetime.now(UTC) < timedelta(days=3.1)
    assert (await client.post(f"/api/leads/{lead['id']}/stage", headers=H, json={"stage": "won"})).json()["stage"] == "won"
    assert (await client.post(f"/api/leads/{lead['id']}/stage", headers=H, json={"stage": "bogus"})).status_code == 422
    assert (await client.post(f"/api/leads/{lead['id']}/research", headers=H)).status_code == 503


async def test_engagement_round(client, session):
    session.add_all([
        Creator(handle="never1"), Creator(handle="never2"),
        Creator(handle="recent", last_engaged_at=datetime.now(UTC) - timedelta(days=1)),
        Post(platform="instagram", source="manual_link", external_id="A", text="x", status="pending", relevance_score=0.9),
        Post(platform="instagram", source="manual_link", external_id="B", text="y", status="pending", relevance_score=0.8),
    ])
    await session.commit()
    from app.prefs import SettingsPatch, save_settings
    await save_settings(session, "tester", SettingsPatch(engagement_daily_target=4))
    first = (await client.get("/api/engagement/today", headers=H)).json()
    kinds = [t["kind"] for t in first["tasks"]]
    assert kinds == ["opportunity", "opportunity", "creator", "creator"]
    assert {t["creator"]["handle"] for t in first["tasks"] if t["creator"]} == {"never1", "never2"}
    assert (await client.get("/api/engagement/today", headers=H)).json()["tasks"] == first["tasks"]  # idempotent

    task = next(t for t in first["tasks"] if t["kind"] == "creator")
    await client.post(f"/api/engagement/tasks/{task['id']}/done", headers=H)
    c = await session.scalar(select(Creator).where(Creator.handle == task["creator"]["handle"]))
    await session.refresh(c)
    assert c.last_engaged_at is not None
    assert (await client.get("/api/engagement/today", headers=H)).json()["done"] == 1


T = date(2026, 1, 10)


@pytest.mark.parametrize("done,expected", [
    ({T: 5, T - timedelta(1): 5, T - timedelta(2): 5}, 3),
    ({T - timedelta(1): 5, T - timedelta(2): 5}, 2),
    ({T: 1, T - timedelta(1): 5}, 1),
    ({T - timedelta(2): 5}, 0),
])
def test_streak(done, expected):
    assert growth.streak(done, T, 5) == expected
