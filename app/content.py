"""Content engine: weekly plan -> drafts -> human approval -> media -> scheduled publish via official API."""
import logging
import os
import re
import secrets
from contextlib import suppress
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from sqlalchemy import or_, select, update

from app import craft
from app.automation import get_settings, voice_block
from app.config import settings
from app.models import Account, BrandKnowledge, ContentItem, FunnelRule, aware, audit, now
from app.pipeline import STR, _obj, pick_knowledge, render
from app.platforms import PlatformError, get_adapter

log = logging.getLogger(__name__)
FORMATS = ["reel", "carousel", "image", "story"]
TYPES = ["proof", "teach", "opinion", "story", "offer"]
GOALS = ["saves", "shares", "comments", "follows"]
EDITABLE = {"idea", "drafted", "approved", "failed"}

PLAN_SCHEMA = _obj({"items": {"type": "array", "items": _obj({
    "date": STR, "time": STR, "format": {"type": "string", "enum": FORMATS}, "pillar": STR,
    "type": {"type": "string", "enum": TYPES}, "angle": STR, "hook_formula": STR,
    "goal": {"type": "string", "enum": GOALS}, "why": STR})}, "engagement_note": STR})

DRAFT_SCHEMA = _obj({
    "hooks": {"type": "array", "items": _obj({"spoken": STR, "on_screen": STR, "formula": STR})},
    "caption": STR,
    "hashtags": {"type": "array", "items": _obj({"tag": STR, "tier": {"type": "string", "enum": ["niche", "mid", "broad"]}})},
    "cta": STR, "notes": STR,
    "beats": {"type": "array", "items": _obj({"t": STR, "say": STR, "show": STR, "on_screen": STR})},
    "slides": {"type": "array", "items": _obj({"headline": STR, "body": STR})},
    "frames": {"type": "array", "items": _obj({"text": STR})},
})

FORMAT_BLOCKS = {
    "reel": """Format: Reel (vertical video, 15 to 45 seconds unless the angle truly needs more).
- The first two seconds decide reach. Start at the sentence the creator would normally reach at second six. No intro, no "hey guys", no "in this video".
- beats: 4 to 8. Each has t (time range like "0-2s"), say (the spoken line), show (what is on screen: screen recording, face to camera, terminal, chart, b-roll), on_screen (overlay text, 6 words or fewer, or empty).
- Beat 1 is the hook. The last beat lands the payoff and, if it fits, loops back into beat 1.
- Every claim must be showable on screen: a real UI, code, number, or result.""",
    "carousel": """Format: Carousel (4:5 portrait images, 6 to 10 slides, images only).
- slides: each has headline (8 words or fewer, readable as a thumbnail) and body (1 to 3 short sentences, or empty).
- Slide 1 promises a specific payoff and opens a loop; it does not explain. Put the strongest point on slide 2 or 3, not at the end.
- One point per slide; each slide stands on its own.
- The last slide is the summary worth saving, plus the one ask.""",
    "image": """Format: Single image (4:5 portrait).
- slides: exactly one entry. headline is the text on the image (8 words or fewer, or empty for a plain photo); body describes what the image shows so the human can make it.
- The caption carries this post.""",
    "story": """Format: Story (one vertical frame; the API publishes one image or video per story, and interactive stickers are not available through it).
- frames: exactly one, with text (on-screen text, 12 words or fewer).
- caption: empty string. hashtags: empty list. hooks: still return 5 short options for the on-screen text.""",
}

def TZ() -> ZoneInfo:
    return ZoneInfo(settings.TIMEZONE)


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def creator_system(voice: dict) -> str:
    return render("creator_system.txt", voice=voice_block(voice))


