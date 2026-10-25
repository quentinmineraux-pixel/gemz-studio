import time

from gemz_studio.camera.edsdk import _DirItemInfo32, _DirItemInfo64, _valid_name, find_edsdk_dll
from gemz_studio.camera.fake import FakeCamera
from gemz_studio.camera.worker import CameraWorker


def test_trouve_la_dll_64_bits(tmp_path):
    for sub in ("EDSDK/Dll", "EDSDK_64/Dll"):
        (tmp_path / sub).mkdir(parents=True)
        (tmp_path / sub / "EDSDK.dll").write_bytes(b"")
    assert find_edsdk_dll(tmp_path).parent.parent.name == "EDSDK_64"
    assert find_edsdk_dll(tmp_path / "rien") is None


def test_lit_le_nom_du_fichier_sur_la_carte():
    info = _DirItemInfo64(size=123, isFolder=0, szFileName=b"MVI_0042.MP4")
    assert _valid_name(info.szFileName) == "MVI_0042.MP4"
    # Ancien kit lu avec la nouvelle structure : nom décalé de 4 caractères, rejeté
    old = bytes(_DirItemInfo32(size=123, szFileName=b"MVI_0042.MOV"))
    assert _valid_name(_DirItemInfo64.from_buffer_copy(old + b"\0" * 8).szFileName) is None
    assert _valid_name(_DirItemInfo32.from_buffer_copy(old).szFileName) == "MVI_0042.MOV"
    assert _valid_name(b"100CANON") == "100CANON"


def test_worker_avec_appareil_simule(tmp_path):
    received, states = [], []
    cam = FakeCamera(tmp_path)
    cam.on_file = received.append
    worker = CameraWorker(cam, lambda ok, text: states.append(ok), lambda frame: None, lambda text: None)
    worker.start()
    deadline = time.monotonic() + 5
    while not states and time.monotonic() < deadline:
        time.sleep(0.05)
    worker.send("capture")
    worker.send("capture")
    deadline = time.monotonic() + 5
    while len(received) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    worker.stop()
    assert states[0] is True
    assert [p.split("/")[-1] for p in received] == ["IMG_0001.JPG", "IMG_0002.JPG"]
