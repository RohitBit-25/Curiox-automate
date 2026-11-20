from datetime import UTC, date, datetime
from functools import lru_cache

from cryptography.fernet import Fernet
from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.config import settings


def now() -> datetime:
    return datetime.now(UTC)


@lru_cache
def _fernet() -> Fernet:
    return Fernet(settings.TOKEN_ENCRYPTION_KEY.encode())


class EncryptedText(TypeDecorator):
    """Fernet-encrypted at rest. Plaintext only exists in Python memory."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else _fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        return None if value is None else _fernet().decrypt(value.encode()).decode()


class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True), date: Date}


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("platform", "external_user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))
    handle: Mapped[str] = mapped_column(String(100))
    external_user_id: Mapped[str] = mapped_column(String(64))
    access_token: Mapped[str] = mapped_column(EncryptedText)
    token_expires_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused
    paused_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Creator(Base):
    __tablename__ = "creators"
    __table_args__ = (UniqueConstraint("platform", "handle"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), default="instagram")
    handle: Mapped[str] = mapped_column(String(100))
    display_name: Mapped[str | None] = mapped_column(String(200))
    topics: Mapped[list] = mapped_column(JSON, default=list)
    audience_note: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_engaged_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=now)


class Post(Base):
    """A discovered item = one engagement opportunity. Analysis is stored inline."""
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("platform", "external_id"),
        Index("ix_posts_status_score", "status", "relevance_score"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20))  # manual_link | own_comment
    external_id: Mapped[str] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(Text)
    author_handle: Mapped[str | None] = mapped_column(String(100))
    author_id: Mapped[str | None] = mapped_column(String(64))
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id", ondelete="SET NULL"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    parent_external_id: Mapped[str | None] = mapped_column(String(128))
    parent_text: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None]
    # new | pending | skipped | approved | rejected | published | failed
    status: Mapped[str] = mapped_column(String(20), default="new")
    skip_reason: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(10))
    relevance_score: Mapped[float | None] = mapped_column(Float)
    analysis: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)

    creator: Mapped[Creator | None] = relationship()
    account: Mapped[Account | None] = relationship()
    suggestions: Mapped[list["CommentSuggestion"]] = relationship(
        order_by="CommentSuggestion.position", cascade="all, delete-orphan")
    approval: Mapped["Approval | None"] = relationship(back_populates="post")


class CommentSuggestion(Base):
    __tablename__ = "comment_suggestions"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    strategy: Mapped[str] = mapped_column(String(20))
    language: Mapped[str | None] = mapped_column(String(10))
    quality_flags: Mapped[list] = mapped_column(JSON, default=list)
    edited: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Approval(Base):
    """One human decision per post (unique post_id)."""
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), unique=True)
    suggestion_id: Mapped[int | None] = mapped_column(ForeignKey("comment_suggestions.id"))
    decision: Mapped[str] = mapped_column(String(10))  # approved | rejected
    final_text: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now)

    post: Mapped[Post] = relationship(back_populates="approval")


class EngagementEvent(Base):
    __tablename__ = "engagement_events"
    __table_args__ = (
        # The same normalized text can never be live twice, anywhere.
        Index("uq_event_fingerprint_live", "fingerprint", unique=True,
              postgresql_where=text("status <> 'failed'"), sqlite_where=text("status <> 'failed'")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    approval_id: Mapped[int] = mapped_column(ForeignKey("approvals.id"), unique=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    platform: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(20))  # published | manual_posted
    status: Mapped[str] = mapped_column(String(20))  # publishing | published | failed
    fingerprint: Mapped[str] = mapped_column(String(64))
    external_comment_id: Mapped[str | None] = mapped_column(String(128))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now)


class BrandKnowledge(Base):
    __tablename__ = "brand_knowledge"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # profile | project | fact | style
    title: Mapped[str] = mapped_column(String(200), unique=True)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_entity", "entity", "entity_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(50))
    entity: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)
    updated_by: Mapped[str | None] = mapped_column(String(100))


class ContentItem(Base):
    __tablename__ = "content_items"
    __table_args__ = (Index("ix_content_status_sched", "status", "scheduled_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    week_start: Mapped[date] = mapped_column(index=True)
    format: Mapped[str] = mapped_column(String(10))  # reel | carousel | image | story
    pillar: Mapped[str] = mapped_column(String(100))
    post_type: Mapped[str] = mapped_column(String(10), default="teach")  # proof | teach | opinion | story | offer
    goal: Mapped[str] = mapped_column(String(10), default="saves")  # saves | shares | comments | follows
    angle: Mapped[str] = mapped_column(Text, default="")
    hook_formula: Mapped[str | None] = mapped_column(String(60))
    brief: Mapped[str] = mapped_column(Text, default="")
    scheduled_at: Mapped[datetime | None]
    # idea | drafted | approved | processing | publishing | published | failed
    status: Mapped[str] = mapped_column(String(12), default="idea")
    draft: Mapped[dict | None] = mapped_column(JSON)
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    media: Mapped[list] = mapped_column(JSON, default=list)  # [{"file": "<token>.jpg", "kind": "image|video"}]
    container_id: Mapped[str | None] = mapped_column(String(64))
    container_created_at: Mapped[datetime | None]
    external_media_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    permalink: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None]
    published_at: Mapped[datetime | None]
    insights: Mapped[dict | None] = mapped_column(JSON)
    insights_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class FunnelRule(Base):
    __tablename__ = "funnel_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    media_id: Mapped[str | None] = mapped_column(String(64))  # None = all posts
    dm_text: Mapped[str] = mapped_column(Text)
    public_replies: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=now)


class FunnelSend(Base):
    __tablename__ = "funnel_sends"
    __table_args__ = (
        # one DM per person per rule (failed attempts don't count)
        Index("uq_funnel_rule_user", "rule_id", "commenter_id", unique=True,
              postgresql_where=text("status <> 'failed'"), sqlite_where=text("status <> 'failed'")),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int] = mapped_column(ForeignKey("funnel_rules.id", ondelete="CASCADE"))
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    comment_id: Mapped[str] = mapped_column(String(128), unique=True)
    commenter_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))  # sending | sent | failed
    message_id: Mapped[str | None] = mapped_column(String(128))
    public_reply_id: Mapped[str | None] = mapped_column(String(128))
    public_reply_text: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now)


class Lead(Base):
    __tablename__ = "leads"
    id: Mapped[int] = mapped_column(primary_key=True)
    handle: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    niche: Mapped[str | None] = mapped_column(String(100))
    website: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    profile_text: Mapped[str | None] = mapped_column(Text)
    # new | researched | contacted | replied | call | won | lost
    stage: Mapped[str] = mapped_column(String(12), default="new", index=True)
    research: Mapped[dict | None] = mapped_column(JSON)
    rubric_total: Mapped[int | None] = mapped_column(Integer)
    pitch: Mapped[str | None] = mapped_column(Text)
    followups: Mapped[list] = mapped_column(JSON, default=list)
    followups_sent: Mapped[int] = mapped_column(Integer, default=0)
    contacted_at: Mapped[datetime | None]
    next_followup_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=now)
    updated_at: Mapped[datetime] = mapped_column(default=now, onupdate=now)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (UniqueConstraint("account_id", "igsid"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    igsid: Mapped[str] = mapped_column(String(64))
    username: Mapped[str | None] = mapped_column(String(100))
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    last_inbound_at: Mapped[datetime | None]
    last_message_at: Mapped[datetime] = mapped_column(default=now)
    needs_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=now)

    lead: Mapped[Lead | None] = relationship()


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conv", "conversation_id", "id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    mid: Mapped[str | None] = mapped_column(String(128), unique=True)
    direction: Mapped[str] = mapped_column(String(3))  # in | out
    text: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list] = mapped_column(JSON, default=list)
    # received | draft | approved | sending | sent | failed | discarded
    status: Mapped[str] = mapped_column(String(10))
    actor: Mapped[str | None] = mapped_column(String(100))  # user | auto | instagram_app
    meta: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=now)


class DailyTask(Base):
    __tablename__ = "daily_tasks"
    __table_args__ = (UniqueConstraint("day", "creator_id"), UniqueConstraint("day", "post_id"))
    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(12))  # creator | opportunity
    creator_id: Mapped[int | None] = mapped_column(ForeignKey("creators.id", ondelete="CASCADE"))
    post_id: Mapped[int | None] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"))
    done_at: Mapped[datetime | None]


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"
    __table_args__ = (UniqueConstraint("account_id", "day"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    day: Mapped[date]
    followers: Mapped[int | None] = mapped_column(Integer)
    media_count: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=now)


AUTO_ACTOR = "auto"  # Approval.actor / Message.actor for decisions made by the automation gate


def aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; everything we store is UTC."""
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def audit(session, actor: str, action: str, entity: str, entity_id: int | None, **data) -> None:
    """Adds to the session only; caller commits it together with the change it describes."""
    session.add(AuditLog(actor=actor, action=action, entity=entity, entity_id=entity_id, data=data))
