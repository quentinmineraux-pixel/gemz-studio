import urllib.error
import urllib.request
from datetime import date, datetime

import pytest
from PIL import Image

from gemz_studio.catalog import Catalog, CatalogItem
from gemz_studio.models import ProductInfo
from gemz_studio.sales import SaleRow, day_label, in_period, money, sale_rows, totals, when_label
from gemz_studio.salespage import SalesPage, render

NOW = datetime(2026, 10, 25, 18, 0)


@pytest.fixture
def catalog(tmp_path):
    cat = Catalog(tmp_path / "cat.db")
    yield cat
    cat.close()


def _log_sales(catalog):
    catalog.put(CatalogItem(sku="MFC1", kind_id="mineral-collection", info=ProductInfo(nom="Malachite")))
    catalog.log("vente", "Vente Shopify : Malachite – 120.00 EUR · épuisé partout", "MFC1", "shopify",
                {"prix": 120.0, "devise": "EUR", "commande": "#1001", "quantite": 1, "titre": "Malachite",
                 "date": "2026-10-25T10:15:00"})
    catalog.log("vente", "Vente eBay hors catalogue : Quartz rose (25.00 USD)", "QZ9", "ebay",
                {"prix": 25.0, "devise": "USD", "commande": "O1", "quantite": 2, "titre": "Quartz rose",
                 "date": "2026-10-03T08:00:00"})
    # Ligne écrite par une version précédente : ni titre ni date dans les données
    catalog.log("vente", "Vente Etsy : Améthyste – 60.00 EUR · reste 0", "AM2", "etsy", {"prix": 60.0, "devise": "EUR"})


def test_ventes_lues_dans_le_journal(catalog):
    _log_sales(catalog)
    catalog.log("publication", "Publié sur Etsy", "MFC1", "etsy")
    rows = {r.sku: r for r in sale_rows(catalog)}
    assert set(rows) == {"MFC1", "QZ9", "AM2"}
    assert rows["MFC1"].title == "Malachite" and rows["MFC1"].when == datetime(2026, 10, 25, 10, 15)
    assert rows["QZ9"].amount == 50.0 and rows["QZ9"].currency == "USD" and not rows["QZ9"].in_catalog
    assert rows["AM2"].title == "Améthyste" and rows["AM2"].quantity == 1 and rows["AM2"].in_catalog
    ordered = [r.sku for r in sale_rows(catalog)]
    assert ordered.index("MFC1") < ordered.index("QZ9")   # la plus récente d'abord


def _row(when, platform, price, currency="EUR", quantity=1):
    return SaleRow(when, platform, "X1", "Pièce", price, quantity, currency, "", True)


def test_totaux_par_periode_devise_et_plateforme():
    rows = [_row(datetime(2026, 10, 25, 9), "shopify", 120.0),
            _row(datetime(2026, 10, 3, 8), "ebay", 25.0, "USD", 2),
            _row(datetime(2026, 9, 2, 8), "etsy", 60.0),
            _row(datetime(2025, 12, 31, 23), "etsy", 10.0)]
    assert [len(in_period(rows, p, NOW)) for p in ("today", "7d", "month", "year", "all")] == [1, 1, 2, 3, 4]
    month = totals(in_period(rows, "month", NOW))
    assert month.count == 3
    assert month.by_currency == {"EUR": 120.0, "USD": 50.0}
    assert month.by_platform == {"shopify": {"EUR": 120.0}, "ebay": {"USD": 50.0}}
    assert month.text() == "120,00 € + 50,00 $"
    assert totals([]).text() == "0 €"
    assert money(1245.8) == "1 245,80 €"


def test_dates_en_francais():
    today = date(2026, 10, 25)
    assert day_label(today, today) == "Aujourd'hui"
    assert day_label(date(2026, 10, 24), today) == "Hier"
    assert day_label(date(2026, 10, 20), today) == "mardi 20 octobre"
    assert day_label(date(2026, 10, 1), today) == "jeudi 1er octobre"
    assert day_label(date(2025, 12, 31), today) == "mercredi 31 décembre 2025"
    assert when_label(datetime(2026, 10, 25, 14, 32), NOW) == "aujourd'hui 14:32"
    assert when_label(datetime(2026, 10, 24, 9, 10), NOW) == "hier 09:10"
    assert when_label(datetime(2026, 10, 20, 14, 32), NOW) == "20 oct. 14:32"
    assert when_label(datetime(2025, 12, 31, 8, 0), NOW) == "31 déc. 2025 08:00"


def test_page_telephone_echappe_les_titres(catalog):
    _log_sales(catalog)
    catalog.log("vente", "Vente Etsy hors catalogue : x (5.00 EUR)", "", "etsy",
                {"prix": 5.0, "devise": "EUR", "titre": "<script>alert(1)</script>", "date": "2026-10-25T11:00:00"})
    page = render(catalog, "abc", NOW)
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert "/abc/photo/MFC1" in page and "/abc/photo/QZ9" not in page   # pas de photo hors catalogue
    assert "120,00 €" in page and "Ventes GemZ" in page


def _get(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # adresse locale : pas de proxy
    try:
        with opener.open(url, timeout=10) as r:
            return r.status, r.headers["Content-Type"], r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def test_serveur_de_la_page_ventes(catalog, tmp_path):
    _log_sales(catalog)
    photo = tmp_path / "MFC1-1.jpg"
    Image.new("RGB", (800, 600), "green").save(photo)
    catalog.update("MFC1", lambda it: setattr(it, "photos", [str(photo)]))
    page = SalesPage(catalog)
    page.start(0, "code-secret")
    try:
        base = f"http://127.0.0.1:{page.port}"
        status, kind, body = _get(f"{base}/code-secret")
        assert status == 200 and kind.startswith("text/html") and "Malachite".encode() in body
        assert _get(f"{base}/mauvais-code")[0] == 404
        assert _get(f"{base}/")[0] == 404
        status, kind, body = _get(f"{base}/code-secret/photo/MFC1")
        assert status == 200 and kind == "image/jpeg" and body[:2] == b"\xff\xd8"
        assert _get(f"{base}/code-secret/photo/INCONNU")[0] == 404
    finally:
        page.stop()
    assert not page.running
