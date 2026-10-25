"""Catalogue (photos et état de chaque pièce sur chaque plateforme), ventes et journal d'activité."""
from __future__ import annotations

from datetime import datetime
from html import escape

from PySide6.QtCore import (
    QAbstractTableModel, QEvent, QItemSelectionModel, QModelIndex, QObject, QRect, QSettings, QSize, Qt, QTimer, QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QMenu, QMessageBox, QPushButton, QScrollArea, QSplitter,
    QStackedWidget, QStyle, QStyledItemDelegate, QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..catalog import (A_METTRE_A_JOUR, A_PUBLIER, EN_COURS, EPUISE, ERREUR, PLATFORM_LABELS, PLATFORMS, PUBLIE,
                       STATE_LABELS, CatalogItem)
from ..importer import adopt_etsy_defaults, import_shopify, link_etsy, match_etsy
from ..listing.builder import price_lines
from ..listing.prices import fmt_eur, safe_price
from ..sales import PERIODS, day_label, in_period, money, sale_rows, totals, when_label
from .common import run_in_background
from .listing_editor import NEXT, PREVIOUS, edit_listing
from .thumbcache import ThumbCache, square

STATE_COLORS = {PUBLIE: "#1e8449", ERREUR: "#c0392b", EPUISE: "#7f8c8d", A_PUBLIER: "#b9770e",
                A_METTRE_A_JOUR: "#2874a6", EN_COURS: "#2874a6"}
PLATFORM_COLORS = {"shopify": "#5e8e3e", "etsy": "#f1641e", "ebay": "#0064d2"}
ACCENT = "#7a5c3e"
ITEM_ROLE = Qt.UserRole + 1
TILE = QSize(196, 280)
PHOTO = 176

# Pastilles au-dessus du catalogue
CHIPS = [("all", "Toutes"), ("online", "En ligne"), ("todo", "À traiter"), ("errors", "Erreurs"),
         ("soldout", "Épuisées"), ("incomplete", "Fiche à compléter")]
PLATFORM_FILTERS = [("", "Toutes les plateformes"), ("on:etsy", "Sur Etsy"), ("on:ebay", "Sur eBay"),
                    ("off:etsy", "Pas encore sur Etsy"), ("off:ebay", "Pas encore sur eBay")]
SORTS = [("recent", "Plus récentes"), ("old", "Plus anciennes"), ("name", "Nom (A → Z)"),
         ("price_up", "Prix croissant"), ("price_down", "Prix décroissant"), ("stock", "Stock")]
EVENT_FILTERS = [("all", "Tout", None), ("erreur", "Erreurs", ("erreur",)), ("alerte", "Alertes", ("alerte",)),
                 ("vente", "Ventes", ("vente",)), ("publication", "Publications", ("publication",)),
                 ("synchro", "Synchro", ("synchro",)), ("info", "Infos", ("info",))]
EVENT_STYLE = {"erreur": ("#c0392b", "Erreur"), "alerte": ("#d68910", "Alerte"), "vente": ("#1e8449", "Vente"),
               "publication": ("#2874a6", "Publication"), "synchro": ("#7f8c8d", "Synchro"),
               "info": ("#566573", "Info")}

STYLE = f"""
QPushButton[chip="true"] {{ border:1px solid palette(mid); border-radius:13px; padding:4px 12px; background:palette(base); }}
QPushButton[chip="true"]:checked {{ background:{ACCENT}; color:white; border-color:{ACCENT}; }}
QPushButton[chip="true"]:hover:!checked {{ border-color:{ACCENT}; }}
#tile {{ border:1px solid palette(mid); border-radius:10px; background:palette(base); }}
"""


def item_flags(item: CatalogItem) -> set[str]:
    """Les pastilles auxquelles la pièce appartient."""
    wanted = [item.state(p) for p in PLATFORMS if item.state(p).wanted]
    flags = {"all"}
    if item.stock <= 0:
        flags.add("soldout")
    elif any(s.status == PUBLIE for s in wanted):
        flags.add("online")
    if not item.listing.ready:
        flags.update(("incomplete", "todo"))
    if any(s.status == ERREUR for s in wanted):
        flags.add("errors")
    if item.stock > 0 and any(s.status in (A_PUBLIER, ERREUR, A_METTRE_A_JOUR) for s in wanted):
        flags.add("todo")
    return flags


def dot(color: str, side: int = 10) -> QPixmap:
    pix = QPixmap(side, side)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawEllipse(0, 0, side, side)
    p.end()
    return pix


def ro_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


class _Relay(QObject):
    """Le catalogue prévient depuis n'importe quel thread ; on rafraîchit dans l'interface."""
    changed = Signal()


class ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


# --- modèle commun à la liste et à la mosaïque -----------------------------------
class CatalogModel(QAbstractTableModel):
    HEADERS = ["", "SKU", "Pièce", "Type", "Stock", "Prix", "Fiche", "Shopify", "Etsy", "eBay"]

    def __init__(self, thumbs: ThumbCache, settings_getter, parent=None):
        super().__init__(parent)
        self.items: list[CatalogItem] = []
        self.thumbs = thumbs
        self.settings = settings_getter
        self._rows_by_source: dict[str, list[int]] = {}
        self._squares: dict[tuple[str, int], QPixmap] = {}
        thumbs.ready.connect(self._thumb_ready)

    def set_items(self, items: list[CatalogItem]) -> None:
        if [i.sku for i in items] == [i.sku for i in self.items]:
            # Mêmes pièces dans le même ordre : sélection et défilement gardés
            self.items = items
            if items:
                self.dataChanged.emit(self.index(0, 0), self.index(len(items) - 1, len(self.HEADERS) - 1))
        else:
            self.beginResetModel()
            self.items = items
            self.endResetModel()
        self._rows_by_source = {}
        for row, item in enumerate(items):
            self._rows_by_source.setdefault(item.thumbnail_source, []).append(row)

    def square(self, item: CatalogItem, side: int) -> QPixmap:
        key = (item.thumbnail_source, side)
        pix = self._squares.get(key)
        if pix is None:
            raw = self.thumbs.get(item.thumbnail_source)
            pix = square(raw, side)
            if raw is not None:
                self._squares[key] = pix   # le carré vide d'attente n'est pas gardé
        return pix

    def _thumb_ready(self, source: str) -> None:
        for key in [k for k in self._squares if k[0] == source]:
            del self._squares[key]
        for row in self._rows_by_source.get(source, []):
            self.dataChanged.emit(self.index(row, 0), self.index(row, 0))

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        col = index.column()
        if role == ITEM_ROLE:
            return item
        if role == Qt.DecorationRole and col == 0:
            return self.square(item, 44)
        if role == Qt.DisplayRole:
            if col == 1:
                return item.sku
            if col == 2:
                return item.title
            if col == 3:
                return self.settings().kind(item.kind_id).label
            if col == 4:
                return str(item.stock) if item.stock > 0 else "Épuisé"
            if col == 5:
                if not item.info.prix:
                    return ""
                return f"{item.info.prix} €" + (f" (au lieu de {item.info.prix_barre} €)" if item.info.prix_barre else "")
            if col == 6:
                return "Complète" if item.listing.ready else "À compléter"
            if col >= 7:
                state = item.state(PLATFORMS[col - 7])
                return STATE_LABELS.get(state.status, state.status) if state.wanted else "—"
        if role == Qt.ForegroundRole:
            if col == 4 and item.stock <= 0:
                return QColor("#7f8c8d")
            if col == 6 and not item.listing.ready:
                return QColor("#b9770e")
            if col >= 7:
                state = item.state(PLATFORMS[col - 7])
                if state.wanted:
                    return QColor(STATE_COLORS.get(state.status, "#000000"))
        if role == Qt.ToolTipRole:
            if col == 6 and not item.listing.ready:
                return "Manque : " + ", ".join(item.listing.missing())
            if col >= 7:
                state = item.state(PLATFORMS[col - 7])
                return "\n".join(t for t in (state.error, state.url) if t) or None
        if role == Qt.TextAlignmentRole and col in (4, 5):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class TileDelegate(QStyledItemDelegate):
    """Vignette de la mosaïque : photo, nom, SKU et prix, puis une pastille par plateforme."""

    def __init__(self, model: CatalogModel, parent=None):
        super().__init__(parent)
        self.model = model

    def sizeHint(self, option, index) -> QSize:
        return TILE

    def paint(self, p: QPainter, option, index) -> None:
        item: CatalogItem = index.data(ITEM_ROLE)
        if item is None:
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        palette = option.palette
        rect = option.rect.adjusted(4, 4, -4, -4)
        selected = bool(option.state & QStyle.State_Selected)
        hover = bool(option.state & QStyle.State_MouseOver)
        p.setPen(QPen(QColor(ACCENT) if selected else palette.mid().color(), 2 if selected else 1))
        p.setBrush(QColor(ACCENT).lighter(215) if selected else (palette.alternateBase() if hover else palette.base()))
        p.drawRoundedRect(rect, 10, 10)

        photo = QRect(rect.left() + (rect.width() - PHOTO) // 2, rect.top() + 8, PHOTO, PHOTO)
        p.drawPixmap(photo, self.model.square(item, PHOTO))
        if item.stock <= 0:
            band = QRect(photo.left(), photo.bottom() - 32, photo.width(), 26)
            p.fillRect(band, QColor(0, 0, 0, 150))
            bold = QFont(option.font)
            bold.setBold(True)
            p.setFont(bold)
            p.setPen(Qt.white)
            p.drawText(band, Qt.AlignCenter, "Épuisé")
        if not item.listing.ready:
            p.setPen(QPen(Qt.white, 2))
            p.setBrush(QColor("#d68910"))
            p.drawEllipse(photo.right() - 20, photo.top() + 6, 14, 14)

        text = QRect(rect.left() + 10, photo.bottom() + 6, rect.width() - 20, 36)
        title_font = QFont(option.font)
        title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(palette.text().color())
        p.drawText(text, Qt.AlignLeft | Qt.AlignTop, "\n".join(self._two_lines(item.title, QFontMetrics(title_font),
                                                                                text.width())))
        small = QFont(option.font)
        small.setPointSizeF(max(7.0, option.font.pointSizeF() - 1))
        p.setFont(small)
        line = QRect(text.left(), text.bottom() + 2, text.width(), 16)
        p.setPen(palette.placeholderText().color())
        p.drawText(line, Qt.AlignLeft | Qt.AlignVCenter, item.sku)
        p.setPen(palette.text().color())
        price = f"{item.info.prix} €" if item.info.prix else ""
        p.drawText(line, Qt.AlignRight | Qt.AlignVCenter, price)
        metrics = QFontMetrics(small)
        if price and item.info.prix_barre:
            # Promotion : ancien prix barré à gauche du prix
            struck = QFont(small)
            struck.setStrikeOut(True)
            p.setFont(struck)
            p.setPen(palette.placeholderText().color())
            p.drawText(line.adjusted(0, 0, -(metrics.horizontalAdvance(price) + 6), 0), Qt.AlignRight | Qt.AlignVCenter,
                       f"{item.info.prix_barre} €")
            p.setFont(small)

        x, y = text.left(), line.bottom() + 5
        for name in PLATFORMS:
            state = item.state(name)
            if not state.wanted:
                continue
            label = PLATFORM_LABELS[name]
            pill = QRect(x, y, metrics.horizontalAdvance(label) + 20, 17)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(STATE_COLORS.get(state.status, "#7f8c8d")))
            p.drawRoundedRect(pill, 8, 8)
            p.setBrush(Qt.white)
            p.drawEllipse(pill.left() + 6, pill.center().y() - 2, 5, 5)
            p.setPen(Qt.white)
            p.drawText(pill.adjusted(14, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, label)
            x = pill.right() + 5
        p.restore()

    @staticmethod
    def _two_lines(text: str, metrics: QFontMetrics, width: int) -> list[str]:
        words = text.split()
        first = ""
        for i, word in enumerate(words):
            trial = f"{first} {word}".strip()
            if first and metrics.horizontalAdvance(trial) > width:
                return [first, metrics.elidedText(" ".join(words[i:]), Qt.ElideRight, width)]
            first = trial
        return [metrics.elidedText(first, Qt.ElideRight, width)]


# --- panneau de détail ------------------------------------------------------------
class DetailPanel(QScrollArea):
    open_requested = Signal(str)
    retry_requested = Signal(str)

    def __init__(self, app, thumbs: ThumbCache, parent=None):
        super().__init__(parent)
        self.app = app
        self.thumbs = thumbs
        self.item: CatalogItem | None = None
        self.images: list[str] = []
        self.index = 0
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setMinimumWidth(340)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 4, 12, 12)

        self.photo = ClickableLabel()
        self.photo.setFixedSize(306, 306)
        self.photo.setAlignment(Qt.AlignCenter)
        self.photo.setCursor(Qt.PointingHandCursor)
        self.photo.setToolTip("Clic : photo suivante")
        self.photo.clicked.connect(lambda: self.step(1))
        lay.addWidget(self.photo, 0, Qt.AlignHCenter)
        nav = QHBoxLayout()
        self.prev_btn = QToolButton(text="◀")
        self.next_btn = QToolButton(text="▶")
        self.counter = QLabel()
        self.counter.setAlignment(Qt.AlignCenter)
        self.prev_btn.clicked.connect(lambda: self.step(-1))
        self.next_btn.clicked.connect(lambda: self.step(1))
        nav.addStretch(1)
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.counter)
        nav.addWidget(self.next_btn)
        nav.addStretch(1)
        lay.addLayout(nav)

        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() + 3)
        f.setBold(True)
        self.title.setFont(f)
        self.subtitle = QLabel()
        self.subtitle.setStyleSheet("color: palette(placeholder-text);")
        self.prices = QLabel()
        self.platforms = QLabel()
        self.platforms.setOpenExternalLinks(True)
        self.facts = QLabel()
        for w in (self.title, self.subtitle, self.prices, self.platforms, self.facts):
            w.setWordWrap(True)
            w.setTextFormat(Qt.RichText if w is not self.title else Qt.PlainText)
            w.setTextInteractionFlags(Qt.TextBrowserInteraction)
            lay.addWidget(w)
        self.platforms.setOpenExternalLinks(True)

        buttons = QHBoxLayout()
        self.open_btn = QPushButton("Ouvrir la fiche")
        self.open_btn.clicked.connect(lambda: self.item and self.open_requested.emit(self.item.sku))
        self.retry_btn = QPushButton("Réessayer maintenant")
        self.retry_btn.clicked.connect(lambda: self.item and self.retry_requested.emit(self.item.sku))
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.retry_btn)
        lay.addLayout(buttons)

        history_title = QLabel("Derniers événements")
        history_title.setStyleSheet("font-weight:600; margin-top:8px;")
        lay.addWidget(history_title)
        self.history = QLabel()
        self.history.setWordWrap(True)
        self.history.setTextFormat(Qt.RichText)
        lay.addWidget(self.history)
        lay.addStretch(1)
        self.setWidget(body)
        thumbs.ready.connect(self._thumb_ready)
        self.show_item(None)

    def show_item(self, item: CatalogItem | None) -> None:
        same = item is not None and self.item is not None and item.sku == self.item.sku
        self.item = item
        if item is None:
            self.widget().setEnabled(False)
            self.images = []
            self.photo.setPixmap(square(None, 306, radius=12))
            self.title.setText("Choisis une pièce")
            for w in (self.subtitle, self.prices, self.platforms, self.facts, self.history, self.counter):
                w.setText("")
            self.prev_btn.hide()
            self.next_btn.hide()
            self.retry_btn.hide()
            return
        self.widget().setEnabled(True)
        images = item.all_images()
        if not same or images != self.images:
            self.images, self.index = images, 0
        self._show_photo()
        settings = self.app.settings
        kind = settings.kind(item.kind_id)
        try:
            created = when_label(datetime.fromisoformat(item.created)).rsplit(" ", 1)[0]
        except ValueError:
            created = item.created[:10]
        self.title.setText(item.title)
        self.subtitle.setText(f"{escape(item.sku)} · {escape(kind.label)} · ajoutée {created}"
                              + (" · importée de Shopify" if item.origin == "import" else ""))

        prices = price_lines(item.info, settings)
        barre = f" <s style='color:#888'>{fmt_eur(safe_price(item.info.prix_barre))}</s>" if item.info.prix_barre else ""
        stock = "<b style='color:#7f8c8d'>Épuisé</b>" if item.stock <= 0 else f"Stock : <b>{item.stock}</b>"
        self.prices.setText(f"<span style='font-size:16px'><b>{fmt_eur(prices['shopify'])}</b>{barre}</span>"
                            f" &nbsp;·&nbsp; {stock}<br><span style='color:#888'>Etsy {fmt_eur(prices['etsy'])}"
                            f" · eBay {fmt_eur(prices['ebay_fr'])}</span>")

        rows = []
        engine = self.app.engine
        for name in PLATFORMS:
            state = item.state(name)
            label = PLATFORM_LABELS[name]
            if not state.wanted:
                rows.append(f"<span style='color:#999'>{label} : pas publié</span>")
                continue
            color = STATE_COLORS.get(state.status, "#000")
            line = f"<b>{label}</b> : <span style='color:{color}'>{STATE_LABELS.get(state.status, state.status)}</span>"
            if state.url:
                line += f" · <a href='{escape(state.url)}'>voir l'annonce</a>"
            platform = engine.platforms.get(name) if engine else None
            if platform is not None and not platform.configured():
                line += " · <span style='color:#888'>connexion à faire</span>"
            if state.error:
                line += f"<br><span style='color:#c0392b'>{escape(state.error)}</span>"
            rows.append(line)
        self.platforms.setText("<br>".join(rows))
        self.retry_btn.setVisible(any(item.state(p).wanted and item.state(p).status in (ERREUR, A_PUBLIER, A_METTRE_A_JOUR)
                                      for p in PLATFORMS) and item.stock > 0)

        info = item.info
        facts = [("Poids", f"{info.poids_g} g" if info.poids_g else ""),
                 ("Dimensions", f"{info.dimensions} cm" if info.dimensions else ""),
                 ("Provenance", ", ".join(v for v in (info.region, info.pays) if v)),
                 ("Taille", info.taille_reelle if kind.form == "bague" else ""),
                 ("Pierres", ", ".join(item.listing.pierres)), ("Couleurs", ", ".join(item.listing.couleurs))]
        fiche = "complète" if item.listing.ready else \
            f"<span style='color:#b9770e'>à compléter ({', '.join(item.listing.missing())})</span>"
        self.facts.setText("<br>".join(f"<span style='color:#888'>{k}</span> &nbsp;{escape(v)}" for k, v in facts if v)
                           + f"<br><span style='color:#888'>Fiche</span> &nbsp;{fiche}")

        lines = []
        for ev in self.app.catalog.events(limit=6, sku=item.sku):
            color = EVENT_STYLE.get(ev.kind, ("#566573", ""))[0]
            when = when_label(datetime.fromisoformat(ev.ts))
            lines.append(f"<span style='color:#888'>{when}</span> &nbsp;<span style='color:{color}'>{escape(ev.message)}</span>")
        self.history.setText("<br>".join(lines) or "<span style='color:#888'>Rien pour l'instant.</span>")

    def step(self, delta: int) -> None:
        if len(self.images) > 1:
            self.index = (self.index + delta) % len(self.images)
            self._show_photo()

    def _show_photo(self) -> None:
        source = self.images[self.index] if self.images else ""
        self.photo.setPixmap(square(self.thumbs.get(source, 640) if source else None, 306, radius=12))
        many = len(self.images) > 1
        self.prev_btn.setVisible(many)
        self.next_btn.setVisible(many)
        self.counter.setText(f"{self.index + 1} / {len(self.images)}" if self.images else "Pas de photo")

    def _thumb_ready(self, source: str) -> None:
        if self.images and self.images[self.index] == source:
            self._show_photo()


