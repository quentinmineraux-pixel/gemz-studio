"""Réglages de prise de vue : codes Canon (EDSDK) et libellés affichés."""
from __future__ import annotations

# clé, identifiant de propriété EDSDK, libellé
SETTINGS = [
    ("mode", 0x00000400, "Mode"),
    ("iso", 0x00000402, "ISO"),
    ("tv", 0x00000406, "Vitesse"),
    ("av", 0x00000405, "Ouverture"),
    ("ev", 0x00000407, "Correction"),
    ("wb", 0x00000106, "Balance des blancs"),
    ("kelvin", 0x00000107, "Température"),
]
PROP_IDS = {key: prop for key, prop, _ in SETTINGS}
PROP_KEYS = {prop: key for key, prop, _ in SETTINGS}
TITLES = {key: title for key, _, title in SETTINGS}

WB_KELVIN = 9
KELVIN_CHOICES = list(range(2500, 10001, 100))

MODES = {0: "P", 1: "Tv", 2: "Av", 3: "M", 4: "Bulb", 5: "A-DEP", 7: "C", 9: "Auto", 16: "C1", 17: "C2",
         18: "C3", 22: "Auto (A+)", 20: "Vidéo"}

ISO = {0x00: "Auto", 0x28: "6", 0x30: "12", 0x38: "25", 0x40: "50", 0x48: "100", 0x4B: "125", 0x4D: "160",
       0x50: "200", 0x53: "250", 0x55: "320", 0x58: "400", 0x5B: "500", 0x5D: "640", 0x60: "800",
       0x63: "1000", 0x65: "1250", 0x68: "1600", 0x6B: "2000", 0x6D: "2500", 0x70: "3200", 0x73: "4000",
       0x75: "5000", 0x78: "6400", 0x7B: "8000", 0x7D: "10000", 0x80: "12800", 0x83: "16000",
       0x85: "20000", 0x88: "25600", 0x8B: "32000", 0x8D: "40000", 0x90: "51200", 0x93: "64000",
       0x95: "80000", 0x98: "102400"}

AV = {0x08: "1", 0x0B: "1.1", 0x0C: "1.2", 0x0D: "1.2", 0x10: "1.4", 0x13: "1.6", 0x14: "1.8", 0x15: "1.8",
      0x18: "2", 0x1B: "2.2", 0x1C: "2.5", 0x1D: "2.5", 0x20: "2.8", 0x23: "3.2", 0x85: "3.4", 0x24: "3.5",
      0x25: "3.5", 0x28: "4", 0x2B: "4.5", 0x2C: "4.5", 0x2D: "5", 0x30: "5.6", 0x33: "6.3", 0x34: "6.7",
      0x35: "7.1", 0x38: "8", 0x3B: "9", 0x3C: "9.5", 0x3D: "10", 0x40: "11", 0x43: "13", 0x44: "13",
      0x45: "14", 0x48: "16", 0x4B: "18", 0x4C: "19", 0x4D: "20", 0x50: "22", 0x53: "25", 0x54: "27",
      0x55: "29", 0x58: "32", 0x5B: "36", 0x5C: "38", 0x5D: "40", 0x60: "45", 0x63: "51", 0x64: "54",
      0x65: "57", 0x68: "64", 0x6B: "72", 0x6C: "76", 0x6D: "80", 0x70: "91"}

TV = {0x0C: "Bulb", 0x10: "30\"", 0x13: "25\"", 0x14: "20\"", 0x15: "20\"", 0x18: "15\"", 0x1B: "13\"",
      0x1C: "10\"", 0x1D: "10\"", 0x20: "8\"", 0x23: "6\"", 0x24: "6\"", 0x25: "5\"", 0x28: "4\"",
      0x2B: "3\"2", 0x2C: "3\"", 0x2D: "2\"5", 0x30: "2\"", 0x33: "1\"6", 0x34: "1\"5", 0x35: "1\"3",
      0x38: "1\"", 0x3B: "0\"8", 0x3C: "0\"7", 0x3D: "0\"6", 0x40: "0\"5", 0x43: "0\"4", 0x44: "0\"3",
      0x45: "0\"3", 0x48: "1/4", 0x4B: "1/5", 0x4C: "1/6", 0x4D: "1/6", 0x50: "1/8", 0x53: "1/10",
      0x54: "1/10", 0x55: "1/13", 0x58: "1/15", 0x5B: "1/20", 0x5C: "1/20", 0x5D: "1/25", 0x60: "1/30",
      0x63: "1/40", 0x64: "1/45", 0x65: "1/50", 0x68: "1/60", 0x6B: "1/80", 0x6C: "1/90", 0x6D: "1/100",
      0x70: "1/125", 0x73: "1/160", 0x74: "1/180", 0x75: "1/200", 0x78: "1/250", 0x7B: "1/320",
      0x7C: "1/350", 0x7D: "1/400", 0x80: "1/500", 0x83: "1/640", 0x84: "1/750", 0x85: "1/800",
      0x88: "1/1000", 0x8B: "1/1250", 0x8C: "1/1500", 0x8D: "1/1600", 0x90: "1/2000", 0x93: "1/2500",
      0x94: "1/3000", 0x95: "1/3200", 0x98: "1/4000", 0x9B: "1/5000", 0x9C: "1/6000", 0x9D: "1/6400",
      0xA0: "1/8000"}

WB = {0: "Auto (ambiance)", 23: "Auto (blanc)", 1: "Lumière du jour", 8: "Ombre", 2: "Nuageux",
      3: "Tungstène", 4: "Fluorescent", 5: "Flash", 6: "Personnalisée", 9: "Température (K)"}

_EV_FRACTIONS = {0: "", 3: " 1/3", 4: " 1/2", 5: " 2/3"}


def ev_label(code: int) -> str:
    """Correction d'exposition : octet signé en huitièmes d'IL (0x05 = +2/3, 0xF8 = -1)."""
    value = code & 0xFF
    if value >= 0x80:
        value -= 0x100
    if value == 0:
        return "0"
    sign = "+" if value > 0 else "-"
    whole, rest = divmod(abs(value), 8)
    frac = _EV_FRACTIONS.get(rest, f" {rest}/8").strip()
    if whole and frac:
        return f"{sign}{whole} {frac}"
    return f"{sign}{whole or frac}"


def label(key: str, code: int) -> str:
    if key == "mode":
        return MODES.get(code, f"mode {code}")
    if key == "iso":
        return ISO.get(code, f"0x{code:X}")
    if key == "av":
        if code in AV:
            return "f/" + AV[code]
        return f"f/{2 ** ((code - 8) / 16):.1f}"  # 8 unités = 1 IL, f = √2 par IL
    if key == "tv":
        if code in TV:
            return TV[code]
        seconds = 2 ** (-(code - 0x38) / 8)
        return f"{seconds:g}\"" if seconds >= 1 else f"1/{round(1 / seconds)}"
    if key == "ev":
        return ev_label(code)
    if key == "wb":
        return WB.get(code, f"Préréglage {code}")
    if key == "kelvin":
        return f"{code} K"
    return str(code)
