"""Serveur HTTP local (127.0.0.1 uniquement) : interface + /api/*."""
from __future__ import annotations

import json
import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .api import AppAPI

RESOURCES = Path(__file__).parent / "resources"
MAX_BODY = 1_048_576


class LocalServer:
    def __init__(self, api: AppAPI, port: int = 0):
        self.api = api
        self.token = secrets.token_urlsafe(32)
        handler = self._handler_class()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/"

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True, name="dreameui-http").start()

    def stop(self):
        self.httpd.shutdown()

    def _handler_class(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "DreameUI"

            def log_message(self, *args):  # pas de journal des requêtes (pas de fuite d'identifiants)
                pass

            def _send(self, status: int, data: bytes, content_type: str, extra: dict | None = None):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def _json(self, status: int, body):
                self._send(status, json.dumps(body, ensure_ascii=False).encode(), "application/json; charset=utf-8")

            def _trusted_host(self) -> bool:
                # Protection contre le DNS rebinding : seul l'hôte loopback exact est accepté.
                return self.headers.get("Host") in (f"127.0.0.1:{server.port}", f"localhost:{server.port}")

            def do_GET(self):
                if not self._trusted_host():
                    return self._json(403, {"error": "Hôte non autorisé."})
                if self.path.startswith("/api/"):
                    return self._api("GET", {})
                path = self.path.split("?", 1)[0]
                if path in ("/", "/index.html"):
                    html = (RESOURCES / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", server.token)
                    return self._send(200, html.encode(), "text/html; charset=utf-8",
                                      {"Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; "
                                       "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                                       "media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'"})
                file = (RESOURCES / path.lstrip("/")).resolve()
                if RESOURCES.resolve() not in file.parents or not file.is_file() or file.name.startswith("index."):
                    return self._json(404, {"error": "Introuvable."})
                kind = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                self._send(200, file.read_bytes(), kind)

            def do_POST(self):
                if not self._trusted_host() or not self.path.startswith("/api/"):
                    return self._json(403, {"error": "Requête refusée."})
                if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), server.token):
                    return self._json(403, {"error": "Jeton de sécurité invalide."})
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._json(413, {"error": "Requête trop volumineuse."})
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw) if raw else {}
                except ValueError:
                    body = {}
                self._api("POST", body if isinstance(body, dict) else {})

            def _api(self, method: str, body: dict):
                response = server.api.handle(method, self.path, body)
                if response.data is not None:
                    return self._send(response.status, response.data, response.content_type)
                self._json(response.status, response.body)

        return Handler
