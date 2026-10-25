"""Watermark de la vidéo 360 avec ffmpeg (fourni par imageio-ffmpeg)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg

from .config import Settings
from .imaging import prepared_logo, watermark_position

# Pas de fenêtre console qui s'ouvre sous Windows pendant le traitement
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def video_size(path: str | Path) -> tuple[int, int]:
    reader = imageio_ffmpeg.read_frames(str(path))
    try:
        meta = next(reader)
    finally:
        reader.close()
    return tuple(meta["size"])


def watermark_video(src: str | Path, dest: Path, settings: Settings) -> Path:
    width, height = video_size(src)
    logo = prepared_logo(settings, width)
    x, y = watermark_position((width, height), logo.size, settings.watermark_margin)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        logo_file = Path(tmp) / "logo.png"
        logo.save(logo_file)
        cmd = [
            imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
            "-i", str(src), "-i", str(logo_file),
            "-filter_complex", f"[0:v][1:v]overlay={x}:{y}:format=auto,format=yuv420p[v]",
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
            str(dest),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg a échoué : {result.stderr.strip()[-500:]}")
    return dest


def video_frame(path: str | Path, at: float = 1.0):
    """Une image de la vidéo (pour la vignette)."""
    from io import BytesIO

    from PIL import Image

    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-loglevel", "error", "-ss", f"{at}", "-i", str(path),
           "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"]
    result = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW, timeout=30)
    if result.returncode != 0 or not result.stdout:
        if at > 0:
            return video_frame(path, 0)
        raise OSError(f"image de la vidéo illisible : {Path(path).name}")
    return Image.open(BytesIO(result.stdout)).convert("RGB")
