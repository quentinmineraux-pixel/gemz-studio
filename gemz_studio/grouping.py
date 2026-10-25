"""Rattache chaque photo ou vidéo reçue au bon produit de la séance."""
from __future__ import annotations

from dataclasses import dataclass

from .config import ProductKind
from .models import A_COMPLETER, EN_PRISE, Product, Session, media_type


@dataclass
class PendingReplace:
    product_id: str
    path: str


class Grouper:
    def __init__(self, session: Session, kind: ProductKind, auto_split: bool = True):
        self.session = session
        self.kind = kind
        self.auto_split = auto_split
        self.pending_replace: PendingReplace | None = None

    def known_paths(self) -> set[str]:
        return {m for p in self.session.products for m in p.media()}

    def start_product(self) -> Product:
        current = self.session.current()
        if current and current.status == EN_PRISE:
            current.status = A_COMPLETER
        product = Product(kind_id=self.kind.id)
        self.session.products.append(product)
        self.session.current_id = product.id
        return product

    def next_product(self) -> None:
        """Bouton « Produit suivant » : le prochain fichier ouvrira un nouveau produit."""
        current = self.session.current()
        if current and current.media():
            current.status = A_COMPLETER
            self.session.current_id = None

    def request_replace(self, product_id: str, path: str) -> None:
        """Bouton « Reprendre » : la prochaine photo reçue remplacera celle-ci."""
        self.pending_replace = PendingReplace(product_id, path)

    def add(self, path: str) -> Product | None:
        """Ajoute un fichier ; renvoie le produit modifié, ou None si le fichier est ignoré."""
        kind = media_type(path)
        if kind is None or path in self.known_paths():
            return None

        if self.pending_replace and kind == media_type(self.pending_replace.path):
            target = self.session.get(self.pending_replace.product_id)
            old = self.pending_replace.path
            self.pending_replace = None
            if target:
                items = target.photos if kind == "photo" else target.videos
                if old in items:
                    items[items.index(old)] = path
                    return target

        product = self.session.current()
        if product is None:
            product = self.start_product()
        (product.photos if kind == "photo" else product.videos).append(path)

        if self.auto_split and product.is_full(self.kind.photos_per_product, self.kind.videos_per_product):
            product.status = A_COMPLETER
            self.session.current_id = None
        return product

    def remove(self, product_id: str, path: str) -> None:
        product = self.session.get(product_id)
        if not product:
            return
        for items in (product.photos, product.videos):
            if path in items:
                items.remove(path)
        if not product.media() and product.status in (EN_PRISE, A_COMPLETER):
            self.session.products.remove(product)
            if self.session.current_id == product.id:
                self.session.current_id = None

    def set_main(self, product_id: str, path: str) -> None:
        product = self.session.get(product_id)
        if product and path in product.photos:
            product.photos.remove(path)
            product.photos.insert(0, path)

    def move_photo(self, product_id: str, path: str, offset: int) -> None:
        product = self.session.get(product_id)
        if not product or path not in product.photos:
            return
        i = product.photos.index(path)
        j = max(0, min(len(product.photos) - 1, i + offset))
        product.photos.insert(j, product.photos.pop(i))
