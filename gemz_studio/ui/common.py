from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap

_RUNNING: set = set()


class _Done(QObject):
    finished = Signal(object, object)   # résultat, exception


class _Task(QRunnable):
    def __init__(self, fn, done: _Done):
        super().__init__()
        self.fn, self.done = fn, done

    def run(self) -> None:
        try:
            result, error = self.fn(), None
        except Exception as e:  # noqa: BLE001 - renvoyée à l'interface
            result, error = None, e
        self.done.finished.emit(result, error)


def run_in_background(fn, on_done) -> None:
    """Lance fn() hors de l'interface (réseau, IA) ; on_done(résultat, erreur) revient dans l'interface."""
    done = _Done()
    _RUNNING.add(done)

    def finish(result, error):
        _RUNNING.discard(done)
        on_done(result, error)

    done.finished.connect(finish)
    QThreadPool.globalInstance().start(_Task(fn, done))


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


def video_thumbnail_icon(path: str | Path, size: int = 240) -> QIcon:
    """Vignette d'une vidéo : une image du film avec le symbole lecture par-dessus."""
    from PIL import ImageDraw

    from ..video import video_frame

    img = video_frame(path)
    img.thumbnail((size, size))
    d = ImageDraw.Draw(img, "RGBA")
    c, r = (img.width // 2, img.height // 2), min(img.size) // 6
    d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=(0, 0, 0, 140))
    d.polygon([(c[0] - r // 3, c[1] - r // 2), (c[0] - r // 3, c[1] + r // 2), (c[0] + r // 2, c[1])],
              fill=(255, 255, 255, 230))
    return QIcon(pil_to_pixmap(img))


def open_in_explorer(path: str | Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
