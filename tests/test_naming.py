from gemz_studio.naming import make_sku, product_folder_name


def test_sku_au_format_de_la_boutique():
    assert make_sku("Malachite fibreuse", "Congo", 20) == "MFC20"


def test_sku_ignore_les_petits_mots():
    assert make_sku("Œil de tigre", "Afrique du Sud", 3) == "OTAS3"


def test_nom_de_dossier_compatible_windows():
    assert product_folder_name("2026-10-25", "malachite fibreuse", "congo", "MFC20") == \
        "2026-10-25_Malachite-fibreuse_Congo_MFC20"
    assert ":" not in product_folder_name("2026-10-25", 'quartz "rare": test', "usa", "QR1")
