"""System status, automation settings, and the shared 'is X configured' guards."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.automation import SettingsPatch, get_settings, save_settings
from app.config import settings
from app.deps import current_user, get_session
from app.llm import llm_configured
from app.models import Account

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
