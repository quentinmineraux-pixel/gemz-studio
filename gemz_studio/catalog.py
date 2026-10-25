"""Catalogue des produits publiés (ou à publier), journal des ventes et des publications.

Une petite base SQLite dans les données de l'application : partagée entre l'interface, la file de
publication et la synchro du stock, qui tournent dans des threads différents.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .config import data_dir
from .listing.model import Listing
from .models import ProductInfo

PLATFORMS = ("shopify", "etsy", "ebay")
PLATFORM_LABELS = {"shopify": "Shopify", "etsy": "Etsy", "ebay": "eBay"}

# États d'une plateforme pour un produit
A_PUBLIER = "a_publier"
EN_COURS = "en_cours"
PUBLIE = "publie"
A_METTRE_A_JOUR = "a_maj"
ERREUR = "erreur"
EPUISE = "epuise"         # stock à 0 (vendu ici ou ailleurs)
RETIRE = "retire"
STATE_LABELS = {A_PUBLIER: "À publier", EN_COURS: "Publication…", PUBLIE: "En ligne",
                A_METTRE_A_JOUR: "Mise à jour à envoyer", ERREUR: "Erreur", EPUISE: "Épuisé", RETIRE: "Retiré"}


@dataclass
class PlatformState:
    wanted: bool = False
    status: str = A_PUBLIER
    remote_id: str = ""                                     # produit / annonce / SKU distant
    extra: dict = field(default_factory=dict)               # variantes, offres eBay, inventaire…
    url: str = ""
    error: str = ""
    attempts: int = 0
    next_try: float = 0.0
    price: float = 0.0
    published_at: str = ""


@dataclass
class CatalogItem:
    sku: str
    kind_id: str
    info: ProductInfo = field(default_factory=ProductInfo)
    listing: Listing = field(default_factory=Listing)
    folder: str = ""
    photos: list[str] = field(default_factory=list)         # photos web watermarkées, la 1re = principale
    videos: list[str] = field(default_factory=list)
    stock: int = 1
    created: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    origin: str = "gemz"                                    # « gemz » ou « import » (annonce existante reliée)
    platforms: dict[str, PlatformState] = field(default_factory=lambda: {p: PlatformState() for p in PLATFORMS})

    @property
    def title(self) -> str:
        return self.listing.texts["fr"].title or self.info.nom or self.sku

    def state(self, platform: str) -> PlatformState:
        return self.platforms.setdefault(platform, PlatformState())

    def to_json(self) -> str:
        raw = asdict(self)
        raw["listing"] = self.listing.to_dict()
        return json.dumps(raw, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "CatalogItem":
        raw = json.loads(text)
        known_info = set(ProductInfo.__dataclass_fields__)
        info = ProductInfo(**{k: v for k, v in raw.pop("info", {}).items() if k in known_info})
        listing = Listing.from_dict(raw.pop("listing", None))
        known_state = set(PlatformState.__dataclass_fields__)
        platforms = {p: PlatformState(**{k: v for k, v in s.items() if k in known_state})
                     for p, s in raw.pop("platforms", {}).items()}
        for p in PLATFORMS:
            platforms.setdefault(p, PlatformState())
        raw = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        return cls(info=info, listing=listing, platforms=platforms, **raw)


@dataclass
class Event:
    id: int
    ts: str
    kind: str          # vente, publication, erreur, synchro, info
    sku: str
    platform: str
    message: str
    data: dict


class Catalog:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else data_dir() / "catalogue.db"
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS items (sku TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT,
                sku TEXT, platform TEXT, message TEXT, data TEXT);
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, ts REAL);
        """)
        self._db.commit()
        self.listeners: list = []   # fonctions appelées après chaque modification (rafraîchir l'interface)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _changed(self, sku: str = "") -> None:
        for listener in list(self.listeners):
            try:
                listener(sku)
            except Exception:  # noqa: BLE001 - un écran fermé ne doit pas casser la synchro
                pass

    # --- produits ----------------------------------------------------------
    def get(self, sku: str) -> CatalogItem | None:
        with self._lock:
            row = self._db.execute("SELECT data FROM items WHERE sku = ?", (sku,)).fetchone()
        return CatalogItem.from_json(row[0]) if row else None

    def put(self, item: CatalogItem) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO items (sku, data, updated) VALUES (?, ?, ?)",
                             (item.sku, item.to_json(), time.time()))
            self._db.commit()
        self._changed(item.sku)

    def update(self, sku: str, fn) -> CatalogItem | None:
        """Lecture-modification-écriture atomique (la synchro et la file travaillent en parallèle)."""
        with self._lock:
            item = self.get(sku)
            if item is None:
                return None
            fn(item)
            self._db.execute("UPDATE items SET data = ?, updated = ? WHERE sku = ?",
                             (item.to_json(), time.time(), sku))
            self._db.commit()
        self._changed(sku)
        return item

    def delete(self, sku: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM items WHERE sku = ?", (sku,))
            self._db.commit()
        self._changed(sku)

    def all(self) -> list[CatalogItem]:
        with self._lock:
            rows = self._db.execute("SELECT data FROM items ORDER BY updated DESC").fetchall()
        return [CatalogItem.from_json(r[0]) for r in rows]

    def skus(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT sku FROM items")}

    def find_remote(self, platform: str, remote_id: str) -> CatalogItem | None:
        for item in self.all():
            if item.state(platform).remote_id == str(remote_id):
                return item
        return None

    # --- journal -----------------------------------------------------------
    def log(self, kind: str, message: str, sku: str = "", platform: str = "", data: dict | None = None) -> None:
        with self._lock:
            self._db.execute("INSERT INTO events (ts, kind, sku, platform, message, data) VALUES (?, ?, ?, ?, ?, ?)",
                             (datetime.now().isoformat(timespec="seconds"), kind, sku, platform, message,
                              json.dumps(data or {}, ensure_ascii=False)))
            self._db.commit()
        self._changed(sku)

    def events(self, kinds: tuple[str, ...] | None = None, limit: int = 300) -> list[Event]:
        query = "SELECT id, ts, kind, sku, platform, message, data FROM events"
        args: list = []
        if kinds:
            query += f" WHERE kind IN ({','.join('?' * len(kinds))})"
            args += list(kinds)
        query += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(query, args).fetchall()
        return [Event(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6] or "{}")) for r in rows]

    # --- petites valeurs (curseurs de synchro, coût IA du mois) -------------
    def kv_get(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
            self._db.commit()

    def add_float(self, key: str, amount: float) -> float:
        with self._lock:
            value = float(self.kv_get(key, "0") or 0) + amount
            self.kv_set(key, f"{value:.6f}")
            return value

    # --- ventes déjà traitées (pour ne jamais compter deux fois une vente) ---
    def seen(self, key: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM seen WHERE key = ?", (key,)).fetchone() is not None

    def mark_seen(self, key: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO seen (key, ts) VALUES (?, ?)", (key, time.time()))
            self._db.commit()
