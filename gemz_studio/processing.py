"""Traitement d'un produit : SKU, dossier rangé, originaux, photos et vidéo watermarkées."""
from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from pathlib import Path

from .config import Settings
from .imaging import export_photo
from .models import PRET, Product
from .naming import product_folder_name, sku_prefix
from .video import watermark_video


def unique_folder(base: Path) -> Path:
    if not base.exists():
        return base
    n = 2
    while (candidate := base.with_name(f"{base.name}-{n}")).exists():
        n += 1
    return candidate


def process_product(product: Product, day: str, settings: Settings) -> Path:
    """Crée <sortie>/<date_Nom_Pays_SKU>/ avec originaux/ et web/ ; renvoie le dossier."""
    if not settings.output_folder:
        raise RuntimeError("Choisis d'abord le dossier de sortie dans les réglages.")
    if missing := product.info.missing_required():
        raise RuntimeError("Champs obligatoires manquants : " + ", ".join(missing))
    if not product.photos:
        raise RuntimeError("Aucune photo pour ce produit.")

    if not product.sku:
        prefix = sku_prefix(product.info.nom, product.info.pays)
        product.sku = f"{prefix}{settings.next_sku_number(prefix)}"
    else:
        product.sku = product.sku.strip().upper()

    name = product_folder_name(day, product.info.nom, product.info.pays, product.sku)
    folder = Path(product.folder) if product.folder else unique_folder(Path(settings.output_folder) / name)
    originals, web = folder / "originaux", folder / "web"
    # Un nouveau traitement (après modification) repart d'un dossier web propre
    if web.exists():
        shutil.rmtree(web)
    originals.mkdir(parents=True, exist_ok=True)
    web.mkdir(parents=True, exist_ok=True)

    for i, src in enumerate(product.photos, start=1):
        src_path = Path(src)
        kept = originals / f"{i:02d}_{src_path.name}"
        if not kept.exists():
            shutil.copy2(src_path, kept)
        export_photo(src_path, web / f"{product.sku}_{i:02d}.jpg", settings)

    for i, src in enumerate(product.videos, start=1):
        src_path = Path(src)
        kept = originals / f"video{i}_{src_path.name}"
        if not kept.exists():
            shutil.copy2(src_path, kept)
        suffix = "" if len(product.videos) == 1 else f"_{i}"
        watermark_video(src_path, web / f"{product.sku}_video{suffix}.mp4", settings)

    product.folder = str(folder)
    product.status = PRET
    product.error = ""
    kind = settings.kind(product.kind_id)
    record = asdict(product) | {
        "shopify_type": kind.shopify_type,
        "tags_fixes": kind.fixed_tags,
        "date": day,
        "web_photos": sorted(p.name for p in web.glob("*.jpg")),
        "web_videos": sorted(p.name for p in web.glob("*.mp4")),
    }
    (folder / "produit.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    return folder