# --- onglet Ventes ----------------------------------------------------------------
class Tile(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("tile")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        self.caption = QLabel(title)
        self.caption.setStyleSheet("color: palette(placeholder-text);")
        self.value = QLabel()
        f = self.value.font()
        f.setPointSizeF(f.pointSizeF() + 6)
        f.setBold(True)
        self.value.setFont(f)
        self.detail = QLabel()
        self.detail.setStyleSheet("color: palette(placeholder-text);")
        self.detail.setWordWrap(True)
        for w in (self.caption, self.value, self.detail):
            lay.addWidget(w)

    def set(self, value: str, detail: str) -> None:
        self.value.setText(value)
        self.detail.setText(detail)


class SalesTab(QWidget):
    open_requested = Signal(str)

    def __init__(self, app, thumbs: ThumbCache, parent=None):
        super().__init__(parent)
        self.app = app
        self.thumbs = thumbs
        lay = QVBoxLayout(self)
        tiles = QHBoxLayout()
        self.tiles = {key: Tile(label) for key, label in (("today", "Aujourd'hui"), ("month", "Ce mois-ci"),
                                                           ("year", "Cette année"), ("average", "Vente moyenne"))}
        for tile in self.tiles.values():
            tiles.addWidget(tile, 1)
        lay.addLayout(tiles)
        bar = QHBoxLayout()
        self.period = QComboBox()
        for key, label in PERIODS.items():
            self.period.addItem(label, key)
        self.period.setCurrentIndex(list(PERIODS).index("month"))
        self.platform = QComboBox()
        self.platform.addItem("Toutes les plateformes", "")
        for name in PLATFORMS:
            self.platform.addItem(PLATFORM_LABELS[name], name)
        self.period.currentIndexChanged.connect(self.refresh)
        self.platform.currentIndexChanged.connect(self.refresh)
        bar.addWidget(QLabel("Période"))
        bar.addWidget(self.period)
        bar.addWidget(self.platform)
        bar.addStretch(1)
        hint = QLabel("Double-clic sur une vente : ouvrir la fiche de la pièce")
        hint.setStyleSheet("color: palette(placeholder-text);")
        bar.addWidget(hint)
        lay.addLayout(bar)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["", "Date", "Plateforme", "Pièce", "Montant", "Commande"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setIconSize(QSize(44, 44))
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self._open)
        lay.addWidget(self.table, 1)
        self.empty = QLabel("Aucune vente sur cette période.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet("color: palette(placeholder-text); padding: 20px;")
        lay.addWidget(self.empty)
        self.rows = []
        self._sources: dict[str, list[int]] = {}
        thumbs.ready.connect(self._thumb_ready)

    def refresh(self) -> None:
        rows = sale_rows(self.app.catalog)
        now = datetime.now()
        for key in ("today", "month", "year"):
            t = totals(in_period(rows, key, now))
            self.tiles[key].set(t.text(), f"{t.count} vente{'s' if t.count > 1 else ''}")
        period = self.period.currentData()
        chosen = in_period(rows, period, now)
        t = totals(chosen)
        split = " · ".join(f"{PLATFORM_LABELS.get(p, p)} {' + '.join(money(v, c) for c, v in amounts.items())}"
                           for p, amounts in sorted(t.by_platform.items()))
        euros = [r.unit_price for r in chosen if r.currency == "EUR" for _ in range(r.quantity)]
        self.tiles["average"].caption.setText(f"Vente moyenne · {PERIODS[period].lower()}")
        self.tiles["average"].set(money(sum(euros) / len(euros)) if euros else "—", split or "Aucune vente")
        platform = self.platform.currentData()
        self.rows = [r for r in chosen if not platform or r.platform == platform]
        self.empty.setVisible(not self.rows)
        self.table.setVisible(bool(self.rows))
        self.table.setRowCount(len(self.rows))
        self._sources = {}
        items = {i.sku: i for i in self.app.catalog.all()}
        for row, r in enumerate(self.rows):
            item = items.get(r.sku)
            photo = ro_item("")
            source = item.thumbnail_source if item else ""
            if source:
                self._sources.setdefault(source, []).append(row)
                photo.setIcon(square(self.thumbs.get(source), 44))
            self.table.setItem(row, 0, photo)
            self.table.setItem(row, 1, ro_item(when_label(r.when, now)))
            plat = ro_item(PLATFORM_LABELS.get(r.platform, r.platform))
            plat.setForeground(QColor(PLATFORM_COLORS.get(r.platform, "#000")))
            self.table.setItem(row, 2, plat)
            title = ro_item(r.title + ("" if r.in_catalog else "  (hors catalogue)"))
            title.setToolTip(r.sku)
            self.table.setItem(row, 3, title)
            amount = ro_item(money(r.amount, r.currency) + (f"  ({r.quantity} × {money(r.unit_price, r.currency)})"
                                                            if r.quantity > 1 else ""))
            amount.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))
            self.table.setItem(row, 4, amount)
            self.table.setItem(row, 5, ro_item(r.order.rsplit("/", 1)[-1]))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

    def _thumb_ready(self, source: str) -> None:
        for row in self._sources.get(source, []):
            cell = self.table.item(row, 0)
            if cell:
                cell.setIcon(square(self.thumbs.get(source), 44))

    def _open(self, index) -> None:
        if 0 <= index.row() < len(self.rows) and self.rows[index.row()].in_catalog:
            self.open_requested.emit(self.rows[index.row()].sku)


