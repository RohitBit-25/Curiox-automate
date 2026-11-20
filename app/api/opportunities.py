"""Review queue. An opportunity is a `posts` row; its id is the post id."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import workers
from app.deps import current_user, get_session
from app.models import Approval, BrandKnowledge, CommentSuggestion, EngagementEvent, Post, audit
from app.rules import HARD, fingerprint, quality_issues

router = APIRouter()
STATUSES = {"new", "pending", "skipped", "approved", "rejected", "published", "failed", "funnel"}


def serialize(p: Post) -> dict:
    a = (p.analysis or {}).get("analysis", {})
    g = (p.analysis or {}).get("generation", {})
    return {
        "id": p.id, "source": p.source, "url": p.url, "platform": p.platform,
        "author_handle": p.author_handle,
        "creator": {"handle": p.creator.handle, "display_name": p.creator.display_name} if p.creator else None,
        "text": p.text, "parent_text": p.parent_text, "language": p.language,
        "relevance_score": p.relevance_score, "status": p.status, "skip_reason": p.skip_reason,
        "post_summary": a.get("post_summary"), "topics": a.get("topics", []),
        "detected_tone": a.get("detected_tone"), "relevance_reason": a.get("relevance_reason"),
        "response_strategy": g.get("response_strategy"), "factuality_warnings": g.get("factuality_warnings", []),
        "needs_human_review": g.get("needs_human_review"), "auto": (p.analysis or {}).get("auto"),
        "suggestions": [{"id": s.id, "text": s.text, "strategy": s.strategy, "language": s.language,
                         "quality_flags": s.quality_flags, "edited": s.edited} for s in p.suggestions],
        "approval": {"decision": p.approval.decision, "actor": p.approval.actor, "final_text": p.approval.final_text,
                     "reason": p.approval.reason, "created_at": p.approval.created_at} if p.approval else None,
        "created_at": p.created_at,
    }


LOAD = (selectinload(Post.suggestions), selectinload(Post.creator), selectinload(Post.approval))


async def load(session, post_id: int) -> Post:
    # populate_existing: refresh relationships (e.g. a just-created approval) on an already-loaded Post
    post = await session.scalar(select(Post).options(*LOAD).where(Post.id == post_id)
                                .execution_options(populate_existing=True))
    if post is None:
        raise HTTPException(404, "opportunity not found")
    return post


async def _quality_context(session, post: Post) -> tuple[list[str], list[str]]:
    previous = list(await session.scalars(
        select(Approval.final_text).where(Approval.decision == "approved", Approval.post_id != post.id)
        .order_by(Approval.id.desc()).limit(50)))
    verified = list(await session.scalars(
        select(BrandKnowledge.title).where(BrandKnowledge.kind == "project", BrandKnowledge.verified)))
    return previous, verified


@router.get("/opportunities")
async def list_opportunities(status: str = "pending", source: str | None = None, limit: int = 50,
                             session=Depends(get_session)):
    if status not in STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(STATUSES)}")
    q = (select(Post).options(*LOAD).where(Post.status == status)
         .order_by(Post.relevance_score.desc().nulls_last(), Post.created_at.desc()).limit(min(limit, 200)))
    if source:
        q = q.where(Post.source == source)
    return [serialize(p) for p in await session.scalars(q)]


@router.get("/opportunities/{post_id}")
async def get_opportunity(post_id: int, session=Depends(get_session)):
    return serialize(await load(session, post_id))


class EditBody(BaseModel):
    suggestion_id: int
    text: str = Field(min_length=1, max_length=2200)


def _suggestion(post: Post, suggestion_id: int) -> CommentSuggestion:
    s = next((s for s in post.suggestions if s.id == suggestion_id), None)
    if s is None:
        raise HTTPException(404, "suggestion does not belong to this opportunity")
    return s


def _require_pending(post: Post):
    if post.status != "pending":
        raise HTTPException(409, f"opportunity is {post.status}, not pending")


@router.post("/opportunities/{post_id}/edit")
async def edit(post_id: int, body: EditBody, user=Depends(current_user), session=Depends(get_session)):
    post = await load(session, post_id)
    _require_pending(post)
    s = _suggestion(post, body.suggestion_id)
    previous, verified = await _quality_context(session, post)
    before = s.text
    s.text, s.edited = body.text, True
    s.quality_flags = quality_issues(body.text, post.language, previous, verified, s.strategy)
    audit(session, user, "edit", "suggestion", s.id, before=before, after=body.text)
    await session.commit()
    return serialize(await load(session, post_id))


class ApproveBody(BaseModel):
    suggestion_id: int


@router.post("/opportunities/{post_id}/approve")
async def approve(post_id: int, body: ApproveBody, user=Depends(current_user), session=Depends(get_session)):
    post = await load(session, post_id)
    _require_pending(post)
    s = _suggestion(post, body.suggestion_id)
    previous, verified = await _quality_context(session, post)
    if blocking := sorted(set(quality_issues(s.text, post.language, previous, verified, s.strategy)) & HARD):
        raise HTTPException(422, {"message": "suggestion fails hard quality checks", "issues": blocking})
    approval = Approval(post_id=post.id, suggestion_id=s.id, decision="approved", final_text=s.text, actor=user)
    session.add(approval)
    post.status = "approved"
    try:
        await session.flush()
        audit(session, user, "approve", "post", post.id, suggestion_id=s.id, approval_id=approval.id)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "already decided")
    if post.source == "own_comment":
        workers.enqueue_publish(approval.id)  # publisher re-validates everything
    return serialize(await load(session, post_id))


class RejectBody(BaseModel):
    reason: str = Field(default="", max_length=1000)


@router.post("/opportunities/{post_id}/reject")
async def reject(post_id: int, body: RejectBody, user=Depends(current_user), session=Depends(get_session)):
    post = await load(session, post_id)
    _require_pending(post)
    session.add(Approval(post_id=post.id, decision="rejected", actor=user, reason=body.reason))
    post.status = "rejected"
    audit(session, user, "reject", "post", post.id, reason=body.reason)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "already decided")
    return serialize(await load(session, post_id))


@router.post("/opportunities/{post_id}/mark-posted")
async def mark_posted(post_id: int, user=Depends(current_user), session=Depends(get_session)):
    """Third-party posts are never published by the system; the human posts manually and records it here."""
    post = await load(session, post_id)
    if post.source != "manual_link" or post.status != "approved":
        raise HTTPException(409, "only approved manual-link opportunities can be marked as posted")
    a = post.approval
    session.add(EngagementEvent(approval_id=a.id, post_id=post.id, platform=post.platform, kind="manual_posted",
                                status="published", fingerprint=fingerprint(a.final_text)))
    post.status = "published"
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, "this exact text was already posted")
    audit(session, user, "manual_posted", "post", post.id)
    await session.commit()
    return serialize(await load(session, post_id))
