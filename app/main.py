import logging

from fastapi import Depends, FastAPI
from sqlalchemy import select

from app.api import analytics, opportunities, sources, webhooks
from app.config import settings
from app.deps import current_user, get_session
from app.models import AuditLog

logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs full URLs, which carry access_token

app = FastAPI(title="Curiox Growth Engine")
auth = [Depends(current_user)]
for r in (opportunities.router, sources.router, analytics.router):
    app.include_router(r, prefix="/api", dependencies=auth)
app.include_router(webhooks.router, prefix="/api")  # HMAC-authenticated


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/config", dependencies=auth)
async def config():
    keys = ("HUMAN_APPROVAL_REQUIRED", "THIRD_PARTY_AUTO_COMMENT", "DISCOVERY_MODE", "RELEVANCE_THRESHOLD",
            "RELEVANCE_WEIGHTS", "LLM_PROVIDER", "LLM_MODEL")
    return {k: getattr(settings, k) for k in keys}  # safe flags only, never secrets


@app.get("/api/audit", dependencies=auth)
async def audit_log(limit: int = 100, session=Depends(get_session)):
    rows = await session.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500)))
    return [{"id": a.id, "created_at": a.created_at, "actor": a.actor, "action": a.action,
             "entity": a.entity, "entity_id": a.entity_id, "data": a.data} for a in rows]
