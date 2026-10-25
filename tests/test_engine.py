import pytest

from gemz_studio.catalog import A_METTRE_A_JOUR, A_PUBLIER, EPUISE, ERREUR, PUBLIE, Catalog, CatalogItem
from gemz_studio.config import Settings
from gemz_studio.engine import Engine, base_sku
from gemz_studio.importer import import_shopify, link_etsy, match_etsy
from gemz_studio.listing.builder import build_listing
from gemz_studio.models import ProductInfo
from gemz_studio.platforms import NotConfigured, PlatformError, RemoteListing, Sale


class FakePlatform:
    def __init__(self, name, ok=True):
        self.name, self.ok = name, ok
        self.published, self.quantities, self.sales, self.prices = [], {}, [], {}
        self.fail = None
        self.snap = {}

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

    def set_price(self, item, state):
        self.prices[item.sku] = item.info.prix
        state.price = float(item.info.prix)

    def snapshot(self):
        return self.snap


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
    if ready:
        for lang in ("fr", "en"):
            item.listing.pieces[lang].update(aspect="Fibres vertes.", lithotherapie="Pierre de protection.")
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


def test_etsy_attend_les_textes_anglais(setup):
    settings, catalog, engine = setup
    settings.etsy_shop_language = "en"
    item = make_item(settings)
    item.listing.pieces["en"] = {}
    catalog.put(item)
    engine.process_queue()
    engine.process_queue()
    assert engine.platforms["shopify"].published == ["MFC1"]
    assert engine.platforms["etsy"].published == []
    assert "anglais" in catalog.get("MFC1").state("etsy").error


def test_import_avance_les_numeros_de_sku(setup):
    settings, catalog, engine = setup
    remote = RemoteListing(platform="shopify", remote_id="gid://shopify/Product/1", title="Malachite", skus=["MFC42"], price=90.0,
                           quantity=1)
    import_shopify(catalog, [remote], settings)
    assert settings.sku_counters["MFC"] == 42
    assert settings.next_sku_number("MFC") == 43


def test_import_retrouve_poids_et_provenance():
    from gemz_studio.importer import facts_from_shopify
    facts = facts_from_shopify("Vanadinite sur baryte en éventail – Mibladen, Maroc – 47 g",
                               "<p><strong>Poids :</strong> 47 g<br><strong>Dimensions :</strong> 6 × 4 × 3 cm</p>")
    assert facts == {"poids_g": "47", "region": "Mibladen", "pays": "Maroc",
                     "nom": "Vanadinite sur baryte en éventail", "dimensions": "6x4x3"}
    facts = facts_from_shopify("Hémimorphite bleu turquoise, cristaux en relief – Congo – 71,8 g", "")
    assert facts["poids_g"] == "71,8" and facts["pays"] == "Congo" and facts["variete"] == "cristaux en relief"


def test_annonce_existante_en_erreur_est_mise_a_jour(setup):
    settings, catalog, engine = setup
    item = make_item(settings)
    item.state("shopify").status, item.state("shopify").remote_id = PUBLIE, "gid://1"
    item.state("etsy").status, item.state("etsy").remote_id = ERREUR, "123"
    catalog.put(item)
    calls = []
    engine.platforms["etsy"].publish = lambda it, st: calls.append("publish")
    engine.platforms["etsy"].update = lambda it, st: calls.append("update")
    engine.process_queue()
    assert calls == ["update"]


def test_reglages_etsy_repris_des_annonces(setup):
    from gemz_studio.importer import adopt_etsy_defaults
    settings, catalog, engine = setup
    item = make_item(settings)
    item.state("etsy").remote_id = "55"
    catalog.put(item)
    remote = RemoteListing(platform="etsy", remote_id="55", title="x", extra={
        "taxonomy_id": 1234, "shipping_profile_id": 9, "return_policy_id": 8, "who_made": "someone_else",
        "when_made": "made_to_order", "language": "fr"})
    filled = adopt_etsy_defaults(catalog, [remote], settings)
    assert settings.kind("mineral-collection").etsy_taxonomy_id == 1234
    assert settings.etsy_shipping_profile_id == "9" and settings.etsy_who_made == "someone_else"
    assert filled


