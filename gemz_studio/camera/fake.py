"""Appareil simulé : permet d'essayer l'écran de prise de vue sans boîtier branché."""
from __future__ import annotations

import io
import math
import subprocess
import time

import imageio_ffmpeg
from PIL import Image, ImageDraw

from .base import CameraBackend


class FakeCamera(CameraBackend):
    name = "Appareil simulé"

    def __init__(self, download_dir):
        super().__init__(download_dir)
        self.liveview = False
        self.counter = 1
        self.zoom = 1
        self.focus = 0
        self._record_start = 0.0

    def connect(self) -> bool:
        return True

    def start_liveview(self) -> None:
        self.liveview = True

    def stop_liveview(self) -> None:
        self.liveview = False

    def _scene(self, size=(960, 640)) -> Image.Image:
        img = Image.new("RGB", size, (236, 236, 236))
        d = ImageDraw.Draw(img)
        angle = time.monotonic() * 0.6
        cx, cy, r = size[0] / 2, size[1] / 2, size[1] / 4 * self.zoom ** 0.5
        pts = [(cx + r * math.cos(angle + k * math.pi / 3), cy + r * 0.8 * math.sin(angle + k * math.pi / 3))
               for k in range(6)]
        d.polygon(pts, fill=(40, 120, 80), outline=(20, 60, 40))
        d.text((10, 10), f"SIMULATION  zoom x{self.zoom}  mise au point {self.focus:+d}", fill=(80, 80, 80))
        if self.recording:
            d.ellipse((size[0] - 40, 12, size[0] - 16, 36), fill=(220, 30, 30))
        return img

    def liveview_frame(self) -> bytes | None:
        if not self.liveview:
            return None
        buf = io.BytesIO()
        self._scene().save(buf, "JPEG", quality=80)
        return buf.getvalue()

    def autofocus(self) -> None:
        self.focus = 0

    def focus_step(self, step: int) -> None:
        self.focus += step

    def set_zoom(self, zoom: int) -> None:
        self.zoom = zoom

    def capture(self) -> None:
        path = self.target_path(f"IMG_{self.counter:04d}.JPG")
        self.counter += 1
        partial = path.with_name(path.name + ".part")
        self._scene((3000, 2000)).save(partial, "JPEG", quality=90)
        partial.replace(path)
        self.on_status(f"{path.name} reçu")
        self.on_file(str(path))

    def start_movie(self) -> None:
        self.recording = True
        self._record_start = time.monotonic()

    def stop_movie(self) -> None:
        self.recording = False
        seconds = max(1.0, min(10.0, time.monotonic() - self._record_start))
        path = self.target_path(f"MVI_{self.counter:04d}.MP4")
        self.counter += 1
        partial = path.with_name(path.name + ".part")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", f"testsrc=s=1280x720:d={seconds:.1f}", "-pix_fmt", "yuv420p", "-f", "mp4",
                        str(partial)],
                       check=True)
        partial.replace(path)
        self.on_status(f"{path.name} reçu")
        self.on_file(str(path))
