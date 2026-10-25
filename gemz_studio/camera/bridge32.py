"""Passerelle vers un kit Canon 32 bits.

GemZ Studio est en 64 bits (Qt n'existe plus en 32 bits) et Windows refuse de charger une DLL 32 bits
dans un programme 64 bits. Le kit est donc chargé dans un petit programme 32 bits séparé
(gemz-canon32.exe, fourni dans le .exe) que GemZ Studio pilote par ses entrée et sortie standard.

Chaque message est un en-tête JSON précédé de sa longueur (4 octets), suivi éventuellement de
données binaires (images de la visée) dont la taille est donnée par la clé « bin » de l'en-tête.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from pathlib import Path

from .base import CameraBackend, CameraDisconnected, CameraError

HELPER_NAME = "gemz-canon32.exe"


def _send(stream, header: dict, payload: bytes = b"") -> None:
    if payload:
        header["bin"] = len(payload)
    data = json.dumps(header).encode("utf-8")
    stream.write(struct.pack("<I", len(data)) + data + payload)
    stream.flush()


def _read_exact(stream, n: int) -> bytes:
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data


def _receive(stream) -> tuple[dict, bytes]:
    (length,) = struct.unpack("<I", _read_exact(stream, 4))
    header = json.loads(_read_exact(stream, length))
    payload = _read_exact(stream, header["bin"]) if header.get("bin") else b""
    return header, payload


def helper_command() -> list[str] | None:
    """Commande qui lance le programme 32 bits (GEMZ_CANON32 permet de la remplacer pour les tests)."""
    override = os.environ.get("GEMZ_CANON32")
    if override:
        return json.loads(override)
    from ..config import resource_path
    exe = resource_path(f"canon32/{HELPER_NAME}")
    return [str(exe)] if exe.is_file() else None


class BridgeCamera(CameraBackend):
    """Appareil piloté à travers gemz-canon32.exe ; s'utilise exactement comme EdsdkCamera."""

    name = "Canon"

    def __init__(self, download_dir, sdk_folder: str | Path):
        super().__init__(download_dir)
        self.sdk_folder = str(sdk_folder)
        self.command = helper_command()
        if self.command is None:
            raise CameraError("Ce kit Canon est en 32 bits et le module 32 bits de GemZ Studio est absent : "
                              "utilise le kit officiel 64 bits (dossier EDSDK_64).")
        self.process: subprocess.Popen | None = None
        self._log = None

    def _call(self, method: str, *args):
        if self.process is None or self.process.poll() is not None:
            raise CameraError("Le module Canon 32 bits s'est arrêté (voir canon32.log dans les données de GemZ Studio).")
        try:
            _send(self.process.stdin, {"call": method, "args": list(args)})
            while True:
                header, payload = _receive(self.process.stdout)
                kind = header.get("t")
                if kind == "status":
                    self.on_status(header["text"])
                elif kind == "file":
                    self.on_file(header["path"])
                elif kind == "reply":
                    break
        except (EOFError, OSError) as e:
            raise CameraError("Le module Canon 32 bits s'est arrêté (voir canon32.log dans les données "
                              "de GemZ Studio).") from e
        self.name = header.get("name", self.name)
        self.recording = header.get("recording", self.recording)
        if "error" in header:
            raise (CameraDisconnected if header.get("disconnected") else CameraError)(header["error"])
        return payload if header.get("bin") else header.get("value")

    # --- cycle de vie ------------------------------------------------------
    def thread_init(self) -> None:
        from ..config import data_dir
        self._log = open(data_dir() / "canon32.log", "wb")
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        self.process = subprocess.Popen(
            self.command + ["--download-dir", str(self.download_dir), "--sdk", self.sdk_folder],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log, creationflags=flags)
        try:
            self._call("thread_init")
        except CameraError:
            self.thread_exit()  # le CameraWorker n'appelle pas thread_exit si thread_init échoue
            raise

    def thread_exit(self) -> None:
        if self.process is None:
            return
        try:
            self._call("thread_exit")
        except CameraError:
            pass
        try:
            self.process.stdin.close()
            self.process.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.process.kill()
        self.process = None
        if self._log:
            self._log.close()

    def connect(self) -> bool:
        return bool(self._call("connect"))

    def disconnect(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self._call("disconnect")

    def poll(self) -> None:
        self._call("poll")

    # --- commandes transmises telles quelles -------------------------------
    def start_liveview(self) -> None:
        self._call("start_liveview")

    def stop_liveview(self) -> None:
        self._call("stop_liveview")

    def liveview_frame(self) -> bytes | None:
        return self._call("liveview_frame") or None

    def autofocus(self) -> None:
        self._call("autofocus")

    def focus_step(self, step: int) -> None:
        self._call("focus_step", step)

    def set_zoom(self, zoom: int) -> None:
        self._call("set_zoom", zoom)

    def capture(self) -> None:
        self._call("capture")

    def start_movie(self) -> None:
        self._call("start_movie")

    def stop_movie(self) -> None:
        self._call("stop_movie")

    def collect_card_movies(self) -> int:
        return int(self._call("collect_card_movies") or 0)


ALLOWED = {"thread_init", "thread_exit", "connect", "disconnect", "poll", "start_liveview", "stop_liveview",
           "liveview_frame", "autofocus", "focus_step", "set_zoom", "capture", "start_movie", "stop_movie",
           "collect_card_movies"}


def main(argv: list[str] | None = None) -> None:
    """Programme 32 bits : charge le kit Canon et exécute les commandes reçues de GemZ Studio."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--download-dir", required=True)
    parser.add_argument("--sdk", default="")
    parser.add_argument("--fake", action="store_true", help="appareil simulé (tests)")
    opts = parser.parse_args(argv)

    channel_in = sys.stdin.buffer
    channel_out = sys.stdout.buffer
    sys.stdout = sys.stderr  # un print égaré ne doit pas casser le protocole

    if opts.fake:
        from .fake import FakeCamera
        camera = FakeCamera(opts.download_dir)
    else:
        from .edsdk import EdsdkCamera
        try:
            camera = EdsdkCamera(opts.download_dir, opts.sdk)
        except CameraError as e:
            camera = None
            startup_error = str(e)
    camera_ok = camera is not None
    if camera_ok:
        camera.on_status = lambda text: _send(channel_out, {"t": "status", "text": text})
        camera.on_file = lambda path: _send(channel_out, {"t": "file", "path": path})

    while True:
        try:
            request, _ = _receive(channel_in)
        except EOFError:
            break
        method = request.get("call")
        reply: dict = {"t": "reply"}
        payload = b""
        try:
            if not camera_ok:
                raise CameraError(startup_error)
            if method not in ALLOWED:
                raise CameraError(f"commande inconnue : {method}")
            value = getattr(camera, method)(*request.get("args", []))
            if isinstance(value, bytes):
                payload = value
            else:
                reply["value"] = value
        except CameraDisconnected as e:
            reply.update(error=str(e), disconnected=True)
        except Exception as e:  # noqa: BLE001 - toute erreur est renvoyée à GemZ Studio
            reply["error"] = str(e) or type(e).__name__
        if camera_ok:
            reply.update(name=camera.name, recording=camera.recording)
        _send(channel_out, reply, payload)
        if method == "thread_exit":
            break


if __name__ == "__main__":
    main()
