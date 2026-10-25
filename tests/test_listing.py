from gemz_studio.config import Settings
from gemz_studio.listing.builder import build_listing, clean_etsy_tags, safe_text, tag_slug
from gemz_studio.listing.model import Listing
from gemz_studio.listing.prices import parse_price, platform_price, round_90, to_usd
from gemz_studio.models import ProductInfo

AI = {
    lang: {"nom": nom, "particularite": part, "pays": pays, "accroche": "",
           "aspect": "Lames de <strong>baryte blanche</strong> <script>x</script>",
           "anecdote": "La vanadinite est un chlorovanadate de plomb.",
           "lithotherapie": "Dans la tradition…", "seo_description": "", "alt": ""}
    for lang, nom, part, pays in (("fr", "Vanadinite sur baryte", "en éventail", "Maroc"),
                                   ("en", "Vanadinite on baryte", "fan-shaped", "Morocco"),
                                   ("es", "Vanadinita sobre barita", "en abanico", "Marruecos"),
                                   ("de", "Vanadinit auf Baryt", "fächerförmig", "Marokko"))
}
AI |= {"etsy_tags": ["vanadinite", "Mineral specimen!", "vanadinite", "un tag beaucoup trop long pour etsy"],
       "couleurs": ["rouge"], "pierres": ["vanadinite", "baryte"]}


def test_prix_arrondis_a_90():
    assert round_90(188.60) == 188.90
    assert round_90(188.95) == 189.90
    assert round_90(188.90) == 188.90
    assert platform_price(164.0, 15) == 188.90  # exemple du cahier des charges
    assert platform_price(164.0, 0) == 164.0
    assert parse_price("150 €") == 150.0 and parse_price("12,5") == 12.5
    assert to_usd(100, 1.15) == 115.90


def test_fiche_au_format_de_la_boutique():
    info = ProductInfo(nom="vanadinite", pays="maroc", region="Mibladen", poids_g="47",
                       dimensions="6 × 3,5 × 2", prix="67", sous_categories=["cristal"])
    s = Settings()
    listing = build_listing(info, s.kind("mineral-collection"), s, AI, 0.05)
    fr = listing.texts["fr"]
    assert fr.title == "Vanadinite sur baryte en éventail – Mibladen, Maroc – 47 g"
    assert fr.seo_title == "Vanadinite sur baryte en éventail, Mibladen (Maroc) – 47 g | GemZ"
    assert "<strong>Poids :</strong> 47 g" in fr.description
    assert "<strong>Minéraux :</strong> vanadinite, baryte" in fr.description
    assert "<h3>Anecdote scientifique</h3>" in fr.description
    assert "<script>" not in fr.description and "<strong>baryte blanche</strong>" in fr.description
    en = listing.texts["en"]
    assert en.title == "Vanadinite on baryte fan-shaped – Mibladen, Morocco – 47 g"
    assert "<strong>Weight:</strong> 47 g" in en.description and "6 × 3.5 × 2 cm" in en.description
    assert listing.tags == ["cristal", "vanadinite", "baryte", "maroc"]
    assert listing.metafields == {"pierre": ["vanadinite", "baryte"], "provenance": ["maroc"], "forme": ["cristal"]}
    assert len(listing.etsy_tags) == 13 and all(len(t) <= 20 for t in listing.etsy_tags)
    assert listing.etsy_tags[:2] == ["vanadinite", "mineral specimen"]
    assert listing.ready
    again = Listing.from_dict(listing.to_dict())
    assert again.texts["de"].title == listing.texts["de"].title


def test_fiche_sans_ia_a_completer():
    info = ProductInfo(nom="malachite", variete="fibreuse", pays="afrique du sud", prix="150", poids_g="120")
    s = Settings()
    listing = build_listing(info, s.kind("mineral-collection"), s)
    assert listing.texts["fr"].title == "Malachite fibreuse – Afrique du Sud – 120 g"
    assert listing.texts["en"].title == "Malachite fibreuse – South Africa – 120 g"
    assert not listing.ready and "aspect" in listing.missing()
    assert "afrique-du-sud" in listing.tags


def test_bague_et_bracelet():
    s = Settings()
    ring = ProductInfo(nom="labradorite", pays="madagascar", prix="45", metal="argent 925", taille_reelle="54")
    fr = build_listing(ring, s.kind("bague"), s).texts["fr"]
    assert fr.title == "Bague labradorite – argent 925 – Taille 54"
    assert "Mise à taille possible de 40 à 70" in fr.description and "12 €" in fr.description
    bracelet = ProductInfo(nom="amazonite", pays="bresil", prix="14", perles_mm="8", fil="élastique")
    en = build_listing(bracelet, s.kind("bracelet"), s).texts["en"]
    assert en.title.startswith("Bracelet amazonite – 8 mm")
    assert "17 cm, 19 cm, 21 cm" in en.description


def test_outils_texte():
    assert tag_slug("Afrique du Sud") == "afrique-du-sud"
    assert safe_text("a <b>b</b> <strong>c</strong>") == "a &lt;b&gt;b&lt;/b&gt; <strong>c</strong>"
    assert clean_etsy_tags(["Pierre ★ naturelle"], []) == ["pierre naturelle"]
