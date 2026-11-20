"""Instagram webhooks: comments on Curiox's own media. Authenticated by HMAC signature, not dashboard token."""
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select

from app import workers
from app.config import settings
from app.deps import get_session
from app.discovery import ingest_item
from app.models import Account
from app.platforms import RemoteItem

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/webhooks/instagram")
async def verify(mode: str = Query("", alias="hub.mode"), token: str = Query("", alias="hub.verify_token"),
                 challenge: str = Query("", alias="hub.challenge")):
    expected = settings.IG_WEBHOOK_VERIFY_TOKEN
    if mode == "subscribe" and expected and hmac.compare_digest(token, expected):
        return PlainTextResponse(challenge)
    return Response(status_code=403)


@router.post("/webhooks/instagram")
async def receive(request: Request, session=Depends(get_session)):
    raw = await request.body()
    secret = settings.IG_APP_SECRET.encode()
    expected = "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()
    if not secret or not hmac.compare_digest(request.headers.get("X-Hub-Signature-256", ""), expected):
        return Response(status_code=403)

    new_ids = []
    try:
        for entry in json.loads(raw).get("entry", []):
            account = await session.scalar(select(Account).where(
                Account.external_user_id == str(entry.get("id")), Account.status == "active"))
            if account is None:
                continue
            for change in entry.get("changes", []):
                v = change.get("value", {})
                if change.get("field") != "comments" or v.get("parent_id"):
                    continue
                frm = v.get("from", {})
                item = RemoteItem(
                    external_id=v["id"], text=v.get("text", ""), author_handle=frm.get("username"),
                    author_id=frm.get("id"), parent_external_id=(v.get("media") or {}).get("id"),
                    parent_text=None, url=None,
                    published_at=datetime.fromtimestamp(entry["time"], UTC) if entry.get("time") else None)
                if post := await ingest_item(session, account, item):
                    new_ids.append(post.id)
        await session.commit()
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        # Payload shape not verified against live traffic yet; log and ack so Meta doesn't retry forever.
        log.warning("unrecognised instagram webhook payload: %s", e)
        await session.rollback()
    for post_id in new_ids:
        workers.enqueue_process(post_id)
    return {"ok": True}
