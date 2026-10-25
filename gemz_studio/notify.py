"""Notifications sur le téléphone avec ntfy (application gratuite, sans compte)."""
from __future__ import annotations

import threading

NTFY_SERVER = "https://ntfy.sh"


def _post(topic: str, title: str, message: str, tags: list[str]):
    import requests
    # Publication en JSON : accents et emojis passent sans souci dans le titre
    r = requests.post(NTFY_SERVER, json={"topic": topic, "title": title, "message": message, "tags": tags},
                      timeout=15)
    r.raise_for_status()


def phone(topic: str, title: str, message: str, tags: list[str] | None = None) -> None:
    """Envoi en arrière-plan ; une panne de réseau ne doit jamais bloquer la synchro."""
    if not topic:
        return

    def send():
        try:
            _post(topic, title, message, tags or ["gem"])
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=send, daemon=True).start()


def phone_now(topic: str, title: str, message: str) -> None:
    """Envoi immédiat (bouton « Tester ») : lève une erreur si ça ne passe pas."""
    _post(topic, title, message, ["white_check_mark"])
