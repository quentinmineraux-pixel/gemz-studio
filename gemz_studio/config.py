"""Réglages de l'application et types de produits.

Les types reprennent exactement l'organisation de la boutique Shopify GemZ :
les collections automatiques sont pilotées par le type de produit et quelques tags.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "GemZ Studio"
_SKU_LOCK = threading.Lock()


def resource_path(relative: str) -> Path:
    """Chemin d'un fichier fourni avec l'application (fonctionne aussi dans le .exe)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / relative


def data_dir() -> Path:
    """Dossier des données de l'application (%APPDATA%\\GemZ Studio sous Windows)."""
    override = os.environ.get("GEMZ_STUDIO_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
    else:
        path = Path.home() / ".gemz-studio"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class ProductKind:
    id: str
    label: str
    shopify_type: str
    fixed_tags: list[str] = field(default_factory=list)
    subtags: list[str] = field(default_factory=list)
    photos_per_product: int = 8
    videos_per_product: int = 1
    # "mineral", "bague" ou "bracelet" : choisit les champs du formulaire
    form: str = "mineral"
    # Catégories des places de marché (choisies ensemble au branchement d'Etsy et d'eBay)
    etsy_taxonomy_id: int = 0
    ebay_category_id: str = ""


DEFAULT_KINDS: list[ProductKind] = [
    ProductKind("mineral-collection", "Minéral de collection", "mineral-collection",
                subtags=["cristal", "brut", "geode", "rare"]),
    ProductKind("pierre-polie", "Pierre polie", "pierre-polie",
                subtags=["forme-libre", "sphere", "galet", "cabochon"]),
    ProductKind("bague", "Bague", "bijou", fixed_tags=["bague"], form="bague"),
    ProductKind("pendentif", "Pendentif", "bijou", fixed_tags=["pendentif"]),
    ProductKind("boucles", "Boucles d'oreilles", "bijou", fixed_tags=["boucles"]),
    ProductKind("bracelet", "Bracelet", "bracelet",
                subtags=["bracelet-chips", "bracelet-perle"], form="bracelet"),
    ProductKind("fossile", "Fossile", "fossile",
                subtags=["ammonite", "trilobite", "marin", "vegetal", "plaque", "rare"]),
    ProductKind("sculpture", "Sculpture", "sculpture",
                subtags=["animal", "symbolique", "esoterique"]),
    ProductKind("fait-main", "Pièce unique (fait main)", "fait-main"),
]


@dataclass
class Settings:
    watch_folder: str = ""
    output_folder: str = ""
    logo_path: str = ""
    # Position fixe en bas à droite (choix du QCM) ; taille et opacité réglables
    watermark_scale: float = 0.22
    watermark_opacity: float = 0.6
    watermark_margin: float = 0.03
    watermark_color: str = "original"  # original, noir, blanc
    brightness: float = 1.0
    contrast: float = 1.0
    web_max_side: int = 2048
    jpeg_quality: int = 90
    auto_split: bool = True
    # « dossier » (EOS Utility), « canon » (pilotage direct EDSDK) ou « simule »
    camera_mode: str = "dossier"
    edsdk_folder: str = ""
    # Dernier numéro utilisé par préfixe, comme dans la boutique (MFC1…MFC42, FLLAB1…)
    sku_counters: dict[str, int] = field(default_factory=dict)
    kinds: list[ProductKind] = field(default_factory=lambda: [ProductKind(**asdict(k)) for k in DEFAULT_KINDS])

    # --- prix et règles de vente ------------------------------------------
    etsy_markup: float = 0.0      # % ajouté au prix Shopify (à régler dans les réglages)
    ebay_markup: float = 0.0
    usd_per_eur: float = 1.15     # conversion pour ebay.com
    ring_resize_price: float = 12.0
    ring_resize_days: int = 5
    ring_sizes: list[int] = field(default_factory=lambda: list(range(40, 71)))
    bracelet_sizes: list[str] = field(default_factory=lambda: ["17 cm", "19 cm", "21 cm"])
    default_etsy: bool = True     # plateformes cochées d'office sur une nouvelle fiche
    default_ebay: bool = False

    # --- IA (la clé est rangée à part, chiffrée) ---------------------------
    ai_model: str = "claude-opus-5-5"
    ai_monthly_cap_eur: float = 5.0
    ai_photos: int = 3
    ai_auto: bool = True          # rédiger la fiche dès la fin du traitement des photos

    # --- connexions (identifiants non secrets ; jetons dans secrets.py) ---
    shopify_domain: str = ""      # ex. gem-z.myshopify.com
    shopify_location_id: str = ""
    etsy_shop_id: str = ""
    etsy_shipping_profile_id: str = ""
    etsy_return_policy_id: str = ""
    etsy_when_made: str = "made_to_order"
    etsy_readiness_state_id: str = ""
    etsy_language: str = "en"     # langue principale des annonces Etsy (les autres en traductions)
    ebay_marketplaces: list[str] = field(default_factory=lambda: ["EBAY_FR", "EBAY_US"])
    ebay_location_key: str = ""
    ebay_policies: dict[str, dict[str, str]] = field(default_factory=dict)  # place -> fulfillment/payment/return
    ebay_ru_name: str = ""        # « RuName » de l'application eBay (adresse de retour après connexion)

    # --- synchro du stock et notifications -------------------------------
    sync_enabled: bool = True
    sync_minutes: int = 3
    ntfy_topic: str = ""          # notifications téléphone (application gratuite ntfy)
    publish_paused: bool = False

    def kind(self, kind_id: str) -> ProductKind:
        for k in self.kinds:
            if k.id == kind_id:
                return k
        raise KeyError(kind_id)

    def effective_logo(self) -> Path:
        if self.logo_path and Path(self.logo_path).is_file():
            return Path(self.logo_path)
        return resource_path("assets/logo.png")

    # --- persistance -----------------------------------------------------
    @classmethod
    def path(cls) -> Path:
        return data_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        p = cls.path()
        if not p.exists():
            return cls()
        raw = json.loads(p.read_text(encoding="utf-8"))
        kind_fields = set(ProductKind.__dataclass_fields__)
        kinds = [ProductKind(**{f: v for f, v in k.items() if f in kind_fields}) for k in raw.pop("kinds", [])] or None
        known = {f for f in cls.__dataclass_fields__}
        s = cls(**{k: v for k, v in raw.items() if k in known})
        if kinds:
            # Nouveaux types ajoutés depuis la dernière version (ex. fait-main) : ajoutés à la fin
            known_ids = {k.id for k in kinds}
            kinds += [ProductKind(**asdict(k)) for k in DEFAULT_KINDS if k.id not in known_ids]
            s.kinds = kinds
        return s

    def save(self) -> None:
        tmp = self.path().with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path())

    def next_sku_number(self, prefix: str) -> int:
        # Deux produits peuvent être traités en même temps : le numéro ne doit jamais se répéter
        with _SKU_LOCK:
            n = self.sku_counters.get(prefix, 0) + 1
            self.sku_counters[prefix] = n
            self.save()
            return n
