import pytest

from gemz_studio.catalog import A_PUBLIER, EPUISE, ERREUR, PUBLIE, Catalog, CatalogItem
from gemz_studio.config import Settings
from gemz_studio.engine import Engine, base_sku
from gemz_studio.importer import import_shopify, link_etsy, match_etsy
from gemz_studio.listing.builder import build_listing
from gemz_studio.models import ProductInfo
from gemz_studio.platforms import NotConfigured, PlatformError, RemoteListing, Sale


class FakePlatform:
    def __init__(self, name, ok=True):
        self.name, self.ok = name, ok
        self.published, self.quantities, self.sales = [], {}, []
        self.fail = None

    def configured(self):
        return self.ok

    def missing_setup(self):
        return "" if self.ok else "à brancher"

    def publish(self, item, state):
        if self.fail:
            raise self.fail
        self.published.append(item.sku)
        state.remote_id = f"{self.name}-{item.sku}"

    update = publish

    def set_quantity(self, item, state, quantity):
        self.quantities[item.sku] = quantity

    def sales_since(self, since):
        return list(self.sales)

    def remote_quantity(self, item, state):
        return None


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMZ_STUDIO_HOME", str(tmp_path))
    settings = Settings()
    catalog = Catalog(tmp_path / "cat.db")
    engine = Engine(settings, catalog)
    engine.platforms = {n: FakePlatform(n) for n in ("shopify", "etsy", "ebay")}
    yield settings, catalog, engine
    catalog.close()


def make_item(settings, sku="MFC1", etsy=True, ebay=False, ready=True):
    info = ProductInfo(nom="malachite", pays="congo", prix="150", poids_g="120")
    item = CatalogItem(sku=sku, kind_id="mineral-collection", info=info,
                       listing=build_listing(info, settings.kind("mineral-collection"), settings))
    item.listing.ready = ready
    item.state("shopify").wanted = True
    item.state("etsy").wanted = etsy
    item.state("ebay").wanted = ebay
    return item


def test_base_sku():
    assert base_sku("mfc20-t56") == "MFC20"
    assert base_sku("BR3-17") == "BR3"
    assert base_sku("MFC20-US") == "MFC20"
    assert base_sku("MFC20") == "MFC20"


def test_publication_shopify_puis_etsy(setup):
    settings, catalog, engine = setup
    catalog.put(make_item(settings))
    catalog.put(make_item(settings, "PAS1", ready=False))  # fiche incomplète : attend
    engine.process_queue()
    assert engine.platforms["shopify"].published == ["MFC1"]
    assert engine.platforms["etsy"].published == []          # Shopify d'abord
    engine.process_queue()
    assert engine.platforms["etsy"].published == ["MFC1"]
    item = catalog.get("MFC1")
    assert item.state("shopify").status == PUBLIE and item.state("etsy").status == PUBLIE
    assert catalog.get("PAS1").state("shopify").status == A_PUBLIER
    assert [e.kind for e in catalog.events()] == ["publication", "publication"]


def test_plateforme_non_branchee_et_erreur(setup):
    settings, catalog, engine = setup
    engine.platforms["shopify"].ok = False
    catalog.put(make_item(settings))
    engine.process_queue()
    state = catalog.get("MFC1").state("shopify")
    assert state.status == A_PUBLIER and state.error == "à brancher" and state.next_try > 0
    engine.platforms["shopify"].ok = True
    engine.platforms["shopify"].fail = PlatformError("panne", retryable=True)
    catalog.update("MFC1", lambda it: setattr(it.state("shopify"), "next_try", 0))
    engine.process_queue()
    state = catalog.get("MFC1").state("shopify")
    assert state.status == ERREUR and state.attempts == 1 and "panne" in state.error
    engine.platforms["shopify"].fail = NotConfigured("catégorie à choisir")
    catalog.update("MFC1", lambda it: setattr(it.state("shopify"), "next_try", 0))
    engine.process_queue()
    assert catalog.get("MFC1").state("shopify").status == A_PUBLIER


