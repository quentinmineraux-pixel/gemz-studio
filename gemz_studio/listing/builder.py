"""Assemblage des fiches dans le format de la boutique GemZ (titre, description HTML, SEO, tags)."""
from __future__ import annotations

import html
import re

from ..config import ProductKind, Settings
from ..models import ProductInfo
from ..quickentry import strip_accents
from .countries import country_name
from .model import SOURCE_IA, SOURCE_MODELE, Listing, LangText
from .prices import fmt_eur, parse_price
from .texts import LANGS, METALS, RING_SIZES_US, TREATMENTS, t, translated

KIND_WORDS = {
    "bague": {"fr": "Bague", "en": "Ring", "es": "Anillo", "de": "Ring"},
    "bracelet": {"fr": "Bracelet", "en": "Bracelet", "es": "Pulsera", "de": "Armband"},
    "pendentif": {"fr": "Pendentif", "en": "Pendant", "es": "Colgante", "de": "Anhänger"},
    "boucles": {"fr": "Boucles d'oreilles", "en": "Earrings", "es": "Pendientes", "de": "Ohrringe"},
}
ETSY_TAG_MAX = 13
ETSY_TAG_LEN = 20


def tag_slug(text: str) -> str:
    """Tag Shopify : minuscules, sans accent, mots séparés par des tirets (« Afrique du Sud » -> afrique-du-sud)."""
    words = re.findall(r"[a-z0-9]+", strip_accents(text).lower())
    return "-".join(words)


def safe_text(text: str) -> str:
    """Texte rédigé -> HTML : tout est échappé sauf le gras <strong>, utilisé dans les fiches GemZ."""
    escaped = html.escape(text.strip(), quote=False)
    return re.sub(r"&lt;(/?)strong&gt;", r"<\1strong>", escaped).replace("\n", "<br>")


def _colon(label: str, lang: str) -> str:
    return f"{label} :" if lang == "fr" else f"{label}:"


def _num(text: str, lang: str) -> str:
    """Décimales à la française dans la saisie ; point en anglais."""
    return text.replace(",", ".") if lang == "en" else text.replace(".", ",")


def _weight(info: ProductInfo, lang: str, total: bool = False) -> str:
    value = (info.poids_total_g if total else info.poids_g).strip()
    return f"{_num(value, lang)} g" if value else ""


def _dims(text: str, lang: str) -> str:
    text = text.strip()
    if not text:
        return ""
    return _num(text if text.endswith(("cm", "mm")) else f"{text} cm", lang)


def _location(info: ProductInfo, pieces: dict, lang: str, style: str) -> str:
    pays = pieces.get("pays") or country_name(info.pays, lang)
    region = info.region.strip()
    if not region:
        return pays
    return f"{region}, {pays}" if style == "title" else f"{region} ({pays})"


def _name(info: ProductInfo, pieces: dict) -> str:
    nom = (pieces.get("nom") or info.nom).strip()
    nom = nom[:1].upper() + nom[1:]
    part = (pieces.get("particularite") or ("" if pieces.get("nom") else info.variete)).strip()
    return f"{nom} {part}".strip()


def build_title(info: ProductInfo, kind: ProductKind, pieces: dict, lang: str) -> str:
    name = _name(info, pieces)
    word = KIND_WORDS.get(kind.id, {}).get(lang)
    if word and not strip_accents(name.lower()).startswith(strip_accents(word.lower())):
        name = f"{word} {name[:1].lower() + name[1:]}"
    parts = [name]
    if kind.form == "bague":
        if info.metal:
            parts.append(translated(METALS, info.metal, lang))
        if info.taille_reelle:
            parts.append(f"{t('taille', lang)} {info.taille_reelle}")
    elif kind.form == "bracelet":
        if info.perles_mm:
            parts.append(f"{_num(info.perles_mm, lang)} mm")
    else:
        parts.append(_location(info, pieces, lang, "title"))
        if weight := _weight(info, lang):
            parts.append(weight)
    return " – ".join(p for p in parts if p)


