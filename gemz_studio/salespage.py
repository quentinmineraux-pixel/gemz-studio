"""Page « Ventes » à ouvrir sur le téléphone, sur le même Wi-Fi que le PC.

Un petit serveur web dans l'application (rien ne passe par internet) : totaux du jour, du mois et de
l'année, et les dernières ventes avec la photo de la pièce. L'adresse contient un code secret : sans
lui, la page ne montre rien. Elle n'existe que tant que GemZ Studio est ouvert.
"""
from __future__ import annotations

import hmac
import secrets
import socket
import threading
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote

from .catalog import PLATFORM_LABELS, Catalog
from .sales import Totals, in_period, money, sale_rows, totals, when_label
from .thumbs import make_thumb

BADGES = {"shopify": "#5e8e3e", "etsy": "#f1641e", "ebay": "#0064d2"}
SHOWN = 40


def new_code() -> str:
    return secrets.token_urlsafe(9)


def local_ip() -> str:
    """Adresse du PC sur le réseau local (le choix de la route n'envoie aucun paquet)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def page_url(port: int, code: str) -> str:
    return f"http://{local_ip()}:{port}/{code}"


STYLE = """
:root { --bg:#f6f4f1; --card:#fff; --ink:#1f1d1a; --muted:#77716a; --line:#e7e2dc; --accent:#7a5c3e; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151412; --card:#211f1c; --ink:#f1ede8; --muted:#a39b91; --line:#34312c; --accent:#d2b48c; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:15px/1.4 -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width:640px; margin:0 auto; padding:16px; }
h1 { font-size:20px; margin:4px 0 14px; letter-spacing:.02em; }
h1 small { color:var(--muted); font-weight:400; font-size:13px; margin-left:6px; }
.tiles { display:grid; grid-template-columns:repeat(3, 1fr); gap:8px; margin-bottom:18px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:10px; }
.tile b { display:block; font-size:17px; margin:2px 0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tile span, .tile i { color:var(--muted); font-size:12px; font-style:normal; }
.split { color:var(--muted); font-size:12px; margin:-8px 0 16px; }
.sale { display:flex; gap:12px; align-items:center; background:var(--card); border:1px solid var(--line);
        border-radius:12px; padding:8px; margin-bottom:8px; }
.sale img, .sale .ph { width:64px; height:64px; border-radius:8px; object-fit:cover; flex:none; background:var(--line); }
.sale .txt { flex:1; min-width:0; }
.sale .title { font-weight:600; overflow:hidden; text-overflow:ellipsis; display:-webkit-box;
               -webkit-line-clamp:2; -webkit-box-orient:vertical; }
.sale .meta { color:var(--muted); font-size:12px; margin-top:2px; }
.badge { display:inline-block; color:#fff; border-radius:6px; padding:0 6px; font-size:11px; margin-right:4px; }
.price { font-weight:700; white-space:nowrap; }
.empty { color:var(--muted); text-align:center; padding:30px 0; }
footer { color:var(--muted); font-size:12px; text-align:center; margin:18px 0 8px; }
"""


def _tile(label: str, t: Totals) -> str:
    count = f"{t.count} vente{'s' if t.count > 1 else ''}"
    return f"<div class='tile'><span>{label}</span><b>{escape(t.text())}</b><i>{count}</i></div>"


def render(catalog: Catalog, code: str, now: datetime | None = None) -> str:
    now = now or datetime.now()
    rows = sale_rows(catalog, limit=3000)
    month = totals(in_period(rows, "month", now))
    split = " · ".join(f"{PLATFORM_LABELS.get(p, p)} {' + '.join(money(v, c) for c, v in amounts.items())}"
                       for p, amounts in sorted(month.by_platform.items()))
    cards = []
    for r in rows[:SHOWN]:
        color = BADGES.get(r.platform, "#666")
        photo = (f"<img src='/{code}/photo/{quote(r.sku)}' alt='' loading='lazy' "
                 f"onerror=\"this.style.visibility='hidden'\">" if r.in_catalog and r.sku else "<div class='ph'></div>")
        qty = f" · ×{r.quantity}" if r.quantity > 1 else ""
        cards.append(
            f"<div class='sale'>{photo}<div class='txt'><div class='title'>{escape(r.title)}</div>"
            f"<div class='meta'><span class='badge' style='background:{color}'>{PLATFORM_LABELS.get(r.platform, r.platform)}"
            f"</span>{escape(when_label(r.when, now))}{qty}</div></div>"
            f"<div class='price'>{escape(money(r.amount, r.currency))}</div></div>")
    body = "".join(cards) or "<div class='empty'>Pas encore de vente enregistrée.</div>"
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60"><meta name="robots" content="noindex">
<title>GemZ · Ventes</title><style>{STYLE}</style></head><body><main>
<h1>Ventes GemZ<small>mis à jour à {now:%H:%M}</small></h1>
<div class="tiles">{_tile("Aujourd'hui", totals(in_period(rows, "today", now)))}{_tile("Ce mois-ci", month)}{_tile("Cette année", totals(in_period(rows, "year", now)))}</div>
{f'<div class="split">Ce mois-ci : {escape(split)}</div>' if split else ''}
{body}
<footer>Page servie par GemZ Studio sur ton PC · actualisée toute seule chaque minute</footer>
</main></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "GemZ"

    def do_GET(self) -> None:  # noqa: N802 - nom imposé par http.server
        page: SalesPage = self.server.page  # type: ignore[attr-defined]
        parts = [unquote(p) for p in self.path.split("?", 1)[0].split("/") if p]
        if not parts or not page.code or not hmac.compare_digest(parts[0].encode(), page.code.encode()):
            return self._send(404, b"Page introuvable.", "text/plain; charset=utf-8")
        try:
            if len(parts) == 1:
                return self._send(200, render(page.catalog, page.code).encode(), "text/html; charset=utf-8")
            if len(parts) == 3 and parts[1] == "photo":
                item = page.catalog.get(parts[2])
                path = make_thumb(item.thumbnail_source) if item else None
                if path:
                    return self._send(200, path.read_bytes(), "image/jpeg", cache=True)
        except Exception:  # noqa: BLE001 - une erreur de la page ne doit pas toucher l'application
            return self._send(500, b"Erreur.", "text/plain; charset=utf-8")
        return self._send(404, b"Introuvable.", "text/plain; charset=utf-8")

    def _send(self, status: int, body: bytes, content_type: str, cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=86400" if cache else "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass   # application sans console


class SalesPage:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog
        self.code = ""
        self.port = 0
        self._server: ThreadingHTTPServer | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def start(self, port: int, code: str) -> None:
        """Lève OSError si le port est déjà pris."""
        self.stop()
        server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
        server.daemon_threads = True
        server.page = self  # type: ignore[attr-defined]
        self.code, self.port, self._server = code, server.server_address[1], server
        threading.Thread(target=server.serve_forever, name="gemz-page-ventes", daemon=True).start()

    def stop(self) -> None:
        if self._server is not None:
            server, self._server = self._server, None
            server.shutdown()
            server.server_close()
