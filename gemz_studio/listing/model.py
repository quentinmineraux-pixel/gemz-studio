"""Fiche d'un produit : textes par langue, tags et métachamps, prête pour chaque plateforme."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .texts import LANGS

# Morceaux rédigés (par l'IA ou à la main) pour chaque langue ; le reste de la fiche est assemblé
# par l'outil à partir des textes fixes et des données saisies
PIECES = ("nom", "particularite", "pays", "accroche", "aspect", "anecdote", "lithotherapie",
          "seo_description", "alt")

# D'où viennent les textes
SOURCE_MODELE = "modele"   # textes fixes seulement : à compléter (IA ou à la main) avant publication
SOURCE_IA = "ia"
SOURCE_MANUEL = "manuel"


@dataclass
class LangText:
    title: str = ""
    description: str = ""      # HTML
    seo_title: str = ""
    seo_description: str = ""
    alt: str = ""


@dataclass
class Listing:
    pieces: dict[str, dict[str, str]] = field(default_factory=lambda: {lang: {} for lang in LANGS})
    texts: dict[str, LangText] = field(default_factory=lambda: {lang: LangText() for lang in LANGS})
    tags: list[str] = field(default_factory=list)            # tags Shopify (collections automatiques)
    etsy_tags: list[str] = field(default_factory=list)       # 13 tags FR + EN, 20 caractères max
    couleurs: list[str] = field(default_factory=list)
    pierres: list[str] = field(default_factory=list)
    metafields: dict[str, list[str]] = field(default_factory=dict)  # pierre, provenance, forme
    source: str = SOURCE_MODELE
    ready: bool = False        # textes complets : la publication automatique peut partir
    cost_eur: float = 0.0
    edited: bool = False       # retouchée à la main : une régénération demande confirmation

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict | None) -> "Listing":
        if not raw:
            return cls()
        raw = dict(raw)
        texts = {lang: LangText(**t) for lang, t in raw.pop("texts", {}).items()}
        listing = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        listing.texts = {lang: texts.get(lang, LangText()) for lang in LANGS}
        listing.pieces = {lang: dict(listing.pieces.get(lang, {})) for lang in LANGS}
        return listing

    def missing(self) -> list[str]:
        """Ce qui manque avant publication (vide = fiche complète)."""
        out = []
        for lang in LANGS:
            p = self.pieces.get(lang, {})
            for key in ("aspect", "lithotherapie"):
                if not p.get(key, "").strip():
                    out.append(f"{key} ({lang})")
        if not self.texts["fr"].title.strip():
            out.append("titre")
        return out
