"""Écran « Prise de vue » : visée en direct, mise au point, déclenchement photo et vidéo."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..camera.base import CameraBackend
from ..camera.worker import CameraWorker


class _Bridge(QObject):
    state = Signal(bool, str)
    frame = Signal(bytes)
    status = Signal(str)


class CameraPanel(QWidget):
    fileArrived = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: CameraWorker | None = None
        self.bridge = _Bridge()
        self.bridge.state.connect(self._on_state)
        self.bridge.frame.connect(self._on_frame)
        self.bridge.status.connect(self._on_status)
        self.recording = False

        layout = QVBoxLayout(self)
        self.state_label = QLabel("Pilotage de l'appareil désactivé (voir Réglages)")
        self.state_label.setStyleSheet("font-weight: bold")
        layout.addWidget(self.state_label)

        self.view = QLabel()
        self.view.setAlignment(Qt.AlignCenter)
        self.view.setMinimumSize(480, 320)
        self.view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.view.setStyleSheet("background: #202020; color: #bbbbbb")
        self.view.setText("Visée en direct")
        layout.addWidget(self.view, 1)

        focus = QHBoxLayout()
        focus.addWidget(QLabel("Mise au point :"))
        af = QPushButton("Auto (AF)")
        af.clicked.connect(lambda: self._send("autofocus"))
        focus.addWidget(af)
        for label, step in (("<<<", -3), ("<<", -2), ("<", -1), (">", 1), (">>", 2), (">>>", 3)):
            b = QPushButton(label)
            b.setToolTip("Plus près" if step < 0 else "Plus loin")
            b.setFixedWidth(46)
            b.clicked.connect(lambda _=False, s=step: self._send("focus_step", s))
            focus.addWidget(b)
        focus.addSpacing(16)
        focus.addWidget(QLabel("Loupe :"))
        self.zoom_group = QButtonGroup(self)
        for z in (1, 5, 10):
            b = QPushButton(f"x{z}")
            b.setCheckable(True)
            b.setChecked(z == 1)
            b.setFixedWidth(46)
            b.clicked.connect(lambda _=False, zz=z: self._send("set_zoom", zz))
            self.zoom_group.addButton(b)
            focus.addWidget(b)
        focus.addStretch(1)
        layout.addLayout(focus)

        shoot = QHBoxLayout()
        self.capture_btn = QPushButton("Déclencher la photo  (Espace)")
        self.capture_btn.setMinimumHeight(44)
        self.capture_btn.setStyleSheet("font-size: 15px; font-weight: bold")
        self.capture_btn.clicked.connect(lambda: self._send("capture"))
        self.movie_btn = QPushButton("● Démarrer la vidéo")
        self.movie_btn.setMinimumHeight(44)
        self.movie_btn.clicked.connect(self._toggle_movie)
        card = QPushButton("Récupérer les vidéos de la carte")
        card.setToolTip("Rapatrie les vidéos restées sur la carte SD puis les efface de la carte")
        card.clicked.connect(lambda: self._send("collect_card_movies"))
        shoot.addWidget(self.capture_btn, 2)
        shoot.addWidget(self.movie_btn, 1)
        shoot.addWidget(card, 1)
        layout.addLayout(shoot)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        shortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        shortcut.activated.connect(lambda: self._send("capture"))
        self._set_controls(False)

    # --- branchement ---------------------------------------------------------
    def start(self, backend: CameraBackend) -> None:
        self.stop()
        backend.on_file = self.fileArrived.emit
        self.worker = CameraWorker(backend, self.bridge.state.emit, self.bridge.frame.emit, self.bridge.status.emit)
        self.state_label.setText("Recherche de l'appareil… (branche-le en USB, allume-le, ferme EOS Utility)")
        self.worker.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.recording = False
        self._set_controls(False)
        self.view.clear()
        self.view.setText("Visée en direct")

    def set_liveview(self, on: bool) -> None:
        self._send("liveview", on)

    def _send(self, command: str, *args) -> None:
        if self.worker:
            self.worker.send(command, *args)

    # --- retours du thread appareil -----------------------------------------
    def _on_state(self, connected: bool, text: str) -> None:
        self.state_label.setText(text)
        self._set_controls(connected)
        if not connected:
            self.recording = False
            self.movie_btn.setText("● Démarrer la vidéo")
            self.view.clear()
            self.view.setText("Visée en direct")

    def _on_frame(self, data: bytes) -> None:
        image = QImage.fromData(data, "JPEG")
        if image.isNull():
            return
        self.view.setPixmap(QPixmap.fromImage(image).scaled(
            self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _set_controls(self, enabled: bool) -> None:
        for w in self.findChildren(QPushButton):
            w.setEnabled(enabled)

    def _toggle_movie(self) -> None:
        if not self.worker:
            return
        if self.recording:
            self._send("stop_movie")
            self.movie_btn.setText("● Démarrer la vidéo")
            self.status_label.setText("Vidéo arrêtée, transfert en cours…")
        else:
            self._send("start_movie")
            self.movie_btn.setText("■ Arrêter la vidéo")
        self.recording = not self.recording
