"""Stages A-D: understand -> retrieve Curiox context -> generate with strategy -> quality control."""
from datetime import UTC, datetime
from pathlib import Path
from string import Template

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app import craft, prefs
from app.config import settings
from app.llm import LLMError
from app.models import Approval, BrandKnowledge, CommentSuggestion, Post, audit
from app.rules import DROP, detect_language, quality_issues, recency_score, relevance_score

PROMPTS = Path(__file__).parent.parent / "prompts"
STRATEGIES = ["insight", "question", "experience", "discussion", "appreciation"]


def render(template: str, /, **kw) -> str:
    # Positional-only so prompts can use any variable name (e.g. $name).
    # string.Template: user content with braces or $ is never re-interpreted.
    kw.setdefault("human_voice", (PROMPTS / "human_voice.txt").read_text())  # any prompt can use $human_voice
    return Template((PROMPTS / template).read_text()).safe_substitute(kw)


def _obj(props: dict) -> dict:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


STR, BOOL, NUM = {"type": "string"}, {"type": "boolean"}, {"type": "number"}
STRS = {"type": "array", "items": STR}

ANALYSIS_SCHEMA = _obj({
    "post_summary": STR,
    "detected_language": {"type": "string", "enum": ["en", "hi", "hinglish", "other"]},
    "detected_tone": STR,
    "author_intent": STR,
    "topics": STRS,
    "technical_concepts": STRS,
    "invites_discussion": BOOL,
    "scores": _obj({"topic": NUM, "audience": NUM, "conversation": NUM, "quality": NUM}),
    "reject_reason": {"type": "string", "enum": [
        "none", "giveaway", "engagement_bait", "irrelevant_promo", "sensitive", "no_value", "spam"]},
    "relevance_reason": STR,
})

GENERATION_SCHEMA = _obj({
    "no_comment": BOOL,
    "no_comment_reason": STR,
    "response_strategy": {"type": "string", "enum": STRATEGIES},
    "comment_suggestions": {"type": "array", "items": _obj({
        "text": STR, "strategy": {"type": "string", "enum": STRATEGIES}})},
    "factuality_warnings": STRS,
    "needs_human_review": BOOL,
})


def system_prompt(voice: dict | None = None) -> str:
    base = (PROMPTS / "personality.txt").read_text() + "\n\n" + (PROMPTS / "human_voice.txt").read_text()
    return base + "\n\n" + prefs.voice_block(voice) if voice else base


REWRITE_SCHEMA = _obj({"texts": STRS})


async def humanize(llm, system: str, texts: list[str], kind: str) -> list[str]:
    """One rewrite pass for drafts that read machine-written. Clean drafts are returned untouched, at no LLM cost."""
    bad = [(i, t, h) for i, t in enumerate(texts) if (h := craft.slop_hits(t))]
    if not bad:
        return texts
    try:
        out = (await llm.json(system, render("humanize.txt", kind=kind, drafts="\n\n".join(
            f"<draft>{t}</draft>\nProblems: {'; '.join(h)}" for _, t, h in bad)), REWRITE_SCHEMA, "low"))["texts"]
    except LLMError:
        return texts  # keep the originals; they stay flagged ai_slop for the reviewer
    texts = list(texts)
    for (i, _, _), new in zip(bad, out):
        if new.strip():
            texts[i] = craft.scrub(new.strip())
    return texts


def pick_knowledge(rows: list[BrandKnowledge], terms: list[str], limit: int = 8) -> list[BrandKnowledge]:
    # ponytail: keyword/tag match; switch to embeddings when brand_knowledge grows past ~200 rows
    terms_l = {t.lower() for t in terms if t}
    picked = [r for r in rows if r.kind == "profile"]
    for r in rows:
        if r in picked:
            continue
        tags = {t.lower() for t in (r.tags or [])}
        content = r.content.lower()
        if tags & terms_l or any(t in content for t in terms_l):
            picked.append(r)
    return picked[:limit]


async def _skip(session, post: Post, reason: str) -> None:
    post.status, post.skip_reason = "skipped", reason
    audit(session, "system", "skip", "post", post.id, reason=reason)
    await session.commit()


