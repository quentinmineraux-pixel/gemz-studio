"""Formulaire de fiche produit, avec la saisie rapide en haut."""
from __future__ import annotations

from dataclasses import fields

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from ..config import ProductKind
from ..models import STATUS_LABELS, Product, ProductInfo
from ..quickentry import KNOWN_COUNTRIES, parse_line

TRAITEMENTS = ["naturel", "poli", "chauffé", "teinté", "stabilisé"]
METAUX = ["argent 925", "laiton", "plaqué or", "acier inoxydable", "cuivre"]

COMMON = [("nom", "Nom *"), ("variete", "Variété"), ("dimensions", "Dimensions (cm)"),
          ("poids_g", "Poids (g)"), ("pays", "Provenance *"), ("region", "Région / mine"),
          ("traitement", "Traitement"), ("prix", "Prix TTC (€) *")]
EXTRA = {
    "bague": [("taille_reelle", "Taille réelle (FR)"), ("metal", "Métal"),
              ("dimensions_pierre", "Dimensions de la pierre"), ("poids_total_g", "Poids total (g)")],
    "bracelet": [("pierres", "Pierres"), ("perles_mm", "Perles (mm)"), ("fil", "Type de fil")],
}


class ProductForm(QWidget):
    processRequested = Signal(str)   # id du produit
    openFolderRequested = Signal(str)
    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.product: Product | None = None
        self.kind: ProductKind | None = None
        self.inputs: dict[str, QWidget] = {}
        self.subtag_boxes: list[QCheckBox] = []

        layout = QVBoxLayout(self)
        quick = QGroupBox("Saisie rapide")
        ql = QVBoxLayout(quick)
        self.quick = QLineEdit()
        self.quick.setPlaceholderText("malachite fibreuse congo 140g 8x6x5cm 164€  puis Entrée")
        self.quick.returnPressed.connect(self._apply_quick)
        ql.addWidget(self.quick)
        layout.addWidget(quick)

        self.form_box = QGroupBox("Fiche")
        self.form = QFormLayout(self.form_box)
        layout.addWidget(self.form_box)

        self.subtags_box = QGroupBox("Sous-catégories (collections Shopify)")
        self.subtags_layout = QHBoxLayout(self.subtags_box)
        layout.addWidget(self.subtags_box)

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.process_btn = QPushButton("Enregistrer et traiter")
        self.process_btn.setDefault(True)
        self.process_btn.clicked.connect(self._process)
        self.folder_btn = QPushButton("Ouvrir le dossier")
        self.folder_btn.clicked.connect(lambda: self.product and self.openFolderRequested.emit(self.product.id))
        buttons.addWidget(self.process_btn)
        buttons.addWidget(self.folder_btn)
        layout.addLayout(buttons)
        layout.addStretch(1)
        self.setEnabled(False)

    # --- construction ------------------------------------------------------
    def _build(self, kind: ProductKind) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.inputs.clear()
        for key, label in COMMON + EXTRA.get(kind.form, []):
            if key == "pays":
                w = QComboBox()
                w.setEditable(True)
                w.addItems([""] + KNOWN_COUNTRIES)
            elif key == "traitement":
                w = QComboBox()
                w.setEditable(True)
                w.addItems(TRAITEMENTS)
            elif key == "metal":
                w = QComboBox()
                w.setEditable(True)
                w.addItems([""] + METAUX)
            else:
                w = QLineEdit()
            self.inputs[key] = w
            self.form.addRow(label, w)
        self.sku = QLineEdit()
        self.sku.setPlaceholderText("automatique si vide (ex. MFC43)")
        self.form.addRow("SKU", self.sku)
        qty = QSpinBox()
        qty.setRange(1, 999)
        self.inputs["quantite"] = qty
        self.form.addRow("Quantité", qty)

        for box in self.subtag_boxes:
            self.subtags_layout.removeWidget(box)
            box.setParent(None)
            box.deleteLater()
        self.subtag_boxes = []
        for tag in kind.subtags:
            box = QCheckBox(tag)
            self.subtags_layout.addWidget(box)
            self.subtag_boxes.append(box)
        self.subtags_box.setVisible(bool(kind.subtags))

    # --- lecture / écriture ------------------------------------------------
    def set_product(self, product: Product | None, kind: ProductKind | None) -> None:
        if self.product is not None:
            self.commit()
        self.product, self.kind = product, kind
        self.setEnabled(product is not None)
        if product is None or kind is None:
            self.status.clear()
            return
        self._build(kind)
        for key, w in self.inputs.items():
            value = getattr(product.info, key)
            if isinstance(w, QSpinBox):
                w.setValue(int(value or 1))
            elif isinstance(w, QComboBox):
                w.setCurrentText(str(value))
            else:
                w.setText(str(value))
        for box in self.subtag_boxes:
            box.setChecked(box.text() in product.info.sous_categories)
        self.sku.setText(product.sku)
        self.quick.clear()
        self.refresh_status()

    def commit(self) -> None:
        """Recopie le formulaire dans le produit."""
        if not self.product:
            return
        info = self.product.info
        for key, w in self.inputs.items():
            if isinstance(w, QSpinBox):
                setattr(info, key, w.value())
            elif isinstance(w, QComboBox):
                setattr(info, key, w.currentText().strip())
            else:
                setattr(info, key, w.text().strip())
        info.sous_categories = [b.text() for b in self.subtag_boxes if b.isChecked()]
        self.product.sku = self.sku.text().strip().upper()
        self.changed.emit(self.product.id)

    def refresh_status(self) -> None:
        p = self.product
        if not p:
            return
        text = f"<b>{STATUS_LABELS.get(p.status, p.status)}</b> · {len(p.photos)} photo(s), {len(p.videos)} vidéo(s)"
        if p.sku:
            text += f" · SKU {p.sku}"
        if p.error:
            text += f"<br><span style='color:#c0392b'>{p.error}</span>"
        self.status.setText(text)
        self.folder_btn.setEnabled(bool(p.folder))

    def _apply_quick(self) -> None:
        values = parse_line(self.quick.text())
        valid = {f.name for f in fields(ProductInfo)}
        for key, value in values.items():
            w = self.inputs.get(key)
            if key not in valid or w is None:
                continue
            if isinstance(w, QComboBox):
                w.setCurrentText(value)
            else:
                w.setText(value)
        self.quick.clear()
        self.commit()

    def _process(self) -> None:
        if self.product:
            self.commit()
            self.processRequested.emit(self.product.id)
