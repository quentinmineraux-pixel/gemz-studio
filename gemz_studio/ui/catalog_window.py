"""Catalogue (état de chaque produit sur chaque plateforme) et journal des ventes et publications."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..catalog import A_PUBLIER, EPUISE, ERREUR, PLATFORM_LABELS, PLATFORMS, PUBLIE, STATE_LABELS
from ..importer import import_shopify, link_etsy, match_etsy
from .common import run_in_background
from .listing_editor import ListingEditor

STATE_COLORS = {PUBLIE: "#1e8449", ERREUR: "#c0392b", EPUISE: "#7f8c8d", A_PUBLIER: "#b9770e"}
EVENT_FILTERS = [("Tout", None), ("Ventes", ("vente",)), ("Erreurs", ("erreur",)),
                 ("Publications", ("publication",)), ("Synchro du stock", ("synchro",))]


class _Relay(QObject):
    """Le catalogue prévient depuis n'importe quel thread ; on rafraîchit dans l'interface."""
    changed = Signal()


def ro_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class CatalogWindow(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent, Qt.Window)
        self.app = app
        self.setWindowTitle("Catalogue et ventes")
        self.resize(1300, 750)
        self._relay = _Relay()
        self._timer = QTimer(self, singleShot=True, interval=500)
        self._timer.timeout.connect(self.refresh)
        self._relay.changed.connect(self._timer.start)
        self._listener = lambda _sku: self._relay.changed.emit()
        app.catalog.listeners.append(self._listener)

        root = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        # --- catalogue -------------------------------------------------------
        page = QWidget()
        pl = QVBoxLayout(page)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher (nom, SKU)…")
        self.search.textChanged.connect(self.refresh)
        self.only_problems = QCheckBox("Seulement à traiter (erreurs, en attente)")
        self.only_problems.toggled.connect(self.refresh)
        top.addWidget(self.search, 1)
        top.addWidget(self.only_problems)
        pl.addLayout(top)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["SKU", "Produit", "Stock", "Prix", "Fiche", "Shopify", "Etsy", "eBay"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self.open_listing)
        pl.addWidget(self.table, 1)
        actions = QHBoxLayout()
        for text, slot in (("Ouvrir la fiche", self.open_listing), ("Ajouter à eBay", lambda: self.add_platform("ebay")),
                           ("Ajouter à Etsy", lambda: self.add_platform("etsy")), ("Réessayer maintenant", self.retry),
                           ("Synchroniser le stock", self.sync_now), ("Importer depuis Shopify", self.import_shopify),
                           ("Relier les annonces Etsy", self.link_etsy), ("Retirer du catalogue", self.remove)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            actions.addWidget(b)
        pl.addLayout(actions)
        tabs.addTab(page, "Catalogue")

        # --- journal ---------------------------------------------------------
        log_page = QWidget()
        ll = QVBoxLayout(log_page)
        self.filter = QComboBox()
        for label, _kinds in EVENT_FILTERS:
            self.filter.addItem(label)
        self.filter.currentIndexChanged.connect(self.refresh)
        ll.addWidget(self.filter)
        self.events = QTableWidget(0, 5)
        self.events.setHorizontalHeaderLabels(["Date", "Type", "Plateforme", "SKU", "Détail"])
        self.events.verticalHeader().setVisible(False)
        self.events.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.events.setEditTriggers(QAbstractItemView.NoEditTriggers)
        ll.addWidget(self.events, 1)
        tabs.addTab(log_page, "Ventes et journal")

        self.refresh()

    def closeEvent(self, event) -> None:
        if self._listener in self.app.catalog.listeners:
            self.app.catalog.listeners.remove(self._listener)
        super().closeEvent(event)

    # --- affichage ---------------------------------------------------------
    def refresh(self) -> None:
        items = self.app.catalog.all()
        query = self.search.text().strip().lower()
        if query:
            items = [i for i in items if query in i.sku.lower() or query in i.title.lower()]
        if self.only_problems.isChecked():
            items = [i for i in items if not i.listing.ready or any(
                i.state(p).wanted and i.state(p).status != PUBLIE and i.state(p).status != EPUISE for p in PLATFORMS)]
        selected = self._selected_sku()
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            self.table.setItem(row, 0, ro_item(item.sku))
            self.table.setItem(row, 1, ro_item(item.title))
            self.table.setItem(row, 2, ro_item(str(item.stock)))
            self.table.setItem(row, 3, ro_item(f"{item.info.prix} €" if item.info.prix else ""))
            fiche = ro_item("Complète" if item.listing.ready else "À compléter")
            if not item.listing.ready:
                fiche.setForeground(QColor("#b9770e"))
                fiche.setToolTip("Manque : " + ", ".join(item.listing.missing()))
            self.table.setItem(row, 4, fiche)
            for col, name in enumerate(PLATFORMS, start=5):
                state = item.state(name)
                if not state.wanted:
                    cell = ro_item("—")
                else:
                    cell = ro_item(STATE_LABELS.get(state.status, state.status))
                    cell.setForeground(QColor(STATE_COLORS.get(state.status, "#000000")))
                    tip = [state.error] if state.error else []
                    if state.url:
                        tip.append(state.url)
                    cell.setToolTip("\n".join(tip))
                self.table.setItem(row, col, cell)
            if item.sku == selected:
                self.table.selectRow(row)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        kinds = EVENT_FILTERS[self.filter.currentIndex()][1]
        events = self.app.catalog.events(kinds)
        self.events.setRowCount(len(events))
        for row, ev in enumerate(events):
            values = [ev.ts.replace("T", " "), ev.kind, PLATFORM_LABELS.get(ev.platform, ev.platform), ev.sku, ev.message]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if ev.kind == "erreur":
                    cell.setForeground(QColor("#c0392b"))
                elif ev.kind == "vente":
                    cell.setForeground(QColor("#1e8449"))
                self.events.setItem(row, col, cell)
        self.events.resizeColumnsToContents()
        self.events.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        engine = self.app.engine
        parts = []
        for name, platform in (engine.platforms.items() if engine else []):
            parts.append(f"{PLATFORM_LABELS[name]} : " + ("connecté" if platform.configured()
                                                          else "<span style='color:#888'>connexion à faire</span>"))
        if self.app.settings.publish_paused:
            parts.append("<b>publication en pause</b>")
        if not self.app.settings.sync_enabled:
            parts.append("synchro du stock désactivée")
        self.summary.setText(" · ".join(parts))

    def _selected_sku(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.text() if item else ""

    def _selected_skus(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.table.item(r, 0).text() for r in rows if self.table.item(r, 0)]

    # --- actions -----------------------------------------------------------
    def add_platform(self, name: str) -> None:
        """Publier aussi les pièces sélectionnées (Ctrl+clic ou Maj+clic pour en choisir plusieurs)."""
        skus = self._selected_skus()
        if not skus:
            QMessageBox.information(self, "Catalogue", "Sélectionne d'abord une ou plusieurs pièces "
                                    "(Ctrl+clic pour en choisir plusieurs).")
            return
        label = PLATFORM_LABELS[name]
        if QMessageBox.question(self, "Catalogue", f"Publier {len(skus)} pièce(s) sur {label} ?") != QMessageBox.Yes:
            return

        def apply(item):
            state = item.state(name)
            if not state.wanted:
                state.wanted, state.status, state.next_try, state.error = True, A_PUBLIER, 0.0, ""
        for sku in skus:
            self.app.catalog.update(sku, apply)
        self.app.catalog.log("info", f"{len(skus)} pièce(s) ajoutée(s) à {label}", platform=name)
        self.app.engine.wake()
        self.refresh()

    def open_listing(self, *_):
        sku = self._selected_sku()
        if sku:
            ListingEditor(sku, self.app, self).exec()
            self.refresh()

    def retry(self) -> None:
        sku = self._selected_sku()
        if not sku:
            return

        def apply(item):
            for name in PLATFORMS:
                state = item.state(name)
                state.next_try = 0.0
                if state.status == ERREUR:
                    state.attempts = 0
        self.app.catalog.update(sku, apply)
        self.app.engine.wake()

    def sync_now(self) -> None:
        self.app.engine.sync_now()
        self.summary.setText("Synchro lancée…")

    def remove(self) -> None:
        sku = self._selected_sku()
        if not sku:
            return
        item = self.app.catalog.get(sku)
        online = [PLATFORM_LABELS[p] for p in PLATFORMS if item and item.state(p).remote_id]
        text = f"Retirer {sku} du catalogue de GemZ Studio ?"
        if online:
            text += ("\n\nLes annonces en ligne (" + ", ".join(online) + ") ne sont PAS supprimées, "
                     "mais leur stock ne sera plus synchronisé.")
        if QMessageBox.question(self, "Catalogue", text) == QMessageBox.Yes:
            self.app.catalog.delete(sku)
            self.app.catalog.log("info", "Retiré du catalogue", sku)

    def import_shopify(self) -> None:
        shopify = self.app.engine.platforms["shopify"]
        if not shopify.configured():
            QMessageBox.information(self, "Shopify", "Shopify n'est pas encore connecté (Réglages → Connexions).")
            return
        self.summary.setText("Lecture des produits Shopify…")

        def done(result, error):
            if error:
                QMessageBox.warning(self, "Shopify", str(error))
                return
            added = import_shopify(self.app.catalog, result, self.app.settings)
            QMessageBox.information(self, "Shopify", f"{len(result)} produits lus, {added} ajoutés au catalogue.")
            self.refresh()

        run_in_background(shopify.list_listings, done)

    def link_etsy(self) -> None:
        etsy = self.app.engine.platforms["etsy"]
        if not etsy.configured():
            QMessageBox.information(self, "Etsy", "Etsy n'est pas encore connecté (Réglages → Connexions).")
            return
        self.summary.setText("Lecture des annonces Etsy…")

        def done(result, error):
            if error:
                QMessageBox.warning(self, "Etsy", str(error))
                return
            matches = match_etsy(self.app.catalog, result)
            if not matches:
                QMessageBox.information(self, "Etsy", "Toutes les annonces Etsy sont déjà reliées.")
                return
            dialog = MatchDialog(matches, self)
            if dialog.exec() == QDialog.Accepted:
                for match in dialog.chosen():
                    link_etsy(self.app.catalog, match)
                self.app.engine.wake()
            self.refresh()

        run_in_background(etsy.list_listings, done)


class MatchDialog(QDialog):
    """Vérification des correspondances Etsy ↔ Shopify avant de les relier."""

    METHODS = {"sku": "même SKU", "photo": "même photo", "titre": "titre proche", "aucune": "rien trouvé"}

    def __init__(self, matches, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Relier les annonces Etsy")
        self.resize(1100, 650)
        self.matches = matches
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Coche les paires à relier. Les correspondances sûres sont cochées d'office ; "
                                "vérifie les titres proches."))
        self.table = QTableWidget(len(matches), 4)
        self.table.setHorizontalHeaderLabels(["Relier", "Annonce Etsy", "Produit du catalogue", "Trouvé par"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.boxes: list[QCheckBox] = []
        for row, m in enumerate(matches):
            box = QCheckBox()
            box.setChecked(m.sure)
            box.setEnabled(m.item is not None)
            self.boxes.append(box)
            self.table.setCellWidget(row, 0, box)
            self.table.setItem(row, 1, ro_item(m.etsy.title))
            self.table.setItem(row, 2, ro_item(f"{m.item.sku} – {m.item.title}" if m.item else "—"))
            method = self.METHODS.get(m.method, m.method)
            if m.method == "titre":
                method += f" ({m.score:.0%})"
            self.table.setItem(row, 3, ro_item(method))
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Relier la sélection")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self):
        return [m for m, box in zip(self.matches, self.boxes) if box.isChecked() and m.item is not None]
