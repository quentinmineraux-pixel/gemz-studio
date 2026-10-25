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
from ..listing import vocab
from ..listing.prices import parse_price
from ..listing.texts import LANGS
from ..quickentry import strip_accents
from .base import NotConfigured, Platform, PlatformError, RemoteListing, Sale, USER_AGENT, request

API_VERSION = "2026-07"
VENDOR = "GemZ"
SECRET_TOKEN = "shopify_token"
SECRET_CLIENT_ID = "shopify_client_id"
SECRET_CLIENT_SECRET = "shopify_client_secret"
RING_OPTION = "Taille"
BRACELET_OPTION = "Tour de poignet"
COLOR_KEY = "color-pattern"   # champ « Couleur » de Shopify (filtres de la boutique)

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

PRODUCT_CURRENT = """
query GemzCurrent($id: ID!) {
  product(id: $id) {
    tags status
    metafields(first: 20, namespace: "custom") { nodes { key value type } }
    color: metafield(namespace: "shopify", key: "color-pattern") { value }
    media(first: 50) { nodes { id } }
    variants(first: 100) { nodes { id sku price compareAtPrice } }
  }
}"""

PRODUCT_UPDATE = """
mutation GemzUpdate($product: ProductUpdateInput!, $media: [CreateMediaInput!]) {
  productUpdate(product: $product, media: $media) { product { id } userErrors { field message } }
}"""

DETACH_MEDIA = """
mutation GemzDetachMedia($files: [FileUpdateInput!]!) {
  fileUpdate(files: $files) { userErrors { field message } }
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

COLORS = """
query GemzColors { metaobjects(type: "shopify--color-pattern", first: 100) { nodes { id handle displayName } } }
"""

INVENTORY = """
query GemzInventory($id: ID!) {
  product(id: $id) {
    status
    variants(first: 100) {
      nodes { id sku inventoryItem { id inventoryLevels(first: 3) { nodes { location { id } quantities(names: ["available"]) { name quantity } } } } }
    }
  }
}"""

SNAPSHOT = """
query GemzSnapshot($after: String) {
  productVariants(first: 250, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { sku price compareAtPrice inventoryQuantity product { id status } }
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
      seo { title description }
      metafields(first: 10, namespace: "custom") { nodes { key value } }
      variants(first: 100) { nodes { id sku price compareAtPrice inventoryItem { id } } }
      media(first: 10) { nodes { ... on MediaImage { image { url } } } }
    }
  }
}"""

# Clés Shopify des champs traduits
TRANSLATION_KEYS = {"title": "title", "body_html": "description", "meta_title": "seo_title",
                    "meta_description": "seo_description"}

# Entrées du champ « Couleur » de la boutique (par leur poignée) et mots que l'IA peut employer
COLOR_WORDS = {
    "vert": "vert verte verts vertes verdatre emeraude olive kaki menthe jade",
    "bleu": "bleu bleue bleus bleues bleute bleutee azur cyan indigo marine turquoise saphir",
    "rouge": "rouge rouges bordeaux carmin cerise rubis vermillon grenat sang",
    "orange": "orange oranges orangee orangees cuivre cuivree corail abricot mandarine",
    "jaune": "jaune jaunes dore doree or ocre moutarde citron miel ambre",
    "rose": "rose roses rosee fuchsia saumon magenta framboise",
    "violet": "violet violette violets violacee mauve lavande lilas pourpre prune amethyste",
    "marron": "marron brun brune bruns brunes chocolat caramel bronze fauve havane chatain acajou cognac",
    "beige": "beige creme ivoire sable ecru champagne",
    "blanc": "blanc blanche blancs blanches laiteux laiteuse nacre nacree",
    "noir": "noir noire noirs noires anthracite",
    "gris": "gris grise argent argente argentee metallique acier plomb",
    "clair": "clair claire transparent transparente incolore translucide cristallin limpide",
    "multicolore": "multicolore multicolores irise irisee arcenciel chatoyant",
    "orrose": "orrose",
}
_COLOR_ALIASES = {word: handle for handle, words in COLOR_WORDS.items() for word in words.split()}
# Entrées « Couleur » de la boutique GemZ (secours si l'application n'a pas le droit de les lire)
GEMZ_DOMAIN = "p6cqtd-c1.myshopify.com"
GEMZ_COLORS = {
    "vert": "291670851923", "clair": "291694215507", "blanc": "291695034707", "bleu": "291695329619",
    "orange": "291695493459", "rouge": "291695984979", "beige": "291700375891", "orrose": "291700932947",
    "violet": "291701522771", "rose": "291876929875", "noir": "291878240595", "gris": "291878928723",
    "marron": "291879977299", "jaune": "291880010067", "multicolore": "298331439443",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z]", "", strip_accents(text).lower())


def _list_value(raw: str) -> list[str]:
    """Valeur d'un métachamp liste (« ["a","b"] ») ou simple."""
    try:
        value = json.loads(raw) if str(raw).startswith("[") else [raw]
    except ValueError:
        return []
    return [str(v) for v in value if str(v).strip()]


