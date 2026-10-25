"""Saisie rapide sur une ligne, au format du fichier .txt actuel.

Exemple : « malachite fibreuse congo 120g 10x5x5cm 150€ »
"""
from __future__ import annotations

import re
import unicodedata

# Pays déjà présents dans les tags de la boutique (+ quelques fréquents)
KNOWN_COUNTRIES = [
    "afghanistan", "afrique du sud", "allemagne", "angleterre", "argentine", "australie",
    "bolivie", "bresil", "bulgarie", "canada", "chine", "colombie", "congo", "espagne",
    "france", "inde", "indonesie", "italie", "japon", "kazakhstan", "madagascar", "maroc",
    "mexique", "mongolie", "namibie", "norvege", "pakistan", "perou", "portugal",
    "republique tcheque", "roumanie", "russie", "slovaquie", "sri lanka", "suede", "tanzanie",
    "ukraine", "uruguay", "usa", "zambie",
]

_NUM = r"\d+(?:[.,]\d+)?"
RE_PRICE = re.compile(rf"(?<!\S)({_NUM})\s*(?:€|eur(?:os?)?)(?!\S)", re.I)
RE_DIMS = re.compile(rf"(?<!\S)({_NUM}(?:\s*[x×*]\s*{_NUM}){{1,2}})\s*(mm|cm)?(?!\S)", re.I)
RE_WEIGHT = re.compile(rf"(?<!\S)({_NUM})\s*(kg|g|gr|grammes?|ct|carats?)(?!\S)", re.I)
RE_RING = re.compile(r"(?<!\S)(?:t|taille)\s*(\d{2})(?!\S)", re.I)


def strip_accents(text: str) -> str:
    for lig, repl in (("œ", "oe"), ("Œ", "Oe"), ("æ", "ae"), ("Æ", "Ae")):
        text = text.replace(lig, repl)
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _fmt(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def parse_line(line: str) -> dict[str, str]:
    """Renvoie les champs reconnus : nom, pays, poids_g, dimensions (cm), prix, taille_reelle."""
    result: dict[str, str] = {}
    text = " " + line.strip() + " "

    if m := RE_PRICE.search(text):
        result["prix"] = _fmt(_num(m.group(1)))
        text = text[:m.start()] + " " + text[m.end():]

    if m := RE_DIMS.search(text):
        parts = [_num(p) for p in re.split(r"\s*[x×*]\s*", m.group(1))]
        if (m.group(2) or "cm").lower() == "mm":
            parts = [p / 10 for p in parts]
        result["dimensions"] = " × ".join(_fmt(p) for p in parts)
        text = text[:m.start()] + " " + text[m.end():]

    if m := RE_WEIGHT.search(text):
        value, unit = _num(m.group(1)), m.group(2).lower()
        if unit == "kg":
            value *= 1000
        if unit.startswith(("ct", "carat")):
            value *= 0.2
        result["poids_g"] = _fmt(round(value, 2))
        text = text[:m.start()] + " " + text[m.end():]

    if m := RE_RING.search(text):
        result["taille_reelle"] = m.group(1)
        text = text[:m.start()] + " " + text[m.end():]

    words = text.split()
    normalized = [strip_accents(w).lower() for w in words]
    # Le pays est cherché en fin de ligne, en commençant par les noms en plusieurs mots
    for country in sorted(KNOWN_COUNTRIES, key=lambda c: -len(c.split())):
        n = len(country.split())
        for start in range(len(words) - n, -1, -1):
            if " ".join(normalized[start:start + n]) == country:
                result["pays"] = " ".join(words[start:start + n])
                del words[start:start + n]
                break
        if "pays" in result:
            break

    nom = " ".join(words).strip(" ,;-")
    if nom:
        result["nom"] = nom
    return result
