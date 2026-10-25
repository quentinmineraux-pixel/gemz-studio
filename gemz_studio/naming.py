"""SKU et noms de dossiers."""
from __future__ import annotations

import re

from .quickentry import strip_accents

_STOPWORDS = {"de", "du", "des", "la", "le", "les", "d", "l", "et", "en", "sur", "a"}


def sku_prefix(nom: str, pays: str) -> str:
    """Initiales du nom puis du pays : « Malachite fibreuse » + « Congo » -> « MFC »."""
    words = re.findall(r"[a-z0-9]+", strip_accents(f"{nom} {pays}").lower())
    initials = "".join(w[0] for w in words if w not in _STOPWORDS)
    return (initials.upper() or "GZ")[:5]


def make_sku(nom: str, pays: str, number: int) -> str:
    return f"{sku_prefix(nom, pays)}{number}"


def slug(text: str) -> str:
    """Texte sans accents ni caractères interdits sous Windows, mots séparés par des tirets."""
    words = re.findall(r"[A-Za-z0-9]+", strip_accents(text))
    return "-".join(words)[:60]


def product_folder_name(day: str, nom: str, pays: str, sku: str) -> str:
    parts = [day, slug(nom.capitalize()) or "Produit", slug(pays.capitalize()), sku]
    return "_".join(p for p in parts if p)
