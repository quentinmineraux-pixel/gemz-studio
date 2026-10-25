"""Moteur de fond : file de publication et synchro du stock et des prix entre Shopify, Etsy et eBay.

Il tourne dans un thread de l'application, tant que le PC est allumé (décision du cahier des
charges). Au démarrage, il rattrape d'abord les ventes faites pendant l'arrêt, avant toute publication.
"""
from __future__ import annotations

import re
import threading
import time
import traceback
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import notify
from .catalog import (A_METTRE_A_JOUR, A_PUBLIER, EPUISE, ERREUR, PLATFORM_LABELS, PLATFORMS, PUBLIE,
                      Catalog, CatalogItem)
from .config import Settings
from .listing.prices import price_text, safe_price
from .platforms import NotConfigured, PlatformError, make_platforms
from .platforms.shopify import main_variant, reference_sku, remote_stock, spread_stock

TICK = 5.0
OVERLAP = timedelta(minutes=15)      # les commandes arrivent parfois avec un peu de retard dans les API
DRIFT_EVERY = 3                      # contrôle de Shopify (stock, prix) toutes les 3 synchros (~10 min)
PUSH_EVERY = 60.0                    # stock et prix en attente : nouvel essai chaque minute
ERROR_REPEAT = 6 * 3600              # une même erreur n'est renotée au journal qu'au bout de 6 h
MAX_BACKOFF = 6 * 3600
VARIANT_SUFFIX = re.compile(r"-(T\d{2}|\d{2,3}|US)$")
PENDING = ("stock_a_envoyer", "prix_a_envoyer")   # le stock d'abord : une annonce remise en vente, puis son prix


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
        self._next_push = 0.0
        self._sync_count = 0
        self._errors: dict[tuple, tuple[str, float]] = {}   # dernière erreur notée, par opération
        self._drift: dict[str, tuple] = {}                  # écarts vus avec Shopify, à confirmer

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

    def push_soon(self) -> None:
        """Prix ou stock modifiés dans une fiche : envoyés dans les secondes qui viennent."""
        self._next_push = 0.0
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
                    if self.caught_up and time.time() >= self._next_push:
                        # Prix ou stock modifiés dans la fiche : envoyés sans attendre la synchro
                        self._next_push = time.time() + PUSH_EVERY
                        self.push_pending_stock()
            except Exception as e:  # noqa: BLE001 - le moteur ne doit jamais s'arrêter
                traceback.print_exc()
                self.catalog.log("erreur", f"Erreur interne : {e}")
            self._wake.wait(TICK)
            self._wake.clear()

    def _error(self, key: tuple, message: str, sku: str = "", platform: str = "") -> bool:
        """Note une erreur au journal, sauf si la même vient d'y être notée (essais répétés) ;
        renvoie vrai si elle a été notée."""
        family = re.sub(r"0x[0-9a-f]+|\d+", "#", message)[:160]   # adresses et numéros changent d'un essai à l'autre
        last = self._errors.get(key)
        if last and last[0] == family and time.time() - last[1] < ERROR_REPEAT:
            return False
        self._errors[key] = (family, time.time())
        self.catalog.log("erreur", message, sku, platform)
        return True

    def _ok(self, key: tuple) -> None:
        self._errors.pop(key, None)

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
        item = self.catalog.get(item.sku) or item   # dernière version (fiche retouchée depuis la liste d'attente)
        state = replace(item.state(name), extra=dict(item.state(name).extra))
        state.extra.pop("avertissement", None)
        sent = _content(item)
        # Annonce déjà en ligne (même après une erreur) : on la met à jour, on n'en crée jamais une seconde
        was_update = bool(state.remote_id) or state.status == A_METTRE_A_JOUR
        label = PLATFORM_LABELS[name]
        english = name == "etsy" and self.settings.etsy_shop_language == "en" or \
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
            self._error(("publication", item.sku, name), f"{label} : {e}", item.sku, name)
            if state.attempts == 3:
                self.on_notify(f"Publication {label} en échec", f"{item.title} : {e}")
            return
        self._ok(("publication", item.sku, name))
        warning = state.extra.pop("avertissement", "")
        state.status, state.error, state.attempts, state.next_try = PUBLIE, "", 0, 0.0
        state.published_at = state.published_at or utc_now().isoformat(timespec="seconds")
        self._save_state(item.sku, name, state, sent)
        self.catalog.log("publication", f"{label} : {'mis à jour' if was_update else 'en ligne'}", item.sku, name,
                         {"url": state.url, "titre": item.title})
        if warning:
            # Publié, mais une partie n'est pas passée (traduction refusée…) : à regarder
            self.catalog.log("alerte", f"{label} : {warning}", item.sku, name)
        self.on_status(f"{label} : « {item.title} » {'mis à jour' if was_update else 'en ligne'}")

    def _save_state(self, sku: str, name: str, state, sent: tuple | None = None) -> None:
        """Enregistre le résultat d'un envoi sans perdre ce qui a été demandé pendant qu'il durait."""
        def apply(it: CatalogItem):
            current = it.state(name)
            state.wanted = current.wanted
            for flag in PENDING:
                if current.extra.get(flag):
                    state.extra[flag] = True   # prix ou stock modifiés pendant l'envoi : ils suivront
            if sent is not None and state.status == PUBLIE and _content(it) != sent:
                state.status = A_METTRE_A_JOUR   # fiche retouchée pendant l'envoi : la nouvelle version suivra
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
                self._error(("synchro", name), f"Synchro {PLATFORM_LABELS[name]} : {e}", platform=name)
                continue
            self._ok(("synchro", name))
            for sale in sales:
                self.handle_sale(sale)
            self.catalog.kv_set(cursor_key, started.isoformat())
        self.push_pending_stock()
        self._sync_count += 1
        if self._sync_count % DRIFT_EVERY == 1:
            self.check_shopify_stock()
        self.on_status(f"Synchro faite à {datetime.now():%H:%M}")

    def find_item(self, sale) -> CatalogItem | None:
        """L'annonce vendue d'abord (sûr), puis le SKU (SKU saisi à la main, annonce pas encore reliée)."""
        if sale.remote_id:
            item = self.catalog.find_remote(sale.platform, sale.remote_id)
            if item:
                return item
        if sale.sku:
            return self.catalog.get(base_sku(sale.sku)) or self.catalog.get(sale.sku.strip().upper())
        return None

    def handle_sale(self, sale) -> None:
        if self.catalog.seen(sale.key):
            return
        self.catalog.mark_seen(sale.key)
        if sale.cancelled:
            return
        item = self.find_item(sale)
        label = PLATFORM_LABELS.get(sale.platform, sale.platform)
        data = {"prix": sale.price, "devise": sale.currency, "commande": sale.order_id, "quantite": sale.quantity,
                "titre": sale.title, "date": sale.created}
        if item is None:
            self.catalog.log("vente", f"Vente {label} hors catalogue : {sale.title} ({sale.price:.2f} {sale.currency})",
                             sale.sku, sale.platform, data)
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
                # La plateforme qui a vendu a déjà retiré la pièce ; les autres sont mises à jour. Shopify
                # aussi quand il a vendu : une bague y reste en vente dans les autres tailles, et la pièce
                # épuisée y est archivée
                if name != sale.platform or name == "shopify":
                    st.extra["stock_a_envoyer"] = True

        item = self.catalog.update(item.sku, apply)
        self._drift.pop(item.sku, None)
        message = f"{item.title} – {sale.price:.2f} {sale.currency}" + (" · épuisé partout" if item.stock == 0 else f" · reste {item.stock}")
        self.catalog.log("vente", f"Vente {label} : {message}", item.sku, sale.platform, {**data, "titre": item.title})
        notify.phone(self.settings.ntfy_topic, f"Vente {label} !", message, ["moneybag"])
        self.on_notify(f"Vente {label}", message)
        self.push_pending_stock(item.sku)

    def push_pending_stock(self, only_sku: str | None = None) -> None:
        """Recopie le stock et les prix du catalogue sur les plateformes en retard (nouvel essai régulier)."""
        items = [self.catalog.get(only_sku)] if only_sku else self.catalog.all()
        for item in items:
            if item is None:
                continue
            for name in PLATFORMS:
                state = item.state(name)
                flags = [f for f in PENDING if state.extra.get(f)]
                platform = self.platforms[name]
                if not flags or not platform.configured():
                    continue
                label = PLATFORM_LABELS[name]
                for flag in flags:
                    is_price = flag == "prix_a_envoyer"
                    if is_price and name != "shopify" and item.stock <= 0:
                        continue   # annonce désactivée : son prix suivra quand elle sera remise en vente
                    try:
                        if is_price:
                            platform.set_price(item, state)
                        else:
                            platform.set_quantity(item, state, item.stock)
                    except Exception as e:  # noqa: BLE001
                        what = "Prix" if is_price else "Stock"
                        self._error((flag, item.sku, name), f"{what} {label} non mis à jour : {e}", item.sku, name)
                        continue
                    self._ok((flag, item.sku, name))

                    def done(it: CatalogItem, n=name, f=flag, sent=item, price=state.price):
                        # Remodifié pendant l'envoi : on garde l'indicateur pour renvoyer la nouvelle valeur
                        if f == "prix_a_envoyer":
                            it.state(n).price = price
                            if (it.info.prix, it.info.prix_barre) != (sent.info.prix, sent.info.prix_barre):
                                return
                        elif it.stock != sent.stock:
                            return
                        it.state(n).extra.pop(f, None)
                    self.catalog.update(item.sku, done)
                    if is_price:
                        self.catalog.log("synchro", f"Prix {label} → {price_text(state.price)} €", item.sku, name)
                    else:
                        self.catalog.log("synchro", f"Stock {label} → {item.stock}", item.sku, name)

    def check_shopify_stock(self) -> None:
        """Shopify fait foi : stock, prix et prix barré modifiés dans l'admin Shopify (ou produit archivé)
        sont repris dans le catalogue puis recopiés sur Etsy et eBay.

        Un écart n'est repris que s'il est encore là au contrôle suivant : une vente que les commandes
        n'ont pas encore montrée, ou une modification de GemZ pas encore envoyée, ne le déclenche jamais.
        """
        shopify = self.platforms["shopify"]
        if not shopify.configured():
            return
        try:
            snapshot = shopify.snapshot()
        except Exception as e:  # noqa: BLE001
            self._error(("controle",), f"Contrôle du stock Shopify impossible : {e}", platform="shopify")
            return
        self._ok(("controle",))
        seen: dict[str, tuple] = {}
        for item in self.catalog.all():
            state = item.state("shopify")
            product = snapshot.get(state.remote_id) if state.remote_id else None
            if product is None or state.status not in (PUBLIE, EPUISE, A_METTRE_A_JOUR, ERREUR) \
                    or any(state.extra.get(f) for f in PENDING):
                continue
            remote = shopify_values(item, product)
            if remote == local_values(item):
                continue
            seen[item.sku] = remote
            if self._drift.get(item.sku) != remote:
                continue   # nouvel écart : on attend le prochain contrôle pour le confirmer
            self.adopt_shopify(item.sku, remote)
        self._drift = seen

    def adopt_shopify(self, sku: str, remote: tuple) -> None:
        """Reprend (stock, prix, prix barré en centimes) de Shopify et prévient Etsy et eBay."""
        stock, price, compare = remote
        changes: list[str] = []

        def adopt(it: CatalogItem):
            changes.clear()
            if any(it.state("shopify").extra.get(f) for f in PENDING):
                return   # modifié dans GemZ entre-temps : c'est cette valeur qui sera envoyée
            old_stock, old_price, old_compare = local_values(it)
            if stock != old_stock:
                it.stock = stock
                changes.append(f"stock {stock}")
                for name in PLATFORMS:
                    st = it.state(name)
                    if not st.remote_id:
                        continue
                    if name != "shopify":
                        st.extra["stock_a_envoyer"] = True
                    if stock == 0 and st.status == PUBLIE:
                        st.status = EPUISE
                    elif stock > 0 and st.status == EPUISE:
                        st.status = PUBLIE
            if price != old_price:
                it.info.prix = price_text(price / 100)
                it.state("shopify").price = price / 100
                changes.append(f"prix {it.info.prix} €")
                for name in PLATFORMS:
                    if name != "shopify" and it.state(name).remote_id:
                        it.state(name).extra["prix_a_envoyer"] = True
            if compare != old_compare:
                # Le prix barré ne concerne que Shopify (Etsy et eBay n'ont pas ce champ)
                it.info.prix_barre = price_text(compare / 100) if compare else ""
                changes.append(f"prix barré {it.info.prix_barre} €" if compare else "fin du prix barré")
        self.catalog.update(sku, adopt)
        if changes:
            self.catalog.log("synchro", "Modifié sur Shopify : " + ", ".join(changes), sku, "shopify")
            self.push_pending_stock(sku)