def build_seo_title(info: ProductInfo, kind: ProductKind, pieces: dict, lang: str) -> str:
    if kind.form in ("bague", "bracelet"):
        return f"{build_title(info, kind, pieces, lang)} | GemZ"
    text = f"{_name(info, pieces)}, {_location(info, pieces, lang, 'seo')}"
    if weight := _weight(info, lang):
        text += f" – {weight}"
    return f"{text} | GemZ"


def _seo_description(info: ProductInfo, pieces: dict, lang: str) -> str:
    if pieces.get("seo_description"):
        return pieces["seo_description"].strip()
    bits = [f"{_name(info, pieces)}, {_location(info, pieces, lang, 'seo')}."]
    specs = ", ".join(x for x in (_weight(info, lang), _dims(info.dimensions, lang)) if x)
    if specs:
        bits.append(specs + ".")
    bits.append(t("unique", lang))
    return " ".join(bits)


def _specs_block(rows: list[tuple[str, str]], lang: str) -> str:
    lines = [f"<strong>{_colon(label, lang)}</strong> {html.escape(value)}" for label, value in rows if value]
    return f"<p>{'<br>'.join(lines)}</p>" if lines else ""


def _section(title: str, text: str) -> str:
    return f"<hr>\n<h3>{title}</h3>\n<p>{safe_text(text)}</p>" if text.strip() else ""


def _mineral_html(info: ProductInfo, kind: ProductKind, pieces: dict, pierres: list[str], lang: str) -> str:
    rows = [(t("poids", lang), _weight(info, lang)),
            (t("dimensions", lang), _dims(info.dimensions, lang)),
            (t("provenance", lang), _location(info, pieces, lang, "seo"))]
    if len(pierres) > 1:
        rows.append((t("mineraux", lang), ", ".join(pierres)))
    if info.traitement and info.traitement.strip().lower() not in ("naturel", ""):
        rows.append((t("traitement", lang), translated(TREATMENTS, info.traitement, lang)))
    parts = [_specs_block(rows, lang)]
    if pieces.get("aspect"):
        parts.append(f"<p><strong>{_colon(t('aspect', lang), lang)}</strong><br>{safe_text(pieces['aspect'])}</p>")
    parts.append(_section(t("anecdote", lang), pieces.get("anecdote", "")))
    if pieces.get("lithotherapie"):
        parts.append(_section(t("litho", lang), pieces["lithotherapie"]))
        parts.append(f"<p><em>{t('bienetre', lang)}</em></p>")
    parts.append(f"<hr>\n<p><em>{t('photo', lang)}</em></p>")
    return "\n".join(p for p in parts if p)


def _ring_html(info: ProductInfo, kind: ProductKind, pieces: dict, settings: Settings, lang: str) -> str:
    rows = [(t("taille_reelle", lang), info.taille_reelle),
            (t("metal", lang), translated(METALS, info.metal, lang) if info.metal else ""),
            (t("pierre", lang), (pieces.get("nom") or info.nom).strip()),
            (t("dims_pierre", lang), _dims(info.dimensions_pierre, lang)),
            (t("poids_total", lang), _weight(info, lang, total=True)),
            (t("provenance", lang), _location(info, pieces, lang, "seo"))]
    parts = []
    if pieces.get("accroche"):
        parts.append(f"<p>{safe_text(pieces['accroche'])}</p>")
    parts.append(_specs_block(rows, lang))
    if info.taille_reelle:
        supplement = fmt_eur(settings.ring_resize_price, lang)
        parts.append(f"<p>{t('mise_a_taille', lang, taille=info.taille_reelle, supplement=supplement, jours=settings.ring_resize_days)}</p>")
        guide = ", ".join(f"{fr} FR = {us} US" for fr, us in RING_SIZES_US.items())
        parts.append(f"<p><strong>{_colon(t('guide_tailles', lang), lang)}</strong> {guide}</p>")
    if pieces.get("aspect"):
        parts.append(f"<hr>\n<p><strong>{_colon(t('aspect', lang), lang)}</strong><br>{safe_text(pieces['aspect'])}</p>")
    if pieces.get("lithotherapie"):
        parts.append(_section(t("litho", lang), pieces["lithotherapie"]))
        parts.append(f"<p><em>{t('bienetre', lang)}</em></p>")
    parts.append(f"<hr>\n<p><em>{t('photo', lang)}</em></p>")
    return "\n".join(p for p in parts if p)


