"""Surveillance du dossier où EOS Utility enregistre les photos."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .models import media_type


def wait_until_complete(path: Path, stable_for: float = 0.6, timeout: float = 120.0) -> bool:
    """Attend que la taille du fichier ne bouge plus (EOS Utility écrit en plusieurs fois)."""
    deadline = time.monotonic() + timeout
    last_size, stable_since = -1, time.monotonic()
    while time.monotonic() < deadline:
        try:
            size = path.stat().st_size
            with open(path, "rb"):
                pass
        except OSError:
            time.sleep(0.3)
            continue
        if size != last_size or size == 0:
            last_size, stable_since = size, time.monotonic()
        elif time.monotonic() - stable_since >= stable_for:
            return True
        time.sleep(0.3)
    return False


class _Handler(FileSystemEventHandler):
    def __init__(self, pending: "queue.Queue[Path]"):
        self.pending = pending

    def on_created(self, event):
        if not event.is_directory:
            self.pending.put(Path(event.src_path))

    def on_moved(self, event):
        if not event.is_directory:
            self.pending.put(Path(event.dest_path))


class FolderWatcher:
    """Appelle on_file(chemin) pour chaque photo ou vidéo terminée, dans l'ordre d'arrivée."""

    def __init__(self, folder: str | Path, on_file: Callable[[str], None]):
        self.folder = Path(folder)
        self.on_file = on_file
        self._pending: "queue.Queue[Path]" = queue.Queue()
        self._observer: Observer | None = None
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._seen: set[str] = set()

    def start(self) -> None:
        self._stop.clear()
        self._observer = Observer()
        # Pas de sous-dossiers : le dossier « _rejets » ne doit pas revenir dans la séance
        self._observer.schedule(_Handler(self._pending), str(self.folder), recursive=False)
        self._observer.start()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                path = self._pending.get(timeout=0.5)
            except queue.Empty:
                continue
            key = str(path)
            if key in self._seen or media_type(path) is None:
                continue
            if wait_until_complete(path):
                self._seen.add(key)
                self.on_file(key)
