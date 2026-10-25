import json

import pytest

from gemz_studio.catalog import CatalogItem
from gemz_studio.config import Settings
from gemz_studio.models import ProductInfo
from gemz_studio.platforms import PlatformError
from gemz_studio.platforms.ebay import Ebay
from gemz_studio.platforms.etsy import Etsy
from gemz_studio.platforms.shopify import (Shopify, color_refs, left_behind, main_variant, merge_values,
                                           plan_quantities, reference_sku, remote_stock, variant_prices)


def test_couleurs_vers_les_entrees_de_la_boutique():
    palette = {"rouge": "gid://r", "bleu": "gid://b", "jaune": "gid://j", "blanc": "gid://w"}
    assert color_refs(["Rouge", "bleu turquoise", "doré", "blanc"], palette) == ["gid://r", "gid://b", "gid://j"]
    assert color_refs(["fuchsia électrique"], palette) == []   # pas d'entrée « rose » : rien n'est créé


def test_stock_par_variante():
    levels = [("BAG4", "inv1", 1), ("BAG4-T52", "inv2", 1), ("BAG4-T60", "inv3", 0)]
    # Pièce unique GemZ : toutes les tailles suivent
    assert plan_quantities(levels, 0, spread=False) == {"inv1": (1, 0), "inv2": (1, 0)}
    # Produit importé, stock par taille : on retire à la taille la mieux fournie
    sizes = [("BR3-17", "a", 3), ("BR3-19", "b", 1), ("BR3-21", "c", 2)]
    assert plan_quantities(sizes, 5, spread=True) == {"a": (3, 2)}
    assert plan_quantities(sizes, 7, spread=True) == {"a": (3, 4)}
    assert remote_stock([{"sku": "BR3-17", "quantity": 3}, {"sku": "BR3-19", "quantity": 1}], "BR3", True) == 4
    assert remote_stock([{"sku": "BAG4", "quantity": 0}, {"sku": "BAG4-T52", "quantity": 1}], "BAG4", False) == 0


def test_prix_des_variantes_gardent_leur_ecart():
    variants = [{"id": "v1", "sku": "BAG4", "price": "100.00", "compareAtPrice": None},
                {"id": "v2", "sku": "BAG4-T52", "price": "112.00", "compareAtPrice": None}]
    assert main_variant(variants, "BAG4")["id"] == "v1"
    assert main_variant(variants, "")["id"] == "v1"   # la moins chère
    changes = variant_prices(variants, 110.0, 100.0, 150.0)
    assert changes == [{"id": "v1", "price": "110.00", "compareAtPrice": "150.00"},
                       {"id": "v2", "price": "122.00", "compareAtPrice": "162.00"}]
    assert variant_prices(variants, 100.0, 100.0, None) == []


def test_variante_de_reference():
    item = CatalogItem(sku="BAG4-T52", kind_id="bague", origin="import")
    assert reference_sku(item) == ""
    assert reference_sku(CatalogItem(sku="BAG4", kind_id="bague")) == "BAG4"


def test_tags_et_filtres_respectent_l_admin_shopify():
    assert merge_values(["Promo", "cristal"], ["cristal", "maroc"], None) == ["Promo", "cristal", "maroc"]
    # Catégorie changée dans GemZ : rien n'est retiré (collections intactes), l'ancien tag est signalé
    assert merge_values(["promo", "cristal", "maroc"], ["geode", "maroc"], ["cristal", "maroc"]) == \
        ["promo", "cristal", "maroc", "geode"]
    assert left_behind(["promo", "cristal", "maroc"], ["geode", "maroc"], ["cristal", "maroc"]) == ["cristal"]
    assert left_behind(["promo", "cristal"], ["geode"], None) == []
    # Tag retiré à la main dans Shopify : pas remis
    assert merge_values(["maroc"], ["cristal", "maroc"], ["cristal", "maroc"]) == ["maroc"]
    assert merge_values(["Maroc"], ["maroc"], None) == ["Maroc"]   # même tag, autre casse : pas de doublon


