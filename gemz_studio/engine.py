"""Moteur de fond : file de publication et synchro du stock entre Shopify, Etsy et eBay.

Il tourne dans un thread de l'application, tant que le PC est allumé (décision du cahier des
charges). Au démarrage, il rattrape d'abord les ventes faites pendant l'arrêt, avant toute publication.
"""
from __future__ import annotations

import re
import threading
import time
import traceback
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import notify
from .catalog import (A_METTRE_A_JOUR, A_PUBLIER, EPUISE, ERREUR, PLATFORM_LABELS, PLATFORMS, PUBLIE,
                      Catalog, CatalogItem)
from .config import Settings
from .platforms import NotConfigured, PlatformError, make_platforms

TICK = 5.0
OVERLAP = timedelta(minutes=15)      # les commandes arrivent parfois avec un peu de retard dans les API
DRIFT_EVERY = 10                     # contrôle du stock Shopify toutes les 10 synchros (~30 min)
MAX_BACKOFF = 6 * 3600
VARIANT_SUFFIX = re.compile(r"-(T\d{2}|\d{2,3}|US)$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def base_sku(sku: str) -> str:
    """MFC20-T56 (taille de bague), BR3-17 (tour de poignet), MFC20-US (ebay.com) -> MFC20."""
    return VARIANT_SUFFIX.sub("", sku.strip().upper())


class Engine:
    def __init__(self, settings: Settings, catalog: Catalog,
                 on_notify: Callable[[str, str], None] = lambda title, text: None,
                 on_status: Callable[[str], None] = lambda text: None):
        self.settings = settings
        self.catalog = catalog
        self.on_notify = on_notify
        self.on_status = on_status
        self.platforms = make_platforms(settings)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.caught_up = False        # vrai après le rattrapage des ventes du démarrage
        self._next_sync = 0.0
        self._sync_count = 0

    # --- cycle de vie --------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="gemz-moteur", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=20)

    def wake(self) -> None:
        """Nouvelle fiche ou modification : traiter la file sans attendre."""
        self._wake.set()

    def update_settings(self, settings: Settings) -> None:
        with self._lock:
            self.settings = settings
            self.platforms = make_platforms(settings)
        self.wake()

    def sync_now(self) -> None:
        self._next_sync = 0.0
        self.wake()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with self._lock:
                    now = time.time()
                    if self.settings.sync_enabled and now >= self._next_sync:
                        self._next_sync = now + max(1, self.settings.sync_minutes) * 60
                        self.sync()
                        self.caught_up = True
                    elif not self.settings.sync_enabled:
                        self.caught_up = True
                    if self.caught_up and not self.settings.publish_paused:
                        self.process_queue()
            except Exception as e:  # noqa: BLE001 - le moteur ne doit jamais s'arrêter
                traceback.print_exc()
                self.catalog.log("erreur", f"Erreur interne : {e}")
            self._wake.wait(TICK)
            self._wake.clear()

    # --- file de publication -------------------------------------------------
    def pending(self) -> list[tuple[CatalogItem, str]]:
        now = time.time()
        out = []
        for item in self.catalog.all():
            if not item.listing.ready or item.stock <= 0:
                continue
            for name in PLATFORMS:
                state = item.state(name)
                if not state.wanted or state.status not in (A_PUBLIER, A_METTRE_A_JOUR, ERREUR):
                    continue
                if state.next_try > now:
                    continue
                # Shopify d'abord : c'est la référence du stock
                if name != "shopify" and item.state("shopify").wanted and item.state("shopify").status != PUBLIE:
                    continue
                out.append((item, name))
        out.sort(key=lambda pair: PLATFORMS.index(pair[1]))
        return out

    def process_queue(self) -> None:
        for item, name in self.pending():
            if self._stop.is_set():
                return
            self.publish_one(item, name)

    def publish_one(self, item: CatalogItem, name: str) -> None:
        platform = self.platforms[name]
        state = replace(item.state(name))
        was_update = state.status == A_METTRE_A_JOUR or bool(state.remote_id and state.status == PUBLIE)
        label = PLATFORM_LABELS[name]
        english = name == "etsy" and self.settings.etsy_language == "en" or \
            name == "ebay" and "EBAY_US" in self.settings.ebay_marketplaces
        if english and not item.listing.has_lang("en"):
            state.error = "Textes anglais à compléter dans la fiche (ou à rédiger avec l'IA)."
            state.status = A_PUBLIER
            state.next_try = time.time() + 600
            self._save_state(item.sku, name, state)
            return
        if not platform.configured():
            state.error = platform.missing_setup()
            state.status = A_PUBLIER
            state.next_try = time.time() + 600
            self._save_state(item.sku, name, state)
            return
        self.on_status(f"{label} : envoi de « {item.title} »…")
        try:
            if was_update:
                platform.update(item, state)
            else:
                platform.publish(item, state)
        except NotConfigured as e:
            state.status, state.error = A_PUBLIER, str(e)
            state.next_try = time.time() + 600
            self._save_state(item.sku, name, state)
            return
        except Exception as e:  # noqa: BLE001 - erreur réseau ou refus de la plateforme
            state.attempts += 1
            state.status, state.error = ERREUR, str(e)
            retryable = isinstance(e, PlatformError) and e.retryable or not isinstance(e, PlatformError)
            delay = min(MAX_BACKOFF, 60 * 2 ** state.attempts) if retryable else min(MAX_BACKOFF, 1800 * state.attempts)
            state.next_try = time.time() + delay
            self._save_state(item.sku, name, state)
            self.catalog.log("erreur", f"{label} : {e}", item.sku, name)
            if state.attempts == 3:
                self.on_notify(f"Publication {label} en échec", f"{item.title} : {e}")
            return
        state.status, state.error, state.attempts, state.next_try = PUBLIE, "", 0, 0.0
        state.published_at = state.published_at or utc_now().isoformat(timespec="seconds")
        self._save_state(item.sku, name, state)
        self.catalog.log("publication", f"{label} : {'mis à jour' if was_update else 'en ligne'}", item.sku, name,
                         {"url": state.url})
        self.on_status(f"{label} : « {item.title} » {'mis à jour' if was_update else 'en ligne'}")

    def _save_state(self, sku: str, name: str, state) -> None:
        def apply(it: CatalogItem):
            it.platforms[name] = state
        self.catalog.update(sku, apply)

    # --- synchro du stock ----------------------------------------------------
    def sync(self) -> None:
        active = {n: p for n, p in self.platforms.items() if p.configured()}
        if not active:
            return
        self.on_status("Synchro des ventes…")
        for name, platform in active.items():
            cursor_key = f"sync_depuis_{name}"
            started = utc_now()
            since_text = self.catalog.kv_get(cursor_key)
            since = datetime.fromisoformat(since_text) - OVERLAP if since_text else started - timedelta(hours=1)
            try:
                sales = platform.sales_since(since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
            except Exception as e:  # noqa: BLE001
                self.catalog.log("erreur", f"Synchro {PLATFORM_LABELS[name]} : {e}", platform=name)
                continue
            for sale in sales:
                self.handle_sale(sale)
            self.catalog.kv_set(cursor_key, started.isoformat())
        self.push_pending_stock()
        self._sync_count += 1
        if self._sync_count % DRIFT_EVERY == 1:
            self.check_shopify_stock()
        self.on_status(f"Synchro faite à {datetime.now():%H:%M}")

    def find_item(self, sale) -> CatalogItem | None:
        if sale.sku:
            item = self.catalog.get(base_sku(sale.sku))
            if item:
                return item
        if sale.remote_id:
            return self.catalog.find_remote(sale.platform, sale.remote_id)
        return None

    def handle_sale(self, sale) -> None:
        if self.catalog.seen(sale.key):
            return
        self.catalog.mark_seen(sale.key)
        if sale.cancelled:
            return
        item = self.find_item(sale)
        label = PLATFORM_LABELS.get(sale.platform, sale.platform)
        if item is None:
            self.catalog.log("vente", f"Vente {label} hors catalogue : {sale.title} ({sale.price:.2f} {sale.currency})",
                             sale.sku, sale.platform, {"prix": sale.price, "devise": sale.currency})
            notify.phone(self.settings.ntfy_topic, f"Vente {label}", f"{sale.title} – {sale.price:.2f} {sale.currency}")
            return

        if item.stock <= 0:
            self.catalog.log("erreur", f"Pièce déjà épuisée vendue sur {label} : {item.title} (commande {sale.order_id})",
                             item.sku, sale.platform)
            self.on_notify("Vente en double !", f"{item.title} vendu sur {label} alors qu'il était épuisé.")

        def apply(it: CatalogItem):
            it.stock = max(0, it.stock - sale.quantity)
            for name in PLATFORMS:
                st = it.state(name)
                if not st.remote_id:
                    continue
                if it.stock == 0:
                    st.status = EPUISE
                # La plateforme qui a vendu a déjà retiré la pièce ; les autres (Shopify compris) sont mises à jour
                if name != sale.platform:
                    st.extra["stock_a_envoyer"] = True

        item = self.catalog.update(item.sku, apply)
        message = f"{item.title} – {sale.price:.2f} {sale.currency}" + (" · épuisé partout" if item.stock == 0 else f" · reste {item.stock}")
        self.catalog.log("vente", f"Vente {label} : {message}", item.sku, sale.platform,
                         {"prix": sale.price, "devise": sale.currency, "commande": sale.order_id, "quantite": sale.quantity})
        notify.phone(self.settings.ntfy_topic, f"Vente {label} !", message, ["moneybag"])
        self.on_notify(f"Vente {label}", message)
        self.push_pending_stock(item.sku)

    def push_pending_stock(self, only_sku: str | None = None) -> None:
        """Recopie le stock du catalogue sur les plateformes en retard (nouvel essai à chaque synchro)."""
        items = [self.catalog.get(only_sku)] if only_sku else self.catalog.all()
        for item in items:
            if item is None:
                continue
            for name in PLATFORMS:
                state = item.state(name)
                if not state.extra.get("stock_a_envoyer"):
                    continue
                platform = self.platforms[name]
                if not platform.configured():
                    continue
                try:
                    platform.set_quantity(item, state, item.stock)
                except Exception as e:  # noqa: BLE001
                    self.catalog.log("erreur", f"Stock {PLATFORM_LABELS[name]} non mis à jour : {e}", item.sku, name)
                    continue

                def done(it: CatalogItem, n=name):
                    it.state(n).extra.pop("stock_a_envoyer", None)
                self.catalog.update(item.sku, done)
                self.catalog.log("synchro", f"Stock {PLATFORM_LABELS[name]} → {item.stock}", item.sku, name)

    def check_shopify_stock(self) -> None:
        """Shopify fait foi : un stock modifié à la main dans l'admin Shopify est recopié partout."""
        shopify = self.platforms["shopify"]
        if not shopify.configured():
            return
        for item in self.catalog.all()[:200]:
            state = item.state("shopify")
            if not state.remote_id or state.status not in (PUBLIE, EPUISE):
                continue
            try:
                remote = shopify.remote_quantity(item, state)
            except Exception:  # noqa: BLE001
                continue
            if remote is None or remote == item.stock:
                continue

            def adopt(it: CatalogItem, q=remote):
                it.stock = q
                for name in PLATFORMS:
                    st = it.state(name)
                    if name != "shopify" and st.remote_id:
                        st.extra["stock_a_envoyer"] = True
                    if st.remote_id:
                        st.status = EPUISE if q == 0 else (PUBLIE if st.status == EPUISE else st.status)
            self.catalog.update(item.sku, adopt)
            self.catalog.log("synchro", f"Stock modifié sur Shopify : {remote}", item.sku, "shopify")
            self.push_pending_stock(item.sku)
