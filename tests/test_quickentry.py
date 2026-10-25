from gemz_studio.quickentry import parse_line


def test_format_du_fichier_txt():
    assert parse_line("malachite congo 120g 10x5x5cm  150€") == {
        "prix": "150", "dimensions": "10 × 5 × 5", "poids_g": "120", "pays": "congo", "nom": "malachite",
    }


def test_nom_en_plusieurs_mots_et_pays_compose():
    r = parse_line("Quartz fumé Afrique du Sud 1,2kg 80x60x40mm 249,90 €")
    assert r["nom"] == "Quartz fumé"
    assert r["pays"] == "Afrique du Sud"
    assert r["poids_g"] == "1200"
    assert r["dimensions"] == "8 × 6 × 4"
    assert r["prix"] == "249,9"


def test_accents_et_taille_de_bague():
    r = parse_line("bague améthyste brésil t54 45€")
    assert r["pays"] == "brésil"
    assert r["taille_reelle"] == "54"
    assert r["nom"] == "bague améthyste"


def test_ligne_partielle():
    assert parse_line("fluorite") == {"nom": "fluorite"}