def _sent_values(fields: dict) -> dict[str, list[str]]:
    """Tags, filtres et couleurs que GemZ envoie : la prochaine mise à jour ne retirera que ceux-là."""
    out = {"tags": list(fields["tags"])}
    for m in fields["metafields"]:
        out[m["key"]] = json.loads(m["value"])
    return out


def color_refs(couleurs: list[str], palette: dict[str, str]) -> list[str]:
    """Couleurs de l'IA (« vert foncé », « bleu-vert »…) -> entrées existantes du champ Couleur (3 au plus).

    palette : {poignée sans accent ni tiret: identifiant}. Aucune entrée n'est créée."""
    out: list[str] = []
    for couleur in couleurs:
        whole = _norm(couleur)
        words = [_norm(w) for w in re.split(r"[\s/,-]+", couleur) if _norm(w)]
        for word in ([whole] if palette.get(_COLOR_ALIASES.get(whole, whole)) else words):
            ref = palette.get(_COLOR_ALIASES.get(word, word))
            if ref and ref not in out:
                out.append(ref)
    return out[:3]


def plan_quantities(levels: list[tuple[str, str, int]], quantity: int, spread: bool) -> dict[str, tuple[int, int]]:
    """Nouvelles quantités par article d'inventaire : {article: (avant, après)}.

    spread=False (pièce créée par GemZ, ou une seule variante) : chaque variante prend la quantité
    (une bague unique vendue disparaît dans toutes les tailles).
    spread=True (produit importé à plusieurs variantes, stock par taille géré dans Shopify) : la
    quantité est le total ; on retire des unités à la taille la mieux fournie, sans toucher aux autres.
    """
    quantity = max(0, quantity)
    current = {inv: max(0, q) for _sku, inv, q in levels}
    if not spread:
        target = {inv: quantity for inv in current}
    elif quantity == 0:
        target = {inv: 0 for inv in current}
    else:
        target = dict(current)
        total = sum(target.values())
        while total > quantity:
            inv = max(target, key=target.get)
            target[inv] -= 1
            total -= 1
        if total < quantity and levels:
            target[levels[0][1]] += quantity - total
    return {inv: (current[inv], q) for inv, q in target.items() if q != current[inv]}


def variant_prices(variants: list[dict], new_base: float, old_base: float, compare: float | None) -> list[dict]:
    """Nouveau prix de chaque variante : l'écart avec le prix de base est gardé (+12 € des autres tailles
    d'une bague, perles plus grosses…). Le prix barré suit le même écart (aucun = fin de la promotion)."""
    out = []
    for v in variants:
        current = float(v.get("price") or 0)
        price = max(0.0, round(new_base + current - old_base, 2))
        entry = {"id": v["id"], "price": f"{price:.2f}",
                 "compareAtPrice": f"{compare + price - new_base:.2f}" if compare else None}
        old_compare = f"{float(v['compareAtPrice']):.2f}" if v.get("compareAtPrice") else None
        if entry["price"] != f"{current:.2f}" or entry["compareAtPrice"] != old_compare:
            out.append(entry)
    return out


