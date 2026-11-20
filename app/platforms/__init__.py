"""Adapter registry. Add LinkedIn/X/YouTube by implementing base.PlatformAdapter and registering here."""
from app.platforms.base import PlatformAdapter, PlatformError, RemoteItem
from app.platforms.instagram import InstagramAdapter

ADAPTERS: dict[str, type] = {"instagram": InstagramAdapter}


def get_adapter(platform: str, token: str) -> PlatformAdapter:
    return ADAPTERS[platform](token)


__all__ = ["ADAPTERS", "PlatformAdapter", "PlatformError", "RemoteItem", "get_adapter"]
