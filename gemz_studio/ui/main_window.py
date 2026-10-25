"""Fenêtre principale : produits de la séance, photos du produit, fiche."""
from __future__ import annotations

import re
import shutil
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from ..camera import MODE_FOLDER, CameraError, make_backend
from ..config import APP_NAME, Settings, data_dir
from ..grouping import Grouper
from ..imaging import render_web, thumbnail
from ..models import VIDEO_EXT, A_COMPLETER, EN_PRISE, EN_TRAITEMENT, ERREUR, PRET, STATUS_LABELS, Session
from ..processing import process_product
from ..watcher import FolderWatcher
from .camera_panel import CameraPanel
from .common import open_in_explorer, pil_to_pixmap, video_icon, video_thumbnail_icon
from .product_form import ProductForm
from .settings_dialog import SettingsDialog

SESSION_FILE = "seance_en_cours.json"


def card_name(path: str) -> str:
    """Nom du fichier sur la carte (sans le « _2 » ajouté si un fichier du même nom existait déjà)."""
    p = Path(path)
    return re.sub(r"_\d+$", "", p.stem) + p.suffix if re.match(r"^[A-Z]{3}_\d{4}_\d+$", p.stem) else p.name


class Bridge(QObject):
    """Ramène les événements des threads de fond vers l'interface."""
    fileArrived = Signal(str)
    processed = Signal(str, str)   # id produit, message d'erreur ("" si OK)


class ProcessJob(QRunnable):
    def __init__(self, bridge: Bridge, product, day: str, settings: Settings):
        super().__init__()
        self.bridge, self.product, self.day, self.settings = bridge, product, day, settings

    def run(self) -> None:
        try:
            process_product(self.product, self.day, self.settings)
            self.bridge.processed.emit(self.product.id, "")
        except Exception as e:  # noqa: BLE001 - l'erreur est affichée sur la fiche
            traceback.print_exc()
            self.bridge.processed.emit(self.product.id, str(e))


