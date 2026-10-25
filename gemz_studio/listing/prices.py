"""Prix par plateforme : prix Shopify TTC + majoration, arrondi à ,90 (règle du cahier des charges)."""
from __future__ import annotations

import re


def parse_price(text: str | float | int) -> float:
    """« 150 € », « 150,5 », 150 -> 150.5 ; lève ValueError si illisible."""
    if isinstance(text, (int, float)):
        return float(text)
    match = re.search(r"\d+(?:[.,]\d+)?", str(text).replace(" ", ""))
    if not match:
        raise ValueError(f"prix illisible : {text!r}")
    return float(match.group().replace(",", "."))


def safe_price(text: str | float | int) -> float:
    """Comme parse_price, mais 0 si le prix est vide ou illisible."""
    try:
        return parse_price(text)
    except ValueError:
        return 0.0


def price_text(value: float) -> str:
    """Prix tel qu'on le saisit dans la fiche : 67 -> « 67 », 67.5 -> « 67,50 »."""
    text = f"{value:.2f}"
    return text[:-3] if text.endswith(".00") else text.replace(".", ",")


def round_90(value: float) -> float:
    """Arrondi vers le haut à une terminaison en ,90 : 188,60 -> 188,90 ; 188,95 -> 189,90."""
    cents = round(value * 100)
    euros = cents // 100
    if cents % 100 > 90:
        euros += 1
    return euros + 0.90


def platform_price(base: float, markup_percent: float) -> float:
    """Prix Etsy / eBay. Sans majoration, le prix Shopify est repris tel quel."""
    if not markup_percent:
        return round(base, 2)
    return round(round_90(base * (1 + markup_percent / 100)), 2)


def to_usd(eur: float, usd_per_eur: float) -> float:
    """Prix ebay.com : conversion puis même arrondi en ,90 (en dollars)."""
    return round(round_90(eur * usd_per_eur), 2)


def fmt_eur(value: float, lang: str = "fr") -> str:
    text = f"{value:,.2f}"
    if lang in ("fr", "es", "de"):
        text = text.replace(",", " ").replace(".", ",")
    if text.endswith(",00") or text.endswith(".00"):
        text = text[:-3]
    return f"{text} €"