def test_vente_etsy_retire_partout(setup):
    settings, catalog, engine = setup
    catalog.put(make_item(settings, ebay=True))
    for _ in range(2):
        engine.process_queue()
    sale = Sale("etsy", "R1", "T1", "MFC1", "etsy-MFC1", 1, 172.9, "EUR", "2026-10-25T10:00:00Z", "Malachite")
    engine.platforms["etsy"].sales = [sale]
    engine.sync()
    item = catalog.get("MFC1")
    assert item.stock == 0
    assert all(item.state(n).status == EPUISE for n in ("shopify", "etsy", "ebay"))
    assert engine.platforms["shopify"].quantities == {"MFC1": 0}
    assert engine.platforms["ebay"].quantities == {"MFC1": 0}
    assert "MFC1" not in engine.platforms["etsy"].quantities   # Etsy a déjà retiré la pièce
    engine.sync()                                               # même vente revue : comptée une seule fois
    assert catalog.get("MFC1").stock == 0
    assert len([e for e in catalog.events() if e.kind == "vente"]) == 1


def test_vente_par_variante_et_stock_en_retard(setup):
    settings, catalog, engine = setup
    item = make_item(settings, etsy=False)
    item.stock = 2
    catalog.put(item)
    engine.process_queue()
    engine.platforms["etsy"].ok = True
    catalog.update("MFC1", lambda it: setattr(it.state("etsy"), "remote_id", "E9"))
    engine.platforms["etsy"].set_quantity = lambda *a: (_ for _ in ()).throw(PlatformError("hors ligne", True))
    engine.platforms["shopify"].sales = [Sale("shopify", "O1", "L1", "MFC1-T56", "", 1, 150, "EUR", "x")]
    engine.sync()
    item = catalog.get("MFC1")
    assert item.stock == 1 and item.state("etsy").extra.get("stock_a_envoyer")
    engine.platforms["etsy"].set_quantity = FakePlatform.set_quantity.__get__(engine.platforms["etsy"])
    engine.push_pending_stock()
    assert engine.platforms["etsy"].quantities == {"MFC1": 1}
    assert not catalog.get("MFC1").state("etsy").extra.get("stock_a_envoyer")


def test_import_shopify_et_liaison_etsy(setup):
    settings, catalog, engine = setup
    shopify = [RemoteListing("shopify", "gid://shopify/Product/1", "Vanadinite sur baryte – Maroc – 47 g", ["VABM11"], 1, 67.0,
                             product_type="mineral-collection", tags=["cristal"],
                             image_urls=["https://cdn.shopify.com/files/d299b8f1-il_fullxfull.6605228772_cayo.jpg"]),
               RemoteListing("shopify", "gid://shopify/Product/2", "Baryte bleue sur goethite – Maroc", ["BBG6"], 1, 174.0,
                             product_type="mineral-collection")]
    assert import_shopify(catalog, shopify, settings) == 2
    assert import_shopify(catalog, shopify, settings) == 0
    etsy = [RemoteListing("etsy", "111", "Vanadinite on baryte, Morocco", [], 1, 77.0,
                          image_urls=["https://i.etsystatic.com/1/r/il/x/6605228772/il_fullxfull.6605228772_cayo.jpg"]),
            RemoteListing("etsy", "222", "Baryte bleue sur goethite - Maroc", [], 1, 199.0),
            RemoteListing("etsy", "333", "Bracelet améthyste", [], 3, 20.0)]
    matches = {m.etsy.remote_id: m for m in match_etsy(catalog, etsy)}
    assert matches["111"].method == "photo" and matches["111"].item.sku == "VABM11"
    assert matches["222"].method == "titre" and matches["222"].item.sku == "BBG6" and matches["222"].sure
    assert matches["333"].item is None
    link_etsy(catalog, matches["111"])
    assert catalog.get("VABM11").state("etsy").remote_id == "111"
    assert catalog.find_remote("etsy", "111").sku == "VABM11"
