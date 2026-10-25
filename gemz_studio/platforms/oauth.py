"""Connexion OAuth depuis le PC : ouvre le navigateur et récupère le code sur http://localhost."""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = """<!doctype html><meta charset="utf-8"><title>GemZ Studio</title>
<body style="font-family:sans-serif;text-align:center;padding-top:80px">
<h2>{title}</h2><p>{text}</p></body>"""


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def wait_for_code(auth_url: str, port: int, expected_state: str, timeout: float = 300) -> str:
    """Ouvre la page d'autorisation et attend que la plateforme renvoie vers http://localhost:<port>/…"""
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - nom imposé par http.server
            query = parse_qs(urlparse(self.path).query)
            ok = query.get("state", [""])[0] == expected_state and "code" in query
            if ok:
                result["code"] = query["code"][0]
                body = PAGE.format(title="Connexion réussie", text="Tu peux fermer cet onglet et revenir à GemZ Studio.")
            else:
                result["error"] = query.get("error_description", query.get("error", ["refus"]))[0]
                body = PAGE.format(title="Connexion refusée", text=result["error"])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))
            done.set()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        webbrowser.open(auth_url)
        if not done.wait(timeout):
            raise TimeoutError("Pas de réponse de la page de connexion (5 minutes).")
    finally:
        server.shutdown()
        server.server_close()
    if "code" not in result:
        raise PermissionError(result.get("error", "connexion refusée"))
    return result["code"]
