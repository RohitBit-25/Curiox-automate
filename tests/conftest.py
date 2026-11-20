import os

from cryptography.fernet import Fernet

# Must run before any app import: settings are read at import time.
os.environ.update(
    DATABASE_URL="sqlite+aiosqlite://",
    TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    DASHBOARD_USERS="tester:t0ken",
    IG_APP_SECRET="app-secret",
    IG_WEBHOOK_VERIFY_TOKEN="verify-me",
    # Pin everything key-dependent so a developer's shell/.env can't change test behaviour.
    LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="", PUBLIC_BASE_URL="", AUTOMATION_PAUSED="false",
)

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.models import Base  # noqa: E402


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


class FakeLLM:
    """Returns queued responses in order."""
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    async def json(self, system, prompt, schema, effort="low"):
        self.calls.append(prompt)
        return self.responses.pop(0)


class FakeAdapter:
    """Records every call in .calls; returns deterministic ids. errors={"method": PlatformError(...)}."""
    def __init__(self, error=None, errors=None, statuses=None, quota=(0, 50), me=None, insights=None, profile=None):
        self.error, self.errors = error, errors or {}
        self.statuses, self.quota = list(statuses or ["FINISHED"]), quota
        self.me_data = me or {"user_id": "17841", "username": "curiox.os", "followers_count": 1234, "media_count": 10}
        self.insights, self.profile = insights or {}, profile or {}
        self.calls, self.replies, self.counter = [], [], 0

    def __call__(self, platform, token):  # usable as adapter_factory
        self.token = token
        return self

    def _hit(self, name, *args):
        self.calls.append((name, *args))
        if err := self.errors.get(name):
            raise err

    def _id(self, prefix):
        self.counter += 1
        return f"{prefix}-{self.counter}"

    async def reply_to_comment(self, comment_id, message):
        if self.error:
            raise self.error
        self._hit("reply_to_comment", comment_id, message)
        self.replies.append((comment_id, message))
        return f"reply-{len(self.replies)}"

    async def me(self):
        self._hit("me")
        return self.me_data

    async def create_container(self, ig, params):
        self._hit("create_container", ig, params)
        return self._id("container")

    async def container_status(self, cid):
        self._hit("container_status", cid)
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    async def publish_container(self, ig, cid):
        self._hit("publish_container", ig, cid)
        return self._id("media")

    async def publishing_limit(self, ig):
        self._hit("publishing_limit", ig)
        return self.quota

    async def media_info(self, mid):
        self._hit("media_info", mid)
        return {"permalink": f"https://www.instagram.com/p/{mid}/"}

    async def media_insights(self, mid, metrics):
        self._hit("media_insights", mid, metrics)
        return {m: self.insights.get(m, 1) for m in metrics.split(",")}

    async def account_insights(self, ig, metrics, since, until):
        self._hit("account_insights", ig, metrics)
        return {m: self.insights.get(m, 0) for m in metrics.split(",")}

    async def private_reply(self, ig, comment_id, text):
        self._hit("private_reply", ig, comment_id, text)
        return self._id("msg")

    async def send_message(self, ig, igsid, text):
        self._hit("send_message", ig, igsid, text)
        return self._id("msg")

    async def user_profile(self, igsid):
        self._hit("user_profile", igsid)
        return self.profile

    async def subscribe_webhooks(self, ig, fields="comments,messages"):
        self._hit("subscribe_webhooks", ig, fields)
        return True

H = {"Authorization": "Bearer t0ken"}


@pytest.fixture
async def client(session, monkeypatch):
    """API client bound to the test session. Celery enqueues are recorded in client.queued[name]."""
    import httpx

    from app import workers
    from app.deps import get_session
    from app.main import app

    queued: dict[str, list] = {}
    for name in [n for n in dir(workers) if n.startswith("enqueue_")]:
        bucket = queued.setdefault(name.removeprefix("enqueue_"), [])
        monkeypatch.setattr(workers, name, lambda *a, _b=bucket: _b.append(a[0] if len(a) == 1 else a))

    async def override():
        yield session
    app.dependency_overrides[get_session] = override
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        c.queued = queued
        yield c
    app.dependency_overrides.clear()
