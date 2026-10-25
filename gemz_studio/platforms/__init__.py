"""Plateformes de vente : Shopify (référence), Etsy, eBay."""
from __future__ import annotations

from ..config import Settings
from .base import NotConfigured, Platform, PlatformError, RemoteListing, Sale


def make_platforms(settings: Settings) -> dict[str, Platform]:
    from .ebay import Ebay
    from .etsy import Etsy
    from .shopify import Shopify
    return {"shopify": Shopify(settings), "etsy": Etsy(settings), "ebay": Ebay(settings)}


__all__ = ["make_platforms", "NotConfigured", "Platform", "PlatformError", "RemoteListing", "Sale"]