def main_variant(variants: list[dict], sku: str) -> dict | None:
    """Variante de référence : celle du SKU de la pièce, sinon la moins chère."""
    for v in variants:
        if sku and (v.get("sku") or "").upper() == sku.upper():
            return v
    priced = [v for v in variants if v.get("price") not in (None, "")]
    return min(priced, key=lambda v: float(v["price"])) if priced else None


def _key(value) -> str:
    return str(value).strip().casefold()


def merge_values(current: list[str], ours: list[str], last_sent: list[str] | None) -> list[str]:
    """Liste à renvoyer à Shopify (tags, filtres, couleurs) : rien de ce qui y est n'est jamais retiré
    (collections intactes) ; les nouvelles valeurs de la fiche sont ajoutées, sauf celles que GemZ avait
    déjà envoyées et qu'on a retirées à la main dans Shopify.
    last_sent : ce que GemZ a envoyé la dernière fois (None = inconnu)."""
    there, sent = {_key(v) for v in current}, {_key(v) for v in last_sent or []}
    out = list(current)
    for value in ours:
        if _key(value) not in there and _key(value) not in sent:
            out.append(value)
            there.add(_key(value))
    return out


def left_behind(current: list[str], ours: list[str], last_sent: list[str] | None) -> list[str]:
    """Valeurs que GemZ avait mises, qui ne sont plus dans la fiche mais restent dans Shopify."""
    mine, sent = {_key(v) for v in ours}, {_key(v) for v in last_sent or []}
    return [v for v in current if _key(v) in sent and _key(v) not in mine]


def spread_stock(item: CatalogItem, state: PlatformState) -> bool:
    """Produit importé à plusieurs variantes : le stock du catalogue est le total de ses tailles."""
    return item.origin == "import" and len(state.extra.get("variants", {})) > 1


def reference_sku(item: CatalogItem) -> str:
    """Variante dont le prix est celui de la fiche : la taille réelle d'une pièce GemZ ; pour un produit
    importé, la moins chère (comme à l'import)."""
    return "" if item.origin == "import" else item.sku


def remote_stock(variants: list[dict], sku: str, spread: bool) -> int:
    """Stock d'un produit Shopify au sens du catalogue (voir plan_quantities)."""
    quantities = [max(0, int(v.get("quantity") or 0)) for v in variants]
    if spread:
        return sum(quantities)
    main = [max(0, int(v.get("quantity") or 0)) for v in variants if (v.get("sku") or "").upper() == sku.upper()]
    return main[0] if main else max(quantities, default=0)


