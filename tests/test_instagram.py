import json

import httpx
import pytest

from app.platforms import PlatformError
from app.platforms.instagram import InstagramAdapter

BASE = "https://graph.instagram.com/v27.0"


def adapter(handler):
    return InstagramAdapter("tok", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_private_reply_shape():
    seen = {}

    def h(req):
        seen["url"], seen["body"] = str(req.url).split("?")[0], json.loads(req.content)
        assert req.url.params["access_token"] == "tok"
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
