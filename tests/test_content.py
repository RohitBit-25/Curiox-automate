from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import content as c
from app.models import Account, ContentItem
from tests.conftest import H, FakeAdapter, FakeLLM

MONDAY = date(2026, 11, 23)
PLAN = {"engagement_note": "Join Celery threads.", "items": [
    {"date": "2026-11-24", "time": "19:30", "format": "reel", "pillar": "AI & automation", "type": "teach",
     "angle": "Retry storms in Celery and the jitter fix", "hook_formula": "Cost Confession", "goal": "saves", "why": "x"},
    {"date": "2026-11-26", "time": "19:00", "format": "carousel", "pillar": "nonexistent pillar", "type": "opinion",
     "angle": "Cron is fine, actually", "hook_formula": "none", "goal": "shares", "why": "y"},
    {"date": "2026-12-09", "time": "10:00", "format": "image", "pillar": "Dev & building", "type": "proof",
     "angle": "out of week", "hook_formula": "none", "goal": "follows", "why": "z"},
]}
DRAFT = {
    "hooks": [{"spoken": "Hey guys, today let's talk retries", "on_screen": "RETRIES", "formula": "none"},
              {"spoken": "$18,000 is what one missing clause cost me.", "on_screen": "$18K", "formula": "Cost Confession"}],
    "caption": "One retry setting turned 40 failed jobs into 0.\n\nSave this for your next Celery deploy.",
    "hashtags": [{"tag": "celery", "tier": "niche"}, {"tag": "pythondevs", "tier": "niche"}, {"tag": "#automation", "tier": "mid"}],
    "cta": "Save this", "notes": "", "beats": [{"t": "0-2s", "say": "x", "show": "y", "on_screen": "z"}],
    "slides": [], "frames": [],
}


async def test_plan_week_filters_and_converts_timezone(session):
    ids = await c.plan_week(session, MONDAY, "brief", 5, "tester", FakeLLM(PLAN))
    items = [await session.get(ContentItem, i) for i in ids]
    assert len(items) == 2
    assert c.aware(items[0].scheduled_at) == datetime(2026, 11, 24, 14, 0, tzinfo=UTC)  # 19:30 IST
    assert items[1].pillar == "Dev & building"  # unknown pillar -> first pillar
    assert items[0].hook_formula == "Cost Confession" and items[1].hook_formula is None


async def test_draft_item_scores_and_lints(session):
    [item_id, _] = await c.plan_week(session, MONDAY, "", 5, "tester", FakeLLM(PLAN))
    item = await c.draft_item(session, item_id, FakeLLM(DRAFT))
    assert item.status == "drafted" and item.hashtags == ["celery", "pythondevs", "automation"]
    scores = [h["score"] for h in item.draft["hook_scores"]]
    assert scores == sorted(scores, reverse=True)
    assert item.draft["hook_scores"][0]["classified"]["name"] == "Cost Confession"
    assert item.draft["caption_lint"]["verdict"] in ("READY", "REVIEW")


async def test_api_create_approve_and_llm_gate(client):
    r = await client.post("/api/content", headers=H, json={
        "format": "image", "pillar": "Dev & building", "caption": "I cut deploys from 40 min to 6. Here's the one change."})
    item = r.json()
    assert item["status"] == "drafted"
    bad = await client.patch(f"/api/content/{item['id']}", headers=H, json={"hashtags": list("abcdef")})
    assert bad.status_code == 200
    r = await client.post(f"/api/content/{item['id']}/approve", headers=H)
    assert r.status_code == 422
    await client.patch(f"/api/content/{item['id']}", headers=H, json={"hashtags": ["devops", "ci"]})
    r = await client.post(f"/api/content/{item['id']}/approve", headers=H)
    assert r.status_code == 200 and r.json()["approved_by"] == "tester"

    r = await client.post("/api/content/plan", headers=H, json={"week_start": MONDAY.isoformat()})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "llm_not_configured"


async def test_upload_and_public_media_route(client, tmp_path, monkeypatch):
    monkeypatch.setattr(c.settings, "MEDIA_DIR", str(tmp_path))
    item = (await client.post("/api/content", headers=H, json={"format": "image", "pillar": "x"})).json()
    url = f"/api/content/{item['id']}/media?kind=image"
    png = await client.put(url, headers={**H, "Content-Type": "image/png"}, content=b"\x89PNG....")
    assert png.status_code == 415
    fake = await client.put(url, headers={**H, "Content-Type": "image/jpeg"}, content=b"not a jpeg at all")
    assert fake.status_code == 415
    jpeg = b"\xff\xd8\xff" + b"0" * 100
    r = await client.put(url, headers={**H, "Content-Type": "image/jpeg"}, content=jpeg)
    assert r.status_code == 200
    media = r.json()["media"][0]
    got = await client.get(media["url"])
    assert got.status_code == 200 and got.content == jpeg
    assert (await client.get("/media/x.jpg")).status_code == 404
    assert (await client.get("/media/..%2Fetc%2Fpasswd")).status_code == 404


