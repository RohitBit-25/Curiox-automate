import hmac

from fastapi import Header, HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.DATABASE_URL)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session():
    async with SessionLocal() as session:
        yield session


def current_user(authorization: str = Header(default="")) -> str:
    token = authorization.removeprefix("Bearer ").strip()
    for name, expected in settings.dashboard_users.items():
        if token and hmac.compare_digest(token, expected):
            return name
    raise HTTPException(401, "invalid or missing dashboard token")
