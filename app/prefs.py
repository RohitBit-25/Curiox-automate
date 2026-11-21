"""Settings store (DB-backed, audited) and the brand voice block shared by every prompt. Leaf module."""
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import AUTO_ACTOR, AppSetting, Approval, CommentSuggestion, Message, audit

DEFAULT_VOICE = {
    "handle": "curiox.os", "who": "", "audience": "", "offer": "",
    "pillars": [
        {"name": "Dev & building", "description": "", "share": 30},
        {"name": "AI & automation", "description": "", "share": 30},
        {"name": "Fintech & trading tech", "description": "", "share": 20},
        {"name": "Startups & small-business systems", "description": "", "share": 20},
    ],
    "always": [], "never": [], "examples": [], "cta_link": "", "languages": ["en"],
    "best_times": ["Tue 19:30", "Thu 19:00", "Fri 19:30", "Sun 18:00"],
}

DEFAULTS = {
    "mode_comments": "draft",  # off | draft | auto
    "mode_dms": "draft",
    "auto_min_confidence": 0.85,
    "cap_auto_comment_replies_per_hour": 20,
    "cap_auto_dms_per_hour": 30,
    "cap_funnel_dms_per_hour": 60,  # API allows far more; we stay well below
    "engagement_daily_target": 10,
    "voice": DEFAULT_VOICE,
}

Mode = Literal["off", "draft", "auto"]
Str = Field(default="", max_length=2000)


class Pillar(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Str
    share: int = Field(default=25, ge=0, le=100)


class Voice(BaseModel):
    handle: str = Field(default="curiox.os", max_length=100)
    who: str = Str
    audience: str = Str
    offer: str = Str
    pillars: list[Pillar] = Field(min_length=1, max_length=8)
    always: list[str] = Field(default=[], max_length=30)
    never: list[str] = Field(default=[], max_length=30)
    examples: list[str] = Field(default=[], max_length=10)
    cta_link: str = Field(default="", max_length=500)
    languages: list[str] = Field(default=["en"], max_length=5)
    best_times: list[str] = Field(default=[], max_length=14)


class SettingsPatch(BaseModel):
    mode_comments: Mode | None = None
    mode_dms: Mode | None = None
    auto_min_confidence: float | None = Field(None, ge=0.6, le=1.0)
    cap_auto_comment_replies_per_hour: int | None = Field(None, ge=1, le=100)
    cap_auto_dms_per_hour: int | None = Field(None, ge=1, le=200)
    cap_funnel_dms_per_hour: int | None = Field(None, ge=1, le=500)
    engagement_daily_target: int | None = Field(None, ge=3, le=30)
    voice: Voice | None = None


async def get_settings(session) -> dict:
    rows = {r.key: r.value for r in await session.scalars(select(AppSetting))}
    return {k: rows.get(k, v) for k, v in DEFAULTS.items()}


async def save_settings(session, user: str, patch: SettingsPatch) -> dict:
    current = await get_settings(session)
    for key, value in patch.model_dump(exclude_none=True).items():
        if current[key] == value:
            continue
        row = await session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=value, updated_by=user))
        else:
            row.value, row.updated_by = value, user
        audit(session, user, "settings_changed", "settings", None, key=key, before=current[key], after=value)
    await session.commit()
    return await get_settings(session)


def voice_block(v: dict) -> str:
    def lines(xs):
        return "\n".join(f"- {x}" for x in xs) or "- (none set)"
    learned = ("How the owner actually writes comments and DMs (their own words, newest first; match this register):\n"
               + lines(v["learned"]) + "\n") if v.get("learned") else ""
    pillars = "\n".join(f"- {p['name']} ({p.get('share', 0)}%): {p.get('description', '')}".rstrip(": ")
                        for p in v.get("pillars", []))
    return f"""<voice>
Handle: @{v.get('handle') or 'curiox.os'}
Who we are: {v.get('who') or '(not set)'}
Audience: {v.get('audience') or '(not set)'}
What Curiox sells: {v.get('offer') or '(not set)'}
Pillars:
{pillars}
Always:
{lines(v.get('always', []))}
Never:
{lines(v.get('never', []))}
Sounds like (real examples):
{lines(v.get('examples', []))}
{learned}Primary link: {v.get('cta_link') or '(none)'}
Languages: {', '.join(v.get('languages') or ['en'])}
</voice>"""


async def voice_samples(session, limit: int = 8) -> list[str]:
    """Comments and DMs the owner wrote or edited themselves: the best signal for how drafts should sound."""
    dms = [(m.created_at, m.text) for m in await session.scalars(
        select(Message).where(Message.direction == "out", Message.status == "sent", Message.actor != AUTO_ACTOR)
        .order_by(Message.id.desc()).limit(60))
        if m.text.strip() and (m.actor == "instagram_app" or (m.meta or {}).get("human_written"))]
    comments = (await session.execute(
        select(Approval.created_at, Approval.final_text).join(CommentSuggestion, CommentSuggestion.id == Approval.suggestion_id)
        .where(Approval.decision == "approved", Approval.actor != AUTO_ACTOR, CommentSuggestion.edited)
        .order_by(Approval.id.desc()).limit(limit))).all()
    rows = sorted(dms + [tuple(r) for r in comments if r[1]], key=lambda r: r[0], reverse=True)
    return [t.strip()[:300] for _, t in rows[:limit]]


async def voice_with_samples(session) -> dict:
    return (await get_settings(session))["voice"] | {"learned": await voice_samples(session)}
