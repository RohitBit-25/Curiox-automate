from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import automation as auto
from app import publisher
from app.models import AUTO_ACTOR, Account, Approval, FunnelRule, FunnelSend, Post
from app.prefs import SettingsPatch, save_settings
from tests.conftest import FakeAdapter, FakeLLM
from tests.test_flow import ANALYSIS, GENERATION

DECISION = {"bucket": "support", "pick": 1, "safe_to_auto": True, "confidence": 0.95, "reason": "simple thanks"}


def rule(id=1, keywords=("link",), media_id=None, **kw):
    return FunnelRule(id=id, name=f"r{id}", keywords=list(keywords), media_id=media_id, dm_text="Here you go: x",
                      public_replies=kw.get("public_replies", []), active=kw.get("active", True))


def test_match_funnel():
    g, specific = rule(1, ["link"]), rule(2, ["link"], media_id="m-2")
    assert auto.match_funnel("LINK pls!!", "m-1", [g]) is g
    assert auto.match_funnel("send link", "m-1", [g]) is g
    assert auto.match_funnel("my linkedin is", "m-1", [g]) is None
    assert auto.match_funnel("link", "m-1", [specific]) is None
    assert auto.match_funnel("link", "m-2", [g, specific]) is specific
    assert auto.match_funnel("send me the guide", None, [rule(3, ["the guide"])]).id == 3
    assert auto.match_funnel("link", None, [rule(4, active=False)]) is None


async def seed(session, *, text="LINK please", author="555", age_days=0, rules=(), source="own_comment"):
    session.add(Account(id=1, platform="instagram", handle="curiox.os", external_user_id="17841", access_token="t" * 12))
    for r in rules:
        session.add(r)
    await session.flush()
    n = await session.scalar(select(Post.id).order_by(Post.id.desc())) or 0
    post = Post(platform="instagram", source=source, external_id=f"c-{n + 1}", text=text, author_id=author,
                author_handle="fan", account_id=1 if source == "own_comment" else None, parent_external_id="m-1",
                parent_text="Our post about Celery retries",
                published_at=datetime.now(UTC) - timedelta(days=age_days))
    session.add(post)
    await session.commit()
    return post


async def test_funnel_sends_once_per_person(session):
    fake = FakeAdapter()
    p1 = await seed(session, rules=[rule(public_replies=["Sent! Check your DMs"])])
    assert await auto.handle_comment(session, p1.id, llm=None, adapter_factory=fake) == "sent"
    assert ("private_reply", "17841", p1.external_id, "Here you go: x") in fake.calls
    assert ("reply_to_comment", p1.external_id, "Sent! Check your DMs") in fake.calls
    assert (await session.get(Post, p1.id)).status == "funnel"

    p2 = Post(platform="instagram", source="own_comment", external_id="c-2", text="link again", author_id="555",
              account_id=1, parent_external_id="m-1", published_at=datetime.now(UTC))
    session.add(p2)
    await session.commit()
    assert await auto.handle_comment(session, p2.id, adapter_factory=fake) == "duplicate"
    assert [c[0] for c in fake.calls].count("private_reply") == 1


async def test_funnel_guards(session, monkeypatch):
    old = await seed(session, age_days=8, rules=[rule()])
    assert await auto.handle_comment(session, old.id, adapter_factory=FakeAdapter()) == "window_expired"

    await save_settings(session, "tester", SettingsPatch(cap_funnel_dms_per_hour=1))
    session.add(FunnelSend(rule_id=1, post_id=old.id, comment_id="zz", commenter_id="other", status="sent"))
    await session.commit()
    p = Post(platform="instagram", source="own_comment", external_id="c-9", text="link", author_id="777",
             account_id=1, published_at=datetime.now(UTC))
    session.add(p)
    await session.commit()
    assert await auto.handle_comment(session, p.id, adapter_factory=FakeAdapter()) == "hourly_cap"

    monkeypatch.setattr(auto.settings, "AUTOMATION_PAUSED", True)
    p3 = Post(platform="instagram", source="own_comment", external_id="c-10", text="link", author_id="888",
              account_id=1, published_at=datetime.now(UTC))
    session.add(p3)
    await session.commit()
    assert await auto.handle_comment(session, p3.id, adapter_factory=FakeAdapter()) == "automation_paused"


