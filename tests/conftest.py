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
    def __init__(self, error=None):
        self.error, self.replies = error, []

    def __call__(self, platform, token):  # usable as adapter_factory
        self.token = token
        return self

    async def reply_to_comment(self, comment_id, message):
        if self.error:
            raise self.error
        self.replies.append((comment_id, message))
        return f"reply-{len(self.replies)}"


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
