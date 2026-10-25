"""eBay (API Sell : Inventory, Media, Fulfillment, Account) : ebay.fr en français, ebay.com en anglais.

Chaque place de marché a sa propre fiche d'inventaire (SKU, et SKU-US pour ebay.com) pour garder le
titre dans la bonne langue ; le stock est tenu par GemZ Studio et recopié sur les deux.
"""
from __future__ import annotations

import base64
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

from .. import vault
from ..catalog import CatalogItem, PlatformState
from ..listing.prices import parse_price, platform_price, to_usd
from .base import NotConfigured, Platform, PlatformError, RemoteListing, Sale, USER_AGENT, request

API = "https://api.ebay.com"
MEDIA_API = "https://apim.ebay.com/commerce/media/v1_beta"
AUTH_URL = "https://auth.ebay.com/oauth2/authorize"
TOKEN_URL = f"{API}/identity/v1/oauth2/token"
SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
])
SECRET_CLIENT_ID = "ebay_client_id"
SECRET_CLIENT_SECRET = "ebay_client_secret"
SECRET_REFRESH = "ebay_refresh_token"
TITLE_MAX = 80

MARKETS = {
    "EBAY_FR": {"lang": "fr", "content_language": "fr-FR", "currency": "EUR", "suffix": ""},
    "EBAY_US": {"lang": "en", "content_language": "en-US", "currency": "USD", "suffix": "-US"},
}