class FakeShopify(Shopify):
    def __init__(self, current):
        super().__init__(Settings(shopify_domain="test.myshopify.com"))
        self.current, self.sent = current, []

    def graphql(self, query, variables=None):
        self.sent.append((query, variables))
        if "GemzCurrent" in query:
            return {"product": self.current}
        if "GemzShopSetup" in query:
            return {"shop": {"name": "Test", "primaryDomain": {"url": "https://test"}}, "locations": {"nodes": []},
                    "publications": {"nodes": [{"id": "gid://pub/1", "name": "Boutique en ligne"}]},
                    "shopLocales": [{"locale": "fr", "published": True}]}
        return {"productUpdate": {"userErrors": []}, "publishablePublish": {"userErrors": []}}


def test_mise_a_jour_shopify_garde_les_ajouts_de_l_admin():
    current = {"tags": ["Promo", "cristal", "vanadinite"], "status": "ACTIVE", "color": None,
               "metafields": {"nodes": [{"key": "pierre", "value": '["vanadinite","baryte"]',
                                         "type": "list.single_line_text_field"}]},
               "media": {"nodes": []}, "variants": {"nodes": []}}
    shop = FakeShopify(current)
    item = CatalogItem(sku="MFC1", kind_id="mineral-collection", info=ProductInfo(nom="vanadinite", prix="67"))
    item.listing.texts["fr"].title = "Vanadinite"
    item.listing.tags = ["geode", "vanadinite"]          # sous-catégorie passée de « cristal » à « géode »
    item.listing.metafields = {"pierre": ["vanadinite"]}
    state = item.state("shopify")
    state.remote_id = "gid://shopify/Product/1"
    state.extra.update(envoye={"tags": ["cristal", "vanadinite"], "pierre": ["vanadinite"]}, canaux=False)
    shop.update(item, state)
    product = next(v for q, v in shop.sent if "GemzUpdate" in q)["product"]
    assert product["tags"] == ["Promo", "cristal", "vanadinite", "geode"]   # rien de retiré
    assert "cristal" in state.extra["avertissement"]                        # mais signalé au journal
    assert json.loads(product["metafields"][0]["value"]) == ["vanadinite", "baryte"]   # « baryte » ajouté à la main
    assert state.extra["envoye"]["tags"] == ["geode", "vanadinite"]
    assert any("GemzPublish" in q for q, _v in shop.sent) and state.extra["canaux"] is True


class FakeEtsy(Etsy):
    def __init__(self, responses):
        super().__init__(Settings(etsy_shop_id="42"))
        self.calls, self.responses = [], responses

    def call(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs.get("json_body")))
        result = self.responses.get((method, path.rsplit("/", 1)[-1]))
        if isinstance(result, Exception):
            raise result
        return result or {}

    def _readiness_id(self):
        return 7


def _item_two_langs():
    item = CatalogItem(sku="MFC1", kind_id="mineral-collection", info=ProductInfo(nom="malachite", prix="100"))
    for lang in ("fr", "en", "de"):
        item.listing.pieces[lang] = {"aspect": "x", "lithotherapie": "y"}
        item.listing.texts[lang].title = f"Titre {lang}"
        item.listing.texts[lang].description = "<p>Texte</p>"
    return item


def test_etsy_traduction_creee_si_absente_et_refus_signale():
    etsy = FakeEtsy({("PUT", "en"): PlatformError("Etsy (404) : not found"),
                     ("PUT", "de"): PlatformError("Etsy (400) : language not supported"),
                     ("POST", "de"): PlatformError("Etsy (400) : language not supported")})
    item = _item_two_langs()
    state = item.state("etsy")
    state.remote_id = "555"
    etsy._translate(item, state, "fr")
    methods = [(m, p.rsplit("/", 1)[-1]) for m, p, _b in etsy.calls]
    assert methods == [("PUT", "en"), ("POST", "en"), ("PUT", "de"), ("POST", "de")]
    assert "allemand" in state.extra["avertissement"] and "anglais" not in state.extra["avertissement"]


