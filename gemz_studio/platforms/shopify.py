"""Shopify (API Admin GraphQL) : création des produits, médias, traductions, stock et commandes.

Connexion : une application du Dev Dashboard Shopify installée sur la boutique, avec son identifiant
client et son secret (jeton obtenu automatiquement, renouvelé toutes les 24 h), ou un jeton d'accès
« shpat_… » d'une ancienne application personnalisée.
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import uuid
from pathlib import Path

from .. import vault
from ..catalog import CatalogItem, PlatformState
from ..listing.prices import parse_price
from ..listing.texts import LANGS
from .base import NotConfigured, Platform, PlatformError, RemoteListing, Sale, USER_AGENT, request

API_VERSION = "2026-07"
VENDOR = "GemZ"
SECRET_TOKEN = "shopify_token"
SECRET_CLIENT_ID = "shopify_client_id"
SECRET_CLIENT_SECRET = "shopify_client_secret"
RING_OPTION = "Taille"
BRACELET_OPTION = "Tour de poignet"

PRODUCT_SET = """
mutation GemzProductSet($identifier: ProductSetIdentifiers, $input: ProductSetInput!) {
  productSet(identifier: $identifier, input: $input, synchronous: true) {
    product {
      id handle onlineStoreUrl
      variants(first: 100) { nodes { id sku price inventoryItem { id } } }
    }
    userErrors { field message code }
  }
}"""

STAGED_UPLOADS = """
mutation GemzStagedUploads($input: [StagedUploadInput!]!) {
  stagedUploadsCreate(input: $input) {
    stagedTargets { url resourceUrl parameters { name value } }
    userErrors { field message }
  }
}"""

PUBLISH = """
mutation GemzPublish($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) { userErrors { field message } }
}"""

VARIANTS_PRICE = """
mutation GemzPrices($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
  productVariantsBulkUpdate(productId: $productId, variants: $variants) { userErrors { field message } }
}"""

SET_QUANTITIES = """
mutation GemzSetQuantities($input: InventorySetQuantitiesInput!, $key: String!) {
  inventorySetQuantities(input: $input) @idempotent(key: $key) { userErrors { field message code } }
}"""

TRANSLATABLE = """
query GemzTranslatable($id: ID!) {
  translatableResource(resourceId: $id) { translatableContent { key digest locale } }
}"""

TRANSLATE = """
mutation GemzTranslate($id: ID!, $translations: [TranslationInput!]!) {
  translationsRegister(resourceId: $id, translations: $translations) { userErrors { field message } }
}"""

SHOP_SETUP = """
query GemzShopSetup {
  shop { name primaryDomain { url } }
  locations(first: 5) { nodes { id name } }
  publications(first: 20) { nodes { id name } }
  shopLocales { locale published }
}"""

INVENTORY = """
query GemzInventory($id: ID!) {
  product(id: $id) {
    status
    variants(first: 100) {
      nodes { id sku inventoryItem { id inventoryLevels(first: 5) { nodes { location { id } quantities(names: ["available"]) { name quantity } } } } }
    }
  }
}"""

ORDERS = """
query GemzOrders($query: String!, $after: String) {
  orders(first: 50, after: $after, query: $query, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id name createdAt cancelledAt
      lineItems(first: 50) {
        nodes {
          id sku quantity title
          originalUnitPriceSet { shopMoney { amount currencyCode } }
          product { id }
        }
      }
    }
  }
}"""

SKU_TAKEN = """
query($q: String!) { productVariants(first: 1, query: $q) { nodes { sku product { id title } } } }
"""

PRODUCTS = """
query GemzProducts($after: String) {
  products(first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id title handle productType tags status totalInventory onlineStoreUrl descriptionHtml
      variants(first: 100) { nodes { id sku price inventoryQuantity inventoryItem { id } } }
      media(first: 10) { nodes { ... on MediaImage { image { url } } } }
    }
  }
}"""

# Clés Shopify des champs traduits
TRANSLATION_KEYS = {"title": "title", "body_html": "description", "meta_title": "seo_title",
                    "meta_description": "seo_description"}


class Shopify(Platform):
    name = "shopify"
    label = "Shopify"

    def __init__(self, settings):
        super().__init__(settings)
        self._token = ""
        self._token_expires = 0.0
        self._setup: dict | None = None
        self._lock = threading.Lock()
        self._session = None

    # --- connexion -----------------------------------------------------------
    def configured(self) -> bool:
        return bool(self.settings.shopify_domain and self.domain.endswith(".myshopify.com") and (vault.get(SECRET_TOKEN) or
                    (vault.get(SECRET_CLIENT_ID) and vault.get(SECRET_CLIENT_SECRET))))

    def missing_setup(self) -> str:
        if not self.settings.shopify_domain:
            return "Adresse de la boutique (xxx.myshopify.com) à renseigner."
        if not self.domain.endswith(".myshopify.com"):
            return (f"Mets l'adresse technique en .myshopify.com (Shopify → Paramètres → Domaines), "
                    f"pas {self.domain}.")
        if not self.configured():
            return "Identifiants de l'application Shopify à renseigner."
        return ""

    @property
    def domain(self) -> str:
        d = self.settings.shopify_domain.strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
        return d if "." in d else f"{d}.myshopify.com"

    def _http(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers["User-Agent"] = USER_AGENT
        return self._session

    def token(self) -> str:
        if not self.configured():
            raise NotConfigured(self.missing_setup())
        # Identifiant + secret de l'application (Dev Dashboard) d'abord ; le jeton shpat_ fixe seulement sans eux
        static = vault.get(SECRET_TOKEN)
        if static and not (vault.get(SECRET_CLIENT_ID) and vault.get(SECRET_CLIENT_SECRET)):
            return static
        with self._lock:
            if self._token and time.time() < self._token_expires - 300:
                return self._token
            r = request(self._http(), "POST", f"https://{self.domain}/admin/oauth/access_token",
                        data={"grant_type": "client_credentials", "client_id": vault.get(SECRET_CLIENT_ID),
                              "client_secret": vault.get(SECRET_CLIENT_SECRET)})
            if r.status_code != 200:
                raise PlatformError(f"Shopify refuse la connexion ({r.status_code}) : vérifie l'identifiant, le secret "
                                    f"et que l'application est bien installée sur la boutique. {r.text[:200]}")
            data = r.json()
            self._token = data["access_token"]
            self._token_expires = time.time() + float(data.get("expires_in", 86399))
            return self._token

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        url = f"https://{self.domain}/admin/api/{API_VERSION}/graphql.json"
        for attempt in range(5):
            r = request(self._http(), "POST", url, json={"query": query, "variables": variables or {}},
                        headers={"X-Shopify-Access-Token": self.token(), "Content-Type": "application/json"})
            if r.status_code in (401, 403):
                self._token = ""
                raise PlatformError(f"Shopify refuse l'accès ({r.status_code}) : "
                                    + ("jeton refusé, vérifie l'identifiant et le secret." if r.status_code == 401 else
                                       "droits de l'application à vérifier (produits, stock, commandes, traductions, "
                                       "publications).") + f" {r.text[:200]}")
            if r.status_code != 200:
                raise PlatformError(f"Erreur Shopify {r.status_code} : {r.text[:300]}", retryable=r.status_code >= 500)
            body = r.json()
            errors = body.get("errors") or []
            if any((e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
                time.sleep(2 + attempt * 2)
                continue
            if errors:
                raise PlatformError("Shopify : " + "; ".join(e.get("message", "") for e in errors)[:500])
            return body["data"]
        raise PlatformError("Shopify : trop de demandes, nouvel essai plus tard.", retryable=True)

    @staticmethod
    def _user_errors(payload: dict, what: str) -> None:
        errors = payload.get("userErrors") or []
        if errors:
            raise PlatformError(f"Shopify ({what}) : " + "; ".join(e.get("message", "") for e in errors)[:500])

    def setup(self) -> dict:
        if self._setup is None:
            self._setup = self.graphql(SHOP_SETUP)
        return self._setup

    def test(self) -> str:
        self._setup = None
        data = self.setup()
        return f"Connecté à {data['shop']['name']} ({data['shop']['primaryDomain']['url']})."

    def location_id(self) -> str:
        if self.settings.shopify_location_id:
            return self.settings.shopify_location_id
        locations = self.setup()["locations"]["nodes"]
        if not locations:
            raise PlatformError("Aucun entrepôt (emplacement) dans la boutique Shopify.")
        return locations[0]["id"]

    # --- médias --------------------------------------------------------------
    def upload(self, path: str) -> str:
        """Envoie un fichier local vers Shopify ; renvoie l'adresse à donner comme originalSource."""
        p = Path(path)
        is_video = p.suffix.lower() in (".mp4", ".mov")
        mime = mimetypes.guess_type(p.name)[0] or ("video/mp4" if is_video else "image/jpeg")
        data = self.graphql(STAGED_UPLOADS, {"input": [{
            "filename": p.name, "mimeType": mime, "httpMethod": "POST",
            "resource": "VIDEO" if is_video else "IMAGE", "fileSize": str(p.stat().st_size)}]})
        payload = data["stagedUploadsCreate"]
        self._user_errors(payload, "envoi des médias")
        target = payload["stagedTargets"][0]
        fields = {prm["name"]: prm["value"] for prm in target["parameters"]}
        with p.open("rb") as f:
            r = request(self._http(), "POST", target["url"], data=fields, files={"file": (p.name, f, mime)},
                        timeout=600)
        if r.status_code not in (200, 201, 204):
            raise PlatformError(f"Envoi de {p.name} vers Shopify refusé ({r.status_code}).", retryable=True)
        return target["resourceUrl"]

    # --- produit -------------------------------------------------------------
    def _variants(self, item: CatalogItem, kind_form: str) -> tuple[list[dict], list[dict]]:
        base = parse_price(item.info.prix)
        stock = max(0, item.stock)
        location = self.location_id()

        def variant(option: str, value: str, price: float, sku: str) -> dict:
            return {"optionValues": [{"optionName": option, "name": value}], "price": f"{price:.2f}", "sku": sku,
                    "inventoryItem": {"tracked": True, "sku": sku}, "inventoryPolicy": "DENY",
                    "inventoryQuantities": [{"locationId": location, "name": "available", "quantity": stock}]}

        if kind_form == "bague" and item.info.taille_reelle.strip().isdigit():
            real = int(item.info.taille_reelle)
            sizes = sorted(set(self.settings.ring_sizes) | {real})
            options = [{"name": RING_OPTION, "values": [{"name": str(s)} for s in sizes]}]
            variants = [variant(RING_OPTION, str(s), base if s == real else base + self.settings.ring_resize_price,
                                item.sku if s == real else f"{item.sku}-T{s}") for s in sizes]
            # La taille réelle d'abord : c'est elle qui est proposée par défaut
            variants.sort(key=lambda v: v["sku"] != item.sku)
            return options, variants
        if kind_form == "bracelet" and self.settings.bracelet_sizes:
            sizes = self.settings.bracelet_sizes
            options = [{"name": BRACELET_OPTION, "values": [{"name": s} for s in sizes]}]
            variants = [variant(BRACELET_OPTION, s, base, f"{item.sku}-{''.join(ch for ch in s if ch.isdigit())}")
                        for s in sizes]
            return options, variants
        options = [{"name": "Title", "values": [{"name": "Default Title"}]}]
        return options, [variant("Title", "Default Title", base, item.sku)]

    def _product_fields(self, item: CatalogItem, kind) -> dict:
        fr = item.listing.texts["fr"]
        metafields = [{"namespace": "custom", "key": key, "type": "list.single_line_text_field",
                       "value": json.dumps(values, ensure_ascii=False)}
                      for key, values in item.listing.metafields.items() if values]
        return {"title": fr.title, "descriptionHtml": fr.description, "productType": kind.shopify_type,
                "vendor": VENDOR, "tags": item.listing.tags,
                "seo": {"title": fr.seo_title, "description": fr.seo_description}, "metafields": metafields}

    def publish(self, item: CatalogItem, state: PlatformState) -> None:
        if not state.remote_id and not state.extra.get("uploaded"):
            # SKU déjà pris par un produit existant (créé à la main) : on ne crée pas de doublon
            taken = self.graphql(SKU_TAKEN, {"q": f"sku:{item.sku}"})["productVariants"]["nodes"]
            base = re.sub(r"-(T\d{2}|\d{2,3}|US)$", "", (taken[0]["sku"] or "").upper()) if taken else ""
            if base == item.sku.upper():
                raise PlatformError(f"Le SKU {item.sku} existe déjà sur Shopify (« {taken[0]['product']['title']} ») : "
                                    "change le SKU ou importe ce produit depuis le catalogue.")
        kind = self.settings.kind(item.kind_id)
        fields = self._product_fields(item, kind)
        options, variants = self._variants(item, kind.form)
        alt = item.listing.texts["fr"].alt
        uploaded = state.extra.setdefault("uploaded", {})   # reprise après coupure : rien n'est renvoyé deux fois
        files = []
        for path in item.photos + item.videos:
            if path not in uploaded:
                uploaded[path] = self.upload(path)
            is_video = Path(path).suffix.lower() in (".mp4", ".mov")
            files.append({"originalSource": uploaded[path], "contentType": "VIDEO" if is_video else "IMAGE",
                          "alt": alt})
        product_input = {**fields, "status": "ACTIVE", "productOptions": options, "variants": variants,
                         "files": files}
        identifier = {"id": state.remote_id} if state.remote_id else None
        data = self.graphql(PRODUCT_SET, {"identifier": identifier, "input": product_input})
        payload = data["productSet"]
        self._user_errors(payload, "création du produit")
        product = payload["product"]
        state.remote_id = product["id"]
        state.extra["variants"] = {v["sku"]: {"id": v["id"], "inventory_item": v["inventoryItem"]["id"]}
                                   for v in product["variants"]["nodes"]}
        state.url = product.get("onlineStoreUrl") or f"{self.setup()['shop']['primaryDomain']['url']}/products/{product['handle']}"
        state.price = parse_price(item.info.prix)
        self._publish_channels(product["id"])
        self._translate(item, product["id"])

    def _publish_channels(self, product_id: str) -> None:
        # Boutique en ligne, Google & YouTube, Facebook & Instagram… mais pas la caisse (point de vente)
        publications = [p["id"] for p in self.setup()["publications"]["nodes"]
                        if "point de vente" not in p["name"].lower() and "point of sale" not in p["name"].lower()]
        if publications:
            data = self.graphql(PUBLISH, {"id": product_id, "input": [{"publicationId": p} for p in publications]})
            self._user_errors(data["publishablePublish"], "mise en vente sur les canaux")

    def _translate(self, item: CatalogItem, product_id: str) -> None:
        """Traductions anglais / espagnol / allemand, pour les langues ajoutées à la boutique."""
        locales = {loc["locale"].split("-")[0] for loc in self.setup()["shopLocales"]}
        wanted = [lang for lang in LANGS if lang != "fr" and lang in locales]
        if not wanted:
            return
        content = self.graphql(TRANSLATABLE, {"id": product_id})["translatableResource"]["translatableContent"]
        digests = {c["key"]: c["digest"] for c in content}
        translations = []
        for lang in wanted:
            if not item.listing.has_lang(lang):
                continue
            text = item.listing.texts[lang]
            for key, attr in TRANSLATION_KEYS.items():
                value = getattr(text, attr)
                if value and key in digests:
                    translations.append({"key": key, "locale": lang, "value": value,
                                         "translatableContentDigest": digests[key]})
        if translations:
            data = self.graphql(TRANSLATE, {"id": product_id, "translations": translations})
            self._user_errors(data["translationsRegister"], "traductions")

    def update(self, item: CatalogItem, state: PlatformState) -> None:
        """Textes, tags, métachamps et prix ; les photos ne sont pas renvoyées."""
        if not state.remote_id:
            return self.publish(item, state)
        kind = self.settings.kind(item.kind_id)
        data = self.graphql(PRODUCT_SET, {"identifier": {"id": state.remote_id},
                                          "input": self._product_fields(item, kind)})
        self._user_errors(data["productSet"], "mise à jour")
        _options, variants = self._variants(item, kind.form)
        known = state.extra.get("variants", {})
        prices = [{"id": known[v["sku"]]["id"], "price": v["price"]} for v in variants if v["sku"] in known]
        if prices:
            data = self.graphql(VARIANTS_PRICE, {"productId": state.remote_id, "variants": prices})
            self._user_errors(data["productVariantsBulkUpdate"], "prix")
        state.price = parse_price(item.info.prix)
        self._translate(item, state.remote_id)

    # --- stock ---------------------------------------------------------------
    def _levels(self, product_id: str) -> list[tuple[str, str, int]]:
        """(sku, article d'inventaire, quantité disponible) de chaque variante à l'emplacement utilisé."""
        location = self.location_id()
        product = self.graphql(INVENTORY, {"id": product_id})["product"]
        if product is None:
            raise PlatformError("Produit introuvable sur Shopify (supprimé ?).")
        out = []
        for v in product["variants"]["nodes"]:
            quantity = 0
            for level in v["inventoryItem"]["inventoryLevels"]["nodes"]:
                if level["location"]["id"] == location:
                    quantity = next((q["quantity"] for q in level["quantities"] if q["name"] == "available"), 0)
            out.append((v["sku"] or "", v["inventoryItem"]["id"], quantity))
        return out

    def set_quantity(self, item: CatalogItem, state: PlatformState, quantity: int) -> None:
        if not state.remote_id:
            return
        location = self.location_id()
        quantities = [{"inventoryItemId": inv, "locationId": location, "quantity": max(0, quantity),
                       "changeFromQuantity": current}
                      for _sku, inv, current in self._levels(state.remote_id) if current != max(0, quantity)]
        if not quantities:
            return
        data = self.graphql(SET_QUANTITIES, {"key": str(uuid.uuid4()), "input": {
            "name": "available", "reason": "correction", "quantities": quantities,
            "referenceDocumentUri": f"gemz://synchro/{item.sku}"}})
        self._user_errors(data["inventorySetQuantities"], "stock")

    def remote_quantity(self, item: CatalogItem, state: PlatformState) -> int | None:
        if not state.remote_id:
            return None
        levels = self._levels(state.remote_id)
        main = [q for sku, _inv, q in levels if sku == item.sku]
        return main[0] if main else (max(q for _s, _i, q in levels) if levels else 0)

    # --- commandes -----------------------------------------------------------
    def sales_since(self, since: str) -> list[Sale]:
        sales: list[Sale] = []
        after = None
        while True:
            data = self.graphql(ORDERS, {"query": f"created_at:>='{since}'", "after": after})["orders"]
            for order in data["nodes"]:
                for line in order["lineItems"]["nodes"]:
                    money = (line.get("originalUnitPriceSet") or {}).get("shopMoney") or {}
                    sales.append(Sale(platform=self.name, order_id=order["id"], line_id=line["id"],
                                      sku=line.get("sku") or "", remote_id=(line.get("product") or {}).get("id", ""),
                                      quantity=int(line["quantity"]), price=float(money.get("amount") or 0),
                                      currency=money.get("currencyCode", "EUR"), created=order["createdAt"],
                                      title=line.get("title", ""), cancelled=bool(order.get("cancelledAt"))))
            if not data["pageInfo"]["hasNextPage"]:
                return sales
            after = data["pageInfo"]["endCursor"]

    # --- import --------------------------------------------------------------
    def list_listings(self) -> list[RemoteListing]:
        out: list[RemoteListing] = []
        after = None
        while True:
            data = self.graphql(PRODUCTS, {"after": after})["products"]
            for p in data["nodes"]:
                variants = p["variants"]["nodes"]
                out.append(RemoteListing(
                    platform=self.name, remote_id=p["id"], title=p["title"],
                    skus=[v["sku"] for v in variants if v.get("sku")], quantity=int(p.get("totalInventory") or 0),
                    price=float(variants[0]["price"]) if variants else 0.0, url=p.get("onlineStoreUrl") or "",
                    product_type=p.get("productType", ""), tags=p.get("tags", []),
                    description=p.get("descriptionHtml", ""),
                    image_urls=[m["image"]["url"] for m in p["media"]["nodes"] if m.get("image")],
                    extra={"status": p.get("status"), "variants": {v.get("sku") or v["id"]: {
                        "id": v["id"], "inventory_item": v["inventoryItem"]["id"]} for v in variants}}))
            if not data["pageInfo"]["hasNextPage"]:
                return out
            after = data["pageInfo"]["endCursor"]
