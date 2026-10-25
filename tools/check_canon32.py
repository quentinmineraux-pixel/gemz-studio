"""Vérifie en CI que gemz-canon32.exe est bien en 32 bits et répond (sans kit Canon, il doit le dire)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["GEMZ_STUDIO_HOME"] = tempfile.mkdtemp()
os.environ["GEMZ_CANON32"] = json.dumps([os.path.abspath("dist/gemz-canon32.exe")])

from gemz_studio.camera.base import CameraError  # noqa: E402
from gemz_studio.camera.bridge32 import BridgeCamera  # noqa: E402
from gemz_studio.camera.edsdk import dll_bits  # noqa: E402

assert dll_bits("dist/gemz-canon32.exe") == 32, "le module n'est pas en 32 bits"
cam = BridgeCamera(tempfile.mkdtemp(), "C:/aucun-kit")
try:
    cam.thread_init()
except CameraError as e:
    assert "introuvable" in str(e), e
    print("Réponse attendue :", e)
else:
    raise SystemExit("le module aurait dû signaler l'absence du kit")
finally:
    cam.thread_exit()
