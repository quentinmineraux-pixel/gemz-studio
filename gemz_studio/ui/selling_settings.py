"""Onglets de réglages de la vente : prix, IA, connexions aux boutiques, synchro et notifications.

Les clés et jetons vont dans le coffre chiffré (vault), jamais dans settings.json.
"""
from __future__ import annotations

import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QStyle, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from .. import notify, vault
from ..config import Settings
from ..listing import ai
from ..listing.prices import fmt_eur, platform_price, to_usd
from ..platforms import ebay as ebay_mod, etsy as etsy_mod, shopify as shopify_mod
from .common import run_in_background

PENDING = "<span style='color:#b9770e'>À brancher ensemble, étape par étape.</span>"


def scrolled(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidget(widget)
    area.setWidgetResizable(True)
    return area


class SecretField(QLineEdit):
    """Champ masqué : vide = on garde la valeur déjà enregistrée."""

    def __init__(self, name: str, placeholder: str = ""):
        super().__init__()
        self.name = name
        self.setEchoMode(QLineEdit.Password)
        self.cleared = False
        stored = bool(vault.get(name))
        self.setPlaceholderText("•••••• enregistré (laisser vide pour garder)" if stored else placeholder)
        if stored:
            erase = self.addAction(self.style().standardIcon(QStyle.SP_DialogDiscardButton), QLineEdit.TrailingPosition)
            erase.setToolTip("Effacer la valeur enregistrée")
            erase.triggered.connect(self._clear)

    def _clear(self) -> None:
        self.cleared = True
        self.clear()
        self.setPlaceholderText("sera effacé à l'enregistrement")

    def collect(self, into: dict[str, str]) -> None:
        if self.text().strip():
            into[self.name] = self.text().strip()
        elif self.cleared:
            into[self.name] = ""   # vide = retiré du coffre


def money_spin(value: float, maximum: float = 1000, suffix: str = " €", decimals: int = 2) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(0, maximum)
    s.setDecimals(decimals)
    s.setSuffix(suffix)
    s.setValue(value)
    return s


# --- prix -------------------------------------------------------------------
class PricesTab(QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        layout = QVBoxLayout(self)
        box = QGroupBox("Prix des places de marché (à partir du prix Shopify TTC)")
        form = QFormLayout(box)
        self.etsy = money_spin(settings.etsy_markup, 300, " %", 1)
        self.ebay = money_spin(settings.ebay_markup, 300, " %", 1)
        self.usd = money_spin(settings.usd_per_eur, 5, " $ pour 1 €", 3)
        self.example = QLabel()
        form.addRow("Majoration Etsy", self.etsy)
        form.addRow("Majoration eBay", self.ebay)
        form.addRow("Conversion ebay.com", self.usd)
        form.addRow("Exemple", self.example)
        layout.addWidget(box)
        for spin in (self.etsy, self.ebay, self.usd):
            spin.valueChanged.connect(self._example)

        sizes = QGroupBox("Tailles")
        sl = QFormLayout(sizes)
        self.resize_price = money_spin(settings.ring_resize_price, 200)
        self.resize_days = QSpinBox()
        self.resize_days.setRange(0, 60)
        self.resize_days.setSuffix(" jours")
        self.resize_days.setValue(settings.ring_resize_days)
        self.ring_min, self.ring_max = QSpinBox(), QSpinBox()
        for spin, value in ((self.ring_min, min(settings.ring_sizes or [40])),
                            (self.ring_max, max(settings.ring_sizes or [70]))):
            spin.setRange(30, 80)
            spin.setValue(value)
        ring_row = QHBoxLayout()
        ring_row.addWidget(QLabel("de"))
        ring_row.addWidget(self.ring_min)
        ring_row.addWidget(QLabel("à"))
        ring_row.addWidget(self.ring_max)
        ring_row.addStretch(1)
        self.bracelets = QLineEdit(", ".join(settings.bracelet_sizes))
        sl.addRow("Bague : supplément mise à taille", self.resize_price)
        sl.addRow("Bague : délai de mise à taille", self.resize_days)
        sl.addRow("Bague : tailles proposées (FR)", ring_row)
        sl.addRow("Bracelets : tours de poignet", self.bracelets)
        layout.addWidget(sizes)

        defaults = QGroupBox("Plateformes cochées d'office sur un nouveau produit (Shopify toujours)")
        dl = QHBoxLayout(defaults)
        self.default_etsy = QCheckBox("Etsy")
        self.default_etsy.setChecked(settings.default_etsy)
        self.default_ebay = QCheckBox("eBay")
        self.default_ebay.setChecked(settings.default_ebay)
        dl.addWidget(self.default_etsy)
        dl.addWidget(self.default_ebay)
        dl.addStretch(1)
        layout.addWidget(defaults)
        layout.addStretch(1)
        self._example()

    def _example(self) -> None:
        etsy = platform_price(100, self.etsy.value())
        ebay = platform_price(100, self.ebay.value())
        self.example.setText(f"100,00 € sur Shopify → Etsy {fmt_eur(etsy)} · eBay {fmt_eur(ebay)} · "
                             f"ebay.com ${to_usd(ebay, self.usd.value()):.2f}")

    def collect(self, s: Settings) -> None:
        s.etsy_markup, s.ebay_markup, s.usd_per_eur = self.etsy.value(), self.ebay.value(), self.usd.value()
        s.ring_resize_price, s.ring_resize_days = self.resize_price.value(), self.resize_days.value()
        low, high = sorted((self.ring_min.value(), self.ring_max.value()))
        s.ring_sizes = list(range(low, high + 1))
        s.bracelet_sizes = [b.strip() for b in self.bracelets.text().split(",") if b.strip()] or ["17 cm", "19 cm", "21 cm"]
        s.default_etsy, s.default_ebay = self.default_etsy.isChecked(), self.default_ebay.isChecked()

    def secrets(self, into: dict[str, str]) -> None:
        pass


# --- IA ---------------------------------------------------------------------
class AiTab(QWidget):
    def __init__(self, settings: Settings, catalog=None):
        super().__init__()
        layout = QVBoxLayout(self)
        box = QGroupBox("Rédaction des fiches par l'IA (Claude)")
        form = QFormLayout(box)
        self.key = SecretField(ai.SECRET_KEY, "sk-ant-…")
        get_key = QPushButton("Créer une clé…")
        get_key.clicked.connect(lambda: webbrowser.open("https://console.anthropic.com/settings/keys"))
        key_row = QHBoxLayout()
        key_row.addWidget(self.key, 1)
        key_row.addWidget(get_key)
        self.model = QComboBox()
        for model_id, (label, _in, _out) in ai.MODELS.items():
            self.model.addItem(label, model_id)
        self.model.setCurrentIndex(max(0, self.model.findData(settings.ai_model)))
        self.cap = money_spin(settings.ai_monthly_cap_eur, 100)
        self.photos = QSpinBox()
        self.photos.setRange(0, 4)
        self.photos.setValue(settings.ai_photos)
        self.auto = QCheckBox("Rédiger la fiche automatiquement à la fin du traitement des photos")
        self.auto.setChecked(settings.ai_auto)
        form.addRow("Clé API Anthropic", key_row)
        form.addRow("Modèle", self.model)
        form.addRow("Plafond mensuel", self.cap)
        form.addRow("Photos envoyées à l'IA", self.photos)
        form.addRow(self.auto)
        spent = ai.month_cost(catalog) if catalog is not None else 0.0
        self.usage = QLabel(f"Dépensé ce mois-ci : {spent:.2f} €. Au-delà du plafond, l'IA s'arrête et les fiches "
                            "restent à compléter à la main.")
        self.usage.setWordWrap(True)
        form.addRow(self.usage)
        layout.addWidget(box)
        note = QLabel("Les textes fixes (avertissement lithothérapie, note photo, intitulés) sont déjà traduits dans "
                      "l'outil : l'IA ne rédige que l'aspect, l'anecdote, la lithothérapie et les tags, en un seul "
                      "appel par produit.")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

    def collect(self, s: Settings) -> None:
        s.ai_model = self.model.currentData()
        s.ai_monthly_cap_eur = self.cap.value()
        s.ai_photos = self.photos.value()
        s.ai_auto = self.auto.isChecked()

    def secrets(self, into: dict[str, str]) -> None:
        self.key.collect(into)


# --- connexions -------------------------------------------------------------
class ConnectionsTab(QWidget):
    def __init__(self, settings: Settings, get_settings):
        super().__init__()
        self.get_settings = get_settings   # réglages en cours d'édition (pour tester avant d'enregistrer)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scrolled(inner))

        # Shopify
        shop = QGroupBox("Shopify (gem-z.fr) – référence du stock")
        sf = QFormLayout(shop)
        self.shop_state = QLabel()
        self.shop_domain = QLineEdit(settings.shopify_domain)
        self.shop_domain.setPlaceholderText("xxxx.myshopify.com")
        self.shop_client = SecretField(shopify_mod.SECRET_CLIENT_ID, "identifiant client de l'application")
        self.shop_secret = SecretField(shopify_mod.SECRET_CLIENT_SECRET, "secret de l'application")
        self.shop_token = SecretField(shopify_mod.SECRET_TOKEN, "ou jeton d'accès shpat_… (ancienne méthode)")
        self.shop_location = QLineEdit(settings.shopify_location_id)
        self.shop_location.setPlaceholderText("automatique (le seul emplacement de la boutique)")
        test = QPushButton("Tester la connexion")
        test.clicked.connect(lambda: self._test("shopify", self.shop_state))
        sf.addRow(self.shop_state)
        sf.addRow("Adresse de la boutique", self.shop_domain)
        sf.addRow("Identifiant client", self.shop_client)
        sf.addRow("Secret", self.shop_secret)
        sf.addRow("Jeton d'accès", self.shop_token)
        sf.addRow("Emplacement du stock", self.shop_location)
        self.shop_archive = QCheckBox("Pièce vendue (stock 0) : la retirer de la boutique (archivée, récupérable)")
        self.shop_archive.setChecked(settings.shopify_archive_sold)
        sf.addRow(self.shop_archive)
        sf.addRow(test)
        layout.addWidget(shop)

        # Etsy
        etsy = QGroupBox("Etsy (StoreGemZ)")
        ef = QFormLayout(etsy)
        self.etsy_state = QLabel()
        self.etsy_key = SecretField(etsy_mod.SECRET_KEYSTRING, "keystring de l'application Etsy")
        self.etsy_shared = SecretField(etsy_mod.SECRET_SHARED, "shared secret")
        connect = QPushButton("Connecter Etsy (ouvre le navigateur)")
        connect.clicked.connect(self._connect_etsy)
        self.etsy_shop = QLabel(settings.etsy_shop_id or "—")
        self.etsy_shipping = QComboBox()
        self.etsy_returns = QComboBox()
        for combo, value in ((self.etsy_shipping, settings.etsy_shipping_profile_id),
                             (self.etsy_returns, settings.etsy_return_policy_id)):
            combo.setEditable(True)
            if value:
                combo.addItem(value, value)
        load = QPushButton("Charger les profils de la boutique")
        load.clicked.connect(self._load_etsy_profiles)
        self.etsy_when = QComboBox()
        # Valeurs exactes d'Etsy (choisies ensemble au branchement, selon les annonces existantes)
        self.etsy_when.setEditable(True)
        self.etsy_when.addItems(["made_to_order"])
        self.etsy_when.setCurrentText(settings.etsy_when_made)
        self.etsy_ready = QLineEdit(settings.etsy_readiness_state_id)
        self.etsy_ready.setPlaceholderText("délai de préparation (rempli au branchement)")
        self.etsy_lang = QComboBox()
        for code, label in (("fr", "Français (langue de la boutique)"), ("en", "Anglais")):
            self.etsy_lang.addItem(label, code)
        self.etsy_lang.setCurrentIndex(max(0, self.etsy_lang.findData(settings.etsy_shop_language)))
        ef.addRow(self.etsy_state)
        ef.addRow("Keystring", self.etsy_key)
        ef.addRow("Shared secret", self.etsy_shared)
        ef.addRow(connect)
        ef.addRow("Boutique", self.etsy_shop)
        ef.addRow("Profil d'expédition", self.etsy_shipping)
        ef.addRow("Politique de retour", self.etsy_returns)
        ef.addRow(load)
        ef.addRow("Date de fabrication", self.etsy_when)
        ef.addRow("Délai de préparation", self.etsy_ready)
        ef.addRow("Langue principale", self.etsy_lang)
        layout.addWidget(etsy)

        # eBay
        ebay = QGroupBox("eBay (ebay.fr et ebay.com)")
        bf = QFormLayout(ebay)
        self.ebay_state = QLabel()
        self.ebay_client = SecretField(ebay_mod.SECRET_CLIENT_ID, "App ID (Client ID)")
        self.ebay_secret = SecretField(ebay_mod.SECRET_CLIENT_SECRET, "Cert ID (Client Secret)")
        self.ebay_runame = QLineEdit(settings.ebay_ru_name)
        self.ebay_runame.setPlaceholderText("RuName (adresse de retour de l'application)")
        connect_ebay = QPushButton("Connecter eBay (ouvre le navigateur)")
        connect_ebay.clicked.connect(self._connect_ebay)
        self.ebay_fr = QCheckBox("ebay.fr")
        self.ebay_us = QCheckBox("ebay.com (prix en dollars)")
        self.ebay_fr.setChecked("EBAY_FR" in settings.ebay_marketplaces)
        self.ebay_us.setChecked("EBAY_US" in settings.ebay_marketplaces)
        markets = QHBoxLayout()
        markets.addWidget(self.ebay_fr)
        markets.addWidget(self.ebay_us)
        markets.addStretch(1)
        self.ebay_location = QLineEdit(settings.ebay_location_key)
        self.ebay_location.setPlaceholderText("clé de l'emplacement d'expédition (créé au branchement)")
        self.ebay_policies = QTableWidget(2, 4)
        self.ebay_policies.setHorizontalHeaderLabels(["Site", "Livraison", "Paiement", "Retour"])
        self.ebay_policies.verticalHeader().setVisible(False)
        for row, market in enumerate(("EBAY_FR", "EBAY_US")):
            item = QTableWidgetItem(market)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.ebay_policies.setItem(row, 0, item)
            values = settings.ebay_policies.get(market, {})
            for col, key in enumerate(("fulfillment", "payment", "return"), start=1):
                self.ebay_policies.setItem(row, col, QTableWidgetItem(values.get(key, "")))
        self.ebay_policies.setMaximumHeight(100)
        fill = QPushButton("Remplir avec les politiques du compte")
        fill.clicked.connect(self._load_ebay_policies)
        bf.addRow(self.ebay_state)
        bf.addRow("Client ID", self.ebay_client)
        bf.addRow("Client Secret", self.ebay_secret)
        bf.addRow("RuName", self.ebay_runame)
        bf.addRow(connect_ebay)
        bf.addRow("Sites", markets)
        bf.addRow("Emplacement", self.ebay_location)
        bf.addRow("Politiques", self.ebay_policies)
        bf.addRow(fill)
        layout.addWidget(ebay)

        # Catégories par type de produit
        cats = QGroupBox("Catégories Etsy et eBay par type de produit")
        cl = QVBoxLayout(cats)
        self.kind_ids = [k.id for k in settings.kinds]
        self.categories = QTableWidget(len(settings.kinds), 3)
        self.categories.setHorizontalHeaderLabels(["Type", "Catégorie Etsy (n°)", "Catégorie eBay (n°)"])
        self.categories.verticalHeader().setVisible(False)
        for row, kind in enumerate(settings.kinds):
            item = QTableWidgetItem(kind.label)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.categories.setItem(row, 0, item)
            self.categories.setItem(row, 1, QTableWidgetItem(str(kind.etsy_taxonomy_id or "")))
            self.categories.setItem(row, 2, QTableWidgetItem(kind.ebay_category_id))
        self.categories.resizeColumnsToContents()
        search = QPushButton("Chercher une catégorie Etsy pour la ligne choisie…")
        search.clicked.connect(self._search_taxonomy)
        cl.addWidget(self.categories)
        cl.addWidget(search)
        layout.addWidget(cats)
        layout.addStretch(1)
        self._refresh_states()

    # --- état --------------------------------------------------------------
    def _platforms(self):
        from ..platforms import make_platforms
        return make_platforms(self.get_settings())

    def _refresh_states(self) -> None:
        platforms = self._platforms()
        for name, label in (("shopify", self.shop_state), ("etsy", self.etsy_state), ("ebay", self.ebay_state)):
            missing = platforms[name].missing_setup()
            label.setText(f"{PENDING}<br>{missing}" if missing else
                          "<span style='color:#1e8449'>Identifiants enregistrés.</span> Teste la connexion pour vérifier.")
            label.setWordWrap(True)

    def save_secrets_now(self) -> None:
        values: dict[str, str] = {}
        self.secrets(values)
        if values:
            vault.set_many(values)

    def _test(self, name: str, label: QLabel) -> None:
        self.save_secrets_now()
        platform = self._platforms()[name]
        label.setText("Test en cours…")

        def done(result, error):
            if error:
                label.setText(f"<span style='color:#c0392b'>{error}</span>")
            else:
                label.setText(f"<span style='color:#1e8449'>{result}</span>")

        run_in_background(platform.test, done)

    def _connect_etsy(self) -> None:
        self.save_secrets_now()
        settings = self.get_settings()
        from ..platforms.etsy import Etsy
        platform = Etsy(settings)
        self.etsy_state.setText("Valide l'accès dans la page Etsy ouverte dans ton navigateur…")

        def done(result, error):
            if error:
                self.etsy_state.setText(f"<span style='color:#c0392b'>{error}</span>")
                return
            self.etsy_shop.setText(f"{result} ({settings.etsy_shop_id})")
            self._etsy_shop_id = settings.etsy_shop_id
            self.etsy_state.setText(f"<span style='color:#1e8449'>Connecté à {result}.</span> "
                                    "Charge maintenant les profils de la boutique.")

        run_in_background(platform.authorize, done)

    def _load_etsy_profiles(self) -> None:
        from ..platforms.etsy import Etsy
        platform = Etsy(self.get_settings())

        def fetch():
            return platform.shipping_profiles(), platform.return_policies()

        def done(result, error):
            if error:
                QMessageBox.warning(self, "Etsy", str(error))
                return
            for combo, rows in zip((self.etsy_shipping, self.etsy_returns), result):
                current = combo.currentData() or combo.currentText()
                combo.clear()
                for value, label in rows:
                    combo.addItem(f"{label} ({value})", value)
                index = combo.findData(current)
                combo.setCurrentIndex(index if index >= 0 else 0)

        run_in_background(fetch, done)

    def _connect_ebay(self) -> None:
        self.save_secrets_now()
        from ..platforms.ebay import Ebay
        platform = Ebay(self.get_settings())
        try:
            url = platform.authorize_url()
        except Exception as e:  # noqa: BLE001
            QMessageBox.information(self, "eBay", str(e))
            return
        webbrowser.open(url)
        pasted, ok = QInputDialog.getText(
            self, "eBay", "Après avoir accepté sur eBay, copie ici l'adresse complète de la page affichée :")
        if not ok or not pasted.strip():
            return
        self.ebay_state.setText("Connexion…")

        def done(_result, error):
            if error:
                self.ebay_state.setText(f"<span style='color:#c0392b'>{error}</span>")
            else:
                self.ebay_state.setText("<span style='color:#1e8449'>Connecté à eBay.</span> "
                                        "Remplis maintenant les politiques.")

        run_in_background(lambda: platform.finish_authorization(pasted), done)

    def _load_ebay_policies(self) -> None:
        from ..platforms.ebay import Ebay
        platform = Ebay(self.get_settings())

        def fetch():
            out = {}
            for market in ("EBAY_FR", "EBAY_US"):
                out[market] = {kind: (platform.policies(kind, market) or [("", "")])[0][0]
                               for kind in ("fulfillment", "payment", "return")}
            return out

        def done(result, error):
            if error:
                QMessageBox.warning(self, "eBay", str(error))
                return
            for row, market in enumerate(("EBAY_FR", "EBAY_US")):
                for col, key in enumerate(("fulfillment", "payment", "return"), start=1):
                    self.ebay_policies.setItem(row, col, QTableWidgetItem(result[market][key]))

        run_in_background(fetch, done)

    def _search_taxonomy(self) -> None:
        row = self.categories.currentRow()
        if row < 0:
            QMessageBox.information(self, "Etsy", "Choisis d'abord une ligne (type de produit).")
            return
        term, ok = QInputDialog.getText(self, "Etsy", "Mot à chercher (en anglais, ex. « mineral », « ring ») :")
        if not ok or not term.strip():
            return
        from ..platforms.etsy import Etsy
        platform = Etsy(self.get_settings())

        def done(result, error):
            if error:
                QMessageBox.warning(self, "Etsy", str(error))
                return
            if not result:
                QMessageBox.information(self, "Etsy", "Aucune catégorie trouvée.")
                return
            labels = [f"{path} ({node})" for node, path in result]
            choice, ok = QInputDialog.getItem(self, "Etsy", "Catégorie :", labels, 0, False)
            if ok:
                self.categories.setItem(row, 1, QTableWidgetItem(str(result[labels.index(choice)][0])))

        run_in_background(lambda: platform.search_taxonomy(term.strip()), done)

    # --- enregistrement ----------------------------------------------------
    def collect(self, s: Settings) -> None:
        s.shopify_domain = self.shop_domain.text().strip()
        s.shopify_location_id = self.shop_location.text().strip()
        s.shopify_archive_sold = self.shop_archive.isChecked()
        shop_id = getattr(self, "_etsy_shop_id", "")
        if shop_id:
            s.etsy_shop_id = shop_id
        s.etsy_shipping_profile_id = str(self.etsy_shipping.currentData() or self.etsy_shipping.currentText()).strip()
        s.etsy_return_policy_id = str(self.etsy_returns.currentData() or self.etsy_returns.currentText()).strip()
        s.etsy_when_made = self.etsy_when.currentText().strip() or "made_to_order"
        s.etsy_readiness_state_id = self.etsy_ready.text().strip()
        s.etsy_shop_language = self.etsy_lang.currentData()
        s.ebay_ru_name = self.ebay_runame.text().strip()
        s.ebay_marketplaces = [m for m, box in (("EBAY_FR", self.ebay_fr), ("EBAY_US", self.ebay_us)) if box.isChecked()]
        s.ebay_location_key = self.ebay_location.text().strip()
        policies = {}
        for row, market in enumerate(("EBAY_FR", "EBAY_US")):
            values = {}
            for col, key in enumerate(("fulfillment", "payment", "return"), start=1):
                cell = self.ebay_policies.item(row, col)
                if cell and cell.text().strip():
                    values[key] = cell.text().strip()
            if values:
                policies[market] = values
        s.ebay_policies = policies
        for row, kind_id in enumerate(self.kind_ids):
            kind = s.kind(kind_id)
            etsy_cell, ebay_cell = self.categories.item(row, 1), self.categories.item(row, 2)
            text = etsy_cell.text().strip() if etsy_cell else ""
            kind.etsy_taxonomy_id = int(text) if text.isdigit() else 0
            kind.ebay_category_id = ebay_cell.text().strip() if ebay_cell else ""

    def secrets(self, into: dict[str, str]) -> None:
        for field in (self.shop_client, self.shop_secret, self.shop_token, self.etsy_key, self.etsy_shared,
                      self.ebay_client, self.ebay_secret):
            field.collect(into)


# --- synchro et notifications ----------------------------------------------
class SyncTab(QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        layout = QVBoxLayout(self)
        box = QGroupBox("Synchro du stock (tant que GemZ Studio est ouvert)")
        form = QFormLayout(box)
        self.enabled = QCheckBox("Vérifier les ventes et recopier le stock sur toutes les plateformes")
        self.enabled.setChecked(settings.sync_enabled)
        self.minutes = QSpinBox()
        self.minutes.setRange(1, 60)
        self.minutes.setSuffix(" min")
        self.minutes.setValue(settings.sync_minutes)
        self.paused = QCheckBox("Mettre la publication en pause (les fiches attendent dans la file)")
        self.paused.setChecked(settings.publish_paused)
        form.addRow(self.enabled)
        form.addRow("Toutes les", self.minutes)
        form.addRow(self.paused)
        layout.addWidget(box)

        phone = QGroupBox("Notifications sur le téléphone (application gratuite ntfy)")
        pf = QFormLayout(phone)
        self.topic = QLineEdit(settings.ntfy_topic)
        self.topic.setPlaceholderText("nom de canal secret, ex. gemz-ventes-8k2p")
        test = QPushButton("Envoyer une notification de test")
        test.clicked.connect(self._test)
        self.state = QLabel("Installe ntfy sur ton téléphone, abonne-toi au même nom de canal, puis teste. "
                            "Tu seras prévenu à chaque vente et en cas de vente en double.")
        self.state.setWordWrap(True)
        pf.addRow("Canal", self.topic)
        pf.addRow(test)
        pf.addRow(self.state)
        layout.addWidget(phone)
        layout.addStretch(1)

    def _test(self) -> None:
        topic = self.topic.text().strip()
        if not topic:
            self.state.setText("Choisis d'abord un nom de canal.")
            return

        def done(_result, error):
            self.state.setText(f"<span style='color:#c0392b'>Échec : {error}</span>" if error
                               else "<span style='color:#1e8449'>Envoyée : regarde ton téléphone.</span>")

        run_in_background(lambda: notify.phone_now(topic, "GemZ Studio", "Les notifications fonctionnent 🎉"), done)

    def collect(self, s: Settings) -> None:
        s.sync_enabled = self.enabled.isChecked()
        s.sync_minutes = self.minutes.value()
        s.publish_paused = self.paused.isChecked()
        s.ntfy_topic = self.topic.text().strip()

    def secrets(self, into: dict[str, str]) -> None:
        pass
