import json
import subprocess
from pathlib import Path

import imageio_ffmpeg
from PIL import Image

from gemz_studio.config import Settings
from gemz_studio.imaging import render_web
from gemz_studio.models import PRET, Product, ProductInfo
from gemz_studio.processing import process_product


def make_photo(path: Path, size=(4000, 3000), color=(240, 240, 240)):
    Image.new("RGB", size, color).save(path, "JPEG")
    return str(path)


def make_video(path: Path):
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=white:s=640x480:d=1", "-pix_fmt", "yuv420p", str(path)], check=True)
    return str(path)


def test_watermark_en_bas_a_droite_et_redimensionnement(tmp_path):
    settings = Settings(watermark_color="noir", watermark_opacity=1.0)
    img = render_web(make_photo(tmp_path / "a.jpg"), settings)
    assert max(img.size) == settings.web_max_side
    w, h = img.size
    corner = img.crop((int(w * 0.7), int(h * 0.8), w, h)).convert("L")
    opposite = img.crop((0, 0, int(w * 0.3), int(h * 0.2))).convert("L")
    assert corner.getextrema()[0] < 50       # le logo noir est dans le coin bas droit
    assert opposite.getextrema()[0] > 200    # rien en haut à gauche


def test_preset_luminosite(tmp_path):
    src = make_photo(tmp_path / "a.jpg", size=(800, 600), color=(100, 100, 100))
    settings = Settings(brightness=1.5, watermark_opacity=0.0)
    assert render_web(src, settings).getpixel((10, 10))[0] > 140


def test_traitement_complet(tmp_path):
    settings = Settings(output_folder=str(tmp_path / "sortie"), sku_counters={"MFC": 19})
    photos = [make_photo(tmp_path / f"IMG_{i}.JPG", size=(1200, 900)) for i in range(3)]
    product = Product("mineral-collection", photos=photos, videos=[make_video(tmp_path / "MVI_1.MP4")],
                      info=ProductInfo(nom="malachite fibreuse", pays="congo", prix="164"))
    folder = process_product(product, "2026-10-25", settings)

    assert folder.name == "2026-10-25_Malachite-fibreuse_Congo_MFC20"
    assert product.sku == "MFC20" and product.status == PRET
    assert sorted(p.name for p in (folder / "web").iterdir()) == [
        "MFC20_01.jpg", "MFC20_02.jpg", "MFC20_03.jpg", "MFC20_video.mp4"]
    assert len(list((folder / "originaux").iterdir())) == 4
    record = json.loads((folder / "produit.json").read_text(encoding="utf-8"))
    assert record["shopify_type"] == "mineral-collection"
    assert Settings.load().sku_counters == {"MFC": 20}


def test_champs_obligatoires(tmp_path):
    settings = Settings(output_folder=str(tmp_path))
    product = Product("mineral-collection", photos=[make_photo(tmp_path / "a.jpg", size=(100, 100))])
    try:
        process_product(product, "2026-10-25", settings)
    except RuntimeError as e:
        assert "nom" in str(e) and "prix" in str(e)
    else:
        raise AssertionError("aurait dû échouer")


def test_sku_impose_et_numerotation_par_prefixe(tmp_path):
    settings = Settings(output_folder=str(tmp_path / "sortie"))
    photo = make_photo(tmp_path / "a.jpg", size=(200, 200))
    a = Product("pierre-polie", photos=[photo], sku="fllab21",
                info=ProductInfo(nom="labradorite", pays="madagascar", prix="30"))
    b = Product("pierre-polie", photos=[photo], info=ProductInfo(nom="labradorite", pays="madagascar", prix="30"))
    c = Product("pierre-polie", photos=[photo], info=ProductInfo(nom="labradorite", pays="madagascar", prix="30"))
    for p in (a, b, c):
        process_product(p, "2026-10-25", settings)
    assert [a.sku, b.sku, c.sku] == ["FLLAB21", "LM1", "LM2"]


def test_vignette_video(tmp_path):
    import subprocess

    import imageio_ffmpeg

    from gemz_studio.video import video_frame

    clip = tmp_path / "MVI_0001.MP4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=s=320x240:d=0.5", "-pix_fmt", "yuv420p", str(clip)], check=True)
    img = video_frame(clip)  # film plus court que 1 s : retombe sur la première image
    assert img.size == (320, 240)
