"""Etsy (Open API v3) : annonces, photos, vidéo, traductions, stock et commandes.

Connexion : une application Etsy (keystring + secret partagé, sur etsy.com/developers) puis une
autorisation OAuth faite une fois depuis GemZ Studio (le jeton se renouvelle seul pendant 90 jours
d'inactivité).
"""
from __future__ import annotations

import html
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from .. import vault
from ..catalog import CatalogItem, PlatformState
from ..listing.builder import default_etsy_tags, default_pierres
from ..listing.prices import parse_price, platform_price
from ..listing.texts import LANG_NAMES, LANGS
from .base import NotConfigured, Platform, PlatformError, RemoteListing, Sale, USER_AGENT, request

API = "https://api.etsy.com/v3/application"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
AUTH_URL = "https://www.etsy.com/oauth/connect"
REDIRECT_PORT = 3003
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/etsy"
SCOPES = "listings_r listings_w listings_d shops_r transactions_r"
SECRET_KEYSTRING = "etsy_keystring"
SECRET_SHARED = "etsy_shared_secret"
SECRET_REFRESH = "etsy_refresh_token"
CUSTOM_PROPERTY = 513        # propriété libre d'Etsy, utilisée pour la taille des bagues et bracelets
TITLE_MAX = 140
MIN_PRICE = 0.20
# Question posée à l'acheteur d'une bague (en plus du choix de la taille)
RING_QUESTION = {"question_text": "Taille de doigt (FR) ou précision", "question_type": "text_input",
                 "instructions": "Si vous hésitez entre deux tailles, indiquez votre tour de doigt en mm.",
                 "required": False, "max_allowed_characters": 100}


def offering_amount(price) -> float:
    """Prix d'une offre Etsy ({amount, divisor} ou nombre) en euros."""
    if isinstance(price, dict):
        return float(price.get("amount", 0)) / float(price.get("divisor") or 100)
    return float(price or 0)