def media_url(file: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/media/{file}"


def content_dict(i: ContentItem) -> dict:
    return {c.name: getattr(i, c.name) for c in ContentItem.__table__.columns} | {
        "media": [{**m, "url": f"/media/{m['file']}"} for m in i.media]}


async def _knowledge(session, terms: list[str]) -> str:
    rows = list(await session.scalars(select(BrandKnowledge)))
    picked = pick_knowledge(rows, terms)
    return "\n".join(f"- [{'verified project' if r.kind == 'project' and r.verified else r.kind}] {r.title}: {r.content}"
                     for r in picked) or "none"


async def _funnel_keywords(session) -> str:
    rules = await session.scalars(select(FunnelRule).where(FunnelRule.active))
    return ", ".join(k for r in rules for k in r.keywords) or "none"


# ---------- planning & drafting ----------

async def plan_week(session, week_start: date, brief: str, count: int, user: str, llm) -> list[int]:
    s = await get_settings(session)
    voice = s["voice"]
    pillars = [p["name"] for p in voice["pillars"]]
    week_end = week_start + timedelta(days=6)
    hist_since = datetime.combine(week_start - timedelta(days=14), time(), UTC)
    history = list(await session.scalars(select(ContentItem).where(ContentItem.created_at >= hist_since)))
    top = sorted((i for i in history if i.insights), key=lambda i: -(i.insights.get("reach") or 0))[:3]

    d = await llm.json(creator_system(voice), render(
        "weekly_plan.txt", week_start=week_start.isoformat(), week_end=week_end.isoformat(), brief=brief or "(empty)",
        pillars="\n".join(f"- {p['name']} ({p.get('share', 0)}%): {p.get('description', '')}" for p in voice["pillars"]),
        timezone=settings.TIMEZONE, best_times="\n".join(f"- {t}" for t in voice.get("best_times", [])) or "- any evening",
        history="\n".join(f"- {i.scheduled_at:%Y-%m-%d} {i.format} {i.pillar}: {i.angle}" for i in history if i.scheduled_at)
        or "none",
        performance="\n".join(f"- {i.format}/{i.pillar} reach {i.insights.get('reach')} saves {i.insights.get('saved')}: "
                              f"{i.angle}" for i in top) or "none yet",
        hooks=craft.hooks_for_prompt(), funnels=await _funnel_keywords(session),
        count=count, min_reels=max(1, count // 2),
    ), PLAN_SCHEMA, "medium")

    ids = []
    for it in d["items"]:
        try:
            day = date.fromisoformat(it["date"])
            hh, mm = map(int, it["time"].split(":"))
        except ValueError:
            continue
        if not week_start <= day <= week_end or not re.fullmatch(r"\d{2}:\d{2}", it["time"]):
            continue
        pillar = next((p for p in pillars if p.lower() == it["pillar"].lower()), pillars[0])
        item = ContentItem(
            week_start=week_start, format=it["format"], pillar=pillar, post_type=it["type"], goal=it["goal"],
            angle=it["angle"], hook_formula=None if it["hook_formula"] == "none" else it["hook_formula"][:60],
            brief=brief, draft={"plan_why": it["why"]},
            scheduled_at=datetime.combine(day, time(hh, mm), TZ()).astimezone(UTC))
        session.add(item)
        await session.flush()
        ids.append(item.id)
        if len(ids) >= count:
            break
    audit(session, user, "week_planned", "content", None, week_start=week_start.isoformat(), items=ids,
          engagement_note=d["engagement_note"])
    await session.commit()
    return ids


async def draft_item(session, item_id: int, llm) -> ContentItem | None:
    item = await session.get(ContentItem, item_id)
    if item is None or item.status not in ("idea", "drafted", "failed"):
        return item
    voice = (await get_settings(session))["voice"]
    d = await llm.json(creator_system(voice), render(
        "content_draft.txt", format=item.format, pillar=item.pillar, post_type=item.post_type, goal=item.goal,
        angle=item.angle, hook_formula=item.hook_formula or "any", language=(voice.get("languages") or ["en"])[0],
        brief=item.brief or "(none)", knowledge=await _knowledge(session, [item.pillar, *item.angle.split()[:12]]),
        funnels=await _funnel_keywords(session), hooks=craft.hooks_for_prompt(), format_block=FORMAT_BLOCKS[item.format],
    ), DRAFT_SCHEMA, "medium")

    caption = craft.scrub(d["caption"]) if item.format != "story" else ""
    tags = [t["tag"].lstrip("#") for t in d["hashtags"]][:5] if item.format != "story" else []
    hook_scores = sorted(({**h, **craft.hook_score(h["spoken"]), "classified": craft.classify_hook(h["spoken"])}
                          for h in d["hooks"]), key=lambda h: -h["score"])
    item.draft = {**(item.draft or {}), **d, "hook_scores": hook_scores,
                  "caption_lint": craft.caption_lint(caption, tags) if caption else None,
                  "tells": craft.ai_tells(caption) if caption else None,
                  "hashtag_issues": craft.hashtag_issues(d["hashtags"]) if tags else []}
    item.caption, item.hashtags, item.status, item.error = caption, tags, "drafted", None
    audit(session, "system", "content_drafted", "content", item.id)
    await session.commit()
    return item


def approval_problems(item: ContentItem) -> list[str]:
    if item.format == "story":
        return []
    problems = []
    if not item.caption.strip():
        problems.append("caption is empty")
    elif any(c["status"] == "FAIL" for c in craft.caption_lint(item.caption, item.hashtags)["checks"]):
        problems.append("caption lint has a FAIL")
    if len(item.hashtags) > 5:
        problems.append("more than 5 hashtags")
    return problems


# ---------- media ----------

MEDIA_NAME = re.compile(r"^[A-Za-z0-9_-]{22}\.(jpg|mp4|mov)$")
UPLOAD_TYPES = {"image/jpeg": (".jpg", 8 << 20), "video/mp4": (".mp4", 300 << 20), "video/quicktime": (".mov", 300 << 20)}


def _magic_ok(ext: str, head: bytes) -> bool:
    return head.startswith(b"\xff\xd8\xff") if ext == ".jpg" else head[4:8] == b"ftyp"


async def save_upload(request: Request, item: ContentItem, kind: str) -> str:
    ctype = request.headers.get("content-type", "").split(";")[0].strip()
    if ctype not in UPLOAD_TYPES or (kind == "image") != (ctype == "image/jpeg"):
        raise HTTPException(415, "Instagram publishing accepts JPEG images and MP4/MOV video. Export as JPEG.")
    ext, cap = UPLOAD_TYPES[ctype]
    Path(settings.MEDIA_DIR).mkdir(parents=True, exist_ok=True)
    name = secrets.token_urlsafe(16) + ext
    final = Path(settings.MEDIA_DIR) / name
    part = final.with_suffix(ext + ".part")
    size, head = 0, b""
    try:
        with open(part, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > cap:
                    raise HTTPException(413, f"file too large (max {cap >> 20} MB)")
                if len(head) < 12:
                    head += chunk[:12]
                f.write(chunk)
        if not _magic_ok(ext, head):
            raise HTTPException(415, "file content does not match its type")
        os.replace(part, final)
    finally:
        part.unlink(missing_ok=True)
    # ponytail: published media files are never cleaned up; add a sweep if disk use matters
    item.media = [*item.media, {"file": name, "kind": kind}]
    return name


# ---------- publishing ----------

TRANSIENT = {"not_due", "automation_paused", "account_paused", "publishing_not_configured"}


def check_content_publishable(item, account, *, now_: datetime, public_base: str, paused: bool) -> str | None:
    if item.status != "approved" or not item.approved_by:
        return "not_approved"
    if paused:
        return "automation_paused"
    if account is None or account.status != "active":
        return "account_paused"
    if not public_base.startswith("https://"):
        return "publishing_not_configured"
    if item.scheduled_at is None or aware(item.scheduled_at) > now_:
        return "not_due"
    kinds = [m["kind"] for m in item.media]
    ok = {"image": kinds == ["image"], "story": len(kinds) == 1, "reel": kinds == ["video"],
          "carousel": 2 <= len(kinds) <= 10 and set(kinds) == {"image"}}[item.format]
    if not ok or not all(m["file"].endswith(".jpg") for m in item.media if m["kind"] == "image"):
        return "invalid_media"
    if len(craft.compose_caption(item.caption, item.hashtags)) > 2200 or len(item.hashtags) > 5:
        return "invalid_caption"
    return None


def next_action(code: str, created_at: datetime, now_: datetime) -> str:
    if code == "FINISHED":
        return "publish"
    if code == "PUBLISHED":
        return "already_published"
    if code in ("ERROR", "EXPIRED"):
        return f"fail:container_{code.lower()}"
    # ponytail: docs suggest polling up to 5 min; reels can take longer, so 15 min ceiling
    return "fail:container_timeout" if now_ - aware(created_at) > timedelta(minutes=15) else "wait"


async def cas(session, item_id: int, frm: str, to: str) -> bool:
    """Compare-and-set status: the lock that stops two workers doing the same side effect."""
    r = await session.execute(update(ContentItem).where(ContentItem.id == item_id, ContentItem.status == frm)
                              .values(status=to, updated_at=now()).execution_options(synchronize_session=False))
    await session.commit()
    return r.rowcount == 1


async def create_containers(adapter, ig: str, item: ContentItem) -> str:
    cap = craft.compose_caption(item.caption, item.hashtags)
    m = item.media
    if item.format == "image":
        return await adapter.create_container(ig, {"image_url": media_url(m[0]["file"]), "caption": cap})
    if item.format == "reel":
        return await adapter.create_container(ig, {"media_type": "REELS", "video_url": media_url(m[0]["file"]),
                                                   "caption": cap, "share_to_feed": "true"})
    if item.format == "story":
        key = "image_url" if m[0]["kind"] == "image" else "video_url"
        return await adapter.create_container(ig, {"media_type": "STORIES", key: media_url(m[0]["file"])})
    children = [await adapter.create_container(ig, {"image_url": media_url(x["file"]), "is_carousel_item": "true"})
                for x in m]
    return await adapter.create_container(ig, {"media_type": "CAROUSEL", "children": ",".join(children), "caption": cap})


async def _fail(session, item, reason: str, e: PlatformError | None = None, account: Account | None = None) -> str:
    item.status, item.error = "failed", reason
    audit(session, "system", "content_failed", "content", item.id, reason=reason)
    if e is not None and e.pause and account is not None:
        account.status, account.paused_reason = "paused", str(e)
        audit(session, "system", "account_paused", "account", account.id, reason=str(e), code=e.code)
    await session.commit()
    return "failed"


async def _account(session) -> Account | None:
    return await session.scalar(select(Account).where(Account.platform == "instagram").order_by(Account.id))


async def publish_step(session, item_id: int, adapter_factory=None) -> str:
    item = await session.get(ContentItem, item_id, populate_existing=True)
    if item is None:
        return "missing"
    account = await _account(session)
    t = now()
    if item.status == "approved":
        if r := check_content_publishable(item, account, now_=t, public_base=settings.PUBLIC_BASE_URL,
                                          paused=settings.AUTOMATION_PAUSED):
            if r in TRANSIENT:
                item.error = None if r == "not_due" else r
                await session.commit()
                return r
            return await _fail(session, item, r)
        adapter = (adapter_factory or get_adapter)("instagram", account.access_token)
        ig = account.external_user_id
        try:
            usage, total = await adapter.publishing_limit(ig)
        except PlatformError as e:
            return await _fail(session, item, f"quota_check: {e}", e, account)
        if usage >= total:
            item.error = "quota_reached"
            await session.commit()
            return "quota"
        if not await cas(session, item.id, "approved", "processing"):
            return "taken"
        await session.refresh(item)
        try:
            cid = await create_containers(adapter, ig, item)
        except PlatformError as e:
            return await _fail(session, item, f"container: {e}", e, account)
        item.container_id, item.container_created_at, item.error = cid, now(), None
        audit(session, "system", "container_created", "content", item.id, container_id=cid)
        await session.commit()
        return "processing"

    if item.status in ("processing", "publishing") and account is None:
        return await _fail(session, item, "account_missing")
    adapter = (adapter_factory or get_adapter)("instagram", account.access_token) if account else None

    if item.status == "processing":
        try:
            code = await adapter.container_status(item.container_id)
        except PlatformError as e:
            return await _fail(session, item, f"status: {e}", e, account)
        act = next_action(code, item.container_created_at, t)
        if act == "wait":
            return "wait"
        if act.startswith("fail:"):
            return await _fail(session, item, act[5:])
        if act == "already_published":
            item.status, item.published_at = "published", now()
            await session.commit()
            return "published"
        if not await cas(session, item.id, "processing", "publishing"):
            return "taken"
        await session.refresh(item)
        try:
            mid = await adapter.publish_container(account.external_user_id, item.container_id)
        except PlatformError as e:
            return await _fail(session, item, f"publish: {e}", e, account)
        item.external_media_id, item.status, item.published_at = mid, "published", now()
        with suppress(PlatformError):
            item.permalink = (await adapter.media_info(mid)).get("permalink")
        audit(session, "system", "content_published", "content", item.id, media_id=mid)
        await session.commit()
        return "published"

    if item.status == "publishing" and t - aware(item.updated_at) > timedelta(minutes=10):  # crash recovery
        try:
            code = await adapter.container_status(item.container_id)
        except PlatformError as e:
            return await _fail(session, item, f"status: {e}", e, account)
        if code != "PUBLISHED":
            return await _fail(session, item, "stuck_publishing")
        item.status, item.error = "published", "media id unknown (crash recovery)"
        await session.commit()
        return "published"
    return "noop"


async def publish_tick(session, adapter_factory=None) -> dict:
    due = list(await session.scalars(select(ContentItem.id).where(or_(
        (ContentItem.status == "approved") & (ContentItem.scheduled_at <= now()),
        ContentItem.status.in_(["processing", "publishing"])))))
    out = {}
    for item_id in due:
        try:
            out[item_id] = await publish_step(session, item_id, adapter_factory)
        except Exception:  # one bad item must not block the rest
            log.exception("publish_step failed for content %s", item_id)
            await session.rollback()
            out[item_id] = "error"
    return out
