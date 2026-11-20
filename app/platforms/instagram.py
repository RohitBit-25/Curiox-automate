"""Instagram API with Instagram Login (graph.instagram.com). Own-account media and comments only.

Required scopes: instagram_business_basic, instagram_business_manage_comments.
There is deliberately no method to comment on third-party media.
"""
from datetime import datetime

import httpx

from app.config import settings
from app.platforms.base import PlatformError, RemoteItem

HOST = "https://graph.instagram.com"
# Auth/permission/rate/policy errors: pause the account instead of retrying.
PAUSE_CODES = {3, 10, 190, 368} | set(range(200, 300))


class InstagramAdapter:
    name = "instagram"

    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self.token = token
        self.client = client or httpx.AsyncClient()
        self.base = f"{HOST}/{settings.IG_API_VERSION}"

    async def _call(self, method: str, url: str, **kw) -> dict:
        kw.setdefault("params", {})["access_token"] = self.token
        r = await self.client.request(method, url, timeout=30, **kw)
        if r.status_code >= 400:
            is_json = r.headers.get("content-type", "").startswith("application/json")
            err = (r.json() if is_json else {}).get("error", {})
            code = err.get("code")
            raise PlatformError(err.get("message", r.text[:200]), code,
                                pause=code in PAUSE_CODES or r.status_code in (401, 403))
        return r.json()

    async def me(self) -> dict:
        return await self._call("GET", f"{self.base}/me", params={"fields": "user_id,username"})

    async def list_own_media(self, limit: int = 10) -> list[dict]:
        j = await self._call("GET", f"{self.base}/me/media", params={
            "fields": "id,caption,permalink,timestamp,comments_count", "limit": limit})
        return j.get("data", [])

    async def list_comments(self, media: dict) -> list[RemoteItem]:
        j = await self._call("GET", f"{self.base}/{media['id']}/comments", params={
            "fields": "id,text,timestamp,username,from", "limit": 50})
        return [RemoteItem(
            external_id=c["id"], text=c.get("text", ""), author_handle=c.get("username"),
            author_id=(c.get("from") or {}).get("id"),
            published_at=datetime.strptime(c["timestamp"], "%Y-%m-%dT%H:%M:%S%z") if c.get("timestamp") else None,
            parent_external_id=media["id"], parent_text=media.get("caption"), url=media.get("permalink"),
        ) for c in j.get("data", [])]

    async def reply_to_comment(self, comment_id: str, message: str) -> str:
        j = await self._call("POST", f"{self.base}/{comment_id}/replies", data={"message": message})
        return j["id"]

    async def refresh_token(self) -> tuple[str, int]:
        j = await self._call("GET", f"{HOST}/refresh_access_token", params={"grant_type": "ig_refresh_token"})
        return j["access_token"], int(j["expires_in"])