# --- onglet Activité ---------------------------------------------------------------
class ActivityTab(QWidget):
    open_requested = Signal(str)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self.app = app
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QPushButton] = {}
        for key, label, _kinds in EVENT_FILTERS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setProperty("chip", True)
            b.setCursor(Qt.PointingHandCursor)
            self.group.addButton(b)
            self.buttons[key] = b
            bar.addWidget(b)
        self.buttons["all"].setChecked(True)
        self.group.buttonToggled.connect(lambda _b, on: on and self.refresh())
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Chercher (pièce, SKU, texte)…")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(320)
        self.search.textChanged.connect(self.refresh)
        bar.addWidget(self.search)
        lay.addLayout(bar)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Heure", "Type", "Plateforme", "Pièce", "Détail"])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setStyleSheet("QTreeWidget::item { padding: 3px 0; }")
        self.tree.itemDoubleClicked.connect(self._open)
        self.tree.header().setSectionResizeMode(4, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(True)
        lay.addWidget(self.tree, 1)
        hint = QLabel("Les lignes identiques qui se suivent (même erreur à chaque nouvel essai) sont regroupées (×N). "
                      "Double-clic : ouvrir la fiche de la pièce.")
        hint.setStyleSheet("color: palette(placeholder-text);")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.dots = {kind: dot(color) for kind, (color, _label) in EVENT_STYLE.items()}

    def _kinds(self):
        return next((kinds for key, _label, kinds in EVENT_FILTERS if self.buttons[key].isChecked()), None)

    def refresh(self) -> None:
        events = self.app.catalog.events(self._kinds(), limit=2000)
        events.sort(key=lambda e: (e.ts, e.id), reverse=True)
        titles = {i.sku: i.title for i in self.app.catalog.all()}
        query = self.search.text().strip().lower()
        if query:
            events = [e for e in events if query in e.message.lower() or query in e.sku.lower()
                      or query in titles.get(e.sku, "").lower()]
        groups: list[list] = []
        for ev in events:
            last = groups[-1][0] if groups else None
            if last and (last.kind, last.sku, last.platform, last.message, last.ts[:10]) == \
                    (ev.kind, ev.sku, ev.platform, ev.message, ev.ts[:10]):
                groups[-1].append(ev)
            else:
                groups.append([ev])
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        today = datetime.now().date()
        days: dict[str, QTreeWidgetItem] = {}
        bold = QFont(self.tree.font())
        bold.setBold(True)
        for group in groups:
            ev = group[0]
            day = days.get(ev.ts[:10])
            if day is None:
                day = QTreeWidgetItem([day_label(datetime.fromisoformat(ev.ts).date(), today)])
                day.setFont(0, bold)
                self.tree.addTopLevelItem(day)
                day.setFirstColumnSpanned(True)
                days[ev.ts[:10]] = day
            color, label = EVENT_STYLE.get(ev.kind, ("#566573", ev.kind))
            when = ev.ts[11:16] if len(group) == 1 else f"{group[-1].ts[11:16]}–{ev.ts[11:16]}"
            message = ev.message + (f"   ×{len(group)}" if len(group) > 1 else "")
            piece = titles.get(ev.sku) or ev.data.get("titre") or ev.sku
            row = QTreeWidgetItem([when, label, PLATFORM_LABELS.get(ev.platform, ev.platform), piece, message])
            row.setIcon(1, self.dots.get(ev.kind, self.dots["info"]))
            row.setData(0, Qt.UserRole, ev.sku)
            row.setToolTip(4, ev.message)
            row.setToolTip(3, ev.sku)
            if ev.kind in ("erreur", "alerte", "vente"):
                for col in (1, 4):
                    row.setForeground(col, QColor(color))
            if ev.platform in PLATFORM_COLORS:
                row.setForeground(2, QColor(PLATFORM_COLORS[ev.platform]))
            day.addChild(row)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            count = top.childCount()
            top.setText(0, f"{top.text(0)}  ·  {count} ligne{'s' if count > 1 else ''}")
            top.setExpanded(i < 7)   # la dernière semaine ouverte, le reste replié
        self.tree.setUpdatesEnabled(True)
        for col in range(1, 4):
            self.tree.resizeColumnToContents(col)
        self.tree.setColumnWidth(0, 120)

    def _open(self, row: QTreeWidgetItem, _col: int) -> None:
        sku = row.data(0, Qt.UserRole)
        if sku and self.app.catalog.get(sku):
            self.open_requested.emit(sku)


# --- fenêtre -------------------------------------------------------------------------
class CatalogWindow(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent, Qt.Window)
        self.app = app
        self.setWindowTitle("Catalogue et ventes")
        self.resize(1420, 880)
        self.setStyleSheet(STYLE)
        self.prefs = QSettings("GemZ", "GemZ Studio")
        self.thumbs = ThumbCache(self)
        self._relay = _Relay()
        self._timer = QTimer(self, singleShot=True, interval=500)
        self._timer.timeout.connect(self.refresh)
        self._relay.changed.connect(self._timer.start)
        self._listener = lambda _sku: self._relay.changed.emit()

        root = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # --- catalogue -------------------------------------------------------
        page = QWidget()
        pl = QVBoxLayout(page)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher une pièce : nom, SKU, pierre, pays…   (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_catalog)
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("Tous les types", "")
        for kind in app.settings.kinds:
            self.kind_filter.addItem(kind.label, kind.id)
        self.platform_filter = QComboBox()
        for key, label in PLATFORM_FILTERS:
            self.platform_filter.addItem(label, key)
        self.sort = QComboBox()
        for key, label in SORTS:
            self.sort.addItem(label, key)
        for combo in (self.kind_filter, self.platform_filter, self.sort):
            combo.currentIndexChanged.connect(self.refresh_catalog)
        self.mosaic_btn = QToolButton(text="▦  Mosaïque", checkable=True)
        self.list_btn = QToolButton(text="☰  Liste", checkable=True)
        views = QButtonGroup(self)
        views.addButton(self.mosaic_btn)
        views.addButton(self.list_btn)
        self.mosaic_btn.toggled.connect(lambda on: on and self._set_view("mosaique"))
        self.list_btn.toggled.connect(lambda on: on and self._set_view("liste"))
        tools = QToolButton(text="Outils  ▾")
        tools.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(tools)
        menu.addAction("Synchroniser maintenant (ventes et stock)", self.sync_now)
        menu.addAction("Importer / mettre à jour depuis Shopify", self.import_shopify)
        menu.addAction("Relier les annonces Etsy", self.link_etsy)
        menu.addSeparator()
        menu.addAction("Retirer la sélection du catalogue…", self.remove)
        tools.setMenu(menu)
        top.addWidget(self.search, 1)
        top.addWidget(self.kind_filter)
        top.addWidget(self.platform_filter)
        top.addWidget(self.sort)
        top.addSpacing(10)
        top.addWidget(self.mosaic_btn)
        top.addWidget(self.list_btn)
        top.addWidget(tools)
        pl.addLayout(top)

        chips = QHBoxLayout()
        self.chip_group = QButtonGroup(self)
        self.chips: dict[str, QPushButton] = {}
        for key, label in CHIPS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setProperty("chip", True)
            b.setCursor(Qt.PointingHandCursor)
            self.chip_group.addButton(b)
            self.chips[key] = b
            chips.addWidget(b)
        self.chips["all"].setChecked(True)
        self.chip_group.buttonToggled.connect(lambda _b, on: on and self.refresh_catalog())
        chips.addStretch(1)
        pl.addLayout(chips)

        self.model = CatalogModel(self.thumbs, lambda: self.app.settings, self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        self.table.setIconSize(QSize(44, 44))
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.mosaic = QListView()
        self.mosaic.setModel(self.model)
        self.mosaic.setViewMode(QListView.IconMode)
        self.mosaic.setResizeMode(QListView.Adjust)
        self.mosaic.setMovement(QListView.Static)
        self.mosaic.setUniformItemSizes(True)
        self.mosaic.setSpacing(4)
        self.mosaic.setMouseTracking(True)
        self.mosaic.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.mosaic.setItemDelegate(TileDelegate(self.model, self.mosaic))
        self.mosaic.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.mosaic.verticalScrollBar().setSingleStep(30)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.mosaic)
        self.stack.addWidget(self.table)
        for view in (self.table, self.mosaic):
            view.doubleClicked.connect(lambda _index: self.open_listing())
            view.setContextMenuPolicy(Qt.CustomContextMenu)
            view.customContextMenuRequested.connect(self._context_menu)
            view.selectionModel().selectionChanged.connect(self._selection_changed)
            view.selectionModel().currentChanged.connect(self._selection_changed)
            view.installEventFilter(self)

        self.detail = DetailPanel(app, self.thumbs)
        self.detail.open_requested.connect(self.open_listing)
        self.detail.retry_requested.connect(lambda sku: self.retry([sku]))
        self.splitter = QSplitter()
        self.splitter.addWidget(self.stack)
        self.splitter.addWidget(self.detail)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setSizes([1040, 370])
        pl.addWidget(self.splitter, 1)

        actions = QHBoxLayout()
        for text, slot in (("Ouvrir la fiche", self.open_listing),
                           ("Publier aussi sur Etsy", lambda: self.add_platform("etsy")),
                           ("Publier aussi sur eBay", lambda: self.add_platform("ebay")),
                           ("Réessayer maintenant", self.retry)):
            b = QPushButton(text)
            b.clicked.connect(lambda _checked=False, s=slot: s())
            actions.addWidget(b)
        actions.addStretch(1)
        self.count_label = QLabel()
        self.count_label.setStyleSheet("color: palette(placeholder-text);")
        actions.addWidget(self.count_label)
        pl.addLayout(actions)
        self.tabs.addTab(page, "Catalogue")

        self.sales = SalesTab(app, self.thumbs)
        self.sales.open_requested.connect(self.open_listing)
        self.tabs.addTab(self.sales, "Ventes")
        self.activity = ActivityTab(app)
        self.activity.open_requested.connect(self.open_listing)
        self.tabs.addTab(self.activity, "Activité")
        self.tabs.currentChanged.connect(lambda _i: self.refresh())

        QShortcut(QKeySequence.Find, self, activated=self._focus_search)
        for number in range(3):
            QShortcut(QKeySequence(f"Ctrl+{number + 1}"), self, activated=lambda n=number: self.tabs.setCurrentIndex(n))

        (self.list_btn if self.prefs.value("catalogue/vue", "mosaique") == "liste" else self.mosaic_btn).setChecked(True)

    # --- cycle de vie --------------------------------------------------------
    def showEvent(self, event) -> None:
        if self._listener not in self.app.catalog.listeners:
            self.app.catalog.listeners.append(self._listener)
        self.refresh()
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        if self._listener in self.app.catalog.listeners:
            self.app.catalog.listeners.remove(self._listener)
        super().closeEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.KeyPress and obj in (self.table, self.mosaic):
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.open_listing()
                return True
            if event.key() == Qt.Key_Delete:
                self.remove()
                return True
        return super().eventFilter(obj, event)

    def _focus_search(self) -> None:
        self.tabs.setCurrentIndex(0)
        self.search.setFocus()
        self.search.selectAll()

    def _set_view(self, view: str) -> None:
        skus = self._selected_skus()
        sku = self._current_sku()
        self.stack.setCurrentWidget(self.table if view == "liste" else self.mosaic)
        self.prefs.setValue("catalogue/vue", view)
        if skus or sku:
            self._select(sku or skus[0], skus)

    # --- affichage -----------------------------------------------------------
    def refresh(self) -> None:
        """Rafraîchit l'onglet affiché (les autres le seront quand on les ouvrira)."""
        current = self.tabs.currentWidget()
        if current is self.sales:
            self.sales.refresh()
        elif current is self.activity:
            self.activity.refresh()
        else:
            self.refresh_catalog()
        self._refresh_summary()

    def refresh_catalog(self) -> None:
        everything = self.app.catalog.all()
        flags = {item.sku: item_flags(item) for item in everything}
        for key, label in CHIPS:
            count = sum(1 for f in flags.values() if key in f)
            self.chips[key].setText(f"{label}  {count}")
        chip = next((k for k, b in self.chips.items() if b.isChecked()), "all")
        items = [i for i in everything if chip in flags[i.sku]]
        kind = self.kind_filter.currentData()
        if kind:
            items = [i for i in items if i.kind_id == kind]
        platform = self.platform_filter.currentData()
        if platform:
            mode, name = platform.split(":")
            items = [i for i in items if (i.state(name).wanted or bool(i.state(name).remote_id)) == (mode == "on")]
        words = self.search.text().strip().lower().split()
        if words:
            def haystack(i: CatalogItem) -> str:
                return " ".join([i.sku, i.title, i.info.nom, i.info.pays, i.info.region, *i.listing.pierres,
                                 *i.listing.tags]).lower()
            items = [i for i in items if all(word in haystack(i) for word in words)]
        order = self.sort.currentData()
        if order in ("recent", "old"):
            items.sort(key=lambda i: i.created, reverse=order == "recent")
        elif order == "name":
            items.sort(key=lambda i: i.title.lower())
        elif order in ("price_up", "price_down"):
            items.sort(key=lambda i: safe_price(i.info.prix), reverse=order == "price_down")
        elif order == "stock":
            items.sort(key=lambda i: i.stock, reverse=True)
        sku, skus = self._current_sku(), self._selected_skus()
        scroll = self._view().verticalScrollBar().value()
        self.model.set_items(items)
        if sku or skus:
            self._select(sku or skus[0], skus, scroll)
        else:
            self._view().verticalScrollBar().setValue(scroll)
            self._selection_changed()
        plural = "s" if len(items) > 1 else ""
        self.count_label.setText(f"{len(items)} pièce{plural} affichée{plural} sur {len(everything)}")

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

    def _view(self):
        return self.stack.currentWidget()

    def _current_sku(self) -> str:
        index = self._view().currentIndex()
        item = index.data(ITEM_ROLE) if index.isValid() else None
        return item.sku if item else ""

    def _selected_skus(self) -> list[str]:
        rows = sorted({i.row() for i in self._view().selectionModel().selectedIndexes()})
        return [self.model.items[r].sku for r in rows if r < len(self.model.items)]

    def _select(self, sku: str, skus: list[str] | None = None, scroll: int | None = None) -> None:
        view = self._view()
        rows = {item.sku: row for row, item in enumerate(self.model.items)}
        selection = view.selectionModel()
        selection.blockSignals(True)
        selection.clearSelection()
        flags = QItemSelectionModel.Select | QItemSelectionModel.Rows
        for s in skus or [sku]:
            if s in rows:
                selection.select(self.model.index(rows[s], 0), flags)
        if sku in rows:
            selection.setCurrentIndex(self.model.index(rows[sku], 0), QItemSelectionModel.NoUpdate)
        selection.blockSignals(False)
        view.viewport().update()
        if scroll is not None:
            view.verticalScrollBar().setValue(scroll)
        elif sku in rows:
            view.scrollTo(self.model.index(rows[sku], 0))
        self._selection_changed()

    def _selection_changed(self, *_):
        sku = self._current_sku()
        skus = self._selected_skus()
        if sku not in skus:
            sku = skus[0] if skus else ""
        self.detail.show_item(self.app.catalog.get(sku) if sku else None)

    def _context_menu(self, pos) -> None:
        view = self._view()
        index = view.indexAt(pos)
        if not index.isValid():
            return
        item: CatalogItem = index.data(ITEM_ROLE)
        if item.sku not in self._selected_skus():
            self._select(item.sku)
        menu = QMenu(self)
        menu.addAction("Ouvrir la fiche", self.open_listing)
        for name in PLATFORMS:
            url = item.state(name).url
            if url:
                menu.addAction(f"Voir sur {PLATFORM_LABELS[name]}", lambda u=url: QDesktopServices.openUrl(QUrl(u)))
        menu.addSeparator()
        for name in ("etsy", "ebay"):
            if not item.state(name).wanted:
                menu.addAction(f"Publier aussi sur {PLATFORM_LABELS[name]}", lambda n=name: self.add_platform(n))
        menu.addAction("Réessayer maintenant", self.retry)
        if item.folder:
            menu.addAction("Ouvrir le dossier des photos",
                           lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(item.folder)))
        menu.addAction("Copier le SKU", lambda: QApplication.clipboard().setText(item.sku))
        menu.addSeparator()
        menu.addAction("Retirer du catalogue…", self.remove)
        menu.exec(view.viewport().mapToGlobal(pos))

    # --- actions -------------------------------------------------------------
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

    def open_listing(self, sku: str | None = None) -> None:
        """Ouvre la fiche ; « ◀ Précédente / Suivante ▶ » passent à la pièce voisine dans le catalogue affiché."""
        sku = sku or self._current_sku() or next(iter(self._selected_skus()), "")
        while sku and self.app.catalog.get(sku):
            result = edit_listing(sku, self.app, self, can_navigate=True)
            order = [i.sku for i in self.model.items]
            if result not in (NEXT, PREVIOUS) or sku not in order:
                break
            pos = order.index(sku) + (1 if result == NEXT else -1)
            if not 0 <= pos < len(order):
                break
            sku = order[pos]
            if self.tabs.currentIndex() == 0:
                self._select(sku)
        self.refresh()

    def retry(self, skus: list[str] | None = None) -> None:
        skus = skus or self._selected_skus()

        def apply(item):
            for name in PLATFORMS:
                state = item.state(name)
                state.next_try = 0.0
                if state.status == ERREUR:
                    state.attempts = 0
        for sku in skus:
            self.app.catalog.update(sku, apply)
        if skus:
            self.app.engine.wake()
            self.summary.setText(f"Nouvel essai lancé pour {len(skus)} pièce(s)…")

    def sync_now(self) -> None:
        self.app.engine.sync_now()
        self.summary.setText("Synchro lancée…")

    def remove(self) -> None:
        skus = self._selected_skus()
        if not skus:
            return
        items = [self.app.catalog.get(s) for s in skus]
        online = sorted({PLATFORM_LABELS[p] for it in items if it for p in PLATFORMS if it.state(p).remote_id})
        text = (f"Retirer {skus[0]} du catalogue de GemZ Studio ?" if len(skus) == 1
                else f"Retirer ces {len(skus)} pièces du catalogue de GemZ Studio ?")
        if online:
            text += ("\n\nLes annonces en ligne (" + ", ".join(online) + ") ne sont PAS supprimées, "
                     "mais leur stock ne sera plus synchronisé.")
        if QMessageBox.question(self, "Catalogue", text) == QMessageBox.Yes:
            for sku in skus:
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
            QMessageBox.information(self, "Shopify", f"{len(result)} produits lus : {added} ajoutés au catalogue ; "
                                    "les pièces déjà importées sont remises à jour (textes, photos).")
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
            if matches:
                dialog = MatchDialog(matches, self)
                if dialog.exec() == QDialog.Accepted:
                    for match in dialog.chosen():
                        link_etsy(self.app.catalog, match)
            # Catégories, expédition, retours… repris des annonces existantes pour les nouvelles annonces
            filled = adopt_etsy_defaults(self.app.catalog, result, self.app.settings)
            if filled:
                self.app.engine.update_settings(self.app.settings)
            text = "Toutes les annonces Etsy sont reliées." if not matches else "Liaison terminée."
            if filled:
                text += "\n\nRepris de tes annonces Etsy : " + ", ".join(filled) + "."
            QMessageBox.information(self, "Etsy", text)
            self.app.engine.wake()
            self.refresh()

        # Annonces épuisées aussi : une pièce vendue sur Etsy avant la liaison est retirée de Shopify
        run_in_background(lambda: etsy.list_listings(("active", "sold_out")), done)


