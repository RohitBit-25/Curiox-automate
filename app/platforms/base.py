"""Platform seam: the contract every platform adapter implements."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class RemoteItem:
    external_id: str
    text: str
    author_handle: str | None
    author_id: str | None
    published_at: datetime | None
    parent_external_id: str | None
    parent_text: str | None
    url: str | None


class PlatformError(Exception):
    def __init__(self, message: str, code: int | None = None, pause: bool = False):
        super().__init__(message)
        self.code, self.pause = code, pause


class PlatformAdapter(Protocol):
    name: str
    async def me(self) -> dict: ...
    async def list_own_media(self, limit: int = 10) -> list[dict]: ...
    async def list_comments(self, media: dict) -> list[RemoteItem]: ...
    async def reply_to_comment(self, comment_id: str, message: str) -> str: ...
    async def refresh_token(self) -> tuple[str, int]: ...
