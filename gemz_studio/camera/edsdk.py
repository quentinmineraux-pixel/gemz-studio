"""Pilotage d'un boîtier Canon EOS avec le kit officiel EDSDK (chargé depuis le PC de l'utilisateur).

Le kit n'est pas fourni avec GemZ Studio (licence Canon) : son dossier est choisi dans les réglages.
Toutes les fonctions sont appelées depuis le thread du CameraWorker, qui pompe aussi les messages
Windows et appelle EdsGetEvent pour recevoir les événements de l'appareil.
"""
from __future__ import annotations

import ctypes
import os
import re
import sys
import time
from ctypes import POINTER, Structure, byref, c_char, c_int32, c_uint32, c_uint64, c_void_p
from pathlib import Path

from .base import CameraBackend, CameraDisconnected, CameraError
from .props import KELVIN_CHOICES, PROP_IDS, PROP_KEYS, SETTINGS

# --- constantes EDSDK (EDSDKTypes.h) ---------------------------------------
EDS_ERR_OK = 0x00000000
EDS_ERR_DEVICE_BUSY = 0x00000081
EDS_ERR_COMM_DISCONNECTED = 0x000000C1
EDS_ERR_DEVICE_NOT_FOUND = 0x00000080
EDS_ERR_SESSION_NOT_OPEN = 0x00002003
EDS_ERR_TAKE_PICTURE_AF_NG = 0x00008D01
EDS_ERR_OBJECT_NOTREADY = 0x0000A102

kEdsPropID_ProductName = 0x00000002
kEdsPropID_BatteryLevel = 0x00000008
kEdsPropID_SaveTo = 0x0000000B
kEdsSaveTo_Camera = 1
kEdsSaveTo_Host = 2

kEdsPropID_Evf_OutputDevice = 0x00000500
kEdsPropID_Evf_Mode = 0x00000501
kEdsPropID_Evf_Zoom = 0x00000507
kEdsPropID_Evf_ZoomPosition = 0x00000508
kEdsPropID_Record = 0x00000510
kEdsPropID_Evf_CoordinateSystem = 0x00000540
kEdsPropID_Evf_ZoomRect = 0x00000541
kEdsEvfOutputDevice_PC = 2
kEdsRecord_Start = 4
kEdsRecord_Stop = 0

kEdsCameraCommand_ExtendShutDownTimer = 0x00000001
kEdsCameraCommand_PressShutterButton = 0x00000004
kEdsCameraCommand_DoEvfAf = 0x00000102
kEdsCameraCommand_DriveLensEvf = 0x00000103
kEdsShutterButton_OFF = 0x00000000
kEdsShutterButton_Completely_NonAF = 0x00010003

kEdsObjectEvent_All = 0x00000200
kEdsObjectEvent_DirItemCreated = 0x00000204
kEdsObjectEvent_DirItemRequestTransfer = 0x00000208
kEdsStateEvent_All = 0x00000300
kEdsPropertyEvent_All = 0x00000100
kEdsPropertyEvent_PropertyDescChanged = 0x00000102
kEdsStateEvent_Shutdown = 0x00000301

kEdsFileCreateDisposition_CreateAlways = 1
kEdsAccess_ReadWrite = 2

# DriveLensEvf : Near1..3 = 1..3, Far1..3 = 0x8001..0x8003
FOCUS_STEPS = {-3: 0x3, -2: 0x2, -1: 0x1, 1: 0x8001, 2: 0x8002, 3: 0x8003}

MOVIE_EXT = {".mov", ".mp4"}


class EdsCapacity(Structure):
    _fields_ = [("numberOfFreeClusters", c_int32), ("bytesPerSector", c_int32), ("reset", c_int32)]


class EdsPropertyDesc(Structure):
    _fields_ = [("form", c_int32), ("access", c_int32), ("numElements", c_int32), ("propDesc", c_int32 * 128)]


class EdsPoint(Structure):
    _fields_ = [("x", c_int32), ("y", c_int32)]


