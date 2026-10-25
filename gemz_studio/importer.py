"""Import des produits existants (Shopify) et liaison des annonces Etsy correspondantes.

Les annonces déjà en ligne des deux côtés ne sont pas recréées : elles sont seulement reliées dans le
catalogue, pour que la synchro du stock les couvre.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .catalog import EPUISE, PUBLIE, Catalog, CatalogItem
from .config import Settings
from .listing.model import SOURCE_MANUEL, Listing, LangText
from .models import ProductInfo
from .platforms import RemoteListing
from .quickentry import strip_accents

ETSY_IMAGE = re.compile(r"il_(?:fullxfull|\d+x[\dN]+)\.(\d+)_([a-z0-9]+)", re.I)


def norm_title(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", strip_accents(text).lower()))


def image_keys(urls: list[str]) -> set[str]:
    """Les photos Shopify importées d'Etsy gardent l'identifiant Etsy dans leur nom (il_fullxfull.6605228772_cayo)."""
    keys = set()
    for url in urls:
        for match in ETSY_IMAGE.finditer(url):
            keys.add(match.group(1))
    return keys


def kind_for(product_type: str, tags: list[str], settings: Settings) -> str:
    for kind in settings.kinds:
        if kind.shopify_type == product_type and (not kind.fixed_tags or set(kind.fixed_tags) & set(tags)):
            return kind.id
    return settings.kinds[0].id


def import_shopify(catalog: Catalog, listings: list[RemoteListing], settings: Settings) -> int:
    """Crée une fiche de catalogue pour chaque produit Shopify absent ; renvoie le nombre ajouté."""
    known = {item.state("shopify").remote_id for item in catalog.all()}
    skus = catalog.skus()
    added = 0
    for remote in listings:
        if remote.remote_id in known or remote.extra.get("status") == "ARCHIVED":
            continue
        sku = (remote.skus[0] if remote.skus else "").strip().upper()
        if not sku or sku in skus:
            sku = f"SHOP{remote.remote_id.rsplit('/', 1)[-1]}"
        listing = Listing(source=SOURCE_MANUEL, ready=True, tags=list(remote.tags))
        listing.texts["fr"] = LangText(title=remote.title, description=remote.description)
        item = CatalogItem(sku=sku, kind_id=kind_for(remote.product_type, remote.tags, settings),
                           info=ProductInfo(nom=remote.title, prix=f"{remote.price:.2f}"), listing=listing,
                           stock=remote.quantity, origin="import")
        state = item.state("shopify")
        state.wanted, state.remote_id, state.url, state.price = True, remote.remote_id, remote.url, remote.price
        state.status = PUBLIE if remote.quantity > 0 else EPUISE
        state.extra = {"variants": remote.extra.get("variants", {}), "images": remote.image_urls,
                       "etsy_images": sorted(image_keys(remote.image_urls))}
        catalog.put(item)
        skus.add(sku)
        added += 1
    return added


@dataclass
class Match:
    etsy: RemoteListing
    item: CatalogItem | None
    method: str          # sku, photo, titre, aucune
    score: float

    @property
    def sure(self) -> bool:
        return self.method in ("sku", "photo") or (self.method == "titre" and self.score >= 0.9)


def match_etsy(catalog: Catalog, etsy_listings: list[RemoteListing]) -> list[Match]:
    """Propose un produit du catalogue pour chaque annonce Etsy (par SKU, photo, puis titre)."""
    items = [i for i in catalog.all() if not i.state("etsy").remote_id]
    by_sku = {i.sku: i for i in items}
    by_image: dict[str, CatalogItem] = {}
    for item in items:
        for key in item.state("shopify").extra.get("etsy_images", []):
            by_image[key] = item
    titles = {norm_title(i.title): i for i in items}
    already = {i.state("etsy").remote_id for i in catalog.all() if i.state("etsy").remote_id}
    out: list[Match] = []
    for listing in etsy_listings:
        if listing.remote_id in already:
            continue
        found = next((by_sku[s.upper()] for s in listing.skus if s.upper() in by_sku), None)
        if found:
            out.append(Match(listing, found, "sku", 1.0))
            continue
        found = next((by_image[k] for k in image_keys(listing.image_urls) if k in by_image), None)
        if found:
            out.append(Match(listing, found, "photo", 1.0))
            continue
        best = difflib.get_close_matches(norm_title(listing.title), list(titles), n=1, cutoff=0.6)
        if best:
            score = difflib.SequenceMatcher(None, norm_title(listing.title), best[0]).ratio()
            out.append(Match(listing, titles[best[0]], "titre", score))
        else:
            out.append(Match(listing, None, "aucune", 0.0))
    return out


def link_etsy(catalog: Catalog, match: Match) -> None:
    if match.item is None:
        return

    def apply(item: CatalogItem):
        state = item.state("etsy")
        state.wanted, state.remote_id, state.url, state.price = True, match.etsy.remote_id, match.etsy.url, match.etsy.price
        state.status = PUBLIE if item.stock > 0 else EPUISE
        if item.stock == 0:
            state.extra["stock_a_envoyer"] = True   # vendu sur Shopify entre-temps : l'annonce Etsy sera désactivée
    catalog.update(match.item.sku, apply)
    catalog.log("info", f"Annonce Etsy reliée ({match.method})", match.item.sku, "etsy", {"etsy": match.etsy.remote_id})
