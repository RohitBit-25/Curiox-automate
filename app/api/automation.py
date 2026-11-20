"""System status, automation settings, funnels, and the shared 'is X configured' guards."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.config import settings
from app.deps import current_user, get_session
from app.llm import llm_configured
from app.models import Account, FunnelRule, FunnelSend, audit, now
from app.prefs import SettingsPatch, get_settings, save_settings

router = APIRouter()


def require_llm():
    if not llm_configured():
        raise HTTPException(503, {"code": "llm_not_configured",
                                  "message": "Add ANTHROPIC_API_KEY (or OPENAI_COMPAT_*) to .env and restart."})


async def get_account(session) -> Account | None:
    return await session.scalar(select(Account).where(Account.platform == "instagram").order_by(Account.id))


async def require_account(session) -> Account:
    a = await get_account(session)
    if a is None:
        raise HTTPException(409, {"code": "instagram_not_connected",
                                  "message": "Connect the Instagram account in Settings first."})
    return a


@router.get("/status")
async def status(session=Depends(get_session)):
    a = await get_account(session)
    return {
        "llm": {"configured": llm_configured(), "provider": settings.LLM_PROVIDER, "model": settings.LLM_MODEL},
        "instagram": {"connected": a is not None, "active": bool(a and a.status == "active"),
                      "handle": a.handle if a else None, "paused_reason": a.paused_reason if a else None,
                      "token_expires_at": a.token_expires_at if a else None},
        "webhooks": {"configured": bool(settings.IG_APP_SECRET and settings.IG_WEBHOOK_VERIFY_TOKEN)},
        "publishing": {"configured": settings.PUBLIC_BASE_URL.startswith("https://")},
        "automation_paused": settings.AUTOMATION_PAUSED,
        "safety": {k: getattr(settings, k) for k in
                   ("HUMAN_APPROVAL_REQUIRED", "THIRD_PARTY_AUTO_COMMENT", "DISCOVERY_MODE")},
    }


@router.get("/settings")
async def read_settings(session=Depends(get_session)):
    return await get_settings(session)


@router.patch("/settings")
async def patch_settings(body: SettingsPatch, user=Depends(current_user), session=Depends(get_session)):
    return await save_settings(session, user, body)


# ---------------------------------------------------------------- funnels


class FunnelBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    keywords: list[str] = Field(min_length=1, max_length=10)
    media_id: str | None = Field(None, max_length=64)
    dm_text: str = Field(min_length=1)
    public_replies: list[str] = Field(default=[], max_length=5)
    active: bool = True

    @field_validator("keywords")
    @classmethod
    def _kw(cls, v):
        v = [k.strip() for k in v if k.strip()]
        if not v or any(not 2 <= len(k) <= 40 for k in v):
            raise ValueError("keywords must be 2-40 characters each")
        return v

    @field_validator("dm_text")
    @classmethod
    def _dm(cls, v):
        if len(v.encode()) > 1000:
            raise ValueError("Instagram DMs are limited to 1000 bytes")
        return v

    @field_validator("public_replies")
    @classmethod
    def _pr(cls, v):
        v = [x.strip() for x in v if x.strip()]
        if any(len(x) > 300 for x in v):
            raise ValueError("public replies must be 300 characters or less")
        return v


async def funnel_dict(session, r: FunnelRule) -> dict:
    async def count(*where):
        return await session.scalar(select(func.count()).select_from(FunnelSend).where(FunnelSend.rule_id == r.id, *where))
    return {"id": r.id, "name": r.name, "keywords": r.keywords, "media_id": r.media_id, "dm_text": r.dm_text,
            "public_replies": r.public_replies, "active": r.active, "created_at": r.created_at,
            "sent_7d": await count(FunnelSend.status == "sent", FunnelSend.created_at >= now() - timedelta(days=7)),
            "sent_total": await count(FunnelSend.status == "sent"),
            "failed_total": await count(FunnelSend.status == "failed")}


async def load_rule(session, rule_id: int) -> FunnelRule:
    r = await session.get(FunnelRule, rule_id)
    if r is None:
        raise HTTPException(404, "funnel not found")
    return r


@router.get("/funnels")
async def list_funnels(session=Depends(get_session)):
    return [await funnel_dict(session, r) for r in await session.scalars(select(FunnelRule).order_by(FunnelRule.id.desc()))]


@router.post("/funnels")
async def create_funnel(body: FunnelBody, user=Depends(current_user), session=Depends(get_session)):
    r = FunnelRule(**body.model_dump())
    session.add(r)
    await session.flush()
    audit(session, user, "funnel_created", "funnel", r.id, keywords=r.keywords)
    await session.commit()
    return await funnel_dict(session, r)


@router.patch("/funnels/{rule_id}")
async def update_funnel(rule_id: int, body: FunnelBody, user=Depends(current_user), session=Depends(get_session)):
    r = await load_rule(session, rule_id)
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    audit(session, user, "funnel_updated", "funnel", r.id, active=r.active, keywords=r.keywords)
    await session.commit()
    return await funnel_dict(session, r)


@router.delete("/funnels/{rule_id}")
async def deactivate_funnel(rule_id: int, user=Depends(current_user), session=Depends(get_session)):
    r = await load_rule(session, rule_id)
    r.active = False  # soft delete: keeps send history and the one-DM-per-person lock
    audit(session, user, "funnel_deactivated", "funnel", r.id)
    await session.commit()
    return await funnel_dict(session, r)


@router.get("/funnels/{rule_id}/sends")
async def funnel_sends(rule_id: int, limit: int = 50, session=Depends(get_session)):
    rows = await session.scalars(select(FunnelSend).where(FunnelSend.rule_id == rule_id)
                                 .order_by(FunnelSend.id.desc()).limit(min(limit, 200)))
    return [{"id": s.id, "post_id": s.post_id, "commenter_id": s.commenter_id, "status": s.status, "error": s.error,
             "public_reply_text": s.public_reply_text, "created_at": s.created_at} for s in rows]
