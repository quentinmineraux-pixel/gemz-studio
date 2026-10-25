"""Ventes lues dans le journal : lignes à afficher et totaux (jour, mois, plateforme)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .catalog import Catalog, Event

# Anciennes lignes du journal (sans « titre » dans les données) : « Vente Etsy : Malachite – 172.90 EUR · reste 0 »
_MESSAGE = re.compile(r"^Vente [^:]+?(?: hors catalogue)? : (?P<title>.+?)(?: – | \()[\d.]+ [A-Z]{3}")
PERIODS = {"today": "Aujourd'hui", "7d": "7 derniers jours", "month": "Ce mois-ci", "year": "Cette année", "all": "Tout"}


@dataclass
class SaleRow:
    when: datetime        # heure locale de la vente
    platform: str
    sku: str
    title: str
    unit_price: float
    quantity: int
    currency: str
    order: str
    in_catalog: bool

    @property
    def amount(self) -> float:
        return round(self.unit_price * self.quantity, 2)


def _when(ev: Event) -> datetime:
    """Heure de la commande si la plateforme l'a donnée, sinon l'heure où la synchro l'a vue."""
    raw = str(ev.data.get("date") or "")
    if raw:
        try:
            moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment
        except ValueError:
            pass
    return datetime.fromisoformat(ev.ts)


def sale_rows(catalog: Catalog, limit: int = 5000) -> list[SaleRow]:
    titles = {item.sku: item.title for item in catalog.all()}
    rows = []
    for ev in catalog.events(("vente",), limit=limit):
        match = _MESSAGE.match(ev.message)
        title = ev.data.get("titre") or titles.get(ev.sku) or (match.group("title") if match else ev.message)
        rows.append(SaleRow(when=_when(ev), platform=ev.platform, sku=ev.sku, title=str(title),
                            unit_price=float(ev.data.get("prix") or 0), quantity=int(ev.data.get("quantite") or 1),
                            currency=str(ev.data.get("devise") or "EUR"), order=str(ev.data.get("commande") or ""),
                            in_catalog="hors catalogue" not in ev.message))
    rows.sort(key=lambda r: r.when, reverse=True)
    return rows


def period_start(period: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {"today": today, "7d": today - timedelta(days=6), "month": today.replace(day=1),
            "year": today.replace(month=1, day=1)}.get(period)


def in_period(rows: list[SaleRow], period: str, now: datetime | None = None) -> list[SaleRow]:
    start = period_start(period, now)
    return [r for r in rows if start is None or r.when >= start]


@dataclass
class Totals:
    count: int
    by_currency: dict[str, float]
    by_platform: dict[str, dict[str, float]]

    def text(self) -> str:
        """« 1 245,80 € » ou « 1 245,80 € + 120,00 $ » (ventes ebay.com en dollars)."""
        if not self.by_currency:
            return "0 €"
        return " + ".join(money(v, c) for c, v in sorted(self.by_currency.items(), key=lambda kv: kv[0] != "EUR"))


def totals(rows: list[SaleRow]) -> Totals:
    by_currency: dict[str, float] = {}
    by_platform: dict[str, dict[str, float]] = {}
    for r in rows:
        by_currency[r.currency] = round(by_currency.get(r.currency, 0) + r.amount, 2)
        platform = by_platform.setdefault(r.platform, {})
        platform[r.currency] = round(platform.get(r.currency, 0) + r.amount, 2)
    return Totals(sum(r.quantity for r in rows), by_currency, by_platform)


def money(value: float, currency: str = "EUR") -> str:
    text = f"{value:,.2f}".replace(",", " ").replace(".", ",")
    return f"{text} {'€' if currency == 'EUR' else '$' if currency == 'USD' else currency}"


DAYS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
MONTHS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre")
MONTHS_SHORT = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")


def day_label(day, today) -> str:
    """« Aujourd'hui », « Hier », « lundi 20 octobre » (et l'année si ce n'est pas celle en cours)."""
    if day == today:
        return "Aujourd'hui"
    if day == today - timedelta(days=1):
        return "Hier"
    text = f"{DAYS[day.weekday()]} {day.day}{'er' if day.day == 1 else ''} {MONTHS[day.month - 1]}"
    return text if day.year == today.year else f"{text} {day.year}"


def when_label(moment: datetime, now: datetime | None = None) -> str:
    """« aujourd'hui 14:32 », « hier 09:10 », « 20 oct. 14:32 »."""
    now = now or datetime.now()
    day, today = moment.date(), now.date()
    if day == today:
        prefix = "aujourd'hui"
    elif day == today - timedelta(days=1):
        prefix = "hier"
    else:
        prefix = f"{day.day} {MONTHS_SHORT[day.month - 1]}" + (f" {day.year}" if day.year != today.year else "")
    return f"{prefix} {moment:%H:%M}"
