"""Pilotage direct de l'appareil photo (remplace EOS Utility)."""
from __future__ import annotations

from .base import CameraBackend, CameraDisconnected, CameraError

MODE_FOLDER = "dossier"      # EOS Utility (ou autre logiciel) remplit le dossier surveillé
MODE_CANON = "canon"         # GemZ Studio pilote le boîtier avec le kit Canon EDSDK
MODE_FAKE = "simule"         # appareil simulé, pour essayer sans boîtier

MODE_LABELS = {
    MODE_FOLDER: "Dossier surveillé (EOS Utility)",
    MODE_CANON: "Pilotage direct Canon (EDSDK)",
    MODE_FAKE: "Appareil simulé (essai)",
}


def make_backend(mode: str, download_dir: str, sdk_folder: str = "") -> CameraBackend | None:
    if mode == MODE_CANON:
        import sys
        from .edsdk import EdsdkCamera, dll_bits, find_edsdk_dll
        dll = find_edsdk_dll(sdk_folder)
        if dll is not None and sys.maxsize > 2**32 and dll_bits(dll) == 32:
            # Kit 32 bits : chargé dans le module 32 bits séparé
            from .bridge32 import BridgeCamera
            return BridgeCamera(download_dir, sdk_folder)
        return EdsdkCamera(download_dir, sdk_folder)
    if mode == MODE_FAKE:
        from .fake import FakeCamera
        return FakeCamera(download_dir)
    return None


__all__ = ["CameraBackend", "CameraDisconnected", "CameraError", "make_backend",
           "MODE_FOLDER", "MODE_CANON", "MODE_FAKE", "MODE_LABELS"]
