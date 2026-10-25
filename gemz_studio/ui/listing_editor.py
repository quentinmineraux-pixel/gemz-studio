"""Éditeur de fiche : textes par langue, photos, prix, tags, plateformes et stock d'un produit du catalogue."""
from __future__ import annotations

import copy
import dataclasses
import shutil
from html import escape
from pathlib import Path

import shiboken6
from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QSplitter, QTabWidget, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ..catalog import A_METTRE_A_JOUR, A_PUBLIER, EPUISE, ERREUR, PLATFORM_LABELS, PLATFORMS, PUBLIE, STATE_LABELS
from ..imaging import export_photo
from ..listing import ai, vocab
from ..listing.builder import ETSY_TAG_LEN, ETSY_TAG_MAX, fill_texts, price_lines
from ..listing.model import SOURCE_IA, SOURCE_IMPORT, SOURCE_MANUEL, SOURCE_MODELE, LangText
from ..listing.prices import fmt_eur, parse_price, price_text
from ..listing.texts import LANG_LABELS, LANGS
from ..platforms.shopify import spread_stock
from ..publishing import shop_images, write_listing
from .common import run_in_background
from .thumbcache import ThumbCache, square

PREVIOUS, NEXT = 2, 3   # codes de retour : passer à la pièce voisine du catalogue

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
METAFIELD_LABELS = {"pierre": "Pierre", "provenance": "Provenance", "forme": "Forme"}


