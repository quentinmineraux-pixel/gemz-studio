"""Thread unique qui possède l'appareil : connexion automatique, commandes, visée en direct."""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable

from .base import CameraBackend, CameraDisconnected, CameraError

RECONNECT_EVERY = 2.0
FRAME_EVERY = 1 / 20


class CameraWorker:
    """Toutes les commandes passent par une file : le kit Canon n'est appelé que depuis ce thread."""

    def __init__(self, backend: CameraBackend,
                 on_state: Callable[[bool, str], None],
                 on_frame: Callable[[bytes], None],
                 on_status: Callable[[str], None],
                 on_settings: Callable[[dict], None] = lambda settings: None,
                 on_recording: Callable[[bool], None] = lambda recording: None):
        self.backend = backend
        self.on_state = on_state
        self.on_frame = on_frame
        self.on_status = on_status
        self.on_settings = on_settings
        self.on_recording = on_recording
        backend.on_status = on_status
        self.connected = False
        self._commands: "queue.Queue[tuple[str, tuple]]" = queue.Queue()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="camera", daemon=True)
        self._liveview_wanted = True

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def send(self, command: str, *args) -> None:
        self._commands.put((command, args))

    def _run(self) -> None:
        try:
            self.backend.thread_init()
        except Exception as e:  # noqa: BLE001
            self.on_state(False, f"Kit Canon inutilisable : {e}")
            return
        next_try = 0.0
        next_frame = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if not self.connected:
                    self._drain_commands_while_offline()
                    if now >= next_try:
                        next_try = now + RECONNECT_EVERY
                        self._try_connect()
                    time.sleep(0.05)
                    continue
                try:
                    self._handle_commands()
                    self.backend.poll()
                    if self.backend.take_settings_changed():
                        self._push_settings()
                    if self._liveview_wanted and now >= next_frame:
                        next_frame = now + FRAME_EVERY
                        frame = self.backend.liveview_frame()
                        if frame:
                            self.on_frame(frame)
                except CameraDisconnected:
                    self._lost()
                except CameraError as e:
                    self.on_status(str(e))
                time.sleep(0.01)
        finally:
            try:
                self.backend.thread_exit()
            except Exception:  # noqa: BLE001
                pass

    def _try_connect(self) -> None:
        try:
            if self.backend.connect():
                self.connected = True
                if self._liveview_wanted:
                    self.backend.start_liveview()
                self.on_state(True, f"{self.backend.name} connecté")
                self._push_settings()
        except CameraDisconnected:
            self._lost()
        except CameraError as e:
            self.on_state(False, str(e))

    def _push_settings(self) -> None:
        try:
            self.on_settings(self.backend.get_settings())
        except CameraDisconnected:
            raise
        except CameraError as e:
            self.on_status(str(e))

    def _lost(self) -> None:
        self.connected = False
        try:
            self.backend.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.on_state(False, "Appareil déconnecté, en attente…")

    def _drain_commands_while_offline(self) -> None:
        while True:
            try:
                command, _ = self._commands.get_nowait()
            except queue.Empty:
                return
            if command in ("capture", "start_movie"):
                self.on_status("Aucun appareil connecté : branche le boîtier en USB et allume-le.")

    def _handle_commands(self) -> None:
        while True:
            try:
                command, args = self._commands.get_nowait()
            except queue.Empty:
                return
            if command == "liveview":
                self._liveview_wanted = bool(args[0])
                (self.backend.start_liveview if self._liveview_wanted else self.backend.stop_liveview)()
            elif command == "collect_card_movies":
                n = self.backend.collect_card_movies()
                self.on_status(f"{n} vidéo(s) récupérée(s) depuis la carte" if n else "Aucune vidéo sur la carte")
            elif command in ("start_movie", "stop_movie"):
                try:
                    getattr(self.backend, command)()
                finally:
                    self.on_recording(self.backend.recording)  # bouton juste, même si la commande échoue
            elif command == "set_setting":
                try:
                    self.backend.set_setting(*args)
                finally:
                    self._push_settings()  # remet l'interface d'accord avec le boîtier, même si refusé
            else:
                getattr(self.backend, command)(*args)