class MatchDialog(QDialog):
    """Vérification des correspondances Etsy ↔ Shopify avant de les relier."""

    METHODS = {"sku": "même SKU", "photo": "même photo", "titre": "titre proche", "aucune": "rien trouvé"}

    def __init__(self, matches, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Relier les annonces Etsy")
        self.resize(1100, 650)
        self.matches = matches
        layout = QVBoxLayout(self)
        intro = QLabel("Coche les paires à relier. Les correspondances sûres sont cochées d'office ; vérifie les titres "
                       "proches.")
        if any(m.sold for m in matches):
            intro.setText(intro.text() + "\n« Vendue sur Etsy » : l'annonce Etsy est épuisée alors que la pièce est "
                          "encore en stock ici. Une fois reliée, la dernière pièce est retirée de Shopify (et d'eBay) ; "
                          "s'il en reste plusieurs, l'annonce Etsy est remise en vente avec le stock.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
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
            cell = ro_item(method + (" · vendue sur Etsy" if m.sold else ""))
            if m.sold:
                cell.setForeground(QColor("#c0392b"))
            self.table.setItem(row, 3, cell)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Relier la sélection")
        buttons.button(QDialogButtonBox.Cancel).setText("Annuler")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self):
        return [m for m, box in zip(self.matches, self.boxes) if box.isChecked() and m.item is not None]