class Shopify(Platform):
    name = "shopify"
    label = "Shopify"

    def __init__(self, settings):
        super().__init__(settings)
        self._token = ""
        self._token_expires = 0.0
        self._setup: dict | None = None
        self._palette: dict[str, str] | None = None
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

    def palette(self) -> dict[str, str]:
        """Entrées du champ « Couleur » de la boutique : {poignée: identifiant}."""
        if self._palette is None:
            try:
                self._palette = {}
                for node in self.graphql(COLORS)["metaobjects"]["nodes"]:
                    self._palette[_norm(node["handle"])] = node["id"]
                    self._palette.setdefault(_norm(node.get("displayName") or ""), node["id"])
            except PlatformError:
                # Droit de lecture des « métaobjets » absent : entrées connues de la boutique GemZ
                self._palette = ({h: f"gid://shopify/Metaobject/{i}" for h, i in GEMZ_COLORS.items()}
                                 if self.domain == GEMZ_DOMAIN else {})
        return self._palette

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

    def _media_inputs(self, item: CatalogItem, state: PlatformState) -> list[dict]:
        """Photos puis vidéo envoyées vers Shopify (reprise après coupure : rien n'est renvoyé deux fois)."""
        alt = item.listing.texts["fr"].alt
        uploaded = state.extra.setdefault("uploaded", {})
        out = []
        for path in item.photos + item.videos:
            if path not in uploaded:
                uploaded[path] = self.upload(path)
            is_video = Path(path).suffix.lower() in (".mp4", ".mov")
            out.append({"originalSource": uploaded[path], "contentType": "VIDEO" if is_video else "IMAGE",
                        "alt": alt})
        return out

    # --- produit -------------------------------------------------------------
    def _variants(self, item: CatalogItem, kind_form: str) -> tuple[list[dict], list[dict]]:
        base = parse_price(item.info.prix)
        compare = parse_price(item.info.prix_barre) if item.info.prix_barre.strip() else 0.0
        stock = max(0, item.stock)
        location = self.location_id()

        def variant(option: str, value: str, price: float, sku: str) -> dict:
            out = {"optionValues": [{"optionName": option, "name": value}], "price": f"{price:.2f}", "sku": sku,
                   "inventoryItem": {"tracked": True, "sku": sku}, "inventoryPolicy": "DENY",
                   "inventoryQuantities": [{"locationId": location, "name": "available", "quantity": stock}]}
            if compare > base:
                out["compareAtPrice"] = f"{compare + price - base:.2f}"
            return out

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

    def _metafields(self, item: CatalogItem) -> list[dict]:
        out = [{"namespace": "custom", "key": key, "type": "list.single_line_text_field",
                "value": json.dumps(values, ensure_ascii=False)}
               for key, values in item.listing.metafields.items() if values]
        colors = color_refs(item.listing.couleurs, self.palette()) if item.listing.couleurs else []
        if colors:
            out.append({"namespace": "shopify", "key": COLOR_KEY, "type": "list.metaobject_reference",
                        "value": json.dumps(colors)})
        return out

    def _product_fields(self, item: CatalogItem, kind) -> dict:
        fr = item.listing.texts["fr"]
        fields = {"title": fr.title, "descriptionHtml": fr.description, "productType": kind.shopify_type,
                  "vendor": VENDOR, "tags": item.listing.tags, "metafields": self._metafields(item)}
        if fr.seo_title or fr.seo_description:   # vide : on ne remplace pas le référencement existant
            fields["seo"] = {"title": fr.seo_title, "description": fr.seo_description}
        return fields

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
        product_input = {**fields, "status": "ACTIVE", "productOptions": options, "variants": variants,
                         "files": self._media_inputs(item, state)}
        if kind.shopify_category:
            product_input["category"] = kind.shopify_category
        identifier = {"id": state.remote_id} if state.remote_id else None
        data = self.graphql(PRODUCT_SET, {"identifier": identifier, "input": product_input})
        payload = data["productSet"]
        self._user_errors(payload, "création du produit")
        product = payload["product"]
        state.remote_id = product["id"]
        state.extra["variants"] = {v["sku"]: {"id": v["id"], "inventory_item": v["inventoryItem"]["id"]}
                                   for v in product["variants"]["nodes"]}
        state.extra["media_sig"] = item.media_signature()
        state.extra["envoye"] = _sent_values(fields)
        state.url = product.get("onlineStoreUrl") or f"{self.setup()['shop']['primaryDomain']['url']}/products/{product['handle']}"
        state.price = parse_price(item.info.prix)
        vocab.remember(item.listing.metafields)
        state.extra["canaux"] = False
        self._publish_channels(product["id"])
        state.extra["canaux"] = True
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
        """Textes, tags, métachamps et photos (si elles ont changé) ; les prix passent par set_price."""
        if not state.remote_id:
            return self.publish(item, state)
        kind = self.settings.kind(item.kind_id)
        fields = self._product_fields(item, kind)
        current = self.graphql(PRODUCT_CURRENT, {"id": state.remote_id})["product"]
        if current is None:
            raise PlatformError("Produit introuvable sur Shopify (supprimé ?).")
        # Tags (collections), filtres et couleurs : jamais rien de retiré, et ce qui a été retiré à la main
        # dans Shopify n'est pas remis
        last = state.extra.get("envoye") or {}
        there = {m["key"]: _list_value(m["value"]) for m in (current.get("metafields") or {}).get("nodes", [])}
        there[COLOR_KEY] = _list_value((current.get("color") or {}).get("value") or "[]")
        tags = merge_values(current.get("tags") or [], fields["tags"], last.get("tags"))
        old_tags = left_behind(current.get("tags") or [], fields["tags"], last.get("tags"))
        if old_tags:
            # Sous-catégorie changée dans la fiche : l'ancien tag reste (GemZ n'en retire jamais), à voir
            note = (f"tag{'s' if len(old_tags) > 1 else ''} {', '.join(old_tags)} plus dans la fiche mais gardé"
                    f"{'s' if len(old_tags) > 1 else ''} sur Shopify : retire-le dans Shopify si la pièce ne doit plus "
                    "être dans cette collection")
            previous = state.extra.get("avertissement", "")
            state.extra["avertissement"] = f"{previous} · {note}" if previous else note
        metafields = [{**m, "value": json.dumps(merge_values(there.get(m["key"], []), json.loads(m["value"]),
                                                             last.get(m["key"])), ensure_ascii=False)}
                      for m in fields["metafields"]]
        product = {"id": state.remote_id, "title": fields["title"], "descriptionHtml": fields["descriptionHtml"],
                   "tags": tags, "metafields": metafields}
        if "seo" in fields:
            product["seo"] = fields["seo"]
        # Photos modifiées (nouvelles prises, ordre, photo principale) : remplacées sur la fiche
        signature = item.media_signature()
        media, old_media = None, []
        if signature and state.extra.get("media_sig") and signature != state.extra["media_sig"]:
            state.extra["uploaded"] = {}
            media = [{"originalSource": f["originalSource"], "mediaContentType": f["contentType"], "alt": f["alt"]}
                     for f in self._media_inputs(item, state)]
            old_media = [m["id"] for m in (current.get("media") or {}).get("nodes", [])]
        data = self.graphql(PRODUCT_UPDATE, {"product": product, "media": media})
        self._user_errors(data["productUpdate"], "mise à jour")
        if old_media:
            # Anciennes photos détachées de la fiche (elles restent dans Contenu → Fichiers)
            data = self.graphql(DETACH_MEDIA, {"files": [{"id": mid, "referencesToRemove": [state.remote_id]}
                                                         for mid in old_media]})
            self._user_errors(data["fileUpdate"], "anciennes photos")
        if signature:
            state.extra["media_sig"] = signature
        state.extra["envoye"] = _sent_values(fields)
        vocab.remember(item.listing.metafields)
        if state.extra.get("canaux") is False:
            # Mise en vente sur les canaux coupée à la création : refaite
            self._publish_channels(state.remote_id)
            state.extra["canaux"] = True
        self._translate(item, state.remote_id)

    # --- prix ----------------------------------------------------------------
    def set_price(self, item: CatalogItem, state: PlatformState) -> None:
        """Prix et prix barré (promotion) ; les autres tailles gardent leur écart de prix."""
        if not state.remote_id:
            return
        product = self.graphql(PRODUCT_CURRENT, {"id": state.remote_id})["product"]
        if product is None:
            raise PlatformError("Produit introuvable sur Shopify (supprimé ?).")
        variants = product["variants"]["nodes"]
        main = main_variant(variants, reference_sku(item))
        new_base = parse_price(item.info.prix)
        compare = parse_price(item.info.prix_barre) if item.info.prix_barre.strip() else 0.0
        changes = variant_prices(variants, new_base, float(main["price"]) if main else new_base,
                                 compare if compare > new_base else None)
        if changes:
            data = self.graphql(VARIANTS_PRICE, {"productId": state.remote_id, "variants": changes})
            self._user_errors(data["productVariantsBulkUpdate"], "prix")
        state.price = new_base

    # --- stock ---------------------------------------------------------------
    def _inventory(self, product_id: str) -> tuple[str, list[tuple[str, str, int]]]:
        """Statut du produit, et (sku, article d'inventaire, quantité disponible) de chaque variante à
        l'emplacement utilisé."""
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
        return product.get("status") or "", out

    def set_quantity(self, item: CatalogItem, state: PlatformState, quantity: int) -> None:
        if not state.remote_id:
            return
        location = self.location_id()
        status, levels = self._inventory(state.remote_id)
        plan = plan_quantities(levels, quantity, spread_stock(item, state))
        if plan:
            data = self.graphql(SET_QUANTITIES, {"key": str(uuid.uuid4()), "input": {
                "name": "available", "reason": "correction", "referenceDocumentUri": f"gemz://synchro/{item.sku}",
                "quantities": [{"inventoryItemId": inv, "locationId": location, "quantity": new,
                                "changeFromQuantity": old} for inv, (old, new) in plan.items()]}})
            self._user_errors(data["inventorySetQuantities"], "stock")
        if self.settings.shopify_archive_sold:
            # Pièce vendue : retirée de la boutique (archivée, donc récupérable), remise en ligne si restock
            wanted = "ARCHIVED" if quantity <= 0 else "ACTIVE"
            if status in ("ACTIVE", "ARCHIVED") and status != wanted:
                data = self.graphql(PRODUCT_UPDATE, {"product": {"id": state.remote_id, "status": wanted}})
                self._user_errors(data["productUpdate"], "archivage")

    def remote_quantity(self, item: CatalogItem, state: PlatformState) -> int | None:
        if not state.remote_id:
            return None
        variants = [{"sku": sku, "quantity": q} for sku, _inv, q in self._inventory(state.remote_id)[1]]
        return remote_stock(variants, item.sku, spread_stock(item, state))

    def snapshot(self) -> dict[str, dict]:
        """Stock, prix et statut de tous les produits en quelques appels : {produit: {status, variants}}."""
        out: dict[str, dict] = {}
        after = None
        while True:
            data = self.graphql(SNAPSHOT, {"after": after})["productVariants"]
            for v in data["nodes"]:
                product = out.setdefault(v["product"]["id"], {"status": v["product"]["status"], "variants": []})
                product["variants"].append({"sku": v.get("sku") or "", "price": v.get("price"),
                                            "compareAtPrice": v.get("compareAtPrice"),
                                            "quantity": int(v.get("inventoryQuantity") or 0)})
            if not data["pageInfo"]["hasNextPage"]:
                return out
            after = data["pageInfo"]["endCursor"]

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
                main = main_variant(variants, "")
                metafields = {m["key"]: _list_value(m["value"]) for m in (p.get("metafields") or {}).get("nodes", [])}
                out.append(RemoteListing(
                    platform=self.name, remote_id=p["id"], title=p["title"],
                    skus=[v["sku"] for v in variants if v.get("sku")], quantity=int(p.get("totalInventory") or 0),
                    price=float(main["price"]) if main else 0.0, url=p.get("onlineStoreUrl") or "",
                    product_type=p.get("productType", ""), tags=p.get("tags", []),
                    description=p.get("descriptionHtml", ""),
                    image_urls=[m["image"]["url"] for m in p["media"]["nodes"] if m.get("image")],
                    extra={"status": p.get("status"), "seo": p.get("seo") or {}, "metafields": metafields,
                           "compare_at": (main or {}).get("compareAtPrice"),
                           "variants": {v.get("sku") or v["id"]: {"id": v["id"], "inventory_item": v["inventoryItem"]["id"]}
                                        for v in variants}}))
            if not data["pageInfo"]["hasNextPage"]:
                return out
            after = data["pageInfo"]["endCursor"]
