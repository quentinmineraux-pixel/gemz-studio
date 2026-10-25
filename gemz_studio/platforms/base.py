"""Interface commune des plateformes de vente et accès HTTP avec nouveaux essais."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import Settings

USER_AGENT = "GemZ-Studio/1.0"


class PlatformError(Exception):
    """retryable : la file de publication réessaiera plus tard (réseau, limite de débit, panne)."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class NotConfigured(PlatformError):
    def __init__(self, message: str):
        super().__init__(message, retryable=False)


@dataclass
class Sale:
    platform: str
    order_id: str
    line_id: str
    sku: str
    remote_id: str            # produit / annonce sur la plateforme
    quantity: int
    price: float              # prix unitaire
    currency: str
    created: str
    title: str = ""
    cancelled: bool = False

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.order_id}:{self.line_id}"


@dataclass
class RemoteListing:
    """Annonce existante sur une plateforme (import et liaison au catalogue)."""
    platform: str
    remote_id: str
    title: str
    skus: list[str] = field(default_factory=list)
    quantity: int = 0
    price: float = 0.0
    url: str = ""
    product_type: str = ""
    tags: list[str] = field(default_factory=list)
    description: str = ""
    image_urls: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def request(session, method: str, url: str, attempts: int = 4, **kwargs):
    """Requête HTTP : nouveaux essais sur 429 / 5xx / coupure réseau, erreur claire sinon."""
    import requests

    kwargs.setdefault("timeout", 60)
    delay = 2.0
    for attempt in range(attempts):
        try:
            response = session.request(method, url, **kwargs)
        except requests.RequestException as e:
            if attempt == attempts - 1:
                raise PlatformError(f"Connexion impossible : {e}", retryable=True) from e
            time.sleep(delay)
            delay *= 2
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == attempts - 1:
                raise PlatformError(f"Service indisponible ({response.status_code})", retryable=True)
            retry_after = response.headers.get("Retry-After", "")
            time.sleep(float(retry_after) if retry_after.replace(".", "", 1).isdigit() else delay)
            delay *= 2
            continue
        return response
    raise PlatformError("Service indisponible", retryable=True)


class Platform:
    """Une place de marché. Toutes les méthodes sont appelées depuis un thread de fond."""

    name = ""
    label = ""

    def __init__(self, settings: Settings):
        self.settings = settings

    def configured(self) -> bool:
        return False

    def missing_setup(self) -> str:
        """Ce qu'il reste à brancher, en clair (vide si prêt)."""
        return "" if self.configured() else "Connexion à faire dans Réglages → Connexions."

    def test(self) -> str:
        raise NotConfigured(self.missing_setup())

    # Publication
    def publish(self, item, state) -> None:
        raise NotImplementedError

    def update(self, item, state) -> None:
        raise NotImplementedError

    def set_quantity(self, item, state, quantity: int) -> None:
        raise NotImplementedError

    def set_price(self, item, state) -> None:
        """Prix de la fiche (majoration de la plateforme comprise) ; les autres tailles gardent leur écart."""
        raise NotImplementedError

    # Synchro
    def sales_since(self, since: str) -> list[Sale]:
        return []

    def remote_quantity(self, item, state) -> int | None:
        return None

    # Import des annonces existantes
    def list_listings(self) -> list[RemoteListing]:
        return []
