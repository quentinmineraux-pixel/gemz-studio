"""Éditeur de fiche : textes par langue, tags, plateformes et stock d'un produit du catalogue."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ..catalog import A_METTRE_A_JOUR, A_PUBLIER, EPUISE, ERREUR, PLATFORM_LABELS, PLATFORMS, PUBLIE, STATE_LABELS
from ..listing import ai
from ..listing.builder import ETSY_TAG_LEN, ETSY_TAG_MAX, fill_texts, price_lines
from ..listing.model import SOURCE_IA, SOURCE_MANUEL, SOURCE_MODELE
from ..listing.prices import fmt_eur
from ..listing.texts import LANG_LABELS, LANGS
from ..publishing import write_listing
from .common import run_in_background

INFO_FIELDS = [("nom", "Nom"), ("variete", "Variété"), ("poids_g", "Poids (g)"), ("dimensions", "Dimensions (cm)"),
               ("pays", "Pays"), ("region", "Région / mine")]

PIECE_FIELDS = [
    ("nom", "Nom de la pierre", False),
    ("particularite", "Particularité (titre)", False),
    ("pays", "Pays (traduit)", False),
    ("accroche", "Accroche", True),
    ("aspect", "Aspect visuel *", True),
    ("anecdote", "Anecdote scientifique", True),
    ("lithotherapie", "Lithothérapie *", True),
    ("seo_description", "Description SEO", True),
    ("alt", "Texte des photos (alt)", False),
]


def split_list(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


class ListingEditor(QDialog):
    def __init__(self, sku: str, app, parent=None):
        super().__init__(parent)
        self.app = app   # fenêtre principale : settings, catalog, engine
        self.item = app.catalog.get(sku)
        self.setWindowTitle(f"Fiche {sku}")
        self.resize(1300, 900)
        self.inputs: dict[str, dict[str, QWidget]] = {}
        self.dirty_texts = False   # retouché à la main
        self.regenerated = False   # nouveaux textes de l'IA pas encore enregistrés

        root = QVBoxLayout(self)
        splitter = QSplitter()
        root.addWidget(splitter, 1)

        # --- textes par langue -------------------------------------------
        self.lang_tabs = QTabWidget()
        for lang in LANGS:
            page = QWidget()
            form = QFormLayout(page)
            self.inputs[lang] = {}
            for key, label, long in PIECE_FIELDS:
                if key == "pays" and lang == "fr":
                    continue
                w = QPlainTextEdit() if long else QLineEdit()
                if long:
                    w.setMinimumHeight(70 if key != "aspect" else 110)
                    w.textChanged.connect(self._edited)
                else:
                    w.textEdited.connect(self._edited)
                self.inputs[lang][key] = w
                form.addRow(label, w)
            self.lang_tabs.addTab(page, LANG_LABELS[lang])
        self.lang_tabs.currentChanged.connect(self._render_preview)
        splitter.addWidget(self.lang_tabs)

        # --- aperçu --------------------------------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("Aperçu (langue de l'onglet)"))
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        rl.addWidget(self.preview, 1)
        splitter.addWidget(right)
        splitter.setSizes([700, 600])

        # --- tags, plateformes, stock --------------------------------------
        bottom = QHBoxLayout()
        piece = QGroupBox("La pièce (sert au titre et à la description)")
        pf = QFormLayout(piece)
        self.info_inputs: dict[str, QLineEdit] = {}
        for key, label in INFO_FIELDS:
            w = QLineEdit(str(getattr(self.item.info, key)))
            w.textEdited.connect(self._edited)
            self.info_inputs[key] = w
            pf.addRow(label, w)
        kind = app.settings.kind(self.item.kind_id)
        self.subtag_boxes: list[QCheckBox] = []
        if kind.subtags:
            row = QHBoxLayout()
            for tag in kind.subtags:
                box = QCheckBox(tag)
                box.setChecked(tag in self.item.info.sous_categories)
                box.toggled.connect(self._edited)
                self.subtag_boxes.append(box)
                row.addWidget(box)
            row.addStretch(1)
            pf.addRow("Sous-catégories", row)
        bottom.addWidget(piece, 3)

        tags = QGroupBox("Tags")
        tl = QFormLayout(tags)
        self.etsy_tags = QLineEdit()
        self.etsy_tags.textEdited.connect(self._edited)
        self.etsy_count = QLabel()
        self.pierres = QLineEdit()
        self.pierres.setPlaceholderText("pierres principales, séparées par des virgules")
        self.pierres.textEdited.connect(self._edited)
        self.shopify_tags = QLabel()
        self.shopify_tags.setWordWrap(True)
        tl.addRow("Tags Etsy", self.etsy_tags)
        tl.addRow("", self.etsy_count)
        tl.addRow("Pierres", self.pierres)
        tl.addRow("Tags Shopify", self.shopify_tags)
        bottom.addWidget(tags, 3)

        plat = QGroupBox("Publication")
        pl = QFormLayout(plat)
        self.wanted: dict[str, QCheckBox] = {}
        self.state_labels: dict[str, QLabel] = {}
        for name in PLATFORMS:
            row = QHBoxLayout()
            box = QCheckBox(PLATFORM_LABELS[name])
            label = QLabel()
            label.setTextFormat(Qt.RichText)
            label.setOpenExternalLinks(True)
            label.setWordWrap(True)
            row.addWidget(box)
            row.addWidget(label, 1)
            self.wanted[name], self.state_labels[name] = box, label
            pl.addRow(row)
        self.wanted["shopify"].setEnabled(False)   # Shopify est la référence, toujours publié
        self.stock = QSpinBox()
        self.stock.setRange(0, 999)
        pl.addRow("Stock", self.stock)
        bottom.addWidget(plat, 2)
        root.addLayout(bottom)

        self.missing = QLabel()
        self.missing.setWordWrap(True)
        root.addWidget(self.missing)

        buttons = QHBoxLayout()
        self.regen_btn = QPushButton("Régénérer les textes (IA)")
        self.regen_btn.clicked.connect(self._regenerate)
        folder = QPushButton("Ouvrir le dossier")
        folder.clicked.connect(lambda: self.item.folder and QDesktopServices.openUrl(QUrl.fromLocalFile(self.item.folder)))
        save = QPushButton("Enregistrer")
        save.setDefault(True)
        save.clicked.connect(self._save)
        close = QPushButton("Fermer")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.regen_btn)
        buttons.addWidget(folder)
        buttons.addStretch(1)
        buttons.addWidget(save)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self._timer = QTimer(self, singleShot=True, interval=400)
        self._timer.timeout.connect(self._recompute)
        self._load()

    # --- affichage ---------------------------------------------------------
    def _load(self) -> None:
        listing = self.item.listing
        for lang in LANGS:
            pieces = listing.pieces.get(lang, {})
            for key, w in self.inputs[lang].items():
                w.blockSignals(True)
                (w.setPlainText if isinstance(w, QPlainTextEdit) else w.setText)(pieces.get(key, ""))
                w.blockSignals(False)
        self.etsy_tags.setText(", ".join(listing.etsy_tags))
        self.pierres.setText(", ".join(listing.pierres))
        for name in PLATFORMS:
            state = self.item.state(name)
            self.wanted[name].setChecked(state.wanted or name == "shopify")
            if state.remote_id:
                self.wanted[name].setEnabled(False)
                self.wanted[name].setToolTip("Déjà en ligne : mets le stock à 0 pour la retirer de la vente.")
        self.stock.setValue(self.item.stock)
        self._refresh_states()
        self._render_preview()
        self.dirty_texts = False

    def _refresh_states(self) -> None:
        prices = price_lines(self.item.info, self.app.settings)
        price_text = {"shopify": fmt_eur(prices["shopify"]), "etsy": fmt_eur(prices["etsy"]),
                      "ebay": f"{fmt_eur(prices['ebay_fr'])} · ${prices['ebay_us_usd']:.2f}"}
        platforms = getattr(self.app.engine, "platforms", {})
        for name in PLATFORMS:
            state = self.item.state(name)
            text = f"{price_text[name]} · <b>{STATE_LABELS.get(state.status, state.status)}</b>"
            if state.url:
                text += f" · <a href='{state.url}'>voir</a>"
            platform = platforms.get(name)
            if platform is not None and not platform.configured():
                text += " · <span style='color:#888'>connexion à faire</span>"
            if state.error:
                text += f"<br><span style='color:#c0392b'>{state.error}</span>"
            self.state_labels[name].setText(text)

    def _edited(self, *_):
        self.dirty_texts = True
        self._timer.start()

    def _collect_info(self):
        info = copy.deepcopy(self.item.info)
        for key, w in self.info_inputs.items():
            setattr(info, key, w.text().strip())
        if self.subtag_boxes:
            info.sous_categories = [b.text() for b in self.subtag_boxes if b.isChecked()]
        return info

    def _collect_listing(self):
        listing = copy.deepcopy(self.item.listing)
        for lang in LANGS:
            pieces = dict(listing.pieces.get(lang, {}))
            for key, w in self.inputs[lang].items():
                pieces[key] = (w.toPlainText() if isinstance(w, QPlainTextEdit) else w.text()).strip()
            listing.pieces[lang] = pieces
        listing.etsy_tags = split_list(self.etsy_tags.text())
        listing.pierres = [p.lower() for p in split_list(self.pierres.text())]
        return fill_texts(listing, self._collect_info(), self.app.settings.kind(self.item.kind_id), self.app.settings)

    def _recompute(self) -> None:
        self._render_preview()

    def _render_preview(self, *_):
        listing = self._collect_listing()
        lang = LANGS[max(0, self.lang_tabs.currentIndex())]
        text = listing.texts[lang]
        self.preview.setHtml(
            f"<h2>{text.title}</h2>"
            f"<p style='color:#1a0dab'>{text.seo_title}</p><p style='color:#555'>{text.seo_description}</p><hr>"
            f"{text.description}")
        long_tags = [t for t in listing.etsy_tags if len(t) > ETSY_TAG_LEN]
        self.etsy_count.setText(f"{len(listing.etsy_tags)}/{ETSY_TAG_MAX} tags, {ETSY_TAG_LEN} caractères max"
                                + (" (raccourcis à l'enregistrement)" if long_tags else ""))
        self.shopify_tags.setText(", ".join(listing.tags))
        missing = listing.missing()
        source = {SOURCE_MODELE: "modèle (sans IA)", SOURCE_MANUEL: "manuelle", SOURCE_IA: "IA"}
        origin = source.get(listing.source, listing.source)
        if missing:
            self.missing.setText(f"<span style='color:#c0392b'>Fiche incomplète, publication en attente : "
                                 f"{', '.join(missing)}</span> · rédaction {origin}")
        else:
            self.missing.setText(f"Fiche complète, prête à publier · rédaction {origin}"
                                 + (f" · coût IA {listing.cost_eur:.3f} €" if listing.cost_eur else ""))

    # --- actions -----------------------------------------------------------
    def _save(self) -> None:
        listing = self._collect_listing()
        if self.dirty_texts:
            listing.edited = True
            if listing.source == SOURCE_MODELE and listing.ready:
                listing.source = SOURCE_MANUEL
        wanted = {name: box.isChecked() for name, box in self.wanted.items()}
        stock = self.stock.value()
        texts_changed = self.dirty_texts or self.regenerated

        info = self._collect_info()

        def apply(item):
            item.listing = listing
            item.info = info
            for name in PLATFORMS:
                state = item.state(name)
                state.wanted = wanted[name] or name == "shopify" or bool(state.remote_id)
                if state.remote_id and texts_changed and state.status in (PUBLIE, EPUISE):
                    state.status = A_METTRE_A_JOUR
                state.next_try = 0.0   # nouvel essai tout de suite
                if state.status == ERREUR:
                    state.attempts = 0
            if stock != item.stock:
                item.stock = stock
                for name in PLATFORMS:
                    state = item.state(name)
                    if state.remote_id:
                        state.extra["stock_a_envoyer"] = True
                        if stock == 0:
                            state.status = EPUISE
                        elif state.status == EPUISE:
                            state.status = PUBLIE
                    elif state.status == EPUISE:
                        state.status = A_PUBLIER

        self.item = self.app.catalog.update(self.item.sku, apply) or self.item
        self.app.catalog.log("info", "Fiche modifiée à la main", self.item.sku)
        if self.app.engine:
            self.app.engine.wake()
        self.dirty_texts = False
        self.accept()

    def _regenerate(self) -> None:
        settings = self.app.settings
        if not ai.api_key():
            QMessageBox.information(self, "IA", "Renseigne d'abord la clé Anthropic dans Réglages → IA.")
            return
        estimate = ai.estimate_eur(settings, min(settings.ai_photos, len(self.item.photos)))
        spent = ai.month_cost(self.app.catalog)
        question = (f"Réécrire les textes avec l'IA ?\nCoût estimé : {estimate:.3f} € "
                    f"(déjà {spent:.2f} € sur {settings.ai_monthly_cap_eur:.2f} € ce mois-ci).")
        if self.item.listing.edited or self.dirty_texts:
            question += "\n\nTes retouches à la main seront remplacées."
        if QMessageBox.question(self, "IA", question) != QMessageBox.Yes:
            return
        work = copy.deepcopy(self.item)
        work.info = self._collect_info()
        self.regen_btn.setEnabled(False)
        self.regen_btn.setText("Rédaction en cours…")

        def done(message, error):
            self.regen_btn.setEnabled(True)
            self.regen_btn.setText("Régénérer les textes (IA)")
            if error:
                QMessageBox.warning(self, "IA", str(error))
                return
            if message:
                QMessageBox.warning(self, "IA", message)
                return
            self.item.listing = work.listing
            self._load()
            self.regenerated = True

        run_in_background(lambda: write_listing(work, settings, self.app.catalog, use_ai=True), done)