def _content(item: CatalogItem) -> tuple:
    """Ce qu'une publication envoie, hors stock et prix (envoyés à part)."""
    info = {k: v for k, v in asdict(item.info).items() if k not in ("prix", "prix_barre")}
    return item.listing.to_dict(), info, list(item.photos), list(item.videos)


def _cents(value: float) -> int:
    return round(value * 100)


def local_values(item: CatalogItem) -> tuple[int, int, int]:
    """(stock, prix, prix barré) du catalogue ; prix en centimes pour les comparer à Shopify."""
    price = _cents(safe_price(item.info.prix))
    compare = _cents(safe_price(item.info.prix_barre)) if item.info.prix_barre.strip() else 0
    return item.stock, price, compare if compare > price else 0


def shopify_values(item: CatalogItem, product: dict) -> tuple[int, int, int]:
    """Les mêmes valeurs pour un produit de l'instantané Shopify ; archivé = plus en vente (stock 0)."""
    variants = product["variants"]
    stock = 0 if product.get("status") == "ARCHIVED" else \
        remote_stock(variants, item.sku, spread_stock(item, item.state("shopify")))
    ref = main_variant(variants, reference_sku(item))
    if not ref or ref.get("price") in (None, ""):
        return (stock, *local_values(item)[1:])   # prix inconnu : on ne touche qu'au stock
    price = _cents(float(ref["price"]))
    compare = _cents(float(ref["compareAtPrice"])) if ref.get("compareAtPrice") else 0
    return stock, price, compare if compare > price else 0