class Ebay(Platform):
    name = "ebay"
    label = "eBay"

    def __init__(self, settings):
        super().__init__(settings)
        self._access = ""
        self._expires = 0.0
        self._lock = threading.Lock()
        self._session = None

    # --- connexion -----------------------------------------------------------
    def configured(self) -> bool:
        return bool(vault.get(SECRET_CLIENT_ID) and vault.get(SECRET_CLIENT_SECRET) and vault.get(SECRET_REFRESH))

    def missing_setup(self) -> str:
        if not (vault.get(SECRET_CLIENT_ID) and vault.get(SECRET_CLIENT_SECRET)):
            return "Compte développeur eBay à créer, puis identifiants de l'application à renseigner."
        if not vault.get(SECRET_REFRESH):
            return "Autorisation eBay à faire (bouton « Connecter eBay »)."
        return ""

    def _http(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers["User-Agent"] = USER_AGENT
        return self._session

    def _basic(self) -> str:
        raw = f"{vault.get(SECRET_CLIENT_ID)}:{vault.get(SECRET_CLIENT_SECRET)}".encode()
        return "Basic " + base64.b64encode(raw).decode()

    def authorize_url(self) -> str:
        if not self.settings.ebay_ru_name:
            raise NotConfigured("RuName de l'application eBay à renseigner.")
        return AUTH_URL + "?" + urlencode({"client_id": vault.get(SECRET_CLIENT_ID), "response_type": "code",
                                            "redirect_uri": self.settings.ebay_ru_name, "scope": SCOPES})

    def finish_authorization(self, pasted: str) -> None:
        """L'utilisateur colle l'adresse de la page atteinte après l'accord (ou le code seul)."""
        code = parse_qs(urlparse(pasted.strip()).query).get("code", [pasted.strip()])[0]
        r = request(self._http(), "POST", TOKEN_URL, headers={"Authorization": self._basic()},
                    data={"grant_type": "authorization_code", "code": code,
                          "redirect_uri": self.settings.ebay_ru_name})
        if r.status_code != 200:
            raise PlatformError(f"eBay refuse l'autorisation ({r.status_code}) : {r.text[:200]}")
        data = r.json()
        vault.put(SECRET_REFRESH, data["refresh_token"])
        self._access = data["access_token"]
        self._expires = time.time() + float(data.get("expires_in", 7200))

    def token(self) -> str:
        with self._lock:
            if self._access and time.time() < self._expires - 120:
                return self._access
            if not self.configured():
                raise NotConfigured(self.missing_setup())
            r = request(self._http(), "POST", TOKEN_URL, headers={"Authorization": self._basic()},
                        data={"grant_type": "refresh_token", "refresh_token": vault.get(SECRET_REFRESH),
                              "scope": SCOPES})
            if r.status_code != 200:
                raise PlatformError("Autorisation eBay expirée : refais « Connecter eBay » dans les réglages.")
            data = r.json()
            self._access = data["access_token"]
            self._expires = time.time() + float(data.get("expires_in", 7200))
            return self._access

    def call(self, method: str, url: str, *, json_body=None, params=None, headers=None, files=None,
             ok=(200, 201, 204)):
        h = {"Authorization": f"Bearer {self.token()}", "Accept": "application/json"}
        if json_body is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        r = request(self._http(), method, url if url.startswith("http") else API + url, json=json_body,
                    params=params, headers=h, files=files, timeout=600 if files else 60)
        if r.status_code not in ok:
            try:
                errors = r.json().get("errors", [])
                message = "; ".join(e.get("longMessage") or e.get("message", "") for e in errors) or r.text
            except ValueError:
                message = r.text
            raise PlatformError(f"eBay ({r.status_code}) : {message[:400]}")
        return r

    def test(self) -> str:
        if not self.configured():
            raise NotConfigured(self.missing_setup())
        r = self.call("GET", "/sell/account/v1/privilege")
        return "Connecté à eBay." + (" Compte vendeur actif." if r.json().get("sellingLimit") else "")

    def policies(self, kind: str, marketplace: str) -> list[tuple[str, str]]:
        """kind : fulfillment, payment ou return (pour les choisir dans les réglages)."""
        r = self.call("GET", f"/sell/account/v1/{kind}_policy", params={"marketplace_id": marketplace})
        items = r.json().get(f"{kind}Policies", [])
        return [(p[f"{kind}PolicyId"], p.get("name", "")) for p in items]

    # --- médias --------------------------------------------------------------
    def upload_image(self, path: str) -> str:
        with open(path, "rb") as f:
            r = self.call("POST", f"{MEDIA_API}/image/create_image_from_file",
                          files={"image": (Path(path).name, f, "image/jpeg")})
        if r.content:
            body = r.json()
            if body.get("imageUrl"):
                return body["imageUrl"]
        location = r.headers.get("Location", "")
        if not location:
            raise PlatformError("eBay n'a pas renvoyé l'adresse de la photo.")
        info = self.call("GET", location).json()
        return info["imageUrl"]

    # --- annonces ------------------------------------------------------------
    def _price(self, item: CatalogItem, market: str) -> tuple[float, str]:
        eur = platform_price(parse_price(item.info.prix), self.settings.ebay_markup)
        if MARKETS[market]["currency"] == "USD":
            return to_usd(eur, self.settings.usd_per_eur), "USD"
        return eur, "EUR"

    def _inventory_item(self, item: CatalogItem, market: str, images: list[str], quantity: int) -> dict:
        lang = MARKETS[market]["lang"]
        text = item.listing.texts[lang]
        aspects = {"Type": [self.settings.kind(item.kind_id).label]}
        if item.listing.pierres:
            aspects["Pierre" if lang == "fr" else "Stone"] = item.listing.pierres[:5]
        if item.listing.couleurs:
            aspects["Couleur" if lang == "fr" else "Color"] = item.listing.couleurs[:3]
        return {"availability": {"shipToLocationAvailability": {"quantity": max(0, quantity)}},
                "condition": "NEW",
                "product": {"title": text.title[:TITLE_MAX], "description": text.description,
                            "aspects": aspects, "imageUrls": images[:24]}}

    def _markets(self) -> list[str]:
        return [m for m in self.settings.ebay_marketplaces if m in MARKETS]

    def publish(self, item: CatalogItem, state: PlatformState) -> None:
        kind = self.settings.kind(item.kind_id)
        if not kind.ebay_category_id:
            raise NotConfigured(f"Catégorie eBay du type « {kind.label} » à choisir (Réglages → Connexions).")
        if not self.settings.ebay_location_key:
            raise NotConfigured("Adresse d'expédition eBay (emplacement) à créer (Réglages → Connexions).")
        images = state.extra.setdefault("images", [])
        for path in item.photos[len(images):]:
            images.append(self.upload_image(path))
        offers = state.extra.setdefault("offers", {})
        for market in self._markets():
            policies = self.settings.ebay_policies.get(market, {})
            if not all(policies.get(k) for k in ("fulfillment", "payment", "return")):
                raise NotConfigured(f"Conditions de vente eBay ({market}) à choisir (Réglages → Connexions).")
            sku = item.sku + MARKETS[market]["suffix"]
            self.call("PUT", f"/sell/inventory/v1/inventory_item/{quote(sku)}",
                      json_body=self._inventory_item(item, market, images, item.stock),
                      headers={"Content-Language": MARKETS[market]["content_language"]})
            value, currency = self._price(item, market)
            offer = {"sku": sku, "marketplaceId": market, "format": "FIXED_PRICE",
                     "availableQuantity": max(0, item.stock), "categoryId": kind.ebay_category_id,
                     "listingDescription": item.listing.texts[MARKETS[market]["lang"]].description,
                     "listingDuration": "GTC", "merchantLocationKey": self.settings.ebay_location_key,
                     "pricingSummary": {"price": {"value": f"{value:.2f}", "currency": currency}},
                     "listingPolicies": {"fulfillmentPolicyId": policies["fulfillment"],
                                         "paymentPolicyId": policies["payment"],
                                         "returnPolicyId": policies["return"]}}
            headers = {"Content-Language": MARKETS[market]["content_language"]}
            if market in offers:
                self.call("PUT", f"/sell/inventory/v1/offer/{offers[market]['offer']}", json_body=offer, headers=headers)
            else:
                r = self.call("POST", "/sell/inventory/v1/offer", json_body=offer, headers=headers)
                offers[market] = {"offer": r.json()["offerId"], "sku": sku}
            if not offers[market].get("listing"):
                r = self.call("POST", f"/sell/inventory/v1/offer/{offers[market]['offer']}/publish")
                offers[market]["listing"] = r.json().get("listingId", "")
        first = next(iter(offers.values()), {})
        state.remote_id = item.sku
        state.url = f"https://www.ebay.fr/itm/{first.get('listing', '')}" if first.get("listing") else ""
        state.price = self._price(item, "EBAY_FR")[0]

    def update(self, item: CatalogItem, state: PlatformState) -> None:
        # Même chemin que la publication : fiches d'inventaire et offres remplacées, rien n'est republié
        self.publish(item, state)

    def set_quantity(self, item: CatalogItem, state: PlatformState, quantity: int) -> None:
        offers = state.extra.get("offers", {})
        requests_ = [{"sku": o["sku"], "shipToLocationAvailability": {"quantity": max(0, quantity)},
                      "offers": [{"offerId": o["offer"], "availableQuantity": max(0, quantity)}]}
                     for o in offers.values()]
        if requests_:
            self.call("POST", "/sell/inventory/v1/bulk_update_price_quantity", json_body={"requests": requests_})

    def remote_quantity(self, item: CatalogItem, state: PlatformState) -> int | None:
        offers = state.extra.get("offers", {})
        if not offers:
            return None
        o = next(iter(offers.values()))
        r = self.call("GET", f"/sell/inventory/v1/inventory_item/{quote(o['sku'])}")
        return int(r.json().get("availability", {}).get("shipToLocationAvailability", {}).get("quantity", 0))

    # --- commandes -----------------------------------------------------------
    def sales_since(self, since: str) -> list[Sale]:
        sales: list[Sale] = []
        offset = 0
        while True:
            r = self.call("GET", "/sell/fulfillment/v1/order",
                          params={"filter": f"creationdate:[{since}..]", "limit": 200, "offset": offset})
            data = r.json()
            for order in data.get("orders", []):
                cancelled = (order.get("cancelStatus") or {}).get("cancelState") in ("CANCELED", "IN_PROGRESS")
                for line in order.get("lineItems", []):
                    cost = line.get("lineItemCost") or {}
                    sku = (line.get("sku") or "").removesuffix("-US")
                    sales.append(Sale(platform=self.name, order_id=order["orderId"], line_id=line["lineItemId"],
                                      sku=sku, remote_id=line.get("legacyItemId", ""),
                                      quantity=int(line.get("quantity", 1)), price=float(cost.get("value", 0)),
                                      currency=cost.get("currency", "EUR"), created=order.get("creationDate", ""),
                                      title=line.get("title", ""), cancelled=cancelled))
            offset += 200
            if offset >= int(data.get("total", 0)):
                return sales

    def list_listings(self) -> list[RemoteListing]:
        return []  # boutique eBay nouvelle : rien à importer
