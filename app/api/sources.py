"""Inputs: submitted post links, curated creators, connected accounts, brand knowledge."""
import re
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import workers
from app.deps import current_user, get_session
from app.models import Account, BrandKnowledge, Creator, Post, audit
from app.platforms import PlatformError, get_adapter

router = APIRouter()
INSTAGRAM_POST_RE = re.compile(r"^https://(www\.)?instagram\.com/(?:[\w.]+/)?(p|reel|reels|tv)/([A-Za-z0-9_-]+)")


def norm_handle(h: str | None) -> str | None:
    return h.strip().lstrip("@").lower() if h and h.strip() else None


async def _commit_or_409(session, msg: str):
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, msg)


class LinkBody(BaseModel):
    url: str
    caption: str = Field(min_length=10, max_length=5000)
    author_handle: str | None = None
    creator_id: int | None = None
    published_at: datetime | None = None


@router.post("/posts")
async def submit_link(body: LinkBody, user=Depends(current_user), session=Depends(get_session)):
    """A link plus its pasted caption. Third-party pages are never fetched (no scraping)."""
    m = INSTAGRAM_POST_RE.match(body.url.strip())
    if not m:
        raise HTTPException(422, "url must be an instagram.com /p/, /reel/ or /tv/ link")
    handle = norm_handle(body.author_handle)
    creator_id = body.creator_id
    if creator_id is None and handle:
        creator_id = await session.scalar(select(Creator.id).where(
            Creator.platform == "instagram", Creator.handle == handle))
    post = Post(platform="instagram", source="manual_link", external_id=m.group(3), url=body.url.strip(),
                author_handle=handle, creator_id=creator_id, text=body.caption, published_at=body.published_at)
    session.add(post)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "this post was already submitted")
    audit(session, user, "submit_link", "post", post.id, url=post.url)
    await session.commit()
    workers.enqueue_process(post.id)
    return {"id": post.id, "status": post.status}


@router.get("/posts")
async def recent_posts(status: str | None = None, limit: int = 50, session=Depends(get_session)):
    q = select(Post).order_by(Post.id.desc()).limit(min(limit, 200))
    if status:
        q = q.where(Post.status == status)
    return [{"id": p.id, "url": p.url, "author_handle": p.author_handle, "status": p.status,
             "skip_reason": p.skip_reason, "relevance_score": p.relevance_score, "text": p.text[:200],
             "created_at": p.created_at} for p in await session.scalars(q)]


class CreatorBody(BaseModel):
    platform: Literal["instagram"] = "instagram"
    handle: str = Field(min_length=1, max_length=100)
    display_name: str | None = None
    topics: list[str] = []
    audience_note: str | None = None


def creator_dict(c: Creator) -> dict:
    return {"id": c.id, "platform": c.platform, "handle": c.handle, "display_name": c.display_name,
            "topics": c.topics, "audience_note": c.audience_note, "active": c.active}


@router.get("/creators")
async def list_creators(session=Depends(get_session)):
    return [creator_dict(c) for c in await session.scalars(select(Creator).order_by(Creator.handle))]


@router.post("/creators")
async def add_creator(body: CreatorBody, user=Depends(current_user), session=Depends(get_session)):
    c = Creator(platform=body.platform, handle=norm_handle(body.handle), display_name=body.display_name,
                topics=body.topics, audience_note=body.audience_note)
    session.add(c)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "creator already exists")
    audit(session, user, "add_creator", "creator", c.id, handle=c.handle)
    await session.commit()
    return creator_dict(c)


def account_dict(a: Account) -> dict:
    # Never include access_token.
    return {"id": a.id, "platform": a.platform, "handle": a.handle, "status": a.status,
            "paused_reason": a.paused_reason, "token_expires_at": a.token_expires_at}


@router.get("/accounts")
async def list_accounts(session=Depends(get_session)):
    return [account_dict(a) for a in await session.scalars(select(Account).order_by(Account.id))]


class AccountBody(BaseModel):
    platform: Literal["instagram"] = "instagram"
    access_token: str = Field(min_length=10)


@router.post("/accounts")
async def connect_account(body: AccountBody, user=Depends(current_user), session=Depends(get_session)):
    try:
        me = await get_adapter(body.platform, body.access_token).me()
    except PlatformError as e:
        raise HTTPException(400, f"token rejected by {body.platform}: {e}")
    ext_id = str(me.get("user_id") or me.get("id"))
    a = await session.scalar(select(Account).where(Account.platform == body.platform, Account.external_user_id == ext_id))
    if a is None:
        a = Account(platform=body.platform, external_user_id=ext_id, handle=me.get("username", ""),
                    access_token=body.access_token)
        session.add(a)
    a.access_token, a.handle = body.access_token, me.get("username", a.handle)
    a.status, a.paused_reason = "active", None
    a.token_expires_at = datetime.now(UTC) + timedelta(days=60)
    await session.flush()
    try:  # best effort: comments + messages webhooks for this account
        subscribed = await get_adapter(body.platform, body.access_token).subscribe_webhooks(ext_id)
    except PlatformError:
        subscribed = False
    audit(session, user, "account_connected", "account", a.id, handle=a.handle, webhooks_subscribed=subscribed)
    await session.commit()
    return account_dict(a)


@router.post("/accounts/{account_id}/resume")
async def resume_account(account_id: int, user=Depends(current_user), session=Depends(get_session)):
    a = await session.get(Account, account_id)
    if a is None:
        raise HTTPException(404, "account not found")
    a.status, a.paused_reason = "active", None
    audit(session, user, "account_resumed", "account", a.id)
    await session.commit()
    return account_dict(a)


class KnowledgeBody(BaseModel):
    kind: Literal["profile", "project", "fact", "style"]
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=5000)
    tags: list[str] = []
    verified: bool = False


def knowledge_dict(k: BrandKnowledge) -> dict:
    return {"id": k.id, "kind": k.kind, "title": k.title, "content": k.content, "tags": k.tags, "verified": k.verified}


@router.get("/knowledge")
async def list_knowledge(session=Depends(get_session)):
    return [knowledge_dict(k) for k in await session.scalars(select(BrandKnowledge).order_by(BrandKnowledge.kind))]


@router.post("/knowledge")
async def add_knowledge(body: KnowledgeBody, user=Depends(current_user), session=Depends(get_session)):
    k = BrandKnowledge(**body.model_dump())
    session.add(k)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "a knowledge entry with this title exists")
    audit(session, user, "add_knowledge", "knowledge", k.id, title=k.title, verified=k.verified)
    await session.commit()
    return knowledge_dict(k)


@router.get("/instagram/media")
async def own_media(session=Depends(get_session)):
    """Recent posts on the connected account, for the funnel post picker."""
    from app.api.automation import require_account
    a = await require_account(session)
    try:
        return await get_adapter(a.platform, a.access_token).list_own_media(25)
    except PlatformError as e:
        raise HTTPException(502, f"Instagram: {e}")
