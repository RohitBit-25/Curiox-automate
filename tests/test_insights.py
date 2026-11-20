from datetime import UTC, date, datetime

from sqlalchemy import func, select

from app import insights
from app.models import Account, AccountSnapshot, ContentItem
from app.platforms import PlatformError
from tests.conftest import H, FakeAdapter

TODAY = date(2026, 11, 20)


async def account(session):
    a = Account(id=1, platform="instagram", handle="curiox.os", external_user_id="17841", access_token="t" * 12)
    session.add(a)
    await session.commit()
    return a


async def test_snapshot_upserts_and_tolerates_follows_error(session):
    a = await account(session)
    fake = FakeAdapter(insights={"reach": 50, "total_interactions": 5})
    await insights.snapshot_account(session, a, fake, TODAY)
    await insights.snapshot_account(session, a, fake, TODAY)
    assert await session.scalar(select(func.count()).select_from(AccountSnapshot)) == 2  # yesterday + day before
    y = await session.scalar(select(AccountSnapshot).where(AccountSnapshot.day == date(2026, 11, 19)))
    assert y.followers == 1234 and y.metrics["reach"] == 50

    class Flaky(FakeAdapter):
        async def account_insights(self, ig, metrics, since, until):
            if metrics == "follows_and_unfollows":
                raise PlatformError("not available under 100 followers", 100)
            return await super().account_insights(ig, metrics, since, until)
    await insights.snapshot_account(session, a, Flaky(insights={"reach": 70}), TODAY)
    await session.refresh(y)
    assert y.metrics["reach"] == 70


async def test_media_insights_uses_reels_metrics(session):
    a = await account(session)
    session.add(ContentItem(week_start=TODAY, format="reel", pillar="x", status="published", external_media_id="m-1",
                            published_at=datetime.now(UTC)))
    await session.commit()
    fake = FakeAdapter()
    assert await insights.media_insights(session, a, fake) == 1
    call = next(c for c in fake.calls if c[0] == "media_insights")
    assert "ig_reels_avg_watch_time" in call[2]


async def test_today_without_keys(client):
    r = await client.get("/api/today", headers=H)
    assert r.status_code == 200
    codes = {a["code"] for a in r.json()["alerts"]}
    assert {"llm_not_configured", "instagram_not_connected"} <= codes
    assert r.json()["kpis"]["followers"] is None


async def test_content_analytics_er(client, session):
    session.add(ContentItem(week_start=TODAY, format="image", pillar="Dev", status="published", external_media_id="m-9",
                            published_at=datetime.now(UTC),
                            insights={"reach": 200, "likes": 10, "comments": 4, "saved": 4, "shares": 2}))
    await session.commit()
    body = (await client.get("/api/analytics/content", headers=H)).json()
    assert body["items"][0]["er"] == 0.1 and body["by_pillar"][0] == {"key": "Dev", "posts": 1, "avg_reach": 200,
                                                                       "avg_er": 0.1}