def _bracelet_html(info: ProductInfo, kind: ProductKind, pieces: dict, settings: Settings, lang: str) -> str:
    tailles = ", ".join(_num(s, lang) for s in settings.bracelet_sizes)
    specs = [(t("pierre", lang), (pieces.get("nom") or info.pierres or info.nom).strip()),
             (t("perles", lang), f"{_num(info.perles_mm, lang)} mm" if info.perles_mm else ""),
             (t("montage", lang), info.fil),
             (t("provenance", lang), _location(info, pieces, lang, "seo") if info.pays else "")]
    items = "".join(f"<li><strong>{_colon(label, lang)}</strong> {html.escape(value)}</li>"
                    for label, value in specs if value)
    parts = ['<div class="bracelet">']
    if pieces.get("accroche"):
        parts.append(f"<p>{safe_text(pieces['accroche'])}</p>")
    parts.append(f"<p><strong>{_colon(t('taille_bracelet', lang), lang)}</strong></p>\n"
                 f"<ul><li>{t('tailles_dispo', lang, tailles=tailles)}</li></ul>")
    if pieces.get("lithotherapie"):
        parts.append(f"<hr>\n<p><strong>{_colon(t('litho', lang), lang)}</strong><br>{safe_text(pieces['lithotherapie'])}</p>")
        parts.append(f"<p><em>{t('bienetre', lang)}</em></p>")
    if pieces.get("aspect"):
        parts.append(f"<hr>\n<p><strong>{_colon(t('aspect', lang), lang)}</strong><br>{safe_text(pieces['aspect'])}</p>")
    if items:
        parts.append(f"<hr>\n<p><strong>{_colon(t('caracteristiques', lang), lang)}</strong></p>\n<ul>{items}</ul>")
    parts.append(f"<hr>\n<p><strong>{_colon(t('particularite', lang), lang)}</strong><br>{t('naturel_bracelet', lang)}</p>")
    parts.append("</div>")
    return "\n".join(parts)


def build_description(info: ProductInfo, kind: ProductKind, pieces: dict, pierres: list[str],
                      settings: Settings, lang: str) -> str:
    if kind.form == "bague":
        return _ring_html(info, kind, pieces, settings, lang)
    if kind.form == "bracelet":
        return _bracelet_html(info, kind, pieces, settings, lang)
    return _mineral_html(info, kind, pieces, pierres, lang)