def html_to_text(text: str) -> str:
    """Les descriptions Etsy sont en texte brut : on garde les paragraphes et les puces."""
    text = re.sub(r"<\s*br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<\s*hr\s*/?>", "\n―――――\n", text, flags=re.I)
    text = re.sub(r"<\s*li[^>]*>", "• ", text, flags=re.I)
    text = re.sub(r"</\s*(p|h\d|li|ul|div)\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    lines = [line.strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def etsy_title(title: str) -> str:
    # Etsy refuse certains caractères et limite « & », « : », « % » et « + » à un seul exemplaire
    title = re.sub(r"[$^`]", "", title)
    for ch in "&:%+":
        first = title.find(ch)
        if first != -1:
            title = title[:first + 1] + title[first + 1:].replace(ch, " ")
    return re.sub(r"\s+", " ", title).strip()[:TITLE_MAX]


class Etsy(Platform):
    name = "etsy"
    label = "Etsy"

    def __init__(self, settings):
        super().__init__(settings)
        self._access = ""
        self._expires = 0.0
        self._lock = threading.Lock()
        self._session = None

    # --- connexion -----------------------------------------------------------
    def configured(self) -> bool:
        return bool(vault.get(SECRET_KEYSTRING) and vault.get(SECRET_REFRESH) and self.settings.etsy_shop_id)

    def missing_setup(self) -> str:
        if not vault.get(SECRET_KEYSTRING):
            return "Clé de l'application Etsy à renseigner (accès développeur en attente)."
        if not vault.get(SECRET_REFRESH):
            return "Autorisation Etsy à faire (bouton « Connecter Etsy »)."
        if not self.settings.etsy_shop_id:
            return "Boutique Etsy non identifiée : refais « Connecter Etsy »."
        return ""

    def _http(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers["User-Agent"] = USER_AGENT
        return self._session

    def _api_key(self) -> str:
        keystring, shared = vault.get(SECRET_KEYSTRING), vault.get(SECRET_SHARED)
        return f"{keystring}:{shared}" if shared else keystring

    def authorize(self) -> str:
        """Autorisation OAuth (PKCE) dans le navigateur ; renvoie le nom de la boutique."""
        from .oauth import pkce_pair, wait_for_code
        import secrets as pysecrets

        keystring = vault.get(SECRET_KEYSTRING)
        if not keystring:
            raise NotConfigured("Renseigne d'abord la clé (keystring) de l'application Etsy.")
        verifier, challenge = pkce_pair()
        state = pysecrets.token_urlsafe(16)
        url = AUTH_URL + "?" + urlencode({
            "response_type": "code", "redirect_uri": REDIRECT_URI, "scope": SCOPES, "client_id": keystring,
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256"})
        code = wait_for_code(url, REDIRECT_PORT, state)
        r = request(self._http(), "POST", TOKEN_URL, data={
            "grant_type": "authorization_code", "client_id": keystring, "redirect_uri": REDIRECT_URI,
            "code": code, "code_verifier": verifier})
        if r.status_code != 200:
            raise PlatformError(f"Etsy refuse l'autorisation ({r.status_code}) : {r.text[:200]}")
        self._store_tokens(r.json())
        me = self.call("GET", "/users/me")
        if not me.get("shop_id"):
            raise PlatformError("Ce compte Etsy n'a pas de boutique.")
        self.settings.etsy_shop_id = str(me["shop_id"])
        shop = self.call("GET", f"/shops/{self.settings.etsy_shop_id}")
        return shop.get("shop_name", "")

    def _store_tokens(self, data: dict) -> None:
        self._access = data["access_token"]
        self._expires = time.time() + float(data.get("expires_in", 3600))
        if data.get("refresh_token"):
            vault.put(SECRET_REFRESH, data["refresh_token"])

    def token(self) -> str:
        with self._lock:
            if self._access and time.time() < self._expires - 120:
                return self._access
            refresh = vault.get(SECRET_REFRESH)
            if not refresh:
                raise NotConfigured(self.missing_setup())
            r = request(self._http(), "POST", TOKEN_URL, data={
                "grant_type": "refresh_token", "client_id": vault.get(SECRET_KEYSTRING), "refresh_token": refresh})
            if r.status_code != 200:
                raise PlatformError("Autorisation Etsy expirée : refais « Connecter Etsy » dans les réglages.")
            self._store_tokens(r.json())
            return self._access

    def call(self, method: str, path: str, *, json_body=None, data=None, files=None, params=None) -> dict:
        headers = {"x-api-key": self._api_key(), "Authorization": f"Bearer {self.token()}"}
        r = request(self._http(), method, API + path, json=json_body, data=data, files=files, params=params,
                    headers=headers, timeout=600 if files else 60)
        if r.status_code == 401:
            self._access = ""
            raise PlatformError("Etsy refuse l'accès : refais « Connecter Etsy ».")
        if r.status_code >= 400:
            try:
                message = r.json().get("error", r.text)
            except ValueError:
                message = r.text
            raise PlatformError(f"Etsy ({r.status_code}) : {str(message)[:400]}")
        return r.json() if r.content else {}

    def test(self) -> str:
        if not self.configured():
            raise NotConfigured(self.missing_setup())
        shop = self.call("GET", f"/shops/{self.settings.etsy_shop_id}")
        return f"Connecté à la boutique Etsy {shop.get('shop_name', '')}."

    # Listes utiles au réglage (profil d'expédition, politique de retour, catégories)
    def shipping_profiles(self) -> list[tuple[str, str]]:
        data = self.call("GET", f"/shops/{self.settings.etsy_shop_id}/shipping-profiles")
        return [(str(p["shipping_profile_id"]), p.get("title", "")) for p in data.get("results", [])]

    def return_policies(self) -> list[tuple[str, str]]:
        data = self.call("GET", f"/shops/{self.settings.etsy_shop_id}/policies/return")
        return [(str(p["return_policy_id"]), f"{p.get('return_deadline', '')} jours") for p in data.get("results", [])]

    def search_taxonomy(self, term: str) -> list[tuple[int, str]]:
        data = self.call("GET", "/seller-taxonomy/nodes")
        found: list[tuple[int, str]] = []

        def walk(nodes, path):
            for node in nodes:
                full = f"{path} > {node['name']}" if path else node["name"]
                if term.lower() in node["name"].lower():
                    found.append((int(node["id"]), full))
                walk(node.get("children", []), full)

        walk(data.get("results", []), "")
        return found[:50]

    # --- annonce -------------------------------------------------------------
    def _texts(self, item: CatalogItem, lang: str) -> tuple[str, str]:
        text = item.listing.texts[lang]
        return etsy_title(text.title), html_to_text(text.description)

    def _price(self, item: CatalogItem) -> float:
        return platform_price(parse_price(item.info.prix), self.settings.etsy_markup)

    def _inventory(self, item: CatalogItem, quantity: int) -> dict:
        kind = self.settings.kind(item.kind_id)
        price = self._price(item)
        if kind.form == "bague" and item.info.taille_reelle.strip().isdigit():
            real = int(item.info.taille_reelle)
            extra = platform_price(self.settings.ring_resize_price, self.settings.etsy_markup) if self.settings.etsy_markup else self.settings.ring_resize_price
            sizes = sorted(set(self.settings.ring_sizes) | {real})
            products = [{"sku": item.sku if s == real else f"{item.sku}-T{s}",
                         "property_values": [{"property_id": CUSTOM_PROPERTY, "property_name": "Taille",
                                              "values": [str(s)]}],
                         "offerings": [{"price": price if s == real else round(price + extra, 2),
                                        "quantity": max(1, quantity), "is_enabled": True}]} for s in sizes]
            return {"products": products, "price_on_property": [CUSTOM_PROPERTY],
                    "quantity_on_property": [], "sku_on_property": [CUSTOM_PROPERTY]}
        if kind.form == "bracelet" and self.settings.bracelet_sizes:
            products = [{"sku": f"{item.sku}-{''.join(ch for ch in s if ch.isdigit())}",
                         "property_values": [{"property_id": CUSTOM_PROPERTY, "property_name": "Tour de poignet",
                                              "values": [s]}],
                         "offerings": [{"price": price, "quantity": max(1, quantity), "is_enabled": True}]}
                        for s in self.settings.bracelet_sizes]
            return {"products": products, "price_on_property": [], "quantity_on_property": [],
                    "sku_on_property": [CUSTOM_PROPERTY]}
        return {"products": [{"sku": item.sku, "property_values": [],
                              "offerings": [{"price": price, "quantity": max(1, quantity), "is_enabled": True}]}],
                "price_on_property": [], "quantity_on_property": [], "sku_on_property": []}

    def _readiness_id(self) -> int | None:
        """Délai de préparation (« processing profile ») : obligatoire sur chaque offre depuis 2024."""
        if str(self.settings.etsy_readiness_state_id).isdigit():
            return int(self.settings.etsy_readiness_state_id)
        if not hasattr(self, "_readiness_cache"):
            data = self.call("GET", f"/shops/{self.settings.etsy_shop_id}/readiness-state-definitions")
            results = data.get("results", [])
            self._readiness_cache = int(results[0]["readiness_state_id"]) if results else None
        return self._readiness_cache

    def _with_readiness(self, inventory: dict) -> dict:
        readiness = self._readiness_id()
        if readiness:
            for product in inventory["products"]:
                for offering in product["offerings"]:
                    offering.setdefault("readiness_state_id", readiness)
        return inventory

    def _rewrite_inventory(self, listing_id: str, quantity: int | None = None,
                           base_price: float | None = None) -> None:
        """Change les quantités et/ou les prix de l'annonce en gardant tout le reste tel quel
        (variantes, SKU, délais) : indispensable pour les annonces reliées.

        base_price : nouveau prix de l'offre la moins chère ; les autres tailles gardent leur écart.
        """
        current = self.call("GET", f"/listings/{listing_id}/inventory")
        products = [p for p in current.get("products", []) if not p.get("is_deleted")]
        delta = 0.0
        if base_price is not None:
            prices = [offering_amount(o.get("price")) for p in products for o in p.get("offerings", [])
                      if not o.get("is_deleted")]
            delta = base_price - min(prices) if prices else 0.0
        body_products = []
        for product in products:
            values = [{k: v for k, v in pv.items() if k in ("property_id", "property_name", "scale_id", "value_ids",
                                                          "values") and v is not None}
                      for pv in product.get("property_values", [])]
            offerings = []
            for offering in product.get("offerings", []):
                if offering.get("is_deleted"):
                    continue
                amount = offering_amount(offering.get("price"))
                entry = {"price": round(max(MIN_PRICE, amount + delta), 2),
                         "quantity": max(1, quantity) if quantity is not None else max(1, int(offering.get("quantity") or 1)),
                         "is_enabled": offering.get("is_enabled", True)}
                if offering.get("readiness_state_id"):
                    entry["readiness_state_id"] = offering["readiness_state_id"]
                offerings.append(entry)
            body_products.append({"sku": product.get("sku") or "", "property_values": values, "offerings": offerings})
        body = {"products": body_products,
                "price_on_property": current.get("price_on_property", []),
                "quantity_on_property": current.get("quantity_on_property", []),
                "sku_on_property": current.get("sku_on_property", [])}
        self.call("PUT", f"/listings/{listing_id}/inventory", json_body=self._with_readiness(body))

    def _set_inventory_quantity(self, listing_id: str, quantity: int) -> None:
        self._rewrite_inventory(listing_id, quantity=quantity)

    def _upload_media(self, item: CatalogItem, state: PlatformState, lang: str, replace: bool = False) -> None:
        """Photos (et vidéo) de l'annonce ; replace : nouvelles photos à la place des anciennes."""
        shop, listing_id = self.settings.etsy_shop_id, state.remote_id
        if replace:
            state.extra["uploaded"] = []
        done = state.extra.setdefault("uploaded", [])   # reprise après coupure : pas de doublon de photo
        alt = item.listing.texts[lang].alt[:250]
        # Pièce importée de Shopify (pas de photos locales) : on reprend les photos de la boutique
        sources = item.photos or [u for u in item.state("shopify").extra.get("images", []) if u.startswith("https://")]
        sources = sources[:20]
        for rank, path in enumerate(sources, start=1):
            if path in done:
                continue
            if path.startswith("https://"):
                r = request(self._http(), "GET", path, timeout=120)
                if r.status_code != 200:
                    raise PlatformError(f"Photo Shopify introuvable ({r.status_code}) : {path}", retryable=True)
                content, name = r.content, f"{item.sku}_{rank:02d}.jpg"
            else:
                content, name = Path(path).read_bytes(), Path(path).name
            data = {"rank": rank, "alt_text": alt}
            if replace:
                data["overwrite"] = "true"   # remplace la photo de même rang
            self.call("POST", f"/shops/{shop}/listings/{listing_id}/images", data=data,
                      files={"image": (name, content, "image/jpeg")})
            done.append(path)
        if replace:
            # Photos en trop (la nouvelle série est plus courte) : retirées
            for image in self.call("GET", f"/listings/{listing_id}/images").get("results", []):
                if int(image.get("rank") or 0) > len(sources):
                    self.call("DELETE", f"/shops/{shop}/listings/{listing_id}/images/{image['listing_image_id']}")
            if item.videos:
                for video in self.call("GET", f"/listings/{listing_id}/videos").get("results", []):
                    self.call("DELETE", f"/shops/{shop}/listings/{listing_id}/videos/{video['video_id']}")
        for path in item.videos[:1]:
            if path in done:
                continue
            with open(path, "rb") as f:
                self.call("POST", f"/shops/{shop}/listings/{listing_id}/videos",
                          data={"name": Path(path).name}, files={"video": (Path(path).name, f, "video/mp4")})
            done.append(path)

    def publish(self, item: CatalogItem, state: PlatformState) -> None:
        kind = self.settings.kind(item.kind_id)
        if not kind.etsy_taxonomy_id:
            raise NotConfigured(f"Catégorie Etsy du type « {kind.label} » à choisir (Réglages → Connexions).")
        if not (self.settings.etsy_shipping_profile_id and self.settings.etsy_return_policy_id):
            raise NotConfigured("Profil d'expédition et politique de retour Etsy à choisir (Réglages → Connexions).")
        shop = self.settings.etsy_shop_id
        lang = self._lang()
        title, description = self._texts(item, lang)
        if not state.remote_id:
            body = {
                "quantity": max(1, item.stock), "title": title, "description": description,
                "price": self._price(item), "who_made": self.settings.etsy_who_made, "when_made": self.settings.etsy_when_made,
                "taxonomy_id": kind.etsy_taxonomy_id, "is_supply": False, "type": "physical",
                "shipping_profile_id": int(self.settings.etsy_shipping_profile_id),
                "return_policy_id": int(self.settings.etsy_return_policy_id),
                "tags": self._tags(item), "materials": item.listing.pierres[:13],
                "should_auto_renew": True,
            }
            readiness = self._readiness_id()
            if readiness:
                body["readiness_state_id"] = readiness
            listing = self.call("POST", f"/shops/{shop}/listings", json_body=body)
            state.remote_id = str(listing["listing_id"])
            state.url = listing.get("url", f"https://www.etsy.com/listing/{state.remote_id}")
            state.extra["brouillon"] = True   # pas encore en vente : un envoi coupé reprend ici
        else:
            # Reprise d'un envoi coupé (brouillon déjà créé) : textes peut-être retouchés depuis
            self.call("PATCH", f"/shops/{shop}/listings/{state.remote_id}",
                      json_body={"title": title, "description": description, "tags": self._tags(item)})
        listing_id = state.remote_id
        self._upload_media(item, state, lang)
        state.extra["media_sig"] = item.media_signature()
        self.call("PUT", f"/listings/{listing_id}/inventory", json_body=self._with_readiness(self._inventory(item, item.stock)))
        self._translate(item, state, lang)
        if kind.form == "bague" and not state.extra.get("personnalisation"):
            self._personalize(state)
        self.call("PATCH", f"/shops/{shop}/listings/{listing_id}", json_body={"state": "active"})
        state.extra.pop("brouillon", None)
        state.price = self._price(item)

    def _lang(self) -> str:
        return self.settings.etsy_shop_language if self.settings.etsy_shop_language in LANGS else "en"

    def _tags(self, item: CatalogItem) -> list[str]:
        # Pièce importée de Shopify (aucun tag Etsy rédigé) : tags tirés du nom, des pierres et du pays
        return item.listing.etsy_tags or default_etsy_tags(item.info, item.listing.pierres or default_pierres(item.info))

    def _personalize(self, state: PlatformState) -> None:
        """Bague : en plus des tailles en variantes, un champ libre pour la taille ou une précision."""
        try:
            self.call("POST", f"/shops/{self.settings.etsy_shop_id}/listings/{state.remote_id}/personalization",
                      params={"supports_multiple_personalization_questions": "true"},
                      json_body={"personalization_questions": [RING_QUESTION]})
            state.extra["personnalisation"] = True
        except PlatformError as e:
            self._warn(state, f"champ de personnalisation de la bague non ajouté ({e})")

    @staticmethod
    def _warn(state: PlatformState, text: str) -> None:
        """Avertissement inscrit au journal après l'envoi (l'annonce reste en ligne)."""
        previous = state.extra.get("avertissement", "")
        state.extra["avertissement"] = f"{previous} · {text}" if previous else text

    def _translate(self, item: CatalogItem, state: PlatformState, primary: str) -> None:
        """Traductions des autres langues rédigées ; création (POST) si elles n'existent pas encore."""
        failed = []
        for lang in LANGS:
            if lang == primary or not item.listing.has_lang(lang):
                continue
            title, description = self._texts(item, lang)
            path = f"/shops/{self.settings.etsy_shop_id}/listings/{state.remote_id}/translations/{lang}"
            body = {"title": title, "description": description, "tags": self._tags(item)}
            try:
                try:
                    self.call("PUT", path, json_body=body)
                except PlatformError as e:
                    if "(404)" not in str(e) and "(400)" not in str(e):
                        raise
                    self.call("POST", path, json_body=body)
            except PlatformError as e:
                failed.append(f"{LANG_NAMES[lang]} ({str(e)[:120]})")
        if failed:
            self._warn(state, "traduction refusée : " + ", ".join(failed))

    def update(self, item: CatalogItem, state: PlatformState) -> None:
        """Textes, tags, traductions et photos (si elles ont changé) ; les prix passent par set_price."""
        if not state.remote_id or state.extra.get("brouillon"):
            return self.publish(item, state)   # jamais mise en vente (envoi coupé) : on termine la création
        lang = self._lang()
        title, description = self._texts(item, lang)
        body = {"title": title, "description": description}
        if item.listing.etsy_tags:   # annonce reliée sans tags rédigés : on garde ceux d'Etsy
            body["tags"] = item.listing.etsy_tags
        self.call("PATCH", f"/shops/{self.settings.etsy_shop_id}/listings/{state.remote_id}", json_body=body)
        signature = item.media_signature()
        if signature and state.extra.get("media_sig") and signature != state.extra["media_sig"]:
            self._upload_media(item, state, lang, replace=True)
        if signature:
            state.extra["media_sig"] = signature
        self._translate(item, state, lang)

    def set_price(self, item: CatalogItem, state: PlatformState) -> None:
        """Nouveau prix (majoration comprise) ; les autres tailles gardent leur écart."""
        if not state.remote_id:
            return
        new = self._price(item)
        self._rewrite_inventory(state.remote_id, base_price=new)
        state.price = new

    # --- stock ---------------------------------------------------------------
    def set_quantity(self, item: CatalogItem, state: PlatformState, quantity: int) -> None:
        if not state.remote_id or state.extra.get("brouillon"):
            return   # brouillon pas encore en vente : la fin de la création s'en charge
        path = f"/shops/{self.settings.etsy_shop_id}/listings/{state.remote_id}"
        if quantity <= 0:
            # Etsy n'accepte pas une quantité nulle sur une annonce active : on la désactive
            try:
                self.call("PATCH", path, json_body={"state": "inactive"})
            except PlatformError as e:
                if "sold_out" not in str(e) and "inactive" not in str(e):
                    raise
            return
        self._set_inventory_quantity(state.remote_id, quantity)
        self.call("PATCH", path, json_body={"state": "active"})

    def remote_quantity(self, item: CatalogItem, state: PlatformState) -> int | None:
        if not state.remote_id:
            return None
        listing = self.call("GET", f"/listings/{state.remote_id}")
        if listing.get("state") != "active":
            return 0
        return int(listing.get("quantity") or 0)

    # --- commandes -----------------------------------------------------------
    def sales_since(self, since: str) -> list[Sale]:
        start = int(datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp())
        sales: list[Sale] = []
        offset = 0
        while True:
            data = self.call("GET", f"/shops/{self.settings.etsy_shop_id}/receipts",
                             params={"min_created": start, "limit": 100, "offset": offset})
            for receipt in data.get("results", []):
                cancelled = str(receipt.get("status", "")).lower() in ("canceled", "cancelled", "fully refunded")
                for tr in receipt.get("transactions", []):
                    price = tr.get("price") or {}
                    amount = float(price.get("amount", 0)) / float(price.get("divisor", 100) or 100)
                    created = datetime.fromtimestamp(int(tr.get("created_timestamp") or receipt.get("created_timestamp") or 0),
                                                     tz=timezone.utc).isoformat()
                    sales.append(Sale(platform=self.name, order_id=str(receipt["receipt_id"]),
                                      line_id=str(tr["transaction_id"]), sku=tr.get("sku") or "",
                                      remote_id=str(tr.get("listing_id", "")), quantity=int(tr.get("quantity", 1)),
                                      price=amount, currency=price.get("currency_code", "EUR"), created=created,
                                      title=html.unescape(tr.get("title") or ""), cancelled=cancelled))
            offset += 100
            if offset >= int(data.get("count", 0)):
                return sales

    # --- import --------------------------------------------------------------
    def list_listings(self, states: tuple[str, ...] = ("active",)) -> list[RemoteListing]:
        """Annonces de la boutique ; « sold_out » : annonces épuisées (pièces vendues sur Etsy)."""
        out: list[RemoteListing] = []
        for state in states:
            offset = 0
            while True:
                data = self.call("GET", f"/shops/{self.settings.etsy_shop_id}/listings",
                                 params={"state": state, "limit": 100, "offset": offset, "includes": "Images"})
                for listing in data.get("results", []):
                    price = listing.get("price") or {}
                    out.append(RemoteListing(
                        platform=self.name, remote_id=str(listing["listing_id"]),
                        title=html.unescape(listing.get("title", "")),
                        skus=list(listing.get("skus") or []), quantity=int(listing.get("quantity") or 0),
                        price=float(price.get("amount", 0)) / float(price.get("divisor", 100) or 100),
                        url=listing.get("url", ""), tags=list(listing.get("tags") or []),
                        description=listing.get("description", ""),
                        image_urls=[img.get("url_fullxfull", "") for img in listing.get("images") or []],
                        extra={"state": state, **{k: listing.get(k) for k in (
                            "taxonomy_id", "shipping_profile_id", "return_policy_id", "who_made", "when_made",
                            "language")}}))
                offset += 100
                if offset >= int(data.get("count", 0)):
                    break
        return out
