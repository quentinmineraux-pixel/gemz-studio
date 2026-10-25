"""Réglages : studio photo (dossiers, watermark, preset avec aperçu), puis prix, IA, connexions et synchro."""
from __future__ import annotations

import copy

from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QSpinBox, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..camera import MODE_CANON, MODE_LABELS
from ..config import Settings
from ..imaging import adjust, apply_watermark, load_photo
from .common import pil_to_pixmap
from .selling_settings import AiTab, ConnectionsTab, PricesTab, SyncTab


class PathPicker(QWidget):
    def __init__(self, value: str, folder: bool = True, placeholder: str = ""):
        super().__init__()
        self.folder = folder
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText(placeholder)
        browse = QPushButton("Parcourir…")
        browse.clicked.connect(self._browse)
        layout.addWidget(self.edit)
        layout.addWidget(browse)

    def _browse(self) -> None:
        if self.folder:
            path = QFileDialog.getExistingDirectory(self, "Choisir un dossier", self.edit.text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choisir le logo", self.edit.text(), "Images PNG (*.png)")
        if path:
            self.edit.setText(path)

    def value(self) -> str:
        return self.edit.text().strip()


def ratio_slider(value: float, low: int, high: int) -> QSlider:
    s = QSlider(Qt.Horizontal)
    s.setRange(low, high)
    s.setValue(round(value * 100))
    return s


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, sample_photo: str | None = None, parent=None, catalog=None):
        super().__init__(parent)
        self.setWindowTitle("Réglages")
        self.settings = copy.deepcopy(settings)
        self.sample = self._load_sample(sample_photo)

        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)
        studio = QWidget()
        root = QHBoxLayout(studio)
        left = QVBoxLayout()
        root.addLayout(left, 1)

        folders = QGroupBox("Dossiers")
        fl = QFormLayout(folders)
        self.watch = PathPicker(self.settings.watch_folder, placeholder="Dossier d'enregistrement d'EOS Utility")
        self.output = PathPicker(self.settings.output_folder, placeholder="Où ranger les produits traités")
        fl.addRow("Dossier des photos reçues", self.watch)
        fl.addRow("Dossier de sortie", self.output)
        left.addWidget(folders)

        cam = QGroupBox("Appareil photo")
        cl = QFormLayout(cam)
        self.camera_mode = QComboBox()
        for mode, label in MODE_LABELS.items():
            self.camera_mode.addItem(label, mode)
        self.camera_mode.setCurrentIndex(max(0, self.camera_mode.findData(self.settings.camera_mode)))
        self.edsdk = PathPicker(self.settings.edsdk_folder, placeholder="Dossier du kit Canon dézippé")
        cl.addRow("Mode", self.camera_mode)
        cl.addRow("Kit Canon (EDSDK)", self.edsdk)
        self.camera_mode.currentIndexChanged.connect(
            lambda: self.edsdk.setEnabled(self.camera_mode.currentData() == MODE_CANON))
        self.edsdk.setEnabled(self.settings.camera_mode == MODE_CANON)
        left.addWidget(cam)

        wm = QGroupBox("Watermark (bas droite)")
        wl = QFormLayout(wm)
        self.logo = PathPicker(self.settings.logo_path, folder=False, placeholder="Logo GemZ fourni par défaut")
        self.scale = ratio_slider(self.settings.watermark_scale, 5, 50)
        self.opacity = ratio_slider(self.settings.watermark_opacity, 10, 100)
        self.color = QComboBox()
        self.color.addItems(["original", "noir", "blanc"])
        self.color.setCurrentText(self.settings.watermark_color)
        wl.addRow("Logo (PNG transparent)", self.logo)
        wl.addRow("Taille", self.scale)
        wl.addRow("Opacité", self.opacity)
        wl.addRow("Couleur", self.color)
        left.addWidget(wm)

        preset = QGroupBox("Retouche automatique (preset conservé)")
        pl = QFormLayout(preset)
        self.brightness = ratio_slider(self.settings.brightness, 70, 130)
        self.contrast = ratio_slider(self.settings.contrast, 70, 130)
        reset = QPushButton("Remettre à zéro")
        reset.clicked.connect(lambda: (self.brightness.setValue(100), self.contrast.setValue(100)))
        pl.addRow("Luminosité", self.brightness)
        pl.addRow("Contraste", self.contrast)
        pl.addRow("", reset)
        left.addWidget(preset)

        export = QGroupBox("Export et séance")
        el = QFormLayout(export)
        self.max_side = QSpinBox()
        self.max_side.setRange(800, 6000)
        self.max_side.setSingleStep(100)
        self.max_side.setValue(self.settings.web_max_side)
        self.auto_split = QCheckBox("Passer au produit suivant quand le nombre de photos et vidéos est atteint")
        self.auto_split.setChecked(self.settings.auto_split)
        el.addRow("Grand côté des photos web (px)", self.max_side)
        el.addRow(self.auto_split)
        left.addWidget(export)

        kinds = QGroupBox("Médias par produit")
        kl = QVBoxLayout(kinds)
        self.table = QTableWidget(len(self.settings.kinds), 3)
        self.table.setHorizontalHeaderLabels(["Type", "Photos", "Vidéos"])
        self.table.verticalHeader().setVisible(False)
        for row, kind in enumerate(self.settings.kinds):
            item = QTableWidgetItem(kind.label)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)
            for col, value in ((1, kind.photos_per_product), (2, kind.videos_per_product)):
                spin = QSpinBox()
                spin.setRange(0, 30)
                spin.setValue(value)
                self.table.setCellWidget(row, col, spin)
        self.table.resizeColumnsToContents()
        kl.addWidget(self.table)
        left.addWidget(kinds)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Enregistrer")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        right = QVBoxLayout()
        right.addWidget(QLabel("Aperçu du rendu final"))
        self.preview = QLabel()
        self.preview.setFixedSize(560, 420)
        self.preview.setAlignment(Qt.AlignCenter)
        right.addWidget(self.preview)
        right.addStretch(1)
        root.addLayout(right, 1)
        self.tabs.addTab(studio, "Studio photo")
        self.extra_tabs = [PricesTab(self.settings), AiTab(self.settings, catalog),
                           ConnectionsTab(self.settings, self._collect), SyncTab(self.settings)]
        for tab, title in zip(self.extra_tabs, ("Prix et ventes", "IA", "Connexions", "Synchro et notifications")):
            self.tabs.addTab(tab, title)
        outer.addWidget(buttons)

        self._timer = QTimer(self, singleShot=True, interval=150)
        self._timer.timeout.connect(self._render_preview)
        for slider in (self.scale, self.opacity, self.brightness, self.contrast):
            slider.valueChanged.connect(self._timer.start)
        self.color.currentTextChanged.connect(self._timer.start)
        self.logo.edit.editingFinished.connect(self._timer.start)
        self._render_preview()

    @staticmethod
    def _load_sample(path: str | None) -> Image.Image:
        if path:
            try:
                img = load_photo(path)
                img.thumbnail((1000, 1000))
                return img
            except OSError:
                pass
        return Image.new("RGB", (1000, 750), (235, 235, 235))

    def _collect(self) -> Settings:
        s = self.settings
        s.watch_folder = self.watch.value()
        s.camera_mode = self.camera_mode.currentData()
        s.edsdk_folder = self.edsdk.value()
        s.output_folder = self.output.value()
        s.logo_path = self.logo.value()
        s.watermark_scale = self.scale.value() / 100
        s.watermark_opacity = self.opacity.value() / 100
        s.watermark_color = self.color.currentText()
        s.brightness = self.brightness.value() / 100
        s.contrast = self.contrast.value() / 100
        s.web_max_side = self.max_side.value()
        s.auto_split = self.auto_split.isChecked()
        for row, kind in enumerate(s.kinds):
            kind.photos_per_product = self.table.cellWidget(row, 1).value()
            kind.videos_per_product = self.table.cellWidget(row, 2).value()
        for tab in getattr(self, "extra_tabs", []):
            tab.collect(s)
        return s

    def _render_preview(self) -> None:
        s = self._collect()
        try:
            img = apply_watermark(adjust(self.sample.copy(), s.brightness, s.contrast), s)
        except OSError as e:
            self.preview.setText(f"Logo illisible : {e}")
            return
        self.preview.setPixmap(pil_to_pixmap(img).scaled(
            self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def accept(self) -> None:
        # Clés et jetons : coffre chiffré de Windows, jamais dans settings.json
        from .. import vault
        values: dict[str, str] = {}
        for tab in self.extra_tabs:
            tab.secrets(values)
        if values:
            vault.set_many(values)
        super().accept()

    def result_settings(self) -> Settings:
        return self._collect()