def _online(settings, sku="MFC1", etsy=True):
    item = make_item(settings, sku, etsy=etsy)
    item.state("shopify").status, item.state("shopify").remote_id = PUBLIE, f"gid://shopify/Product/{sku}"
    if etsy:
        item.state("etsy").status, item.state("etsy").remote_id = PUBLIE, f"E-{sku}"
    return item


def _snap(item, quantity=1, price="150.00", compare=None, status="ACTIVE"):
    return {item.state("shopify").remote_id: {"status": status, "variants": [
        {"sku": item.sku, "price": price, "compareAtPrice": compare, "quantity": quantity}]}}


def test_vente_shopify_retire_aussi_les_autres_tailles(setup):
    # Bague vendue sur Shopify : Shopify n'a retiré que la taille vendue, GemZ remet toutes les tailles à 0
    settings, catalog, engine = setup
    catalog.put(_online(settings))
    engine.handle_sale(Sale("shopify", "O1", "L1", "MFC1-T56", "gid://shopify/Product/MFC1", 1, 150, "EUR", "x"))
    assert engine.platforms["shopify"].quantities == {"MFC1": 0}
    assert engine.platforms["etsy"].quantities == {"MFC1": 0}
    ev = catalog.events(("vente",))[0]
    assert ev.data["titre"] == catalog.get("MFC1").title and ev.data["quantite"] == 1


def test_vente_retrouvee_par_annonce_avant_le_sku(setup):
    settings, catalog, engine = setup
    catalog.put(_online(settings, "10", etsy=False))       # SKU bizarre importé tel quel autrefois
    catalog.put(_online(settings, "MFC5"))
    engine.handle_sale(Sale("etsy", "R1", "T1", "10", "E-MFC5", 1, 170, "EUR", "x"))
    assert catalog.get("MFC5").stock == 0 and catalog.get("10").stock == 1


def test_prix_modifie_dans_shopify_repris_apres_double_controle(setup):
    settings, catalog, engine = setup
    item = _online(settings)
    catalog.put(item)
    shopify = engine.platforms["shopify"]
    shopify.snap = _snap(item, price="99.00", compare="120.00")
    engine.check_shopify_stock()
    assert catalog.get("MFC1").info.prix == "150"            # premier contrôle : on attend
    engine.check_shopify_stock()
    item = catalog.get("MFC1")
    assert item.info.prix == "99" and item.info.prix_barre == "120"
    assert engine.platforms["etsy"].prices == {"MFC1": "99"}  # recopié sur Etsy (avec sa majoration)
    assert not item.state("etsy").extra.get("prix_a_envoyer")
    messages = [e.message for e in catalog.events(("synchro",))]
    assert "Modifié sur Shopify : prix 99 €, prix barré 120 €" in messages and "Prix Etsy → 99 €" in messages
    engine.check_shopify_stock()
    assert engine.platforms["etsy"].prices == {"MFC1": "99"}  # plus d'écart : rien de plus


def test_controle_shopify_attend_les_envois_et_confirme(setup):
    settings, catalog, engine = setup
    item = _online(settings)
    item.stock = 0
    item.state("shopify").extra["stock_a_envoyer"] = True   # vente pas encore recopiée sur Shopify
    catalog.put(item)
    engine.platforms["shopify"].snap = _snap(item, quantity=1, price="150.00")
    engine.check_shopify_stock()
    engine.check_shopify_stock()
    assert catalog.get("MFC1").stock == 0                    # jamais remise en vente par erreur
    # Écart passager (vente pas encore vue dans les commandes) : disparu au contrôle suivant, rien n'est repris
    catalog.update("MFC1", lambda it: (it.state("shopify").extra.pop("stock_a_envoyer"), setattr(it, "stock", 1)))
    engine.platforms["shopify"].snap = _snap(item, quantity=0, price="150.00")
    engine.check_shopify_stock()
    engine.platforms["shopify"].snap = _snap(item, quantity=1, price="150.00")
    engine.check_shopify_stock()
    assert catalog.get("MFC1").stock == 1 and not engine.platforms["etsy"].quantities


def test_produit_archive_dans_shopify_retire_partout(setup):
    settings, catalog, engine = setup
    item = _online(settings)
    catalog.put(item)
    engine.platforms["shopify"].snap = _snap(item, quantity=1, status="ARCHIVED")
    engine.check_shopify_stock()
    engine.check_shopify_stock()
    item = catalog.get("MFC1")
    assert item.stock == 0 and item.state("etsy").status == EPUISE
    assert engine.platforms["etsy"].quantities == {"MFC1": 0}


