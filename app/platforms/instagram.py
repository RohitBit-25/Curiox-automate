"""Instagram API: own-account media, comments, publishing, insights, messaging.

Two login flavours, picked from the token (paths are otherwise identical, keyed by the IG account id):
- Unified Login (all new Meta apps): system-user token "EAA...", host graph.facebook.com,
  account id from /me/assigned_instagram_accounts. Scopes: instagram_basic, instagram_manage_comments,
  instagram_content_publish, instagram_manage_messages, instagram_manage_insights.
- Instagram Login (older apps): Instagram user token "IG...", host graph.instagram.com, account id from /me.

There is deliberately no method to comment on third-party media, follow accounts, or message anyone who
hasn't messaged/commented first: those actions don't exist in this adapter.
"""
from datetime import datetime

import httpx

from app.config import settings
from app.platforms.base import PlatformError, RemoteItem

HOST = "https://graph.instagram.com"  # Instagram Login
FB_HOST = "https://graph.facebook.com"  # Unified Login
# Auth/permission/rate/policy errors: pause the account instead of retrying.
PAUSE_CODES = {3, 10, 190, 368} | set(range(200, 300))
# Messaging: only a dead token pauses the account; "outside window" / "already replied" fail just that send.
MSG_PAUSE = {190}


class InstagramAdapter:
    name = "instagram"

    def __init__(self, token: str, client: httpx.AsyncClient | None = None):
        self.token = token
        self.client = client or httpx.AsyncClient()
        self.unified = not token.startswith("IG")
        self.base = f"{FB_HOST if self.unified else HOST}/{settings.IG_API_VERSION}"
        self._ig_id: str | None = None

    async def ig_id(self) -> str:
        """The Instagram professional account id all paths are keyed by."""
        if self._ig_id is None:
            self._ig_id = str((await self.me())["user_id"])
        return self._ig_id

    async def _call(self, method: str, url: str, pause_codes: set = PAUSE_CODES, **kw) -> dict:
        kw.setdefault("params", {})["access_token"] = self.token
        r = await self.client.request(method, url, timeout=30, **kw)
        if r.status_code >= 400:
            is_json = r.headers.get("content-type", "").startswith("application/json")
            err = (r.json() if is_json else {}).get("error", {})
            code = err.get("code")
            raise PlatformError(err.get("message", r.text[:200]), code,
                                pause=code in pause_codes or r.status_code == 401)
        return r.json()

    async def me(self) -> dict:
        """{user_id, username, followers_count, media_count} for the connected account."""
        if not self.unified:
            return await self._call("GET", f"{self.base}/me",
                                    params={"fields": "user_id,username,followers_count,media_count"})
        # Unified: /me is the system user; the IG account is an assigned asset.
        accounts = (await self._call("GET", f"{self.base}/me/assigned_instagram_accounts",
                                     params={"fields": "id,username"})).get("data", [])
        if not accounts:
            raise PlatformError("No Instagram account is assigned to this token. Add the account to the "
                                "configuration (or tester role) and generate the token again.", 100)
        acct = accounts[0]  # ponytail: first assigned account; Curiox runs one account
        self._ig_id = str(acct["id"])
        info = await self._call("GET", f"{self.base}/{self._ig_id}",
                                params={"fields": "username,followers_count,media_count"})
        return {"user_id": self._ig_id, "username": info.get("username", acct.get("username")),
                "followers_count": info.get("followers_count"), "media_count": info.get("media_count")}

    async def list_own_media(self, limit: int = 10) -> list[dict]:
        j = await self._call("GET", f"{self.base}/{await self.ig_id()}/media", params={
            "fields": "id,caption,permalink,timestamp,comments_count,media_type,media_product_type,"
                      "thumbnail_url,media_url", "limit": limit})
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
        if self.unified:
            # ponytail: system-user token lifetime is set in the Meta configuration ("Never" recommended);
            # no refresh endpoint for it here. If it expires, calls fail with 190 and the account pauses.
            return self.token, 365 * 86400
        j = await self._call("GET", f"{HOST}/refresh_access_token", params={"grant_type": "ig_refresh_token"})
        return j["access_token"], int(j["expires_in"])

    # --- Content publishing: container -> status -> publish ---
    async def create_container(self, ig: str, params: dict) -> str:
        return (await self._call("POST", f"{self.base}/{ig}/media", data=params))["id"]

    async def container_status(self, container_id: str) -> str:
        return (await self._call("GET", f"{self.base}/{container_id}", params={"fields": "status_code"}))["status_code"]

    async def publish_container(self, ig: str, container_id: str) -> str:
        return (await self._call("POST", f"{self.base}/{ig}/media_publish", data={"creation_id": container_id}))["id"]

    async def publishing_limit(self, ig: str) -> tuple[int, int]:
        j = await self._call("GET", f"{self.base}/{ig}/content_publishing_limit",
                             params={"fields": "quota_usage,config"})
        if not j.get("data"):
            return 0, 25
        d = j["data"][0]
        return int(d.get("quota_usage", 0)), int((d.get("config") or {}).get("quota_total", 25))

    async def media_info(self, media_id: str) -> dict:
        return await self._call("GET", f"{self.base}/{media_id}",
                                params={"fields": "permalink,media_product_type,timestamp"})

    # --- Insights ---
    async def media_insights(self, media_id: str, metrics: str) -> dict:
        j = await self._call("GET", f"{self.base}/{media_id}/insights", params={"metric": metrics})
        return {m["name"]: (m.get("total_value") or {}).get("value", (m.get("values") or [{}])[0].get("value"))
                for m in j.get("data", [])}

    async def account_insights(self, ig: str, metrics: str, since: int, until: int) -> dict:
        j = await self._call("GET", f"{self.base}/{ig}/insights", params={
            "metric": metrics, "period": "day", "metric_type": "total_value", "since": since, "until": until})
        return {m["name"]: (m.get("total_value") or {}).get("value") for m in j.get("data", [])}

    # --- Messaging: only to people who commented on our media or messaged us ---
    async def private_reply(self, ig: str, comment_id: str, text: str) -> str:
        """One DM to the author of a comment on our own media (within 7 days of the comment)."""
        j = await self._call("POST", f"{self.base}/{ig}/messages", pause_codes=MSG_PAUSE,
                             json={"recipient": {"comment_id": comment_id}, "message": {"text": text}})
        return j.get("message_id", "")

    async def send_message(self, ig: str, igsid: str, text: str) -> str:
        """Reply inside the 24h window opened by their message. Callers enforce the window."""
        j = await self._call("POST", f"{self.base}/{ig}/messages", pause_codes=MSG_PAUSE,
                             json={"recipient": {"id": igsid}, "message": {"text": text}})
        return j.get("message_id", "")

    async def user_profile(self, igsid: str) -> dict:
        return await self._call("GET", f"{self.base}/{igsid}", pause_codes=MSG_PAUSE,
                                params={"fields": "username,name"})

    async def subscribe_webhooks(self, ig: str, fields: str = "comments,messages") -> bool:
        j = await self._call("POST", f"{self.base}/{ig}/subscribed_apps", params={"subscribed_fields": fields})
        return bool(j.get("success"))
