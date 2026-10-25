"""Produits et séances photo, sauvegardés en JSON."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

PHOTO_EXT = {".jpg", ".jpeg", ".png"}
VIDEO_EXT = {".mp4", ".mov"}


def media_type(path: str | Path) -> str | None:
    ext = Path(path).suffix.lower()
    if ext in PHOTO_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    return None


# Statuts d'un produit dans la séance
EN_PRISE = "en_prise"          # photos en cours
A_COMPLETER = "a_completer"    # photos finies, fiche à remplir
EN_TRAITEMENT = "en_traitement"
PRET = "pret"                  # watermark appliqué, dossier rangé (prêt pour la publication)
ERREUR = "erreur"

STATUS_LABELS = {
    EN_PRISE: "Photos en cours",
    A_COMPLETER: "Fiche à remplir",
    EN_TRAITEMENT: "Traitement…",
    PRET: "Prêt",
    ERREUR: "Erreur",
}


@dataclass
class ProductInfo:
    nom: str = ""
    variete: str = ""
    dimensions: str = ""      # "10 × 5 × 5" en cm
    poids_g: str = ""
    pays: str = ""
    region: str = ""
    traitement: str = "naturel"
    prix: str = ""            # prix TTC Shopify en euros
    prix_barre: str = ""      # prix barré (promotion) sur Shopify ; vide = pas de promotion
    quantite: int = 1
    sous_categories: list[str] = field(default_factory=list)
    # bagues
    metal: str = ""
    dimensions_pierre: str = ""
    poids_total_g: str = ""
    taille_reelle: str = ""
    # bracelets
    pierres: str = ""
    perles_mm: str = ""
    fil: str = ""
    # plateformes en plus de Shopify (toujours publié)
    publier_etsy: bool = True
    publier_ebay: bool = False
    plateformes_choisies: bool = False   # sinon, cases cochées selon les réglages

    def missing_required(self) -> list[str]:
        missing = []
        for key, label in (("nom", "nom"), ("pays", "provenance"), ("prix", "prix")):
            if not str(getattr(self, key)).strip():
                missing.append(label)
        return missing


@dataclass
class Product:
    kind_id: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    photos: list[str] = field(default_factory=list)   # chemins des originaux, le 1er = photo principale
    videos: list[str] = field(default_factory=list)
    info: ProductInfo = field(default_factory=ProductInfo)
    status: str = EN_PRISE
    sku: str = ""
    folder: str = ""          # dossier rangé une fois traité
    error: str = ""

    def media(self) -> list[str]:
        return self.photos + self.videos

    def is_full(self, photos_needed: int, videos_needed: int) -> bool:
        return len(self.photos) >= photos_needed and len(self.videos) >= videos_needed

    def display_name(self) -> str:
        if self.info.nom:
            return self.info.nom.strip().capitalize()
        return "Produit sans nom"

    @classmethod
    def from_dict(cls, raw: dict) -> "Product":
        raw = dict(raw)
        known = set(ProductInfo.__dataclass_fields__)
        info = ProductInfo(**{k: v for k, v in raw.pop("info", {}).items() if k in known})
        return cls(info=info, **raw)


@dataclass
class Session:
    kind_id: str
    day: str = field(default_factory=lambda: date.today().isoformat())
    products: list[Product] = field(default_factory=list)
    current_id: str | None = None

    def current(self) -> Product | None:
        return self.get(self.current_id) if self.current_id else None

    def get(self, product_id: str) -> Product | None:
        return next((p for p in self.products if p.id == product_id), None)

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Session":
        raw = json.loads(path.read_text(encoding="utf-8"))
        products = [Product.from_dict(p) for p in raw.pop("products", [])]
        return cls(products=products, **raw)