def test_prix_change_dans_la_fiche_envoye_sauf_annonce_epuisee(setup):
    settings, catalog, engine = setup
    item = _online(settings)
    item.info.prix = "160"
    for name in ("shopify", "etsy"):
        item.state(name).extra["prix_a_envoyer"] = True
    catalog.put(item)
    engine.push_pending_stock()
    assert engine.platforms["shopify"].prices == {"MFC1": "160"} and engine.platforms["etsy"].prices == {"MFC1": "160"}
    assert catalog.get("MFC1").state("etsy").price == 160.0
    catalog.update("MFC1", lambda it: (setattr(it, "stock", 0), it.state("etsy").extra.update(prix_a_envoyer=True)))
    engine.platforms["etsy"].prices.clear()
    engine.push_pending_stock()
    assert engine.platforms["etsy"].prices == {}             # annonce désactivée : le prix suivra à la remise en vente
    assert catalog.get("MFC1").state("etsy").extra.get("prix_a_envoyer")


def test_erreur_repetee_notee_une_seule_fois(setup):
    settings, catalog, engine = setup
    for attempt in range(3):
        engine._error(("synchro", "etsy"), f"Synchro Etsy : connexion impossible (objet 0x7f{attempt}a, essai {attempt})")
    assert len(catalog.events(("erreur",))) == 1
    engine._ok(("synchro", "etsy"))
    engine._error(("synchro", "etsy"), "Synchro Etsy : connexion impossible (essai 9)")
    assert len(catalog.events(("erreur",))) == 2


def test_avertissement_de_publication_note_en_alerte(setup):
    settings, catalog, engine = setup
    catalog.put(make_item(settings, etsy=False))

    def publish(item, state):
        state.remote_id = "gid://1"
        state.extra["avertissement"] = "traduction refusée : allemand"
    engine.platforms["shopify"].publish = publish
    engine.process_queue()
    alert = catalog.events(("alerte",))[0]
    assert "allemand" in alert.message and "avertissement" not in catalog.get("MFC1").state("shopify").extra


def test_fiche_retouchee_pendant_l_envoi_renvoyee(setup):
    settings, catalog, engine = setup
    catalog.put(make_item(settings, etsy=False))

    def publish(item, state):
        # Titre et prix corrigés dans la fiche pendant que les photos partent
        def edit(it):
            it.listing.texts["fr"].title = "Malachite corrigée"
            it.info.prix = "160"
            it.state("shopify").extra["prix_a_envoyer"] = True
        catalog.update(item.sku, edit)
        state.remote_id = "gid://1"
    engine.platforms["shopify"].publish = publish
    engine.process_queue()
    state = catalog.get("MFC1").state("shopify")
    assert state.status == A_METTRE_A_JOUR and state.extra.get("prix_a_envoyer") and state.remote_id == "gid://1"
    engine.process_queue()                                    # la version corrigée part en mise à jour
    assert catalog.get("MFC1").state("shopify").status == PUBLIE
    assert engine.platforms["shopify"].published == ["MFC1"]


def test_import_shopify_garde_textes_seo_promo_et_ecarte_les_sku_bizarres(setup):
    from gemz_studio.listing.model import SOURCE_IMPORT
    settings, catalog, engine = setup
    remote = RemoteListing("shopify", "gid://shopify/Product/7", "Bracelet améthyste", ["10"], 3, 19.0,
                           description="<p>Perles.</p>", image_urls=["https://cdn.shopify.com/a.jpg"],
                           extra={"seo": {"title": "Bracelet améthyste", "description": "Perles d'améthyste."},
                                  "compare_at": "25.00", "metafields": {"provenance": ["afrique du sud"]},
                                  "status": "ACTIVE"})
    bracelet = RemoteListing("shopify", "gid://shopify/Product/8", "Bracelet œil de tigre", ["BR3-17", "BR3-19"], 4, 21.0)
    assert import_shopify(catalog, [remote, bracelet], settings) == 2
    item = catalog.get("SHOP7")
    assert item is not None and catalog.get("BR3") is not None
    assert item.listing.source == SOURCE_IMPORT and item.listing.texts["fr"].seo_description == "Perles d'améthyste."
    assert item.info.prix == "19" and item.info.prix_barre == "25"
    # Texte modifié dans l'admin Shopify : repris au prochain import (rien n'attend d'envoi)
    remote.description = "<p>Perles de 8 mm.</p>"
    remote.image_urls = ["https://cdn.shopify.com/b.jpg"]
    import_shopify(catalog, [remote], settings)
    item = catalog.get("SHOP7")
    assert item.listing.texts["fr"].description == "<p>Perles de 8 mm.</p>"
    assert item.thumbnail_source == "https://cdn.shopify.com/b.jpg"
    from gemz_studio.listing import vocab
    assert vocab.match("provenance", ["afrique-du-sud"]) == ["afrique du sud"]


