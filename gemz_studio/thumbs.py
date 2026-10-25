"""Miniatures des pièces (catalogue, page Ventes du téléphone), gardées en cache sur le disque.

Photo locale réduite avec Pillow, ou photo de la boutique téléchargée directement à la bonne taille
(le CDN de Shopify sert la largeur demandée) : rien n'est refait ni retéléchargé d'une fois sur l'autre.
"""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

from PIL import Image, ImageOps

from .config import data_dir

SIZE = 320
FOLDER = "miniatures"


def sized_url(url: str, width: int) -> str:
    """Les photos de la boutique (cdn.shopify.com) sont servies à la largeur demandée."""
    return f"{url}{'&' if '?' in url else '?'}width={width}" if "cdn.shopify.com" in url else url


def cache_path(source: str, size: int = SIZE) -> Path:
    key = source
    if not source.startswith("https://"):
        try:
            st = Path(source).stat()
            key += f"|{st.st_mtime_ns}|{st.st_size}"   # photo refaite : nouvelle miniature
        except OSError:
            pass
    return data_dir() / FOLDER / f"{hashlib.sha1(f'{key}|{size}'.encode()).hexdigest()}.jpg"


def make_thumb(source: str, size: int = SIZE) -> Path | None:
    """Fichier de la miniature (créée au besoin), ou None si la photo est introuvable."""
    if not source:
        return None
    path = cache_path(source, size)
    if path.exists():
        return path
    try:
        if source.startswith("https://"):
            import requests

            r = requests.get(sized_url(source, size), timeout=15, headers={"User-Agent": "GemZ-Studio/1.0"})
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content))
        else:
            img = Image.open(source)
            img.draft("RGB", (size, size))
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail((size, size))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        img.save(tmp, "JPEG", quality=85)
        tmp.replace(path)
        return path
    except Exception:  # noqa: BLE001 - photo absente ou illisible : la pièce s'affiche sans photo
        return None