def split_list(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


def edit_listing(sku: str, app, parent, can_navigate: bool = False) -> int:
    """Ouvre la fiche ; la fenêtre est libérée en sortant (on peut enchaîner des centaines de fiches)."""
    editor = ListingEditor(sku, app, parent, can_navigate)
    try:
        return editor.exec()
    finally:
        if editor.thumbs.parent() is editor:
            editor.thumbs.shutdown()
        shiboken6.delete(editor)


class ListingEditor(QDialog):
    def __init__(self, sku: str, app, parent=None, can_navigate: bool = False):
        super().__init__(parent)
        self.app = app   # fenêtre principale : settings, catalog, engine
        self.item = app.catalog.get(sku)
        self.setWindowTitle(f"Fiche {sku}")
        self.resize(1320, 920)
        self.inputs: dict[str, dict[str, QWidget]] = {}
        self.dirty = False          # modification pas encore enregistrée
        self.dirty_texts = False    # textes retouchés à la main
        self.regenerated = False    # nouveaux textes de l'IA pas encore enregistrés
        self.busy = False           # rédaction par l'IA en cours
        self.photos = list(self.item.photos)
        # Miniatures du catalogue si la fiche en vient (déjà prêtes), sinon les siennes
        self.thumbs = getattr(parent, "thumbs", None) or ThumbCache(self)
        self.thumbs.ready.connect(self._thumb_ready)
        kind = app.settings.kind(self.item.kind_id)

        root = QVBoxLayout(self)
        self.main_tabs = QTabWidget()
        root.addWidget(self.main_tabs, 1)

        # --- textes : fiche importée telle quelle, ou morceaux rédigés par langue --
        splitter = QSplitter()
        self.lang_tabs = QTabWidget()
        raw_page = QWidget()
        raw = QFormLayout(raw_page)
        hint = QLabel("Fiche reprise telle quelle de la boutique Shopify (rien n'y est changé tant que tu ne la "
                      "modifies pas). « Rédiger avec l'IA » la remet au format GemZ, en français, anglais, espagnol "
                      "et allemand, en gardant ses informations.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")
        raw.addRow(hint)
        self.raw_title = QLineEdit()
        self.raw_desc = QPlainTextEdit()
        self.raw_desc.setMinimumHeight(260)
        self.raw_seo_title = QLineEdit()
        self.raw_seo_desc = QPlainTextEdit()
        self.raw_seo_desc.setMaximumHeight(80)
        for w in (self.raw_title, self.raw_seo_title):
            w.textEdited.connect(self._edited)
        for w in (self.raw_desc, self.raw_seo_desc):
            w.textChanged.connect(self._edited)
        raw.addRow("Titre", self.raw_title)
        raw.addRow("Description (HTML)", self.raw_desc)
        raw.addRow("Titre SEO", self.raw_seo_title)
        raw.addRow("Description SEO", self.raw_seo_desc)
        self.lang_tabs.addTab(raw_page, "Français (repris de Shopify)")
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
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("Aperçu (langue de l'onglet)"))
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        rl.addWidget(self.preview, 1)
        splitter.addWidget(right)
        splitter.setSizes([700, 600])
        self.main_tabs.addTab(splitter, "Textes")

        # --- photos ----------------------------------------------------------
        photos_page = QWidget()
        ph = QVBoxLayout(photos_page)
        self.photo_hint = QLabel()
        self.photo_hint.setWordWrap(True)
        self.photo_hint.setStyleSheet("color: palette(placeholder-text);")
        ph.addWidget(self.photo_hint)
        self.photo_list = QListWidget()
        self.photo_list.setViewMode(QListView.IconMode)
        self.photo_list.setIconSize(QSize(170, 170))
        self.photo_list.setGridSize(QSize(190, 210))
        self.photo_list.setMovement(QListView.Static)
        self.photo_list.setResizeMode(QListView.Adjust)
        self.photo_list.setSpacing(6)
        ph.addWidget(self.photo_list, 1)
        prow = QHBoxLayout()
        self.photo_buttons = []
        for text, slot in (("★ Photo principale", self._photo_main), ("◀ Avancer", lambda: self._photo_move(-1)),
                           ("Reculer ▶", lambda: self._photo_move(1)), ("Retirer de la fiche", self._photo_remove),
                           ("Ajouter des photos…", self._photo_add)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, s=slot: s())
            prow.addWidget(b)
            self.photo_buttons.append(b)
        prow.addStretch(1)
        ph.addLayout(prow)
        self.main_tabs.addTab(photos_page, "Photos")

        # --- la pièce, tags, publication ---------------------------------------
        bottom = QHBoxLayout()
        piece = QGroupBox("La pièce (sert au titre et à la description)")
        pf = QFormLayout(piece)
        self.info_inputs: dict[str, QLineEdit] = {}
        for key, label in INFO_FIELDS:
            w = QLineEdit(str(getattr(self.item.info, key)))
            w.textEdited.connect(self._edited)
            self.info_inputs[key] = w
            pf.addRow(label, w)
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

        tags = QGroupBox("Tags et filtres")
        tl = QFormLayout(tags)
        self.etsy_tags = QLineEdit()
        self.etsy_tags.textEdited.connect(self._edited)
        self.etsy_count = QLabel()
        self.pierres = QLineEdit()
        self.pierres.setPlaceholderText("pierres principales, séparées par des virgules")
        self.pierres.textEdited.connect(self._edited)
        self.shopify_tags = QLabel()
        self.shopify_tags.setWordWrap(True)
        self.filters = QLabel()
        self.filters.setWordWrap(True)
        self.filters.setTextFormat(Qt.RichText)
        tl.addRow("Tags Etsy", self.etsy_tags)
        tl.addRow("", self.etsy_count)
        tl.addRow("Pierres", self.pierres)
        tl.addRow("Tags Shopify", self.shopify_tags)
        tl.addRow("Filtres Shopify", self.filters)
        bottom.addWidget(tags, 3)

        plat = QGroupBox("Prix, stock et publication")
        pl = QFormLayout(plat)
        prices = QHBoxLayout()
        self.price = QLineEdit(self.item.info.prix)
        self.price.setPlaceholderText("ex. 150")
        self.compare = QLineEdit(self.item.info.prix_barre)
        self.compare.setPlaceholderText("vide = pas de promo")
        self.compare.setToolTip("Prix barré affiché sur Shopify (promotion). Etsy et eBay gardent le prix normal majoré.")
        for w in (self.price, self.compare):
            w.setMaximumWidth(110)
            w.textEdited.connect(lambda _t: self._mark_dirty())
        prices.addWidget(self.price)
        prices.addWidget(QLabel("€   barré"))
        prices.addWidget(self.compare)
        prices.addWidget(QLabel("€"))
        prices.addStretch(1)
        pl.addRow("Prix Shopify", prices)
        self.wanted: dict[str, QCheckBox] = {}
        self.state_labels: dict[str, QLabel] = {}
        for name in PLATFORMS:
            row = QHBoxLayout()
            box = QCheckBox(PLATFORM_LABELS[name])
            box.toggled.connect(lambda _on: self._mark_dirty())
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
        self.stock.valueChanged.connect(lambda _v: self._mark_dirty())
        pl.addRow("Stock", self.stock)
        bottom.addWidget(plat, 3)
        root.addLayout(bottom)

        self.missing = QLabel()
        self.missing.setWordWrap(True)
        root.addWidget(self.missing)

        buttons = QHBoxLayout()
        if can_navigate:
            for text, code, tip in (("◀ Précédente", PREVIOUS, "Pièce précédente du catalogue (Alt+←)"),
                                    ("Suivante ▶", NEXT, "Pièce suivante du catalogue (Alt+→)")):
                b = QPushButton(text)
                b.setToolTip(tip)
                b.setShortcut("Alt+Left" if code == PREVIOUS else "Alt+Right")
                b.clicked.connect(lambda _c=False, c=code: self._navigate(c))
                buttons.addWidget(b)
        self.regen_btn = QPushButton()
        self.regen_btn.clicked.connect(self._regenerate)
        folder = QPushButton("Ouvrir le dossier")
        folder.setEnabled(bool(self.item.folder))
        folder.clicked.connect(lambda: self.item.folder and QDesktopServices.openUrl(QUrl.fromLocalFile(self.item.folder)))
        save = QPushButton("Enregistrer")
        save.setDefault(True)
        save.clicked.connect(lambda: self._save())
        close = QPushButton("Fermer")
        close.clicked.connect(self.reject)
        buttons.addWidget(self.regen_btn)
        buttons.addWidget(folder)
        buttons.addStretch(1)
        buttons.addWidget(save)
        buttons.addWidget(close)
        root.addLayout(buttons)

        self._timer = QTimer(self, singleShot=True, interval=400)
        self._timer.timeout.connect(self._render_preview)
        self.price.textEdited.connect(lambda _t: self._refresh_states())
        self._load()

    # --- affichage -----------------------------------------------------------
    @property
    def import_mode(self) -> bool:
        return self.item.listing.imported

    def _load(self) -> None:
        listing = self.item.listing
        self.lang_tabs.setTabVisible(0, self.import_mode)
        for i in range(1, self.lang_tabs.count()):
            self.lang_tabs.setTabVisible(i, not self.import_mode)
        self.lang_tabs.setCurrentIndex(0 if self.import_mode else 1)
        self.regen_btn.setText("Rédiger avec l'IA (format GemZ, 4 langues)" if self.import_mode
                               else "Régénérer les textes (IA)")
        fr = listing.texts["fr"]
        for w, value in ((self.raw_title, fr.title), (self.raw_seo_title, fr.seo_title)):
            w.setText(value)
        for w, value in ((self.raw_desc, fr.description), (self.raw_seo_desc, fr.seo_description)):
            w.blockSignals(True)
            w.setPlainText(value)
            w.blockSignals(False)
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
            box = self.wanted[name]
            box.blockSignals(True)
            box.setChecked(state.wanted or name == "shopify")
            box.blockSignals(False)
            if state.remote_id:
                box.setEnabled(False)
                box.setToolTip("Déjà en ligne : mets le stock à 0 pour la retirer de la vente.")
        self.stock.blockSignals(True)
        if spread_stock(self.item, self.item.state("shopify")):
            # Produit importé à plusieurs tailles : le stock de chaque taille se gère dans Shopify
            self.stock.setMaximum(max(0, self.item.stock))
            self.stock.setToolTip("Stock total des tailles, géré par taille dans Shopify : on peut seulement "
                                  "le baisser ici (la taille la mieux fournie est retirée en premier).")
        self.stock.setValue(self.item.stock)
        self.stock.blockSignals(False)
        self._fill_photos()
        self._refresh_states()
        self._render_preview()
        self.dirty = self.dirty_texts = False

    def _refresh_states(self) -> None:
        prices = price_lines(self._collect_info(), self.app.settings)
        price_text_ = {"shopify": fmt_eur(prices["shopify"]), "etsy": fmt_eur(prices["etsy"]),
                       "ebay": f"{fmt_eur(prices['ebay_fr'])} · ${prices['ebay_us_usd']:.2f}"}
        platforms = getattr(self.app.engine, "platforms", {})
        for name in PLATFORMS:
            state = self.item.state(name)
            text = f"{price_text_[name]} · <b>{STATE_LABELS.get(state.status, state.status)}</b>"
            if state.url:
                text += f" · <a href='{escape(state.url)}'>voir</a>"
            platform = platforms.get(name)
            if platform is not None and not platform.configured():
                text += " · <span style='color:#888'>connexion à faire</span>"
            if state.error:
                text += f"<br><span style='color:#c0392b'>{escape(state.error)}</span>"
            self.state_labels[name].setText(text)

    def _thumb_ready(self, source: str) -> None:
        if any(self.photo_list.item(i).data(Qt.UserRole) == source for i in range(self.photo_list.count())):
            self._fill_photos()

    def _fill_photos(self) -> None:
        local = bool(self.photos) or self.item.origin != "import"
        sources = self.photos if local else shop_images(self.item)
        current = self.photo_list.currentRow()
        self.photo_list.clear()
        for i, source in enumerate(sources):
            entry = QListWidgetItem(QIcon(square(self.thumbs.get(source), 170)),
                                    "★ principale" if i == 0 else str(i + 1))
            entry.setData(Qt.UserRole, source)
            self.photo_list.addItem(entry)
        if 0 <= current < self.photo_list.count():
            self.photo_list.setCurrentRow(current)
        editable = local and bool(self.item.folder)
        for b in self.photo_buttons:
            b.setEnabled(editable)
        if editable:
            self.photo_hint.setText("Ordre des photos sur les annonces (la première est la photo principale). "
                                    "À l'enregistrement, les photos sont remplacées sur Shopify, Etsy et eBay.")
        else:
            self.photo_hint.setText("Photos de la boutique Shopify (pièce importée) : modifie-les dans Shopify, "
                                    "puis « Importer / mettre à jour depuis Shopify » dans le catalogue.")

    def _mark_dirty(self) -> None:
        self.dirty = True

    def _edited(self, *_):
        self.dirty = self.dirty_texts = True
        self._timer.start()

    def _collect_info(self):
        info = copy.deepcopy(self.item.info)
        for key, w in self.info_inputs.items():
            setattr(info, key, w.text().strip())
        if self.subtag_boxes:
            info.sous_categories = [b.text() for b in self.subtag_boxes if b.isChecked()]
        for attr, w in (("prix", self.price), ("prix_barre", self.compare)):
            text = w.text().strip()
            try:
                setattr(info, attr, price_text(parse_price(text)) if text else "")
            except ValueError:
                setattr(info, attr, text)
        return info

    def _collect_listing(self):
        listing = copy.deepcopy(self.item.listing)
        if self.import_mode:
            fr = listing.texts["fr"]
            listing.texts["fr"] = LangText(title=self.raw_title.text().strip(), description=self.raw_desc.toPlainText().strip(),
                                           seo_title=self.raw_seo_title.text().strip(),
                                           seo_description=self.raw_seo_desc.toPlainText().strip(), alt=fr.alt)
        else:
            for lang in LANGS:
                pieces = dict(listing.pieces.get(lang, {}))
                for key, w in self.inputs[lang].items():
                    pieces[key] = (w.toPlainText() if isinstance(w, QPlainTextEdit) else w.text()).strip()
                listing.pieces[lang] = pieces
        listing.etsy_tags = split_list(self.etsy_tags.text())
        listing.pierres = [p.lower() for p in split_list(self.pierres.text())]
        return fill_texts(listing, self._collect_info(), self.app.settings.kind(self.item.kind_id), self.app.settings)

    def _render_preview(self, *_):
        listing = self._collect_listing()
        index = self.lang_tabs.currentIndex()
        lang = "fr" if index <= 0 else LANGS[index - 1]
        text = listing.texts[lang]
        self.preview.setHtml(
            f"<h2>{escape(text.title)}</h2>"
            f"<p style='color:#1a0dab'>{escape(text.seo_title)}</p><p style='color:#555'>{escape(text.seo_description)}</p>"
            f"<hr>{text.description}")
        long_tags = [t for t in listing.etsy_tags if len(t) > ETSY_TAG_LEN]
        self.etsy_count.setText(f"{len(listing.etsy_tags)}/{ETSY_TAG_MAX} tags, {ETSY_TAG_LEN} caractères max"
                                + (" (raccourcis à l'enregistrement)" if long_tags else "")
                                + (" · vide : ceux de l'annonce Etsy sont gardés" if not listing.etsy_tags else ""))
        self.shopify_tags.setText(", ".join(listing.tags))
        if listing.imported:
            self.filters.setText("<span style='color:#888'>Valeurs déjà dans la boutique, gardées telles quelles.</span>")
        else:
            parts = []
            for key, values in listing.metafields.items():
                shown = [f"{v} <span style='color:#d68910'>(nouvelle)</span>" if vocab.is_new(key, v) else v
                         for v in values]
                if shown:
                    parts.append(f"{METAFIELD_LABELS.get(key, key)} : {', '.join(shown)}")
            if listing.couleurs:
                parts.append("Couleur : " + ", ".join(listing.couleurs))
            self.filters.setText(" · ".join(parts) + (
                "<br><span style='color:#888'>« nouvelle » : valeur pas encore utilisée dans la boutique, "
                "elle créera une case de filtre en plus.</span>" if "(nouvelle)" in " ".join(parts) else ""))
        missing = listing.missing()
        source = {SOURCE_MODELE: "modèle (sans IA)", SOURCE_MANUEL: "manuelle", SOURCE_IA: "IA",
                  SOURCE_IMPORT: "reprise de Shopify"}
        origin = source.get(listing.source, listing.source)
        if missing:
            self.missing.setText(f"<span style='color:#c0392b'>Fiche incomplète, publication en attente : "
                                 f"{', '.join(missing)}</span> · rédaction {origin}")
        else:
            self.missing.setText(f"Fiche complète, prête à publier · rédaction {origin}"
                                 + (f" · coût IA {listing.cost_eur:.3f} €" if listing.cost_eur else ""))

    # --- photos ----------------------------------------------------------------
    def _photo_main(self) -> None:
        row = self.photo_list.currentRow()
        if row > 0:
            self.photos.insert(0, self.photos.pop(row))
            self._photos_changed(0)

    def _photo_move(self, delta: int) -> None:
        row = self.photo_list.currentRow()
        target = row + delta
        if row >= 0 and 0 <= target < len(self.photos):
            self.photos[row], self.photos[target] = self.photos[target], self.photos[row]
            self._photos_changed(target)

    def _photo_remove(self) -> None:
        row = self.photo_list.currentRow()
        if row < 0:
            return
        if len(self.photos) == 1:
            QMessageBox.information(self, "Photos", "Il faut au moins une photo sur la fiche.")
            return
        self.photos.pop(row)
        self._photos_changed(min(row, len(self.photos) - 1))

    def _photo_add(self) -> None:
        files, _filter = QFileDialog.getOpenFileNames(self, "Ajouter des photos", "",
                                                      "Photos (*.jpg *.jpeg *.png *.tif *.tiff *.heic *.webp)")
        if not files:
            return
        web, originals = Path(self.item.folder) / "web", Path(self.item.folder) / "originaux"
        originals.mkdir(parents=True, exist_ok=True)
        number = 1
        for src in files:
            while (web / f"{self.item.sku}_{number:02d}.jpg").exists() or \
                    str(web / f"{self.item.sku}_{number:02d}.jpg") in self.photos:
                number += 1
            dest = web / f"{self.item.sku}_{number:02d}.jpg"
            try:
                export_photo(src, dest, self.app.settings)   # même retouche et même logo que les autres
                shutil.copy2(src, originals / f"{number:02d}_{Path(src).name}")
            except OSError as e:
                QMessageBox.warning(self, "Photos", f"{Path(src).name} : {e}")
                continue
            self.photos.append(str(dest))
        self._photos_changed(len(self.photos) - 1)

    def _photos_changed(self, row: int) -> None:
        self.dirty = True
        self._fill_photos()
        self.photo_list.setCurrentRow(row)

    # --- actions ---------------------------------------------------------------
    def _wait_for_ai(self) -> bool:
        """Vrai (et un message) tant que l'IA rédige : ses textes, déjà payés, ne doivent pas être perdus."""
        if self.busy:
            QMessageBox.information(self, "IA", "Rédaction par l'IA en cours : attends qu'elle se termine "
                                    "(quelques secondes), puis enregistre.")
        return self.busy

    def _navigate(self, code: int) -> None:
        if self._wait_for_ai():
            return
        if self.dirty or self.regenerated:
            answer = QMessageBox.question(self, "Fiche", "Enregistrer les modifications de cette fiche ?",
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel or (answer == QMessageBox.Save and not self._save(close=False)):
                return
        self.done(code)

    def reject(self) -> None:
        if self._wait_for_ai():
            return
        if self.dirty or self.regenerated:
            answer = QMessageBox.question(self, "Fiche", "Enregistrer les modifications avant de fermer ?",
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel:
                return
            if answer == QMessageBox.Save:
                self._save()   # ferme la fiche si l'enregistrement passe
                return
        super().reject()

    def _check_prices(self) -> bool:
        try:
            price = parse_price(self.price.text())
        except ValueError:
            QMessageBox.warning(self, "Prix", "Prix illisible : écris par exemple 150 ou 149,90.")
            return False
        if self.compare.text().strip():
            try:
                compare = parse_price(self.compare.text())
            except ValueError:
                QMessageBox.warning(self, "Prix", "Prix barré illisible : écris par exemple 180, ou laisse vide.")
                return False
            if compare <= price:
                QMessageBox.warning(self, "Prix", "Le prix barré doit être plus élevé que le prix (sinon laisse-le vide).")
                return False
        return True

    def _save(self, close: bool = True) -> bool:
        if self._wait_for_ai() or not self._check_prices():
            return False
        listing = self._collect_listing()
        if self.dirty_texts:
            listing.edited = True
            if listing.source == SOURCE_MODELE and listing.ready:
                listing.source = SOURCE_MANUEL
        wanted = {name: box.isChecked() for name, box in self.wanted.items()}
        stock = self.stock.value()
        info = self._collect_info()
        photos = list(self.photos)
        changes: list[str] = []

        def apply(item):
            changes.clear()
            texts_changed = (self.dirty_texts or self.regenerated) and item.listing.to_dict() != listing.to_dict()
            price_changed = (item.info.prix, item.info.prix_barre) != (info.prix, info.prix_barre)
            photos_changed = item.photos != photos
            same_prices = dataclasses.replace(info, prix=item.info.prix, prix_barre=item.info.prix_barre)
            if texts_changed:
                changes.append("textes")
            elif item.info != same_prices:
                changes.append("informations")
            if price_changed:
                changes.append(f"prix {info.prix} €" + (f" (barré {info.prix_barre} €)" if info.prix_barre else ""))
            if photos_changed:
                changes.append("photos")
            item.listing = listing
            item.info = info
            for name in PLATFORMS:
                state = item.state(name)
                if wanted[name] and not state.wanted:
                    changes.append(f"ajoutée à {PLATFORM_LABELS[name]}")
                state.wanted = wanted[name] or name == "shopify" or bool(state.remote_id)
                if state.remote_id and (texts_changed or photos_changed) and state.status in (PUBLIE, EPUISE):
                    state.status = A_METTRE_A_JOUR
                if state.remote_id and photos_changed:
                    state.extra["media_sig"] = "modifiées dans la fiche"   # remplacées à la prochaine mise à jour
                if state.remote_id and price_changed:
                    state.extra["prix_a_envoyer"] = True
                state.next_try = 0.0   # nouvel essai tout de suite
                if state.status == ERREUR:
                    state.attempts = 0
            item.photos = photos
            if stock != item.stock:
                changes.append(f"stock {stock}")
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
        if changes:
            self.app.catalog.log("info", "Fiche modifiée : " + ", ".join(dict.fromkeys(changes)), self.item.sku)
            saved = getattr(self.app, "catalog_item_saved", None)
            if saved:
                saved(self.item)   # la séance en cours garde les mêmes informations
        engine = self.app.engine
        if engine:
            engine.push_soon()
        self.dirty = self.dirty_texts = self.regenerated = False
        if close:
            self.accept()
        return True

    def _regenerate(self) -> None:
        settings = self.app.settings
        if not ai.api_key():
            QMessageBox.information(self, "IA", "Renseigne d'abord la clé Anthropic dans Réglages → IA.")
            return
        photos = len(self.photos) or len(shop_images(self.item))
        estimate = ai.estimate_eur(settings, min(max(1, settings.ai_photos), photos))
        spent = ai.month_cost(self.app.catalog)
        question = (f"{'Rédiger' if self.import_mode else 'Réécrire'} les textes avec l'IA ?\n"
                    f"Coût estimé : {estimate:.3f} € (déjà {spent:.2f} € sur {settings.ai_monthly_cap_eur:.2f} € "
                    "ce mois-ci).")
        if self.import_mode:
            question += "\n\nLa fiche passera au format GemZ (4 langues), avec les informations du texte actuel."
        elif self.item.listing.edited or self.dirty_texts:
            question += "\n\nTes retouches à la main seront remplacées."
        if QMessageBox.question(self, "IA", question) != QMessageBox.Yes:
            return
        work = copy.deepcopy(self.item)
        work.info = self._collect_info()
        work.photos = list(self.photos)
        if self.import_mode:
            work.listing = self._collect_listing()   # texte retouché ici compris
        self.regen_btn.setEnabled(False)
        self.regen_btn.setText("Rédaction en cours…")
        self.busy = True

        def done(message, error):
            self.busy = False
            self.regen_btn.setEnabled(True)
            if error or message:
                self.regen_btn.setText("Rédiger avec l'IA (format GemZ, 4 langues)" if self.import_mode
                                       else "Régénérer les textes (IA)")
                QMessageBox.warning(self, "IA", str(error or message))
                return
            self.item.listing = work.listing
            self._load()
            self.regenerated = True
            self.dirty = True

        run_in_background(lambda: write_listing(work, settings, self.app.catalog, use_ai=True), done)
