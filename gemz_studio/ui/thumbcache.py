"""Miniatures pour l'interface : préparées hors de l'interface, gardées en mémoire et sur le disque."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap

from ..thumbs import SIZE, make_thumb


class _Emitter(QObject):
    loaded = Signal(str, int, QImage)


class _Job(QRunnable):
    def __init__(self, source: str, size: int, emitter: _Emitter):
        super().__init__()
        self.source, self.size, self.emitter = source, size, emitter

    def run(self) -> None:
        path = make_thumb(self.source, self.size)
        self.emitter.loaded.emit(self.source, self.size, QImage(str(path)) if path else QImage())


class ThumbCache(QObject):
    """get(source) rend la miniature tout de suite si elle est prête, sinon la prépare et prévient par ready
    (source : photo locale ou adresse d'une photo de la boutique)."""
    ready = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmaps: dict[str, QPixmap] = {}
        self._pending: set[str] = set()
        self._failed: set[str] = set()
        # Le groupe de threads d'abord : détruit avant l'émetteur, il attend ses tâches pendant que celui-ci existe
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)   # quelques téléchargements à la fois, pas plus
        self._emitter = _Emitter(self)
        self._emitter.loaded.connect(self._loaded)

    def get(self, source: str, size: int = SIZE) -> QPixmap | None:
        key = f"{size}|{source}"
        if not source or key in self._failed:
            return None
        pixmap = self._pixmaps.get(key)
        if pixmap is None and key not in self._pending:
            self._pending.add(key)
            self._pool.start(_Job(source, size, self._emitter))
        return pixmap

    def _loaded(self, source: str, size: int, image: QImage) -> None:
        key = f"{size}|{source}"
        self._pending.discard(key)
        if image.isNull():
            self._failed.add(key)
        else:
            self._pixmaps[key] = QPixmap.fromImage(image)
        self.ready.emit(source)

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(3000)


def square(pixmap: QPixmap | None, side: int, background: str = "#ece8e3", radius: int = 8) -> QPixmap:
    """Miniature centrée dans un carré aux coins arrondis (vide si la photo n'est pas encore là)."""
    out = QPixmap(side, side)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(background))
    p.drawRoundedRect(0, 0, side, side, radius, radius)
    if pixmap is not None and not pixmap.isNull():
        scaled = pixmap.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x, y = (scaled.width() - side) // 2, (scaled.height() - side) // 2
        path = QPainterPath()
        path.addRoundedRect(0, 0, side, side, radius, radius)
        p.setClipPath(path)
        p.drawPixmap(0, 0, scaled, x, y, side, side)
    p.end()
    return out
