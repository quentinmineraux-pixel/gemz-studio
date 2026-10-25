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


def shop_images(item: CatalogItem) -> list[str]:
    """Photos de la boutique (pièce importée de Shopify, sans photo locale)."""
    return [u for u in item.state("shopify").extra.get("images", []) if str(u).startswith("https://")]


def write_listing(item: CatalogItem, settings: Settings, catalog: Catalog, use_ai: bool = True) -> str:
    """(Re)rédige la fiche ; renvoie un message pour l'interface (vide si tout va bien)."""
    kind = settings.kind(item.kind_id)
    if use_ai and ai.api_key():
        from .platforms.etsy import html_to_text
        current = html_to_text(item.listing.texts["fr"].description) if item.listing.imported else ""
        try:
            data, cost = ai.generate(item.info, kind, item.photos, settings, catalog,
                                     image_urls=[] if item.photos else shop_images(item), current_text=current)
        except ai.AiError as e:
            if not item.listing.imported:   # une fiche importée garde son texte en cas d'échec
                item.listing = build_listing(item.info, kind, settings)
            return str(e)
        item.listing = build_listing(item.info, kind, settings, data, cost)
        return ""
    if item.listing.imported:   # sans IA, une fiche importée garde le texte de la boutique
        return "" if not use_ai else "Clé IA non renseignée : la fiche garde le texte de la boutique."
    item.listing = build_listing(item.info, kind, settings)
    return "" if not use_ai else "Clé IA non renseignée : textes à compléter dans la fiche."


def register_product(product: Product, settings: Settings, catalog: Catalog) -> tuple[CatalogItem, str]:
    """Ajoute (ou met à jour) le produit dans le catalogue après le traitement des photos."""
    photos, videos = web_media(product)
    existing = catalog.get(product.sku)
    item = existing or CatalogItem(sku=product.sku, kind_id=product.kind_id)
    old_price = (existing.info.prix, existing.info.prix_barre) if existing else None
    item.kind_id = product.kind_id
    item.info = product.info
    if old_price:
        item.info.prix_barre = old_price[1]   # promotion : réglée dans la fiche du catalogue
    item.folder = product.folder
    item.photos, item.videos = photos, videos
    if not existing:
        # Une pièce déjà au catalogue garde son stock (ventes comprises) quand on la retraite
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
        price_changed = old_price != (item.info.prix, item.info.prix_barre)
        for state in item.platforms.values():
            if state.remote_id and photos and "media_sig" not in state.extra:
                # Photos jamais envoyées d'ici (pièce importée, ou publiée par une version précédente) : les
                # annonces prennent celles-ci ; sinon l'empreinte notée à l'envoi dit si elles ont changé
                state.extra["media_sig"] = "à remplacer"
            if state.remote_id and state.status in (PUBLIE, EPUISE):
                state.status = A_METTRE_A_JOUR
            if state.remote_id and price_changed:
                state.extra["prix_a_envoyer"] = True
    catalog.put(item)
    return item, message
