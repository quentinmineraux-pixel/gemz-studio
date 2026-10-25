"""Retouche légère (preset fixe), watermark et export web des photos."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

from .config import Settings


def adjust(img: Image.Image, brightness: float, contrast: float) -> Image.Image:
    if abs(brightness - 1.0) > 1e-3:
        img = ImageEnhance.Brightness(img).enhance(brightness)
    if abs(contrast - 1.0) > 1e-3:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    return img


@lru_cache(maxsize=8)
def _load_logo(path: str, color: str, mtime: float) -> Image.Image:
    logo = Image.open(path).convert("RGBA")
    if color in ("noir", "blanc"):
        fill = (0, 0, 0) if color == "noir" else (255, 255, 255)
        solid = Image.new("RGBA", logo.size, fill + (0,))
        solid.putalpha(logo.getchannel("A"))
        logo = solid
    return logo


def prepared_logo(settings: Settings, target_width: int) -> Image.Image:
    """Logo à la largeur voulue, avec l'opacité des réglages."""
    path = settings.effective_logo()
    logo = _load_logo(str(path), settings.watermark_color, path.stat().st_mtime)
    width = max(1, int(target_width * settings.watermark_scale))
    height = max(1, round(logo.height * width / logo.width))
    logo = logo.resize((width, height), Image.LANCZOS)
    alpha = logo.getchannel("A").point(lambda a: int(a * settings.watermark_opacity))
    logo.putalpha(alpha)
    return logo


def watermark_position(size: tuple[int, int], logo_size: tuple[int, int], margin: float) -> tuple[int, int]:
    """Coin bas droite, avec une marge proportionnelle au plus petit côté."""
    w, h = size
    pad = int(min(w, h) * margin)
    return w - logo_size[0] - pad, h - logo_size[1] - pad


def apply_watermark(img: Image.Image, settings: Settings) -> Image.Image:
    logo = prepared_logo(settings, img.width)
    base = img.convert("RGBA")
    base.alpha_composite(logo, watermark_position(base.size, logo.size, settings.watermark_margin))
    return base.convert("RGB")


def load_photo(path: str | Path) -> Image.Image:
    img = Image.open(path)
    return ImageOps.exif_transpose(img).convert("RGB")


def render_web(path: str | Path, settings: Settings, max_side: int | None = None) -> Image.Image:
    """Photo finale : preset, redimensionnement, puis watermark."""
    img = load_photo(path)
    img = adjust(img, settings.brightness, settings.contrast)
    limit = max_side or settings.web_max_side
    if max(img.size) > limit:
        img.thumbnail((limit, limit), Image.LANCZOS)
    return apply_watermark(img, settings)


def export_photo(src: str | Path, dest: Path, settings: Settings) -> Path:
    img = render_web(src, settings)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "JPEG", quality=settings.jpeg_quality, optimize=True, progressive=True)
    return dest


def thumbnail(path: str | Path, size: int = 320) -> Image.Image:
    img = Image.open(path)
    img.draft("RGB", (size, size))  # décodage JPEG réduit : rapide sur les gros fichiers
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((size, size))
    return img
