from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QIcon, QImage, QPixmap


def pil_to_pixmap(img: Image.Image) -> QPixmap:
    img = img.convert("RGB")
    data = img.tobytes("raw", "RGB")
    qimg = QImage(data, img.width, img.height, 3 * img.width, QImage.Format_RGB888)
    return QPixmap.fromImage(qimg.copy())


def video_icon(size: int = 160) -> QIcon:
    img = Image.new("RGB", (size, size), (40, 40, 40))
    from PIL import ImageDraw

    d = ImageDraw.Draw(img)
    c = size // 2
    d.polygon([(c - size // 6, c - size // 5), (c - size // 6, c + size // 5), (c + size // 5, c)], fill=(230, 230, 230))
    return QIcon(pil_to_pixmap(img))


def open_in_explorer(path: str | Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