def clean_etsy_tags(tags: list[str], fallback: list[str]) -> list[str]:
    """13 tags au plus, 20 caractères chacun, caractères acceptés par Etsy, sans doublon."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(tags) + list(fallback):
        tag = re.sub(r"[^\w\s'-]", "", raw.strip().lower(), flags=re.UNICODE)
        tag = re.sub(r"\s+", " ", tag).strip()
        if len(tag) > ETSY_TAG_LEN:  # coupé entre deux mots
            tag = tag[:ETSY_TAG_LEN + 1].rsplit(" ", 1)[0] if " " in tag[:ETSY_TAG_LEN + 1] else tag[:ETSY_TAG_LEN]
        key = strip_accents(tag)
        if tag and key not in seen:
            seen.add(key)
            out.append(tag)
        if len(out) == ETSY_TAG_MAX:
            break
    return out


def shopify_tags(info: ProductInfo, kind: ProductKind, pierres: list[str]) -> list[str]:
    tags = list(kind.fixed_tags) + list(info.sous_categories)
    tags += [tag_slug(p) for p in pierres]
    if info.pays:
        tags.append(tag_slug(country_name(info.pays, "fr")))
    out: list[str] = []
    for tag in tags:
        if tag and tag not in out:
            out.append(tag)
    return out


def default_pierres(info: ProductInfo) -> list[str]:
    source = info.pierres if info.pierres else info.nom
    names = [n.strip().lower() for n in re.split(r",| et | sur ", source) if n.strip()]
    return names[:4]


def fill_texts(listing: Listing, info: ProductInfo, kind: ProductKind, settings: Settings) -> Listing:
    """(Re)calcule titres, descriptions, SEO, tags et métachamps à partir des morceaux rédigés."""
    pierres = listing.pierres or default_pierres(info)
    for lang in LANGS:
        pieces = listing.pieces.get(lang, {})
        if lang != "fr" and not pieces.get("nom"):
            # Pas encore traduit : on part du français pour que la fiche reste lisible
            pieces = {**listing.pieces.get("fr", {}), **{k: v for k, v in pieces.items() if v}}
            pieces.pop("pays", None)
        alt = pieces.get("alt") or build_title(info, kind, pieces, lang)
        listing.texts[lang] = LangText(
            title=build_title(info, kind, pieces, lang),
            description=build_description(info, kind, pieces, pierres, settings, lang),
            seo_title=build_seo_title(info, kind, pieces, lang),
            seo_description=_seo_description(info, pieces, lang),
            alt=alt,
        )
    listing.tags = shopify_tags(info, kind, pierres)
    listing.metafields = {
        "pierre": [tag_slug(p) for p in pierres],
        "provenance": [tag_slug(country_name(info.pays, "fr"))] if info.pays else [],
        "forme": list(info.sous_categories),
    }
    fallback_tags = [info.nom, *pierres, country_name(info.pays, "fr") if info.pays else "",
                     country_name(info.pays, "en") if info.pays else "", "pierre naturelle", "natural stone",
                     "mineral", "crystal", "gemstone", "lithotherapie", "collection", "cadeau", "gift idea",
                     "home decor", "specimen"]
    listing.etsy_tags = clean_etsy_tags(listing.etsy_tags, fallback_tags)
    listing.ready = not listing.missing()
    return listing


def build_listing(info: ProductInfo, kind: ProductKind, settings: Settings, ai: dict | None = None,
                  cost_eur: float = 0.0) -> Listing:
    """Fiche à partir de la saisie, et des textes de l'IA si disponibles."""
    listing = Listing()
    if ai:
        listing.pieces = {lang: {k: str(v) for k, v in ai.get(lang, {}).items()} for lang in LANGS}
        listing.etsy_tags = list(ai.get("etsy_tags", []))
        listing.couleurs = list(ai.get("couleurs", []))
        listing.pierres = [p.strip().lower() for p in ai.get("pierres", []) if p.strip()]
        listing.source = SOURCE_IA
        listing.cost_eur = cost_eur
    else:
        listing.pieces["fr"] = {"nom": info.nom, "particularite": info.variete}
        listing.source = SOURCE_MODELE
    return fill_texts(listing, info, kind, settings)


def price_lines(info: ProductInfo, settings: Settings) -> dict[str, float]:
    """Prix de chaque plateforme pour l'affichage dans la fiche."""
    from .prices import platform_price, to_usd
    base = parse_price(info.prix)
    etsy = platform_price(base, settings.etsy_markup)
    ebay = platform_price(base, settings.ebay_markup)
    return {"shopify": base, "etsy": etsy, "ebay_fr": ebay, "ebay_us_usd": to_usd(ebay, settings.usd_per_eur)}