def _item(**kw):
    base = dict(status="approved", approved_by="tester", format="image", caption="ok", hashtags=[],
                media=[{"file": "a.jpg", "kind": "image"}], scheduled_at=datetime(2026, 1, 1, tzinfo=UTC))
    return ContentItem(**{**base, **kw})


ACTIVE = Account(status="active")
NOW = datetime(2026, 1, 2, tzinfo=UTC)


@pytest.mark.parametrize("item,account,base,paused,reason", [
    (_item(), ACTIVE, "https://x", False, None),
    (_item(approved_by=None), ACTIVE, "https://x", False, "not_approved"),
    (_item(), ACTIVE, "https://x", True, "automation_paused"),
    (_item(), Account(status="paused"), "https://x", False, "account_paused"),
    (_item(), ACTIVE, "", False, "publishing_not_configured"),
    (_item(scheduled_at=datetime(2026, 2, 1, tzinfo=UTC)), ACTIVE, "https://x", False, "not_due"),
    (_item(format="carousel"), ACTIVE, "https://x", False, "invalid_media"),
    (_item(format="reel"), ACTIVE, "https://x", False, "invalid_media"),
    (_item(hashtags=list("abcdef")), ACTIVE, "https://x", False, "invalid_caption"),
])
def test_publish_gate(item, account, base, paused, reason):
    assert c.check_content_publishable(item, account, now_=NOW, public_base=base, paused=paused) == reason


@pytest.mark.parametrize("code,age,action", [
    ("FINISHED", 1, "publish"), ("PUBLISHED", 1, "already_published"), ("ERROR", 1, "fail:container_error"),
    ("IN_PROGRESS", 1, "wait"), ("IN_PROGRESS", 20, "fail:container_timeout"),
])
def test_next_action(code, age, action):
    assert c.next_action(code, NOW - timedelta(minutes=age), NOW) == action


async def seed_due(session, **kw):
    session.add(Account(id=1, platform="instagram", handle="curiox.os", external_user_id="17841", access_token="t" * 12))
    item = _item(week_start=MONDAY, pillar="x", **kw)
    session.add(item)
    await session.commit()
    return item.id


async def test_publish_state_machine_publishes_once(session, monkeypatch):
    monkeypatch.setattr(c.settings, "PUBLIC_BASE_URL", "https://curiox.example")
    item_id = await seed_due(session)
    fake = FakeAdapter(statuses=["FINISHED"])
    assert await c.publish_step(session, item_id, fake) == "processing"
    assert await c.publish_step(session, item_id, fake) == "published"
    assert await c.publish_step(session, item_id, fake) == "noop"
    assert [x[0] for x in fake.calls].count("publish_container") == 1
    item = await session.get(ContentItem, item_id)
    assert item.external_media_id and item.permalink
    container = next(x for x in fake.calls if x[0] == "create_container")
    assert container[2]["image_url"].startswith("https://curiox.example/media/")


async def test_publish_quota_and_container_error(session, monkeypatch):
    monkeypatch.setattr(c.settings, "PUBLIC_BASE_URL", "https://curiox.example")
    item_id = await seed_due(session)
    assert await c.publish_step(session, item_id, FakeAdapter(quota=(50, 50))) == "quota"
    assert (await session.get(ContentItem, item_id)).error == "quota_reached"
    fake = FakeAdapter(statuses=["ERROR"])
    await c.publish_step(session, item_id, fake)
    assert await c.publish_step(session, item_id, fake) == "failed"
    assert (await session.get(ContentItem, item_id)).error == "container_error"


async def test_carousel_children(session, monkeypatch):
    monkeypatch.setattr(c.settings, "PUBLIC_BASE_URL", "https://curiox.example")
    item_id = await seed_due(session, format="carousel",
                             media=[{"file": "a.jpg", "kind": "image"}, {"file": "b.jpg", "kind": "image"}])
    fake = FakeAdapter()
    await c.publish_step(session, item_id, fake)
    containers = [x[2] for x in fake.calls if x[0] == "create_container"]
    assert len(containers) == 3 and containers[-1]["children"] == "container-1,container-2"
    ticked = await c.publish_tick(session, fake)
    assert ticked == {item_id: "published"}
    assert (await session.scalar(select(ContentItem.status))) == "published"


async def test_datetimes_roundtrip_as_utc(client):
    """An IST input must be stored as the same instant and come back explicitly UTC (SQLite drops tzinfo)."""
    item = (await client.post("/api/content", headers=H, json={
        "format": "reel", "pillar": "x", "scheduled_at": "2026-11-24T19:30:00+05:30"})).json()
    got = (await client.get(f"/api/content/{item['id']}", headers=H)).json()["scheduled_at"]
    assert datetime.fromisoformat(got) == datetime(2026, 11, 24, 14, 0, tzinfo=UTC)
    assert got.endswith(("Z", "+00:00"))