class EdsSize(Structure):
    _fields_ = [("width", c_int32), ("height", c_int32)]


class EdsRect(Structure):
    _fields_ = [("x", c_int32), ("y", c_int32), ("width", c_int32), ("height", c_int32)]


class _DirItemInfo64(Structure):  # EDSDK 13.x : taille sur 64 bits
    _fields_ = [("size", c_uint64), ("isFolder", c_int32), ("groupID", c_uint32), ("option", c_uint32),
                ("szFileName", c_char * 256), ("format", c_uint32), ("dateTime", c_uint32)]


class _DirItemInfo32(Structure):  # anciennes versions : taille sur 32 bits
    _fields_ = [("size", c_uint32), ("isFolder", c_int32), ("groupID", c_uint32), ("option", c_uint32),
                ("szFileName", c_char * 256), ("format", c_uint32), ("dateTime", c_uint32)]


_DCF_FILE = re.compile(r"^[A-Z0-9_]{8}\.[A-Z0-9]{3,4}$")   # IMG_1234.JPG, MVI_1234.MP4
_DCF_FOLDER = re.compile(r"^[A-Z0-9_]{1,8}$")                # DCIM, 100CANON


def _valid_name(raw: bytes) -> str | None:
    """Nom de fichier ou de dossier au format de la carte (DCF), sinon None."""
    name = raw.split(b"\0", 1)[0].decode("ascii", "ignore")
    if _DCF_FILE.match(name) or _DCF_FOLDER.match(name):
        return name
    return None


if sys.platform == "win32":
    _CALLBACK = ctypes.WINFUNCTYPE
else:  # permet d'importer le module (tests) hors de Windows
    _CALLBACK = ctypes.CFUNCTYPE
ObjectHandler = _CALLBACK(c_uint32, c_uint32, c_void_p, c_void_p)
StateHandler = _CALLBACK(c_uint32, c_uint32, c_uint32, c_void_p)
PropertyHandler = _CALLBACK(c_uint32, c_uint32, c_uint32, c_uint32, c_void_p)


def find_edsdk_dll(folder: str | Path) -> Path | None:
    """Cherche EDSDK.dll dans le dossier du kit, en préférant la version 64 bits."""
    root = Path(folder)
    if root.is_file() and root.name.lower() == "edsdk.dll":
        return root
    if not root.is_dir():
        return None
    candidates = [p for p in root.rglob("*") if p.name.lower() == "edsdk.dll"]
    if not candidates:
        return None
    want_64 = sys.maxsize > 2**32
    # Seule la partie interne au kit compte (le chemin du dossier choisi peut contenir « 64 »)
    candidates.sort(key=lambda p: (("64" in str(p.relative_to(root))) != want_64, len(p.parts)))
    return candidates[0]


