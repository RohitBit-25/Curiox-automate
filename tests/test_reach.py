"""Reading other accounts (Business Discovery), bulk adds and the manual follow list."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import growth
from app.models import Account, Creator, Lead, Post
from app.platforms import PlatformError
from tests.conftest import H, FakeAdapter, FakeLLM
from tests.test_growth import AUDIT


def ts(days_ago=0):
    return (datetime.now(UTC) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")


def media(code, caption="Shipped a Next.js dashboard for a coaching centre", days_ago=0):
    return {"id": f"m{code}", "caption": caption, "permalink": f"https://www.instagram.com/reel/{code}/",
            "timestamp": ts(days_ago)}


def test_parse_handles():
    text = "@CodeWithX, https://www.instagram.com/udaipur.library/?hl=en\nfoo_bar  @codewithx https://instagram.com/p/ABC/ bad!name"
    assert growth.parse_handles(text) == ["codewithx", "udaipur.library", "foo_bar"]


async def seed(session, *handles):
    session.add(Account(id=1, platform="instagram", handle="curiox.os", external_user_id="17841", access_token="t" * 12))
    session.add_all([Creator(handle=h) for h in handles])
    await session.commit()


async def test_fetch_creator_posts(session):
    await seed(session, "dev1", "gone")
    fake = FakeAdapter(discovery={"dev1": {"username": "dev1", "name": "Dev One", "media": [
        media("NEW1"), media("SHORT", caption="hi"), media("OLD", days_ago=30)]}},
        errors={})
    orig = fake.business_discovery

    async def bd(handle, n=3):
        if handle == "gone":
            raise PlatformError("Invalid user id", 110)
        return await orig(handle, n)
    fake.business_discovery = bd

    r = await growth.fetch_creator_posts(session, fake)
    assert r["status"] == "ok" and r["failed"] == ["gone"] and len(r["new_post_ids"]) == 1
    p = await session.get(Post, r["new_post_ids"][0])
    assert (p.external_id, p.source, p.author_handle) == ("NEW1", "manual_link", "dev1") and p.creator_id
    assert (await session.scalar(select(Creator).where(Creator.handle == "dev1"))).display_name == "Dev One"
    assert (await growth.fetch_creator_posts(session, fake))["new_post_ids"] == []  # deduped


async def test_fetch_stops_without_permission(session):
    await seed(session, "a", "b")
    fake = FakeAdapter(errors={"business_discovery": PlatformError("Application does not have permission", 10)})
    r = await growth.fetch_creator_posts(session, fake)
    assert r["status"] == "no_permission"
    assert [c[0] for c in fake.calls].count("business_discovery") == 1  # stopped at the first
    assert (await session.get(Account, 1)).status == "active"  # a missing permission never pauses the account


async def test_research_fills_empty_lead_profile(session):
    await seed(session)
    session.add(Lead(handle="gangaur.lib"))
    await session.commit()
    fake = FakeAdapter(discovery={"gangaur.lib": {"name": "Gangaur Library", "biography": "95 seats, AC, Wi-Fi",
                                                  "website": "https://x.in", "followers_count": 800, "media_count": 40,
                                                  "media": [media("L1", caption="Admissions open for December")]}})
    lead = await growth.research_lead(session, 1, FakeLLM(AUDIT), fake)
    assert lead.name == "Gangaur Library" and lead.website == "https://x.in"
    assert "95 seats" in lead.profile_text and "Admissions open" in lead.profile_text
    assert lead.stage == "researched"


async def test_bulk_add(client, session):
    r = (await client.post("/api/creators/bulk", headers=H, json={"handles": "@a, b\nhttps://instagram.com/c/"})).json()
    assert r["added"] == ["a", "b", "c"]
    r = (await client.post("/api/creators/bulk", headers=H, json={"handles": "a d"})).json()
    assert r == {"added": ["d"], "skipped": ["a"]}

    r = (await client.post("/api/leads/bulk", headers=H, json={"handles": "lib1 lib2", "niche": "library"})).json()
    assert r["added"] == ["lib1", "lib2"] and not r["researching"]  # no LLM key in tests
    assert (await session.scalar(select(Lead).where(Lead.handle == "lib1"))).niche == "library"


async def test_fetch_endpoint_enqueues_drafts(client, session, monkeypatch):
    await seed(session, "dev1")
    fake = FakeAdapter(discovery={"dev1": {"media": [media("X1")]}})
    monkeypatch.setattr(growth, "get_adapter", fake)
    r = (await client.post("/api/engagement/fetch", headers=H)).json()
    assert r["new"] == 1 and len(client.queued["process"]) == 1


async def test_follow_list(client, session, monkeypatch):
    monkeypatch.setattr(growth, "FOLLOW_DAILY", 3)
    session.add_all([Creator(handle=f"c{i}") for i in range(3)] + [Lead(handle="biz", rubric_total=70)])
    session.add(Creator(handle="old", followed_at=datetime.now(UTC) - timedelta(days=5)))
    await session.commit()
    r = (await client.get("/api/engagement/follows", headers=H)).json()
    assert [x["handle"] for x in r["to_follow"]] == ["c0", "c1", "c2"]
    assert [x["handle"] for x in r["check_back"]] == ["old"]

    c0 = r["to_follow"][0]
    assert (await client.post(f"/api/engagement/follows/creator/{c0['id']}", headers=H, json={"action": "followed"})).json()["ok"]
    r = (await client.get("/api/engagement/follows", headers=H)).json()
    assert r["followed_today"] == 1 and [x["handle"] for x in r["to_follow"]] == ["c1", "c2"]  # daily cap holds

    old = r["check_back"][0]
    await client.post(f"/api/engagement/follows/creator/{old['id']}", headers=H, json={"action": "followed_back"})
    r = (await client.get("/api/engagement/follows", headers=H)).json()
    assert r["check_back"] == [] and r["followed_back"] == 1 and r["checked"] == 1
    assert (await client.post("/api/engagement/follows/lead/999", headers=H, json={"action": "followed"})).status_code == 404
