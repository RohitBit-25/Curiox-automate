import json

import httpx
import pytest

from app.platforms import PlatformError
from app.platforms.instagram import InstagramAdapter

BASE = "https://graph.instagram.com/v27.0"


def adapter(handler, token="IGtok"):
    return InstagramAdapter(token, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_private_reply_shape():
    seen = {}

    def h(req):
        seen["url"], seen["body"] = str(req.url).split("?")[0], json.loads(req.content)
        assert req.url.params["access_token"] == "IGtok"
        return httpx.Response(200, json={"recipient_id": "9", "message_id": "m-1"})
    assert await adapter(h).private_reply("123", "c1", "hi") == "m-1"
    assert seen == {"url": f"{BASE}/123/messages", "body": {"recipient": {"comment_id": "c1"}, "message": {"text": "hi"}}}


@pytest.mark.parametrize("code,pause", [(10, False), (190, True)])
async def test_messaging_errors_pause_only_on_dead_token(code, pause):
    def h(req):
        return httpx.Response(400, json={"error": {"code": code, "message": "nope"}})
    with pytest.raises(PlatformError) as e:
        await adapter(h).send_message("123", "555", "hi")
    assert e.value.pause is pause


async def test_publishing_limit_and_insights_parsing():
    def h(req):
        if "content_publishing_limit" in req.url.path:
            return httpx.Response(200, json={"data": [{"quota_usage": 2, "config": {"quota_total": 50, "quota_duration": 86400}}]})
        return httpx.Response(200, json={"data": [{"name": "reach", "total_value": {"value": 120}}]})
    a = adapter(h)
    assert await a.publishing_limit("123") == (2, 50)
    assert await a.account_insights("123", "reach", 0, 1) == {"reach": 120}
    assert await a.media_insights("m", "reach") == {"reach": 120}


async def test_create_container_posts_form():
    def h(req):
        assert req.method == "POST" and req.url.path.endswith("/123/media")
        assert b"is_carousel_item=true" in req.content
        return httpx.Response(200, json={"id": "c-9"})
    assert await adapter(h).create_container("123", {"image_url": "https://x/a.jpg", "is_carousel_item": "true"}) == "c-9"


def test_token_hints():
    from app.api.sources import token_hint
    assert "Unified login" in token_hint("Invalid request: Request parameters are invalid: Invalid platform app", "IGAAx")
    assert "incomplete" in token_hint("Cannot parse access token", "EAAx")
    assert token_hint("something else", "IGAAx") == ""


async def test_unified_login_uses_graph_facebook_and_assigned_account():
    seen = []

    def h(req):
        seen.append(str(req.url).split("?")[0])
        if req.url.path.endswith("/me/assigned_instagram_accounts"):
            return httpx.Response(200, json={"data": [{"id": "1784", "username": "curiox.os"}]})
        if req.url.path.endswith("/1784"):
            return httpx.Response(200, json={"username": "curiox.os", "followers_count": 900, "media_count": 12})
        if req.url.path.endswith("/1784/media"):
            return httpx.Response(200, json={"data": [{"id": "m1"}]})
        return httpx.Response(200, json={"message_id": "x"})
    a = adapter(h, token="EAAsystemusertoken")
    assert await a.me() == {"user_id": "1784", "username": "curiox.os", "followers_count": 900, "media_count": 12}
    assert await a.list_own_media() == [{"id": "m1"}]
    assert await a.private_reply("1784", "c1", "hi") == "x"
    assert all(u.startswith("https://graph.facebook.com/v27.0/") for u in seen)
    assert await a.refresh_token() == ("EAAsystemusertoken", 365 * 86400)


async def test_unified_without_assigned_account_is_explained():
    def h(req):
        return httpx.Response(200, json={"data": []})
    with pytest.raises(PlatformError, match="assigned"):
        await adapter(h, token="EAAx").me()
