"""Import des produits existants (Shopify) et liaison des annonces Etsy correspondantes.

Les annonces déjà en ligne des deux côtés ne sont pas recréées : elles sont seulement reliées dans le
catalogue, pour que la synchro du stock les couvre.
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass

from .catalog import EPUISE, PLATFORMS, PUBLIE, Catalog, CatalogItem
from .config import Settings
from .engine import base_sku
from .listing import vocab
from .listing.model import SOURCE_IMPORT, SOURCE_MANUEL, Listing, LangText
from .listing.prices import price_text
from .models import ProductInfo
from .platforms import RemoteListing
from .quickentry import strip_accents

ETSY_IMAGE = re.compile(r"il_(?:fullxfull|\d+x[\dN]+)\.(\d+)_([a-z0-9]+)", re.I)
GEMZ_SKU = re.compile(r"^[A-Z]{2,}\d+")      # MFC20, VABM11, HE12… (un SKU « 10 » ne sert pas de référence)


def norm_title(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", strip_accents(text).lower()))


def image_keys(urls: list[str]) -> set[str]:
    """Les photos Shopify importées d'Etsy gardent l'identifiant Etsy dans leur nom (il_fullxfull.6605228772_cayo)."""
    keys = set()
    for url in urls:
        for match in ETSY_IMAGE.finditer(url):
            keys.add(match.group(1))
    return keys


def _html_field(html: str, label: str) -> str:
    match = re.search(rf"{label}\s*:?\s*(?:</strong>|</b>)?\s*:?\s*([^<\n]+)", html, re.I)
    return match.group(1).strip(" :") if match else ""


def facts_from_shopify(title: str, html: str) -> dict:
    """Retrouve nom, poids, dimensions et provenance d'une fiche déjà en ligne.

    Titre type : « Vanadinite sur baryte en éventail – Mibladen, Maroc – 47 g » ; description avec les
    lignes Poids / Dimensions / Provenance. Sans ça, une réécriture par l'IA perdrait ces informations.
    """
    facts: dict[str, str] = {}
    parts = [p.strip() for p in re.split(r"\s[–—-]\s", title) if p.strip()]
    if parts and re.fullmatch(r"[\d.,]+\s*g", parts[-1], re.I):
        facts["poids_g"] = re.sub(r"\s*g$", "", parts.pop(), flags=re.I).strip()
    if len(parts) >= 2:
        place = parts.pop()
        if "," in place:
            region, pays = place.rsplit(",", 1)
            facts["region"], facts["pays"] = region.strip(), pays.strip()
        else:
            facts["pays"] = place
    if parts:
        name = parts[0]
        if "," in name:
            name, variete = name.split(",", 1)
            facts["variete"] = variete.strip()
        facts["nom"] = name.strip()
    poids = _html_field(html, "Poids")
    if poids and "poids_g" not in facts:
        facts["poids_g"] = re.sub(r"\s*g\b.*$", "", poids, flags=re.I).strip()
    dims = _html_field(html, "Dimensions")
    if dims:
        facts["dimensions"] = re.sub(r"\s*cm\b.*$", "", dims, flags=re.I).replace("×", "x").replace(" ", "")
    origin = _html_field(html, "Provenance") or _html_field(html, "Origine")
    if origin and "pays" not in facts:
        match = re.match(r"(.+?)\s*\((.+)\)\s*$", origin)
        if match:
            facts["region"], facts["pays"] = match.group(1).strip(), match.group(2).strip()
        else:
            facts["pays"] = origin
    return facts


def info_from_remote(remote: RemoteListing, settings: Settings, kind_id: str) -> ProductInfo:
    info = ProductInfo(nom=remote.title, prix=price_text(remote.price), quantite=max(1, remote.quantity))
    compare = float(remote.extra.get("compare_at") or 0)
    if compare > remote.price:
        info.prix_barre = price_text(compare)
    for key, value in facts_from_shopify(remote.title, remote.description).items():
        setattr(info, key, value)
    kind = settings.kind(kind_id)
    info.sous_categories = [t for t in remote.tags if t in kind.subtags]
    info.plateformes_choisies = True
    info.publier_etsy = info.publier_ebay = False
    return info


def kind_for(product_type: str, tags: list[str], settings: Settings) -> str:
    for kind in settings.kinds:
        if kind.shopify_type == product_type and (not kind.fixed_tags or set(kind.fixed_tags) & set(tags)):
            return kind.id
    return settings.kinds[0].id


def shop_texts(remote: RemoteListing) -> LangText:
    seo = remote.extra.get("seo") or {}
    return LangText(title=remote.title, description=remote.description, seo_title=seo.get("title") or "",
                    seo_description=seo.get("description") or "")


def refresh_imported(item: CatalogItem, remote: RemoteListing, settings: Settings) -> None:
    """Pièce déjà importée : textes, tags et photos repris de Shopify (modifiés dans l'admin depuis),
    tant que la fiche n'a pas été réécrite dans GemZ ni n'attend d'envoi. Prix et stock suivent par la synchro."""
    state = item.state("shopify")
    if not item.photos and remote.image_urls:
        state.extra["images"] = list(remote.image_urls)
        state.extra["etsy_images"] = sorted(image_keys(remote.image_urls))
    if remote.extra.get("variants"):
        state.extra["variants"] = remote.extra["variants"]
    if item.listing.imported and state.status in (PUBLIE, EPUISE):
        item.listing.texts["fr"] = shop_texts(remote)
        item.listing.tags = list(remote.tags)
    if not item.info.poids_g:
        # Importé par une version précédente : on complète poids, dimensions, provenance
        facts = info_from_remote(remote, settings, item.kind_id)
        for key in ("nom", "variete", "poids_g", "dimensions", "pays", "region", "sous_categories"):
            if getattr(facts, key) and not (key == "nom" and item.listing.source not in (SOURCE_MANUEL, SOURCE_IMPORT)):
                setattr(item.info, key, getattr(facts, key))


def import_shopify(catalog: Catalog, listings: list[RemoteListing], settings: Settings) -> int:
    """Crée une fiche de catalogue pour chaque produit Shopify absent ; renvoie le nombre ajouté."""
    items = catalog.all()
    skus = catalog.skus()
    added = 0
    by_remote = {item.state("shopify").remote_id: item for item in items if item.state("shopify").remote_id}
    # Orthographes des métachamps déjà utilisées dans la boutique (Pierre, Provenance, Forme)
    vocab.remember({key: [v for r in listings for v in (r.extra.get("metafields") or {}).get(key, [])]
                    for key in vocab.KEYS})
    for remote in listings:
        existing = by_remote.get(remote.remote_id)
        if existing:
            if existing.origin == "import":
                before = existing.to_json()
                refresh_imported(existing, remote, settings)
                if existing.to_json() != before:   # n'écrit (et ne rafraîchit l'écran) que si quelque chose a changé
                    catalog.update(existing.sku, lambda it, r=remote: refresh_imported(it, r, settings))
            continue
        if remote.extra.get("status") == "ARCHIVED":
            continue
        sku = base_sku(remote.skus[0]) if remote.skus else ""
        if not GEMZ_SKU.match(sku) or sku in skus:
            sku = f"SHOP{remote.remote_id.rsplit('/', 1)[-1]}"
        listing = Listing(source=SOURCE_IMPORT, ready=True, tags=list(remote.tags))
        listing.texts["fr"] = shop_texts(remote)
        kind_id = kind_for(remote.product_type, remote.tags, settings)
        item = CatalogItem(sku=sku, kind_id=kind_id, info=info_from_remote(remote, settings, kind_id),
                           listing=listing, stock=remote.quantity, origin="import")
        state = item.state("shopify")
        state.wanted, state.remote_id, state.url, state.price = True, remote.remote_id, remote.url, remote.price
        state.status = PUBLIE if remote.quantity > 0 else EPUISE
        state.extra = {"variants": remote.extra.get("variants", {}), "images": remote.image_urls,
                       "etsy_images": sorted(image_keys(remote.image_urls))}
        catalog.put(item)
        skus.add(sku)
        added += 1
    bump_sku_counters(settings, [s for r in listings for s in r.skus])
    return added


def bump_sku_counters(settings: Settings, skus: list[str]) -> None:
    """Les numéros déjà pris sur la boutique (MFC1…MFC42) ne seront jamais redonnés à un nouveau produit."""
    changed = False
    for sku in skus:
        match = re.match(r"^([A-Z]+)(\d+)(?:-.*)?$", sku.strip().upper())
        if match and int(match.group(2)) > settings.sku_counters.get(match.group(1), 0):
            settings.sku_counters[match.group(1)] = int(match.group(2))
            changed = True
    if changed:
        settings.save()


@dataclass
class Match:
    etsy: RemoteListing
    item: CatalogItem | None
    method: str          # sku, photo, titre, aucune
    score: float

    @property
    def sold(self) -> bool:
        """Annonce épuisée sur Etsy : la pièce y a été vendue (peut-être avant que GemZ ne surveille)."""
        return self.etsy.extra.get("state") == "sold_out"

    @property
    def sure(self) -> bool:
        return self.method in ("sku", "photo") or (self.method == "titre" and self.score >= 0.9 and not self.sold)


def match_etsy(catalog: Catalog, etsy_listings: list[RemoteListing]) -> list[Match]:
    """Propose un produit du catalogue pour chaque annonce Etsy (par SKU, photo, puis titre).

    Les annonces épuisées (pièce vendue sur Etsy) ne sont proposées que si elles correspondent sûrement
    (SKU ou photo) à une pièce encore en stock dans le catalogue : vendue avant la liaison, elle est
    peut-être encore en vente sur Shopify."""
    items = [i for i in catalog.all() if not i.state("etsy").remote_id]
    by_sku = {i.sku: i for i in items}
    by_image: dict[str, CatalogItem] = {}
    for item in items:
        for key in item.state("shopify").extra.get("etsy_images", []):
            by_image[key] = item
    titles = {norm_title(i.title): i for i in items}
    already = {i.state("etsy").remote_id for i in catalog.all() if i.state("etsy").remote_id}
    out: list[Match] = []
    active_items: set[str] = set()
    # Annonces actives d'abord : une pièce remise en vente garde sa nouvelle annonce plutôt que l'ancienne
    ordered = sorted(etsy_listings, key=lambda r: r.extra.get("state") == "sold_out")
    for listing in ordered:
        if listing.remote_id in already:
            continue
        found = next((by_sku[s.upper()] for s in listing.skus if s.upper() in by_sku), None)
        method = "sku" if found else ""
        if not found:
            found = next((by_image[k] for k in image_keys(listing.image_urls) if k in by_image), None)
            method = "photo" if found else ""
        if listing.extra.get("state") == "sold_out":
            if found and found.stock > 0 and found.sku not in active_items:
                active_items.add(found.sku)   # une seule ancienne annonce par pièce
                out.append(Match(listing, found, method, 1.0))
            continue
        if found:
            active_items.add(found.sku)
            out.append(Match(listing, found, method, 1.0))
            continue
        best = difflib.get_close_matches(norm_title(listing.title), list(titles), n=1, cutoff=0.6)
        if best:
            score = difflib.SequenceMatcher(None, norm_title(listing.title), best[0]).ratio()
            active_items.add(titles[best[0]].sku)
            out.append(Match(listing, titles[best[0]], "titre", score))
        else:
            out.append(Match(listing, None, "aucune", 0.0))
    return out


def link_etsy(catalog: Catalog, match: Match) -> None:
    if match.item is None:
        return
    sold_here: list[bool] = []

    def apply(item: CatalogItem):
        sold_here.clear()
        state = item.state("etsy")
        state.wanted, state.remote_id, state.url, state.price = True, match.etsy.remote_id, match.etsy.url, match.etsy.price
        if match.sold and item.stock <= 1:
            # Dernière pièce vendue sur Etsy avant la liaison : retirée de Shopify et des autres plateformes
            item.stock = 0
            for name in PLATFORMS:
                st = item.state(name)
                if st.remote_id:
                    st.status = EPUISE
                    if name != "etsy":
                        st.extra["stock_a_envoyer"] = True
            sold_here.append(True)
            return
        state.status = PUBLIE if item.stock > 0 else EPUISE
        if item.stock == 0 or match.sold:
            # Vendu sur Shopify entre-temps : l'annonce Etsy sera désactivée ; annonce épuisée sur Etsy alors
            # qu'il reste du stock : remise en vente avec ce stock
            state.extra["stock_a_envoyer"] = True
    catalog.update(match.item.sku, apply)
    catalog.log("info", f"Annonce Etsy reliée ({match.method})", match.item.sku, "etsy", {"etsy": match.etsy.remote_id})
    if sold_here:
        catalog.log("synchro", "Déjà vendue sur Etsy (annonce épuisée) : retirée de la vente partout", match.item.sku, "etsy")


def adopt_etsy_defaults(catalog: Catalog, etsy_listings: list[RemoteListing], settings: Settings) -> list[str]:
    """Reprend les réglages des annonces Etsy existantes (catégorie par type, expédition, retours, langue…)
    pour les réglages encore vides ; renvoie ce qui a été rempli."""
    by_id = {r.remote_id: r for r in etsy_listings}
    linked = [(item, by_id[item.state("etsy").remote_id]) for item in catalog.all()
              if item.state("etsy").remote_id in by_id]
    if not linked:
        return []
    filled: list[str] = []

    def common(values):
        values = [v for v in values if v not in (None, "", 0)]
        return Counter(values).most_common(1)[0][0] if values else None

    for kind in settings.kinds:
        if not kind.etsy_taxonomy_id:
            taxonomy = common([r.extra.get("taxonomy_id") for item, r in linked if item.kind_id == kind.id])
            if taxonomy:
                kind.etsy_taxonomy_id = int(taxonomy)
                filled.append(f"catégorie Etsy « {kind.label} »")
    listings = [r for _item, r in linked]
    for attr, key, label in (("etsy_shipping_profile_id", "shipping_profile_id", "profil d'expédition"),
                             ("etsy_return_policy_id", "return_policy_id", "politique de retour")):
        value = common([r.extra.get(key) for r in listings])
        if value and not getattr(settings, attr):
            setattr(settings, attr, str(value))
            filled.append(label)
    who, when = common([r.extra.get("who_made") for r in listings]), common([r.extra.get("when_made") for r in listings])
    if who and who != settings.etsy_who_made:
        settings.etsy_who_made = who
        filled.append("« qui l'a fabriqué »")
    if when and when != settings.etsy_when_made:
        settings.etsy_when_made = when
        filled.append("date de fabrication")
    language = common([(r.extra.get("language") or "")[:2] for r in listings])
    if language in ("fr", "en", "es", "de") and language != settings.etsy_shop_language:
        settings.etsy_shop_language = language
        filled.append("langue de la boutique")
    if filled:
        settings.save()
    return filled
