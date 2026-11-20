from datetime import UTC, datetime
from functools import lru_cache

from cryptography.fernet import Fernet
from sqlalchemy import (
    JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
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
    type_annotation_map = {datetime: DateTime(timezone=True)}


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


def audit(session, actor: str, action: str, entity: str, entity_id: int | None, **data) -> None:
    """Adds to the session only; caller commits it together with the change it describes."""
    session.add(AuditLog(actor=actor, action=action, entity=entity, entity_id=entity_id, data=data))
