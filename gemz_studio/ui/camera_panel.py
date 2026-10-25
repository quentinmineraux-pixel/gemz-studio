"""Écran « Prise de vue » : visée en direct, mise au point, déclenchement photo et vidéo."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ..camera.base import CameraBackend
from ..camera.props import TITLES, WB_KELVIN, label
from ..camera.worker import CameraWorker

EDITABLE = ("iso", "tv", "av", "ev", "wb", "kelvin")
MARK_SECONDS = 2.0


class _Bridge(QObject):
    state = Signal(bool, str)
    frame = Signal(bytes)
    status = Signal(str)
    settings = Signal(dict)
    recording = Signal(bool)


class LiveView(QLabel):
    """Visée en direct ; un clic donne la position dans l'image (fractions 0..1)."""
    clicked = Signal(float, float)

    def __init__(self):
        super().__init__()
        self.image_rect = QRect()

    def mousePressEvent(self, event):
        r = self.image_rect
        pos = event.position()
        if event.button() == Qt.LeftButton and r.width() > 0 and r.contains(pos.toPoint()):
            self.clicked.emit((pos.x() - r.x()) / r.width(), (pos.y() - r.y()) / r.height())
        super().mousePressEvent(event)


class CameraPanel(QWidget):
    fileArrived = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker: CameraWorker | None = None
        self.bridge = _Bridge()
        self.bridge.state.connect(self._on_state)
        self.bridge.frame.connect(self._on_frame)
        self.bridge.status.connect(self._on_status)
        self.bridge.settings.connect(self._on_settings)
        self.bridge.recording.connect(self._on_recording)
        self.recording = False
        self._mark: tuple[float, float, float] | None = None  # point cliqué et heure du clic

        layout = QVBoxLayout(self)
        self.state_label = QLabel("Pilotage de l'appareil désactivé (voir Réglages)")
        self.state_label.setStyleSheet("font-weight: bold")
        layout.addWidget(self.state_label)

        settings_row = QHBoxLayout()
        self.mode_label = QLabel()
        self.mode_label.setToolTip("Mode choisi avec la molette du boîtier (M pour tout régler d'ici)")
        settings_row.addWidget(self.mode_label)
        self.combos: dict[str, QComboBox] = {}
        self.combo_titles: dict[str, QLabel] = {}
        for key in EDITABLE:
            title = QLabel(TITLES[key] + " :")
            combo = QComboBox()
            combo.setEnabled(False)
            combo.setFocusPolicy(Qt.NoFocus)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)  # la barre d'espace reste le déclencheur
            combo.activated.connect(lambda _i, k=key: self._setting_chosen(k))
            settings_row.addSpacing(8)
            settings_row.addWidget(title)
            settings_row.addWidget(combo)
            self.combos[key] = combo
            self.combo_titles[key] = title
        settings_row.addStretch(1)
        layout.addLayout(settings_row)
        self._show_kelvin(False)

        self.view = LiveView()
        self.view.setToolTip("Clique sur l'image pour faire la mise au point à cet endroit")
        self.view.clicked.connect(self._view_clicked)
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
        for text, step in (("<<<", -3), ("<<", -2), ("<", -1), (">", 1), (">>", 2), (">>>", 3)):
            b = QPushButton(text)
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
        self.worker = CameraWorker(backend, self.bridge.state.emit, self.bridge.frame.emit, self.bridge.status.emit,
                                   self.bridge.settings.emit, self.bridge.recording.emit)
        self.state_label.setText("Recherche de l'appareil… (branche-le en USB, allume-le, ferme EOS Utility)")
        self.worker.start()

    def stop(self) -> None:
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.recording = False
        self._set_controls(False)
        self._clear_settings()
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
            self._clear_settings()
            self.view.clear()
            self.view.setText("Visée en direct")

    def _on_frame(self, data: bytes) -> None:
        image = QImage.fromData(data, "JPEG")
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image).scaled(self.view.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.view.image_rect = QRect((self.view.width() - pixmap.width()) // 2,
                                     (self.view.height() - pixmap.height()) // 2, pixmap.width(), pixmap.height())
        if self._mark and time.monotonic() - self._mark[2] < MARK_SECONDS:
            painter = QPainter(pixmap)
            painter.setPen(QPen(QColor(255, 210, 0), 2))
            center = QPointF(self._mark[0] * pixmap.width(), self._mark[1] * pixmap.height())
            painter.drawRect(int(center.x()) - 24, int(center.y()) - 16, 48, 32)
            painter.end()
        self.view.setPixmap(pixmap)

    def _view_clicked(self, fx: float, fy: float) -> None:
        if not self.worker:
            return
        self._mark = (fx, fy, time.monotonic())
        self.status_label.setText("Mise au point sur la zone cliquée…")
        self._send("focus_at", fx, fy)

    # --- réglages ------------------------------------------------------------
    def _on_settings(self, settings: dict) -> None:
        mode = settings.get("mode")
        self.mode_label.setText(f"Mode : {label('mode', mode['value'])}" if mode else "")
        for key, combo in self.combos.items():
            info = settings.get(key)
            combo.blockSignals(True)
            combo.clear()
            if info:
                choices = list(info["choices"])
                if info["value"] not in choices:
                    choices.insert(0, info["value"])  # valeur actuelle hors liste (ex. réglage auto)
                for code in choices:
                    combo.addItem(label(key, code), code)
                combo.setCurrentIndex(choices.index(info["value"]))
            combo.setEnabled(bool(info and info["choices"]))
            combo.blockSignals(False)
            self.combo_titles[key].setVisible(bool(info))
            combo.setVisible(bool(info))
        wb = settings.get("wb")
        self._show_kelvin(bool(wb and wb["value"] == WB_KELVIN and "kelvin" in settings))

    def _show_kelvin(self, visible: bool) -> None:
        self.combos["kelvin"].setVisible(visible)
        self.combo_titles["kelvin"].setVisible(visible)

    def _clear_settings(self) -> None:
        self.mode_label.setText("")
        for combo in self.combos.values():
            combo.clear()
            combo.setEnabled(False)

    def _setting_chosen(self, key: str) -> None:
        code = self.combos[key].currentData()
        if code is not None:
            self._send("set_setting", key, int(code))

    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _set_controls(self, enabled: bool) -> None:
        for w in self.findChildren(QPushButton):
            w.setEnabled(enabled)
        if not enabled:
            for combo in self.combos.values():
                combo.setEnabled(False)

    def _on_recording(self, recording: bool) -> None:
        self.recording = recording
        self.movie_btn.setText("■ Arrêter la vidéo" if recording else "● Démarrer la vidéo")

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