def dll_bits(path: str | Path) -> int | None:
    """32 ou 64 selon l'en-tête PE de la DLL (None si illisible)."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        pe = int.from_bytes(head[0x3C:0x40], "little")
        if head[:2] != b"MZ" or head[pe:pe + 4] != b"PE\0\0":
            return None
        machine = int.from_bytes(head[pe + 4:pe + 6], "little")
    except (OSError, ValueError):
        return None
    return {0x14C: 32, 0x8664: 64, 0xAA64: 64}.get(machine)


class EdsdkCamera(CameraBackend):
    def __init__(self, download_dir, sdk_folder: str | Path):
        super().__init__(download_dir)
        dll = find_edsdk_dll(sdk_folder)
        if dll is None:
            raise CameraError("EDSDK.dll introuvable : vérifie le dossier du kit Canon dans les réglages.")
        want = 64 if sys.maxsize > 2**32 else 32
        bits = dll_bits(dll)
        if bits is not None and bits != want:
            raise CameraError(
                f"Ce kit Canon est en {bits} bits ({dll}) alors que GemZ Studio est en {want} bits. "
                "Télécharge le kit officiel sur le site développeur Canon et choisis le dossier "
                "qui contient EDSDK_64.")
        self.dll_path = dll
        self.sdk = None
        self.camera = c_void_p()
        self.connected = False
        self.liveview = False
        self._handlers = []  # garder les callbacks en vie
        self._dll_dir_handle = None
        self._last_keepalive = 0.0
        self.downloads = 0
        self.zoom = 1
        self._settings_changed = False
        self._evf_size: tuple[int, int] | None = None       # repère du capteur pour la visée
        self._evf_rect: tuple[int, int, int, int] | None = None  # zone affichée (ou cadre AF en x1)
        self._af_frame: tuple[int, int] | None = None

    # --- outils ------------------------------------------------------------
    def _check(self, err: int, what: str) -> None:
        if err == EDS_ERR_OK:
            return
        if err in (EDS_ERR_COMM_DISCONNECTED, EDS_ERR_DEVICE_NOT_FOUND, EDS_ERR_SESSION_NOT_OPEN):
            raise CameraDisconnected(f"{what} : appareil déconnecté")
        raise CameraError(f"{what} : erreur Canon 0x{err:08X}")

    def _retry_busy(self, fn, what: str, attempts: int = 20) -> None:
        for _ in range(attempts):
            err = fn()
            if err != EDS_ERR_DEVICE_BUSY:
                self._check(err, what)
                return
            time.sleep(0.1)
        self._check(EDS_ERR_DEVICE_BUSY, what)

    def _set_u32(self, prop: int, value: int) -> None:
        v = c_uint32(value)
        self._retry_busy(lambda: self.sdk.EdsSetPropertyData(self.camera, prop, 0, 4, byref(v)), f"réglage 0x{prop:X}")

    def _get_u32(self, prop: int) -> int:
        v = c_uint32(0)
        self._check(self.sdk.EdsGetPropertyData(self.camera, prop, 0, 4, byref(v)), f"lecture 0x{prop:X}")
        return v.value

    def _command(self, cmd: int, param: int = 0) -> int:
        return self.sdk.EdsSendCommand(self.camera, cmd, param)

    # --- cycle de vie ------------------------------------------------------
    def thread_init(self) -> None:
        if sys.platform == "win32":
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED, messages pompés
            self._dll_dir_handle = os.add_dll_directory(str(self.dll_path.parent))
            self.sdk = ctypes.WinDLL(str(self.dll_path))
        else:
            self.sdk = ctypes.CDLL(str(self.dll_path))
        self.sdk.EdsSendCommand.argtypes = [c_void_p, c_uint32, c_int32]
        self.sdk.EdsDownload.argtypes = [c_void_p, c_uint64, c_void_p]
        self.sdk.EdsCreateMemoryStream.argtypes = [c_uint64, POINTER(c_void_p)]
        self.sdk.EdsGetPointer.argtypes = [c_void_p, POINTER(c_void_p)]
        self.sdk.EdsCreateFileStream.argtypes = [ctypes.c_char_p, c_uint32, c_uint32, POINTER(c_void_p)]
        self._check(self.sdk.EdsInitializeSDK(), "initialisation du kit Canon")
        self.name = "Canon"

    def thread_exit(self) -> None:
        self.disconnect()
        if self.sdk is not None:
            self.sdk.EdsTerminateSDK()
            self.sdk = None
        if sys.platform == "win32":
            ctypes.windll.ole32.CoUninitialize()

    def connect(self) -> bool:
        cam_list = c_void_p()
        count = c_uint32(0)
        if self.sdk.EdsGetCameraList(byref(cam_list)) != EDS_ERR_OK:
            return False
        try:
            self.sdk.EdsGetChildCount(cam_list, byref(count))
            if count.value == 0:
                return False
            camera = c_void_p()
            self._check(self.sdk.EdsGetChildAtIndex(cam_list, 0, byref(camera)), "accès à l'appareil")
        finally:
            self.sdk.EdsRelease(cam_list)

        self.camera = camera
        self._handlers = [ObjectHandler(self._on_object_event), StateHandler(self._on_state_event),
                          PropertyHandler(self._on_property_event)]
        self.sdk.EdsSetObjectEventHandler(self.camera, kEdsObjectEvent_All, self._handlers[0], None)
        self.sdk.EdsSetCameraStateEventHandler(self.camera, kEdsStateEvent_All, self._handlers[1], None)
        self.sdk.EdsSetPropertyEventHandler(self.camera, kEdsPropertyEvent_All, self._handlers[2], None)
        err = self.sdk.EdsOpenSession(self.camera)
        if err != EDS_ERR_OK:
            self.sdk.EdsRelease(self.camera)
            self.camera = c_void_p()
            self.on_status(f"Appareil détecté mais session refusée (0x{err:08X}). EOS Utility est-il fermé ?")
            return False

        # Photos envoyées directement au PC, sans passer par la carte
        self._set_u32(kEdsPropID_SaveTo, kEdsSaveTo_Host)
        cap = EdsCapacity(0x7FFFFFFF, 0x1000, 1)
        self._retry_busy(lambda: self.sdk.EdsSetCapacity(self.camera, cap), "capacité")
        self.connected = True
        self.name = self._product_name()
        return True

    def _product_name(self) -> str:
        buf = ctypes.create_string_buffer(256)
        if self.sdk.EdsGetPropertyData(self.camera, kEdsPropID_ProductName, 0, 256, buf) == EDS_ERR_OK:
            return buf.value.decode("ascii", "ignore") or "Canon"
        return "Canon"

    def battery(self) -> int | None:
        try:
            return self._get_u32(kEdsPropID_BatteryLevel)
        except CameraError:
            return None

    def disconnect(self) -> None:
        if not self.camera:
            return
        try:
            if self.liveview:
                self.stop_liveview()
        except CameraError:
            pass
        self.sdk.EdsCloseSession(self.camera)
        self.sdk.EdsRelease(self.camera)
        self.camera = c_void_p()
        self.connected = False
        self.liveview = False

    def poll(self) -> None:
        if sys.platform == "win32":
            _pump_windows_messages()
        self.sdk.EdsGetEvent()
        if not self.connected:
            raise CameraDisconnected("appareil déconnecté")
        # Empêche la mise en veille du boîtier pendant la séance
        if time.monotonic() - self._last_keepalive > 30:
            self._last_keepalive = time.monotonic()
            self._command(kEdsCameraCommand_ExtendShutDownTimer)

    # --- événements --------------------------------------------------------
    def _on_object_event(self, event, ref, _ctx):
        try:
            if event == kEdsObjectEvent_DirItemRequestTransfer:
                self._download(ref, delete_after=False)
            elif event == kEdsObjectEvent_DirItemCreated:
                # Les vidéos sont écrites sur la carte : on les rapatrie puis on les efface
                name = self._item_name(ref)
                if name and Path(name).suffix.lower() in MOVIE_EXT:
                    self._download(ref, delete_after=True)
                elif ref:
                    self.sdk.EdsRelease(ref)
            elif ref:
                self.sdk.EdsRelease(ref)
        except Exception as e:  # noqa: BLE001 - une exception ne doit jamais remonter dans le kit Canon
            self.on_status(f"Transfert impossible : {e}")
        return EDS_ERR_OK

    def _on_state_event(self, event, _data, _ctx):
        if event == kEdsStateEvent_Shutdown:
            self.connected = False
        return EDS_ERR_OK

    def _on_property_event(self, event, prop, _param, _ctx):
        # Molette tournée sur le boîtier, ou choix possibles modifiés (changement de mode)
        if prop in PROP_KEYS or event == kEdsPropertyEvent_PropertyDescChanged:
            self._settings_changed = True
        return EDS_ERR_OK

    def _item_info(self, item) -> tuple[str | None, int, bool]:
        raw = (c_char * 512)()
        self._check(self.sdk.EdsGetDirectoryItemInfo(item, raw), "infos du fichier")
        data = bytes(raw)
        # Ancienne structure d'abord (kit 3.x, celui de Zawada) : lue sur des données 13.x, elle tombe sur
        # le champ « option » (souvent nul) et donne un nom invalide, alors que l'inverse peut donner
        # un nom de dossier tronqué mais plausible (« 100CANON » -> « ANON »)
        for struct in (_DirItemInfo32, _DirItemInfo64):
            info = struct.from_buffer_copy(data[:ctypes.sizeof(struct)])
            name = _valid_name(info.szFileName)
            if name:
                return name, int(info.size), bool(info.isFolder)
        return None, 0, False

    def _item_name(self, item) -> str | None:
        return self._item_info(item)[0]

    def _download(self, item, delete_after: bool) -> None:
        name, size, _is_folder = self._item_info(item)
        if not name:
            self.sdk.EdsRelease(item)
            raise CameraError("nom de fichier illisible")
        final = self.target_path(name)
        partial = final.with_name(final.name + ".part")
        stream = c_void_p()
        try:
            self._check(self.sdk.EdsCreateFileStream(str(partial).encode("mbcs" if sys.platform == "win32" else "utf-8"),
                                                     kEdsFileCreateDisposition_CreateAlways, kEdsAccess_ReadWrite,
                                                     byref(stream)), "création du fichier")
            self.on_status(f"Transfert de {name}…")
            self._retry_busy(lambda: self.sdk.EdsDownload(item, size, stream), "téléchargement")
            self._check(self.sdk.EdsDownloadComplete(item), "fin du téléchargement")
        finally:
            if stream:
                self.sdk.EdsRelease(stream)
        os.replace(partial, final)
        if delete_after:
            self._retry_busy(lambda: self.sdk.EdsDeleteDirectoryItem(item), "effacement de la carte")
        self.sdk.EdsRelease(item)
        self.downloads += 1
        self.on_status(f"{name} reçu")
        self.on_file(str(final))

    # --- visée en direct ---------------------------------------------------
    def start_liveview(self) -> None:
        try:
            self._set_u32(kEdsPropID_Evf_Mode, 1)
        except CameraError:
            pass  # propriété absente sur certains boîtiers
        device = self._get_u32(kEdsPropID_Evf_OutputDevice)
        self._set_u32(kEdsPropID_Evf_OutputDevice, device | kEdsEvfOutputDevice_PC)
        self.liveview = True

    def stop_liveview(self) -> None:
        device = self._get_u32(kEdsPropID_Evf_OutputDevice)
        self._set_u32(kEdsPropID_Evf_OutputDevice, device & ~kEdsEvfOutputDevice_PC)
        self.liveview = False

    def liveview_frame(self) -> bytes | None:
        if not self.liveview:
            return None
        stream, evf = c_void_p(), c_void_p()
        try:
            self._check(self.sdk.EdsCreateMemoryStream(0, byref(stream)), "mémoire visée")
            self._check(self.sdk.EdsCreateEvfImageRef(stream, byref(evf)), "image visée")
            err = self.sdk.EdsDownloadEvfImage(self.camera, evf)
            if err in (EDS_ERR_OBJECT_NOTREADY, EDS_ERR_DEVICE_BUSY):
                return None
            self._check(err, "visée en direct")
            self._read_evf_geometry(evf)
            length = c_uint64(0)
            pointer = c_void_p()
            self.sdk.EdsGetLength(stream, byref(length))
            self.sdk.EdsGetPointer(stream, byref(pointer))
            if not pointer.value or not length.value:
                return None
            return ctypes.string_at(pointer.value, length.value)
        finally:
            if evf:
                self.sdk.EdsRelease(evf)
            if stream:
                self.sdk.EdsRelease(stream)

    def _read_evf_geometry(self, evf) -> None:
        size, rect = EdsSize(), EdsRect()
        if self.sdk.EdsGetPropertyData(evf, kEdsPropID_Evf_CoordinateSystem, 0, ctypes.sizeof(size), byref(size)) == EDS_ERR_OK:
            self._evf_size = (size.width, size.height)
        if self.sdk.EdsGetPropertyData(evf, kEdsPropID_Evf_ZoomRect, 0, ctypes.sizeof(rect), byref(rect)) == EDS_ERR_OK:
            self._evf_rect = (rect.x, rect.y, rect.width, rect.height)
            if self.zoom == 1 and rect.width > 0:
                self._af_frame = (rect.width, rect.height)

    # --- réglages de prise de vue ------------------------------------------
    def get_settings(self) -> dict:
        out = {}
        for key, prop, _title in SETTINGS:
            try:
                value = self._get_u32(prop)
            except CameraDisconnected:
                raise
            except CameraError:
                continue  # réglage absent sur ce boîtier
            desc = EdsPropertyDesc()
            choices = []
            if self.sdk.EdsGetPropertyDesc(self.camera, prop, byref(desc)) == EDS_ERR_OK:
                choices = [desc.propDesc[i] & 0xFFFFFFFF for i in range(max(0, min(desc.numElements, 128)))]
            if key == "kelvin" and not choices:
                choices = list(KELVIN_CHOICES)
            out[key] = {"value": value, "choices": choices}
        return out

    def set_setting(self, key: str, value: int) -> None:
        try:
            self._set_u32(PROP_IDS[key], int(value))
        except CameraDisconnected:
            raise
        except CameraError as e:
            raise CameraError("Réglage refusé par l'appareil : pour tout régler, mets la molette du boîtier sur M.") from e

    def take_settings_changed(self) -> bool:
        changed, self._settings_changed = self._settings_changed, False
        return changed

    # --- mise au point -----------------------------------------------------
    def focus_at(self, fx: float, fy: float) -> None:
        """Mise au point sur un point de la visée (fractions 0..1 de l'image affichée)."""
        if not self._evf_size:
            raise CameraError("Visée pas encore prête pour la mise au point au clic.")
        width, height = self._evf_size
        if self.zoom > 1 and self._evf_rect:
            x0, y0, w, h = self._evf_rect
            tx, ty = x0 + fx * w, y0 + fy * h
        else:
            tx, ty = fx * width, fy * height
        fw, fh = self._af_frame or (width // 5, height // 5)
        point = EdsPoint(int(min(max(tx - fw / 2, 0), width - fw)), int(min(max(ty - fh / 2, 0), height - fh)))
        self._retry_busy(lambda: self.sdk.EdsSetPropertyData(self.camera, kEdsPropID_Evf_ZoomPosition, 0,
                                                            ctypes.sizeof(point), byref(point)), "déplacement du collimateur")
        self.autofocus()
        self.on_status("Mise au point faite sur la zone cliquée")

    def autofocus(self) -> None:
        self._retry_busy(lambda: self._command(kEdsCameraCommand_DoEvfAf, 1), "mise au point")
        time.sleep(0.8)
        self._command(kEdsCameraCommand_DoEvfAf, 0)

    def focus_step(self, step: int) -> None:
        if step in FOCUS_STEPS:
            self._retry_busy(lambda: self._command(kEdsCameraCommand_DriveLensEvf, FOCUS_STEPS[step]), "mise au point manuelle")

    def set_zoom(self, zoom: int) -> None:
        self.zoom = zoom if zoom in (1, 5, 10) else 1
        self._set_u32(kEdsPropID_Evf_Zoom, self.zoom)

    # --- prise de vue ------------------------------------------------------
    def capture(self) -> None:
        # Sans AF : la mise au point faite une fois reste identique pour les 8 angles du plateau
        def shoot():
            err = self._command(kEdsCameraCommand_PressShutterButton, kEdsShutterButton_Completely_NonAF)
            self._command(kEdsCameraCommand_PressShutterButton, kEdsShutterButton_OFF)
            return err
        self._retry_busy(shoot, "déclenchement")

    def start_movie(self) -> None:
        # Le kit ne filme jamais vers le PC : la vidéo doit être enregistrée sur la carte
        self._set_u32(kEdsPropID_SaveTo, kEdsSaveTo_Camera)
        try:
            self._set_u32(kEdsPropID_Record, kEdsRecord_Start)
            started = False
            for _ in range(10):  # le boîtier peut accepter la commande sans filmer : on vérifie
                time.sleep(0.1)
                self._events()
                if self._get_u32(kEdsPropID_Record) == kEdsRecord_Start:
                    started = True
                    break
        except CameraDisconnected:
            raise
        except CameraError:
            started = False
        if not started:
            self._restore_save_to_host()
            raise CameraError("La vidéo n'a pas démarré : place le sélecteur photo/vidéo du 6D Mark II sur la "
                              "caméra rouge et vérifie qu'une carte SD est dans le boîtier.")
        self.recording = True

    def stop_movie(self) -> None:
        try:
            self._set_u32(kEdsPropID_Record, kEdsRecord_Stop)
        except CameraDisconnected:
            raise
        except CameraError:
            if self._get_u32(kEdsPropID_Record) != kEdsRecord_Stop:
                raise CameraError("L'appareil refuse d'arrêter la vidéo pour l'instant, réessaie.")
        self.recording = False
        try:
            # Selon les boîtiers, l'événement de fin d'écriture n'arrive pas : on vérifie aussi la carte
            before = self.downloads
            for _ in range(30):
                time.sleep(0.5)
                self._events()
                if self.downloads > before or self.collect_card_movies():
                    return
            self.on_status("Vidéo non retrouvée sur la carte : utilise « Récupérer les vidéos de la carte ».")
        finally:
            self._restore_save_to_host()

    def _events(self) -> None:
        if sys.platform == "win32":
            _pump_windows_messages()
        self.sdk.EdsGetEvent()

    def _restore_save_to_host(self) -> None:
        """Les photos repartent directement vers le PC."""
        try:
            self._set_u32(kEdsPropID_SaveTo, kEdsSaveTo_Host)
            cap = EdsCapacity(0x7FFFFFFF, 0x1000, 1)
            self._retry_busy(lambda: self.sdk.EdsSetCapacity(self.camera, cap), "capacité")
        except CameraDisconnected:
            raise
        except CameraError as e:
            self.on_status(f"Photos peut-être enregistrées sur la carte au lieu du PC ({e})")

    def collect_card_movies(self) -> int:
        count = 0
        for item in list(self._walk_card()):
            name = self._item_name(item)
            if name and Path(name).suffix.lower() in MOVIE_EXT:
                self._download(item, delete_after=True)
                count += 1
            else:
                self.sdk.EdsRelease(item)
        return count

    def _children(self, ref):
        count = c_uint32(0)
        if self.sdk.EdsGetChildCount(ref, byref(count)) != EDS_ERR_OK:
            return []
        out = []
        for i in range(count.value):
            child = c_void_p()
            if self.sdk.EdsGetChildAtIndex(ref, i, byref(child)) == EDS_ERR_OK:
                out.append(child)
        return out

    def _walk_card(self):
        """Fichiers de DCIM sur chaque carte (le 6D Mark II n'a qu'un emplacement)."""
        for volume in self._children(self.camera):
            for folder in self._children(volume):
                name, _size, is_folder = self._item_info(folder)
                if is_folder and name and name.upper() == "DCIM":
                    for sub in self._children(folder):
                        _n, _s, sub_is_folder = self._item_info(sub)
                        if sub_is_folder:
                            yield from self._children(sub)
                        self.sdk.EdsRelease(sub)
                self.sdk.EdsRelease(folder)
            self.sdk.EdsRelease(volume)


class _MSG(Structure):
    _fields_ = [("hwnd", c_void_p), ("message", c_uint32), ("wParam", c_void_p), ("lParam", c_void_p),
                ("time", c_uint32), ("pt_x", c_int32), ("pt_y", c_int32)]


def _pump_windows_messages() -> None:
    user32 = ctypes.windll.user32
    msg = _MSG()
    while user32.PeekMessageW(byref(msg), None, 0, 0, 1):  # PM_REMOVE
        user32.TranslateMessage(byref(msg))
        user32.DispatchMessageW(byref(msg))
