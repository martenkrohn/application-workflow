"""Loopback-only dashboard with explicit human selection and import actions."""

from __future__ import annotations

import json
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from threading import Lock
from urllib.parse import urlsplit

from job_system.application.portfolio import PortfolioWorkflow


def create_server(workflow: PortfolioWorkflow, port: int = 8765) -> ThreadingHTTPServer:
    mutation_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def _allowed_host(self) -> bool:
            return self.headers.get("Host") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            with suppress(
                BrokenPipeError, ConnectionResetError, ConnectionAbortedError
            ):
                self.wfile.write(body)

        def _json(self, status: int, data: object) -> None:
            self._send(
                status,
                json.dumps(data, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )

        def do_GET(self) -> None:
            if not self._allowed_host():
                self._json(403, {"error": "loopback host required"})
                return
            path = urlsplit(self.path).path
            if path == "/api/state":
                with mutation_lock:
                    self._json(200, workflow.snapshot())
                return
            assets = {
                "/": ("index.html", "text/html"),
                "/app.js": ("app.js", "text/javascript"),
                "/style.css": ("style.css", "text/css"),
            }
            if path not in assets:
                self._json(404, {"error": "not found"})
                return
            filename, mime = assets[path]
            content = files("job_system.web.static").joinpath(filename).read_bytes()
            self._send(200, content, mime + "; charset=utf-8")

        def do_POST(self) -> None:
            origin = self.headers.get("Origin")
            if not self._allowed_host() or origin not in {
                f"http://127.0.0.1:{self.server.server_port}",
                f"http://localhost:{self.server.server_port}",
            }:
                self._json(403, {"error": "same-origin local request required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    self._json(413, {"error": "invalid request size"})
                    return
                if (
                    self.headers.get("Content-Type", "").split(";")[0]
                    != "application/json"
                ):
                    self._json(415, {"error": "JSON required"})
                    return
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("JSON object required")
                path = urlsplit(self.path).path
                with mutation_lock:
                    if path == "/api/prepare":
                        result = workflow.prepare(data["selection"])
                    elif path == "/api/decision":
                        result = workflow.decide(
                            data["source"], data["external_id"], data["status"]
                        )
                    elif path == "/api/validate":
                        result = workflow.validate(data["batch_id"])
                    elif path == "/api/import":
                        result = workflow.finish(data["batch_id"])
                    else:
                        self._json(404, {"error": "unknown action"})
                        return
                self._json(200, result)
            except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
                self._json(400, {"error": str(error)})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_dashboard(workflow: PortfolioWorkflow, port: int = 8765) -> None:
    server = create_server(workflow, port)
    print(
        f"Dashboard: http://127.0.0.1:{server.server_port}/ (Ctrl+C to stop)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