class PreviewDialog(QDialog):
    def __init__(self, path: str, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Aperçu final – {Path(path).name}")
        layout = QVBoxLayout(self)
        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        pix = pil_to_pixmap(render_web(path, settings, max_side=1400))
        label.setPixmap(pix)
        scroll = QScrollArea()
        scroll.setWidget(label)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)
        self.resize(min(pix.width() + 40, 1500), min(pix.height() + 60, 1000))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1500, 900)
        self.settings = Settings.load()
        self.session: Session | None = None
        self.grouper: Grouper | None = None
        self.watcher: FolderWatcher | None = None
        self.thumbs: dict[str, QIcon] = {}
        self.video_icon = video_icon()
        self.pool = QThreadPool.globalInstance()
        self.pool.setMaxThreadCount(2)
        self.bridge = Bridge()
        self.bridge.fileArrived.connect(self._on_file)
        self.bridge.processed.connect(self._on_processed)

        self._build_toolbar()
        self._build_body()
        self._resume_session()
        self._start_camera()

    # --- construction ------------------------------------------------------
    def _build_toolbar(self) -> None:
        bar = self.addToolBar("Séance")
        bar.setMovable(False)
        for text, slot in (("Nouvelle séance", self.new_session), ("Produit suivant", self.next_product),
                           ("Réglages", self.open_settings)):
            action = QAction(text, self)
            action.triggered.connect(slot)
            bar.addAction(action)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy().Preferred)
        bar.addWidget(spacer)
        self.watch_label = QLabel()
        bar.addWidget(self.watch_label)

    def _build_body(self) -> None:
        splitter = QSplitter()
        self.setCentralWidget(splitter)

        left = QWidget()
        ll = QVBoxLayout(left)
        self.session_label = QLabel("Aucune séance")
        ll.addWidget(self.session_label)
        self.products = QListWidget()
        self.products.setIconSize(QSize(64, 64))
        self.products.currentRowChanged.connect(self._on_product_selected)
        ll.addWidget(self.products)
        splitter.addWidget(left)

        center = QWidget()
        cl = QVBoxLayout(center)
        self.media = QListWidget()
        self.media.setViewMode(QListWidget.IconMode)
        self.media.setIconSize(QSize(200, 200))
        self.media.setResizeMode(QListWidget.Adjust)
        self.media.setMovement(QListWidget.Static)
        self.media.setSpacing(8)
        self.media.itemDoubleClicked.connect(self._preview)
        cl.addWidget(self.media)
        actions = QHBoxLayout()
        for text, slot in (("Photo principale", self.set_main), ("◀", lambda: self.move(-1)),
                           ("▶", lambda: self.move(1)), ("Reprendre", self.retake),
                           ("Aperçu final", lambda: self._preview(self.media.currentItem())),
                           ("Supprimer", self.delete_media)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            actions.addWidget(b)
        cl.addLayout(actions)
        self.hint = QLabel()
        cl.addWidget(self.hint)
        self.camera = CameraPanel()
        self.camera.fileArrived.connect(self._on_file)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.camera, "Prise de vue")
        self.tabs.addTab(center, "Photos du produit")
        self.tabs.currentChanged.connect(lambda i: self.camera.set_liveview(self.tabs.widget(i) is self.camera))
        splitter.addWidget(self.tabs)

        self.form = ProductForm()
        self.form.processRequested.connect(self.process)
        self.form.openFolderRequested.connect(self._open_folder)
        self.form.changed.connect(lambda _id: (self._save_session(), self._refresh_products()))
        splitter.addWidget(self.form)
        splitter.setSizes([280, 800, 420])

    # --- séance ------------------------------------------------------------
    def _session_path(self) -> Path:
        return data_dir() / SESSION_FILE

    def _resume_session(self) -> None:
        path = self._session_path()
        if not path.exists():
            self.watch_label.setText("Lance une nouvelle séance pour commencer")
            return
        try:
            session = Session.load(path)
        except (OSError, ValueError, TypeError):
            return
        if any(p.status != PRET for p in session.products):
            self._start(session)

    def new_session(self) -> None:
        if not self.settings.watch_folder or not Path(self.settings.watch_folder).is_dir():
            QMessageBox.information(self, APP_NAME, "Choisis d'abord le dossier surveillé dans les réglages.")
            self.open_settings()
            if not Path(self.settings.watch_folder or "").is_dir():
                return
        labels = [k.label for k in self.settings.kinds]
        label, ok = QInputDialog.getItem(self, "Nouvelle séance", "Type de produit photographié :", labels, 0, False)
        if not ok:
            return
        if self.session and any(p.status not in (PRET,) for p in self.session.products):
            answer = QMessageBox.question(
                self, APP_NAME, "Des produits de la séance actuelle ne sont pas traités. Les abandonner ?")
            if answer != QMessageBox.Yes:
                return
        kind = self.settings.kinds[labels.index(label)]
        self._start(Session(kind_id=kind.id))

    def _start(self, session: Session) -> None:
        if self.watcher:
            self.watcher.stop()
        self.session = session
        kind = self.settings.kind(session.kind_id)
        self.grouper = Grouper(session, kind, self.settings.auto_split)
        for p in session.products:
            if p.status == EN_TRAITEMENT:
                p.status = A_COMPLETER
        self.session_label.setText(f"Séance du {session.day} · {kind.label}")
        self._save_session()
        self._refresh_products()
        if session.products:
            self.products.setCurrentRow(len(session.products) - 1)
        folder = self.settings.watch_folder
        if folder and Path(folder).is_dir():
            self.watcher = FolderWatcher(folder, self.bridge.fileArrived.emit)
            self.watcher.start()
            self.watch_label.setText(f"Surveillance : {folder}")
        else:
            self.watch_label.setText("Dossier surveillé introuvable, vérifie les réglages")

    def _start_camera(self) -> None:
        """Connexion automatique de l'appareil dès l'ouverture, si le pilotage direct est choisi."""
        self.camera.stop()
        mode, folder = self.settings.camera_mode, self.settings.watch_folder
        if mode == MODE_FOLDER:
            self.tabs.setTabVisible(0, False)
            self.tabs.setCurrentIndex(1)
            return
        self.tabs.setTabVisible(0, True)
        self.tabs.setCurrentIndex(0)
        if not folder or not Path(folder).is_dir():
            self.camera.state_label.setText("Choisis le dossier des photos reçues dans les réglages.")
            return
        try:
            backend = make_backend(mode, folder, self.settings.edsdk_folder)
        except CameraError as e:
            self.camera.state_label.setText(str(e))
            return
        if backend:
            self.camera.start(backend)

    def _save_session(self) -> None:
        if self.session:
            self.session.save(self._session_path())

    def next_product(self) -> None:
        if self.grouper:
            self.grouper.next_product()
            self._save_session()
            self._refresh_products()

    # --- réception des fichiers -------------------------------------------
    def _on_file(self, path: str) -> None:
        if not self.grouper:
            self.camera.status_label.setText(
                f"{Path(path).name} reçu, mais aucune séance n'est ouverte : clique sur « Nouvelle séance ».")
            return
        product = self.grouper.add(path)
        if product is None:
            return
        self._save_session()
        self._refresh_products()
        row = self.session.products.index(product)
        if self.products.currentRow() != row:
            self.products.setCurrentRow(row)
        else:
            self._refresh_media()

    # --- affichage ---------------------------------------------------------
    def _icon(self, path: str) -> QIcon:
        if path not in self.thumbs:
            try:
                if Path(path).suffix.lower() in VIDEO_EXT:
                    self.thumbs[path] = video_thumbnail_icon(path)
                else:
                    self.thumbs[path] = QIcon(pil_to_pixmap(thumbnail(path, 240)))
            except Exception:  # noqa: BLE001 - fichier en cours d'écriture ou illisible
                return self.video_icon if Path(path).suffix.lower() in VIDEO_EXT else QIcon()
        return self.thumbs[path]

    def _current_product(self):
        row = self.products.currentRow()
        if self.session and 0 <= row < len(self.session.products):
            return self.session.products[row]
        return None

    def _refresh_products(self) -> None:
        if not self.session:
            return
        row = self.products.currentRow()
        self.products.blockSignals(True)
        self.products.clear()
        for p in self.session.products:
            mark = " ●" if p.id == self.session.current_id else ""
            text = f"{p.display_name()}{mark}\n{STATUS_LABELS.get(p.status, p.status)} · {len(p.photos)} ph. {len(p.videos)} vid."
            item = QListWidgetItem(self._icon(p.photos[0]) if p.photos else self.video_icon, text)
            if p.status == ERREUR:
                item.setForeground(Qt.red)
            self.products.addItem(item)
        if 0 <= row < self.products.count():
            self.products.setCurrentRow(row)
        self.products.blockSignals(False)
        self.form.refresh_status()

    def _refresh_media(self) -> None:
        self.media.clear()
        product = self._current_product()
        if not product:
            return
        for i, path in enumerate(product.photos):
            label = "★ principale" if i == 0 else f"{i + 1}"
            item = QListWidgetItem(self._icon(path), label)
            item.setData(Qt.UserRole, path)
            self.media.addItem(item)
        for path in product.videos:
            item = QListWidgetItem(self._icon(path), Path(path).name)
            item.setData(Qt.UserRole, path)
            self.media.addItem(item)
        pending = self.grouper.pending_replace if self.grouper else None
        if pending and pending.product_id == product.id:
            self.hint.setText(f"La prochaine photo remplacera {Path(pending.path).name}")
        else:
            self.hint.clear()

    def _on_product_selected(self, _row: int) -> None:
        product = self._current_product()
        self.form.set_product(product, self.settings.kind(product.kind_id) if product else None)
        self._refresh_media()

    # --- actions sur les photos -------------------------------------------
    def _selected_path(self) -> str | None:
        item = self.media.currentItem()
        return item.data(Qt.UserRole) if item else None

    def set_main(self) -> None:
        product, path = self._current_product(), self._selected_path()
        if product and path:
            self.grouper.set_main(product.id, path)
            self._after_media_change()

    def move(self, offset: int) -> None:
        product, path = self._current_product(), self._selected_path()
        if product and path:
            self.grouper.move_photo(product.id, path, offset)
            self._after_media_change()
            for i in range(self.media.count()):
                if self.media.item(i).data(Qt.UserRole) == path:
                    self.media.setCurrentRow(i)

    def retake(self) -> None:
        product, path = self._current_product(), self._selected_path()
        if product and path:
            self.grouper.request_replace(product.id, path)
            self._refresh_media()

    def delete_media(self) -> None:
        product, path = self._current_product(), self._selected_path()
        if not (product and path):
            return
        self.grouper.remove(product.id, path)
        # Rien n'est effacé : le fichier part dans « _rejets » à côté des photos
        try:
            rejects = Path(path).parent / "_rejets"
            rejects.mkdir(exist_ok=True)
            shutil.move(path, rejects / Path(path).name)
        except OSError:
            pass
        self._after_media_change()

    def _after_media_change(self) -> None:
        product = self._current_product()
        if product and product.status == PRET:
            product.status = A_COMPLETER  # à retraiter pour mettre à jour le dossier
        self._save_session()
        self._refresh_products()
        self._refresh_media()

    def _preview(self, item) -> None:
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if not path:
            return
        if Path(path).suffix.lower() in VIDEO_EXT:
            # Lecture dans le lecteur vidéo de Windows ; version avec logo si le produit est déjà traité
            product = self._current_product()
            web = sorted(Path(product.folder, "web").glob("*_video.mp4")) if product and product.folder else []
            open_in_explorer(web[0] if web and product.status == PRET else path)
        elif Path(path).suffix.lower() in (".jpg", ".jpeg", ".png"):
            PreviewDialog(path, self.settings, self).exec()

    # --- traitement --------------------------------------------------------
    def process(self, product_id: str) -> None:
        product = self.session.get(product_id) if self.session else None
        if not product:
            return
        if missing := product.info.missing_required():
            QMessageBox.warning(self, APP_NAME, "À remplir avant le traitement : " + ", ".join(missing))
            return
        if not self.settings.output_folder:
            QMessageBox.information(self, APP_NAME, "Choisis d'abord le dossier de sortie dans les réglages.")
            return
        if self.session.current_id == product.id:
            self.grouper.next_product()
        product.status = EN_TRAITEMENT
        product.error = ""
        self._save_session()
        self._refresh_products()
        self.pool.start(ProcessJob(self.bridge, product, self.session.day, self.settings))
        self.camera.delete_card_movies([card_name(v) for v in product.videos])
        # On passe directement au produit suivant à compléter
        for i, p in enumerate(self.session.products):
            if p.status in (A_COMPLETER, EN_PRISE) and p.id != product.id:
                self.products.setCurrentRow(i)
                break

    def _on_processed(self, product_id: str, error: str) -> None:
        product = self.session.get(product_id) if self.session else None
        if not product:
            return
        if error:
            product.status, product.error = ERREUR, error
        self._save_session()
        self._refresh_products()
        if self._current_product() is product:
            self.form.refresh_status()

    def _open_folder(self, product_id: str) -> None:
        product = self.session.get(product_id) if self.session else None
        if product and product.folder:
            open_in_explorer(product.folder)

    # --- réglages ----------------------------------------------------------
    def open_settings(self) -> None:
        product = self._current_product()
        sample = product.photos[0] if product and product.photos else None
        dialog = SettingsDialog(self.settings, sample, self)
        if dialog.exec() == QDialog.Accepted:
            old = (self.settings.watch_folder, self.settings.camera_mode, self.settings.edsdk_folder)
            old_folder = old[0]
            new = dialog.result_settings()
            # Les compteurs de SKU ont pu avancer pendant que la fenêtre était ouverte
            new.sku_counters = dict(self.settings.sku_counters)
            self.settings = new
            self.settings.save()
            if self.session:
                self.grouper.kind = self.settings.kind(self.session.kind_id)
                self.grouper.auto_split = self.settings.auto_split
                if self.settings.watch_folder != old_folder:
                    self._start(self.session)
            if (self.settings.watch_folder, self.settings.camera_mode, self.settings.edsdk_folder) != old:
                self._start_camera()

    def closeEvent(self, event) -> None:
        self.form.commit()
        self._save_session()
        if self.watcher:
            self.watcher.stop()
        self.camera.stop()
        self.pool.waitForDone(30000)
        super().closeEvent(event)
