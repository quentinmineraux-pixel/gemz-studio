from gemz_studio.config import ProductKind
from gemz_studio.grouping import Grouper
from gemz_studio.models import A_COMPLETER, EN_PRISE, Session

MINERAL = ProductKind("mineral-collection", "Minéral", "mineral-collection", photos_per_product=8, videos_per_product=1)


def feed(grouper, names):
    for n in names:
        grouper.add(n)


def test_coupe_automatique_apres_8_photos_et_1_video():
    s = Session("mineral-collection")
    g = Grouper(s, MINERAL)
    feed(g, [f"a{i}.JPG" for i in range(8)] + ["a.MP4"] + ["b0.JPG"])
    assert len(s.products) == 2
    first, second = s.products
    assert len(first.photos) == 8 and first.videos == ["a.MP4"]
    assert first.status == A_COMPLETER
    assert second.photos == ["b0.JPG"] and second.status == EN_PRISE


def test_produit_suivant_force_la_coupure():
    s = Session("bague")
    g = Grouper(s, MINERAL)
    feed(g, ["a.jpg", "b.jpg"])
    g.next_product()
    g.add("c.jpg")
    assert [p.photos for p in s.products] == [["a.jpg", "b.jpg"], ["c.jpg"]]


def test_reprendre_remplace_la_photo_au_meme_endroit():
    s = Session("mineral-collection")
    g = Grouper(s, MINERAL)
    feed(g, ["a.jpg", "b.jpg", "c.jpg"])
    p = s.products[0]
    g.request_replace(p.id, "b.jpg")
    g.add("b2.jpg")
    assert p.photos == ["a.jpg", "b2.jpg", "c.jpg"]


def test_photo_principale_et_suppression():
    s = Session("mineral-collection")
    g = Grouper(s, MINERAL)
    feed(g, ["a.jpg", "b.jpg", "c.jpg"])
    p = s.products[0]
    g.set_main(p.id, "c.jpg")
    assert p.photos[0] == "c.jpg"
    g.remove(p.id, "a.jpg")
    assert p.photos == ["c.jpg", "b.jpg"]


def test_fichiers_ignores():
    s = Session("mineral-collection")
    g = Grouper(s, MINERAL)
    assert g.add("notes.txt") is None
    g.add("a.jpg")
    assert g.add("a.jpg") is None
    assert len(s.products[0].photos) == 1
