import time
from pathlib import Path

from gemz_studio.camera.edsdk import _DirItemInfo32, _DirItemInfo64, _valid_name, find_edsdk_dll
from gemz_studio.camera.fake import FakeCamera
from gemz_studio.camera.worker import CameraWorker


def test_trouve_la_dll_64_bits(tmp_path):
    tmp_path = tmp_path / "Kit 64 bits"  # « 64 » dans le dossier choisi ne doit pas fausser le choix
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
    # Kit 13.x lu avec l'ancienne structure (essayée en premier) : rejeté, l'outil passe à la nouvelle
    new = bytes(_DirItemInfo64(size=123, option=0, szFileName=b"100CANON"))
    assert _valid_name(_DirItemInfo32.from_buffer_copy(new[:288]).szFileName) is None


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
    assert [Path(p).name for p in received] == ["IMG_0001.JPG", "IMG_0002.JPG"]


def test_dll_bits_reads_pe_header(tmp_path):
    from gemz_studio.camera.edsdk import dll_bits

    def fake_dll(name, machine):
        data = bytearray(512)
        data[:2] = b"MZ"
        data[0x3C:0x40] = (0x80).to_bytes(4, "little")
        data[0x80:0x84] = b"PE\0\0"
        data[0x84:0x86] = machine.to_bytes(2, "little")
        path = tmp_path / name
        path.write_bytes(bytes(data))
        return path

    assert dll_bits(fake_dll("a.dll", 0x14C)) == 32
    assert dll_bits(fake_dll("b.dll", 0x8664)) == 64
    (tmp_path / "c.dll").write_bytes(b"pas une dll")
    assert dll_bits(tmp_path / "c.dll") is None


def test_passerelle_32_bits_avec_appareil_simule(tmp_path, monkeypatch):
    import json
    import sys

    from gemz_studio.camera.bridge32 import BridgeCamera

    monkeypatch.setenv("GEMZ_STUDIO_HOME", str(tmp_path / "donnees"))
    monkeypatch.setenv("GEMZ_CANON32", json.dumps([sys.executable, "-m", "gemz_studio.camera.bridge32", "--fake"]))
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parent.parent))
    received, states, frames = [], [], []
    cam = BridgeCamera(tmp_path / "recus", "")
    cam.on_file = received.append
    worker = CameraWorker(cam, lambda ok, text: states.append((ok, text)), frames.append, lambda text: None)
    worker.start()
    deadline = time.monotonic() + 10
    while not states and time.monotonic() < deadline:
        time.sleep(0.05)
    assert states and states[0][0], states
    worker.send("capture")
    while not received and time.monotonic() < deadline:
        time.sleep(0.05)
    worker.stop()
    assert received and Path(received[0]).name == "IMG_0001.JPG"
    assert frames and frames[0][:2] == b"\xff\xd8"  # images JPEG de la visée
    assert cam.process is None  # module 32 bits arrêté proprement
