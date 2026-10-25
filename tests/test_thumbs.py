from PIL import Image

from gemz_studio.thumbs import cache_path, make_thumb, sized_url


def test_miniature_locale_gardee_en_cache(tmp_path):
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1200, 800), "red").save(photo)
    path = make_thumb(str(photo))
    assert path and max(Image.open(path).size) == 320
    assert make_thumb(str(photo)) == path                 # déjà faite : rien n'est refait
    Image.new("RGB", (1200, 900), "blue").save(photo)     # photo refaite : nouvelle miniature
    assert cache_path(str(photo)) != path
    assert make_thumb(str(tmp_path / "absente.jpg")) is None and make_thumb("") is None


def test_taille_demandee_au_cdn_shopify():
    assert sized_url("https://cdn.shopify.com/s/files/a.jpg?v=1", 320) == "https://cdn.shopify.com/s/files/a.jpg?v=1&width=320"
    assert sized_url("https://cdn.shopify.com/s/files/a.jpg", 320).endswith("a.jpg?width=320")
    assert sized_url("https://i.etsystatic.com/a.jpg", 320) == "https://i.etsystatic.com/a.jpg"
