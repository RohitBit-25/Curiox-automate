import pytest
from sqlalchemy import select

from app.models import Account, Approval, AuditLog, EngagementEvent, Post
from app.platforms import PlatformError
from app.publisher import check_publishable, publish
from tests.conftest import FakeAdapter


def make(source="own_comment", account_status="active", decision="approved", text="Thanks, retries are handled by Celery."):
    account = Account(id=1, platform="instagram", handle="curiox", external_user_id="1", access_token="tok",
                      status=account_status)
    post = Post(id=1, platform="instagram", source=source, external_id="c1", text="How do you retry?",
                account_id=1 if source == "own_comment" else None, status="approved")
    approval = Approval(id=1, post_id=1, decision=decision, final_text=text, actor="tester")
    return post, approval, account


@pytest.mark.parametrize("kwargs,already,reason", [
    ({"source": "manual_link"}, False, "third_party_never_auto_published"),
    ({"account_status": "paused"}, False, "account_paused"),
    ({"decision": "rejected"}, False, "not_approved"),
    ({}, True, "duplicate_text"),
    ({"text": ""}, False, "invalid_length"),
    ({}, False, None),
])
def test_gate(kwargs, already, reason):
    post, approval, account = make(**kwargs)
    acct = account if post.source == "own_comment" else None
    assert check_publishable(post, approval, acct, already_published=already) == reason


async def seed(session, **kw):
    post, approval, account = make(**kw)
    session.add_all([account, post])
    await session.flush()
    session.add(approval)
    await session.commit()
    return post, approval, account


async def test_publish_once(session):
    _, approval, _ = await seed(session)
    fake = FakeAdapter()
    assert await publish(session, approval.id, fake) == "published"
    assert await publish(session, approval.id, fake) == "duplicate_text"
    assert len(fake.replies) == 1 and fake.token == "tok"
    event = await session.scalar(select(EngagementEvent))
    assert event.status == "published" and event.external_comment_id == "reply-1"
    assert (await session.get(Post, 1)).status == "published"


async def test_third_party_never_published(session):
    _, approval, _ = await seed(session, source="manual_link")
    fake = FakeAdapter()
    assert await publish(session, approval.id, fake) == "third_party_never_auto_published"
    assert fake.replies == []


async def test_permission_error_pauses_account(session):
    _, approval, _ = await seed(session)
    fake = FakeAdapter(error=PlatformError("token expired", code=190, pause=True))
    assert await publish(session, approval.id, fake) == "failed"
    account = await session.get(Account, 1)
    assert account.status == "paused"
    assert (await session.scalar(select(EngagementEvent))).status == "failed"
    actions = set(await session.scalars(select(AuditLog.action)))
    assert {"publish_failed", "account_paused"} <= actions