GATE_OK = dict(mode="auto", paused=False, account_active=True, bucket="support", allowed=auto.COMMENT_AUTO, safe=True,
               confidence=0.95, min_conf=0.85, needs_review=False, warnings=[], flags=[], tells_verdict="PASS",
               sent_last_hour=0, cap=20, same_target_24h=0, target_cap=3)


@pytest.mark.parametrize("override,reason", [
    ({}, None), ({"mode": "draft"}, "mode_not_auto"), ({"paused": True}, "automation_paused"),
    ({"account_active": False}, "account_paused"), ({"bucket": "lead"}, "bucket_lead"), ({"safe": False}, "model_unsure"),
    ({"confidence": 0.5}, "low_confidence"), ({"needs_review": True}, "needs_human_review"),
    ({"warnings": ["x"]}, "factuality_warnings"), ({"flags": ["generic_praise"]}, "quality_flags"),
    ({"tells_verdict": "FLAGGED"}, "ai_tells"), ({"sent_last_hour": 20}, "hourly_cap"),
    ({"same_target_24h": 3}, "target_cap"),
])
def test_auto_block_reason(override, reason):
    assert auto.auto_block_reason(**{**GATE_OK, **override}) == reason


async def test_auto_reply_path_and_publish(session, monkeypatch):
    queued = []
    monkeypatch.setattr(auto.workers, "enqueue_publish", queued.append)
    await save_settings(session, "tester", SettingsPatch(mode_comments="auto"))
    post = await seed(session, text="This saved my weekend, thank you for the retry tip")
    llm = FakeLLM(ANALYSIS, GENERATION, DECISION)
    assert await auto.handle_comment(session, post.id, llm=llm) == "approved"
    approval = await session.scalar(select(Approval))
    assert approval.actor == AUTO_ACTOR and queued == [approval.id]

    fake = FakeAdapter()
    assert await publisher.publish(session, approval.id, fake) == "published"
    assert len(fake.replies) == 1


async def test_publisher_refuses_auto_approval_after_mode_change(session, monkeypatch):
    monkeypatch.setattr(auto.workers, "enqueue_publish", lambda _: None)
    await save_settings(session, "tester", SettingsPatch(mode_comments="auto"))
    post = await seed(session, text="Thanks, this helped a lot with my Celery setup")
    await auto.handle_comment(session, post.id, llm=FakeLLM(ANALYSIS, GENERATION, DECISION))
    await save_settings(session, "tester", SettingsPatch(mode_comments="draft"))
    approval = await session.scalar(select(Approval))
    fake = FakeAdapter()
    assert await publisher.publish(session, approval.id, fake) == "automation_disabled"
    assert fake.replies == []


async def test_third_party_never_auto(session, monkeypatch):
    monkeypatch.setattr(auto.workers, "enqueue_publish", lambda _: pytest.fail("must not publish"))
    await save_settings(session, "tester", SettingsPatch(mode_comments="auto"))
    post = await seed(session, source="manual_link", text="Replaced cron with Celery, what do you use for retries?",
                      rules=[rule(keywords=["retries"])])
    llm = FakeLLM(ANALYSIS, GENERATION, DECISION)
    assert await auto.handle_comment(session, post.id, llm=llm) == "pipeline"
    assert len(llm.responses) == 1  # decision call never made
    assert (await session.get(Post, post.id)).status == "pending"
    assert await session.scalar(select(FunnelSend)) is None


async def test_mode_off_and_no_llm(session):
    await save_settings(session, "tester", SettingsPatch(mode_comments="off"))
    post = await seed(session, text="nice video")
    assert await auto.handle_comment(session, post.id) == "mode_off"
    assert (await session.get(Post, post.id)).skip_reason == "mode_off"


async def test_discover_pauses_on_dead_token_without_crashing(session, monkeypatch):
    from app import workers
    session.add(Account(id=1, platform="instagram", handle="curiox.os", external_user_id="17841", access_token="t" * 12))
    await session.commit()

    class Dead(FakeAdapter):
        async def list_own_media(self, limit=10):
            raise workers.PlatformError("token expired", 190, pause=True)
    monkeypatch.setattr(workers, "get_adapter", lambda platform, token: Dead())
    monkeypatch.setattr(workers, "enqueue_process", lambda _: None)
    await workers._discover(session)
    acct = await session.get(Account, 1, populate_existing=True)
    assert acct.status == "paused" and "expired" in acct.paused_reason
