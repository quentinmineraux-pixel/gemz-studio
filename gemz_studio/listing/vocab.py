"""Valeurs déjà utilisées dans la boutique pour les métachamps Pierre, Provenance et Forme.

Les filtres de la boutique affichent chaque orthographe à part : « afrique du sud » et « afrique-du-sud »
feraient deux cases. On reprend donc l'orthographe déjà présente quand elle existe ; les valeurs
nouvelles sont signalées dans la fiche avant d'être ajoutées.
"""
from __future__ import annotations

import json
import threading

from ..config import data_dir
from ..quickentry import strip_accents

FILE = "vocabulaire_shopify.json"
KEYS = ("pierre", "provenance", "forme")
_LOCK = threading.Lock()
_cache: tuple[str, dict[str, list[str]]] | None = None


def _norm(value: str) -> str:
    return "".join(ch for ch in strip_accents(value).lower() if ch.isalnum())


def load() -> dict[str, list[str]]:
    global _cache
    path = data_dir() / FILE
    with _LOCK:
        if _cache is None or _cache[0] != str(path):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            _cache = (str(path), data)
        return _cache[1]


def remember(values_by_key: dict[str, list[str]]) -> None:
    """Ajoute les valeurs vues dans la boutique (import) ou envoyées par l'outil."""
    data = load()
    changed = False
    with _LOCK:
        for key, values in values_by_key.items():
            known = data.setdefault(key, [])
            seen = {_norm(v) for v in known}
            for value in values:
                value = str(value).strip()
                if value and _norm(value) not in seen:
                    known.append(value)
                    seen.add(_norm(value))
                    changed = True
        if changed:
            (data_dir() / FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def match(key: str, values: list[str]) -> list[str]:
    """Remplace chaque valeur par l'orthographe déjà utilisée dans la boutique, si elle existe."""
    known = {_norm(v): v for v in load().get(key, [])}
    out: list[str] = []
    for value in values:
        spelled = known.get(_norm(value), value)
        if spelled and spelled not in out:
            out.append(spelled)
    return out


def is_new(key: str, value: str) -> bool:
    known = load().get(key)
    return bool(known) and _norm(value) not in {_norm(v) for v in known}