def test_piece_importee_rephotographiee_garde_texte_promo_et_remplace_les_photos(setup, tmp_path):
    from gemz_studio.listing.model import SOURCE_IMPORT, Listing
    from gemz_studio.models import Product
    from gemz_studio.publishing import register_product
    settings, catalog, engine = setup
    settings.ai_auto = False
    listing = Listing(source=SOURCE_IMPORT, ready=True)
    listing.texts["fr"].title, listing.texts["fr"].description = "Fluorite", "<p>Texte de la boutique</p>"
    item = CatalogItem(sku="FLE7", kind_id="mineral-collection", listing=listing, origin="import",
                       info=ProductInfo(nom="Fluorite", prix="169", prix_barre="199"))
    state = item.state("shopify")
    state.wanted, state.remote_id, state.status = True, "gid://shopify/Product/1", PUBLIE
    catalog.put(item)
    web = tmp_path / "FLE7" / "web"
    web.mkdir(parents=True)
    (web / "FLE7-1.jpg").write_bytes(b"jpeg")
    product = Product(kind_id="mineral-collection", sku="FLE7", folder=str(web.parent),
                      info=ProductInfo(nom="Fluorite", prix="169"))   # le formulaire n'a pas de prix barré
    register_product(product, settings, catalog)
    item = catalog.get("FLE7")
    assert item.info.prix_barre == "199" and item.listing.texts["fr"].description == "<p>Texte de la boutique</p>"
    state = item.state("shopify")
    assert state.status == A_METTRE_A_JOUR and state.extra["media_sig"] != item.media_signature()
    assert not state.extra.get("prix_a_envoyer")


def test_piece_deja_vendue_sur_etsy_retiree_de_shopify_a_la_liaison(setup):
    settings, catalog, engine = setup
    shopify = [RemoteListing("shopify", "gid://shopify/Product/3", "Fluorite verte – Chine – 97 g", ["FLE3"], 1, 39.0,
                             product_type="mineral-collection"),
               RemoteListing("shopify", "gid://shopify/Product/4", "Bracelet amazonite", ["BR9"], 3, 19.0,
                             product_type="bracelet")]
    import_shopify(catalog, shopify, settings)
    etsy = [RemoteListing("etsy", "901", "Fluorite verte, Chine", ["FLE3"], 0, 45.0, extra={"state": "sold_out"}),
            RemoteListing("etsy", "902", "Bracelet amazonite", ["BR9"], 0, 22.0, extra={"state": "sold_out"}),
            RemoteListing("etsy", "903", "Vieille annonce vendue", ["ZZ1"], 0, 10.0, extra={"state": "sold_out"}),
            RemoteListing("etsy", "904", "Fluorite verte – Chine", [], 1, 45.0, extra={"state": "sold_out"})]
    matches = {m.etsy.remote_id: m for m in match_etsy(catalog, etsy)}
    assert set(matches) == {"901", "902"}          # anciennes annonces sans pièce sûre : pas proposées
    assert matches["901"].sold and matches["901"].sure
    for match in matches.values():
        link_etsy(catalog, match)
    fle3, br9 = catalog.get("FLE3"), catalog.get("BR9")
    assert fle3.stock == 0 and fle3.state("shopify").status == EPUISE and fle3.state("shopify").extra["stock_a_envoyer"]
    assert br9.stock == 3 and br9.state("etsy").extra["stock_a_envoyer"]   # remise en vente sur Etsy avec le stock
    engine.push_pending_stock()
    assert engine.platforms["shopify"].quantities == {"FLE3": 0}
    assert engine.platforms["etsy"].quantities == {"BR9": 3}
