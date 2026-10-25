"""Passage d'un produit traité (photos prêtes) au catalogue : fiche rédigée, plateformes choisies, file."""
from __future__ import annotations

from pathlib import Path

from .catalog import A_METTRE_A_JOUR, EPUISE, PUBLIE, Catalog, CatalogItem
from .config import Settings
from .listing import ai
from .listing.builder import build_listing, fill_texts
from .models import Product


def web_media(product: Product) -> tuple[list[str], list[str]]:
    web = Path(product.folder) / "web"
    return sorted(str(p) for p in web.glob("*.jpg")), sorted(str(p) for p in web.glob("*.mp4"))


def write_listing(item: CatalogItem, settings: Settings, catalog: Catalog, use_ai: bool = True) -> str:
    """(Re)rédige la fiche ; renvoie un message pour l'interface (vide si tout va bien)."""
    kind = settings.kind(item.kind_id)
    if use_ai and ai.api_key():
        try:
            data, cost = ai.generate(item.info, kind, item.photos, settings, catalog)
        except ai.AiError as e:
            item.listing = build_listing(item.info, kind, settings)
            return str(e)
        item.listing = build_listing(item.info, kind, settings, data, cost)
        return ""
    item.listing = build_listing(item.info, kind, settings)
    return "" if not use_ai else "Clé IA non renseignée : textes à compléter dans la fiche."


def register_product(product: Product, settings: Settings, catalog: Catalog) -> tuple[CatalogItem, str]:
    """Ajoute (ou met à jour) le produit dans le catalogue après le traitement des photos."""
    photos, videos = web_media(product)
    existing = catalog.get(product.sku)
    item = existing or CatalogItem(sku=product.sku, kind_id=product.kind_id)
    item.kind_id = product.kind_id
    item.info = product.info
    item.folder = product.folder
    item.photos, item.videos = photos, videos
    item.stock = max(0, int(product.info.quantite or 1))
    item.state("shopify").wanted = True
    item.state("etsy").wanted = item.state("etsy").wanted or product.info.publier_etsy
    item.state("ebay").wanted = item.state("ebay").wanted or product.info.publier_ebay

    message = ""
    kind = settings.kind(item.kind_id)
    if existing and existing.listing.edited:
        # Textes retouchés à la main : on garde, seuls les blocs calculés (poids, prix…) sont refaits
        item.listing = fill_texts(existing.listing, item.info, kind, settings)
    elif existing and existing.listing.source == "ia" and existing.listing.ready:
        item.listing = fill_texts(existing.listing, item.info, kind, settings)
    else:
        message = write_listing(item, settings, catalog, use_ai=settings.ai_auto)

    if existing:
        for state in item.platforms.values():
            if state.remote_id and state.status in (PUBLIE, EPUISE):
                state.status = A_METTRE_A_JOUR
    catalog.put(item)
    return item, message
