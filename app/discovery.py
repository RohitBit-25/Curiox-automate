"""Own-account discovery: comments left on Curiox's own media via the official API."""
from sqlalchemy import select

from app.models import Account, Post
from app.platforms.base import PlatformAdapter, RemoteItem


async def ingest_item(session, account: Account, item: RemoteItem) -> Post | None:
    if item.author_id and item.author_id == account.external_user_id:
        return None  # our own comment
    if item.author_handle and item.author_handle.lower() == account.handle.lower():
        return None
    exists = await session.scalar(select(Post.id).where(
        Post.platform == account.platform, Post.external_id == item.external_id))
    if exists:
        return None
    post = Post(platform=account.platform, source="own_comment", account_id=account.id,
                external_id=item.external_id, url=item.url, author_handle=item.author_handle,
                author_id=item.author_id, parent_external_id=item.parent_external_id,
                parent_text=item.parent_text, text=item.text, published_at=item.published_at)
    session.add(post)
    await session.flush()
    return post


async def discover_account(session, account: Account, adapter: PlatformAdapter) -> list[int]:
    new_ids = []
    for media in await adapter.list_own_media(10):
        for item in await adapter.list_comments(media):
            if post := await ingest_item(session, account, item):
                new_ids.append(post.id)
    await session.commit()
    return new_ids