def test_etsy_creation_coupee_reprise_jusqu_a_la_mise_en_vente(tmp_path):
    photo = tmp_path / "MFC1-1.jpg"
    photo.write_bytes(b"jpeg")
    etsy = FakeEtsy({("POST", "listings"): {"listing_id": 555}, ("POST", "images"): PlatformError("Etsy (503) : panne")})
    etsy.settings.etsy_shipping_profile_id, etsy.settings.etsy_return_policy_id = "1", "2"
    etsy.settings.kind("mineral-collection").etsy_taxonomy_id = 123
    item = _item_two_langs()
    item.photos = [str(photo)]
    state = item.state("etsy")
    with pytest.raises(PlatformError):
        etsy.publish(item, state)
    assert state.remote_id == "555" and state.extra["brouillon"]
    etsy.set_quantity(item, state, 0)                        # brouillon : jamais touché par la synchro
    assert etsy.calls[-1][1].endswith("/images")
    del etsy.responses[("POST", "images")]
    etsy.calls.clear()
    etsy.update(item, state)                                 # nouvel essai : la création est terminée
    methods = [(m, p.rsplit("/", 1)[-1]) for m, p, _b in etsy.calls]
    assert methods[0] == ("PATCH", "555") and ("POST", "images") in methods and ("PUT", "inventory") in methods
    assert etsy.calls[-1][2] == {"state": "active"} and "brouillon" not in state.extra


def test_etsy_nouveau_prix_garde_l_ecart_des_tailles():
    inventory = {"products": [
        {"sku": "BAG4", "property_values": [], "offerings": [{"price": {"amount": 2000, "divisor": 100}, "quantity": 1}]},
        {"sku": "BAG4-T52", "property_values": [], "offerings": [{"price": {"amount": 3200, "divisor": 100}, "quantity": 1}]},
    ], "price_on_property": [513], "quantity_on_property": [], "sku_on_property": [513]}
    etsy = FakeEtsy({("GET", "inventory"): inventory})
    etsy._rewrite_inventory("555", base_price=25.0)
    put = [b for m, _p, b in etsy.calls if m == "PUT"][0]
    assert [o["price"] for p in put["products"] for o in p["offerings"]] == [25.0, 37.0]
    assert all(o["readiness_state_id"] == 7 for p in put["products"] for o in p["offerings"])


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


def test_ebay_refus_partiel_signale_et_date_avec_millisecondes():
    ebay = Ebay(Settings())
    seen = {}

    def call(method, url, **kwargs):
        seen.setdefault(url, []).append(kwargs)
        if url.endswith("bulk_update_price_quantity"):
            return FakeResponse({"responses": [{"statusCode": 400, "errors": [{"message": "Prix refusé"}]}]})
        return FakeResponse({"orders": [{"orderId": "O1", "creationDate": "2026-10-25T10:00:00.000Z", "lineItems": [
            {"lineItemId": "L1", "sku": "MFC1-US", "quantity": 2, "lineItemCost": {"value": "50.0", "currency": "USD"}}]}],
            "total": 1})
    ebay.call = call
    item = CatalogItem(sku="MFC1", kind_id="mineral-collection", info=ProductInfo(prix="100"))
    item.state("ebay").extra["offers"] = {"EBAY_FR": {"offer": "1", "sku": "MFC1"}}
    with pytest.raises(PlatformError, match="Prix refusé"):
        ebay.set_price(item, item.state("ebay"))
    sales = ebay.sales_since("2026-10-25T09:00:00Z")
    assert seen["/sell/fulfillment/v1/order"][0]["params"]["filter"] == "creationdate:[2026-10-25T09:00:00.000Z..]"
    assert sales[0].sku == "MFC1" and sales[0].quantity == 2 and sales[0].price == 25.0   # prix unitaire
