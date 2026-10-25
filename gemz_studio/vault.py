"""Jetons et clés (Shopify, Etsy, eBay, IA), chiffrés pour la session Windows (DPAPI).

Ils ne sont jamais écrits dans settings.json : un fichier copié sur une autre machine ou un autre
compte Windows est illisible.
"""
from __future__ import annotations

import base64
import json
import sys
import threading

from .config import data_dir

_LOCK = threading.Lock()
FILE = "secrets.bin"


def _protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return base64.b64encode(data)
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    src = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("chiffrement des identifiants impossible")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return base64.b64decode(data)
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    src = Blob(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("identifiants illisibles (autre compte Windows ?)")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def load_all() -> dict[str, str]:
    path = data_dir() / FILE
    if not path.exists():
        return {}
    try:
        return json.loads(_unprotect(path.read_bytes()).decode("utf-8"))
    except (OSError, ValueError):
        return {}


def get(name: str, default: str = "") -> str:
    with _LOCK:
        return load_all().get(name, default)


def set_many(values: dict[str, str]) -> None:
    with _LOCK:
        data = load_all()
        for key, value in values.items():
            if value:
                data[key] = value
            else:
                data.pop(key, None)
        path = data_dir() / FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(_protect(json.dumps(data).encode("utf-8")))
        tmp.replace(path)


def put(name: str, value: str) -> None:
    set_many({name: value})