async def process_post(session, post_id: int, llm) -> None:
    post = await session.scalar(select(Post).options(selectinload(Post.creator)).where(Post.id == post_id))
    if post is None or post.status != "new":
        return  # idempotent: already processed

    target_type = "comment on Curiox's own post" if post.source == "own_comment" else "post"
    parent_block = f"<parent_post>\n{post.parent_text}\n</parent_post>" if post.parent_text else ""
    c = post.creator
    creator_note = f"topics: {', '.join(c.topics or [])}; audience: {c.audience_note or 'n/a'}" if c else "not in curated list"

    system = system_prompt(await prefs.voice_with_samples(session))
    try:
        # Stage A: understand the post
        a = await llm.json(system, render(
            "analyze_post.txt", platform=post.platform, target_type=target_type,
            author=post.author_handle or "unknown", creator_note=creator_note,
            published_at=post.published_at.isoformat() if post.published_at else "unknown",
            language_hint=detect_language(post.text), parent_block=parent_block, text=post.text,
        ), ANALYSIS_SCHEMA, "low")

        post.analysis = {"analysis": a}
        post.language = a["detected_language"]
        post.relevance_score = relevance_score(
            a["scores"], recency_score(post.published_at, datetime.now(UTC)), settings.RELEVANCE_WEIGHTS)
        if a["reject_reason"] != "none":
            return await _skip(session, post, a["reject_reason"])
        # Own-account comments are replies to people talking to us; no relevance gate for those.
        if post.source != "own_comment" and post.relevance_score < settings.RELEVANCE_THRESHOLD:
            return await _skip(session, post, "low_relevance")

        # Stage B: Curiox context
        rows = list(await session.scalars(select(BrandKnowledge)))
        kn = pick_knowledge(rows, a["topics"] + a["technical_concepts"])
        verified = [r.title for r in rows if r.kind == "project" and r.verified]
        previous = list(await session.scalars(
            select(Approval.final_text).where(Approval.decision == "approved")
            .order_by(Approval.id.desc()).limit(50)))

        # Stage C: strategy + generation
        def label(r):
            return "verified project" if r.kind == "project" and r.verified else r.kind
        g = await llm.json(system, render(
            "generate_comment.txt", target_type=target_type, parent_block=parent_block, text=post.text,
            summary=a["post_summary"], language=post.language, tone=a["detected_tone"],
            intent=a["author_intent"], concepts=", ".join(a["technical_concepts"]) or "none",
            knowledge="\n".join(f"- [{label(r)}] {r.title}: {r.content}" for r in kn) or "none",
            recent="\n".join(previous[:20]) or "none",
        ), GENERATION_SCHEMA, "medium")
    except LLMError as e:
        return await _skip(session, post, f"llm_{e}")

    if g["no_comment"]:
        return await _skip(session, post, f"no_comment: {g['no_comment_reason']}")

    # Stage D: rewrite anything that reads machine-written, then deterministic quality control
    sugg = g["comment_suggestions"][:3]
    for s, text in zip(sugg, await humanize(llm, system, [s["text"] for s in sugg], "comment")):
        s["text"] = text
    kept: list[tuple[dict, list[str]]] = []
    for s in sugg:
        issues = quality_issues(s["text"], post.language, previous + [k["text"] for k, _ in kept],
                                verified, s["strategy"]) + (["ai_slop"] if craft.slop_hits(s["text"]) else [])
        if not set(issues) & DROP:
            kept.append((s, issues))
    if not kept:
        return await _skip(session, post, "quality")

    for i, (s, issues) in enumerate(kept):
        session.add(CommentSuggestion(post_id=post.id, position=i, text=s["text"], strategy=s["strategy"],
                                      language=post.language, quality_flags=issues))
    post.analysis = {**post.analysis, "generation": g}  # reassign so SQLAlchemy sees the change
    post.status = "pending"
    audit(session, "system", "queued", "post", post.id, suggestions=len(kept), score=post.relevance_score)
    await session.commit()
