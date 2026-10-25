"""Interface commune des appareils (Canon EDSDK ou appareil simulé)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable


class CameraError(Exception):
    pass


class CameraDisconnected(CameraError):
    pass


class CameraBackend:
    """Un appareil photo piloté depuis un seul thread (celui du CameraWorker).

    Les photos et vidéos téléchargées sont écrites dans download_dir : le dossier surveillé
    de la séance, pour que le reste de l'outil les traite comme avant.
    """

    name = "Appareil"

    def __init__(self, download_dir: str | Path):
        self.download_dir = Path(download_dir)
        self.on_file: Callable[[str], None] = lambda path: None
        self.on_status: Callable[[str], None] = lambda text: None
        self.recording = False

    # Cycle de vie
    def thread_init(self) -> None: ...
    def thread_exit(self) -> None: ...
    def connect(self) -> bool:
        raise NotImplementedError
    def disconnect(self) -> None: ...
    def poll(self) -> None:
        """Traite les événements de l'appareil ; lève CameraDisconnected si le câble est débranché."""

    # Visée en direct
    def start_liveview(self) -> None: ...
    def stop_liveview(self) -> None: ...
    def liveview_frame(self) -> bytes | None:
        """Image JPEG de la visée en direct, ou None si pas encore prête."""
        return None

    # Mise au point
    def autofocus(self) -> None: ...
    def focus_step(self, step: int) -> None:
        """step de -3 (plus près, grand pas) à +3 (plus loin, grand pas)."""
    def set_zoom(self, zoom: int) -> None:
        """1, 5 ou 10 (loupe de la visée)."""
    def focus_at(self, fx: float, fy: float) -> None:
        """Mise au point sur un point cliqué de la visée (fractions 0..1 de l'image)."""

    # Réglages de prise de vue (clés et codes : voir props.py)
    def get_settings(self) -> dict:
        """{clé: {"value": code, "choices": [codes]}} ; choices vide = réglage non modifiable."""
        return {}
    def set_setting(self, key: str, value: int) -> None: ...
    def take_settings_changed(self) -> bool:
        """Vrai si un réglage a changé sur le boîtier depuis le dernier appel."""
        return False

    # Prise de vue
    def capture(self) -> None:
        raise NotImplementedError
    def start_movie(self) -> None:
        raise NotImplementedError
    def stop_movie(self) -> None:
        raise NotImplementedError
    def collect_card_movies(self) -> int:
        """Télécharge les vidéos restées sur la carte puis les efface ; renvoie leur nombre."""
        return 0
    def delete_card_movies(self, names: list[str]) -> int:
        """Efface de la carte les vidéos portant ces noms (déjà rapatriées) ; renvoie leur nombre."""
        return 0

    def target_path(self, filename: str) -> Path:
        """Chemin libre dans le dossier de téléchargement (ne jamais écraser un fichier)."""
        self.download_dir.mkdir(parents=True, exist_ok=True)
        path = self.download_dir / filename
        stem, suffix, n = path.stem, path.suffix, 2
        while path.exists():
            path = self.download_dir / f"{stem}_{n}{suffix}"
            n += 1
        return path
