"""Loopback dashboard for the focused vacancy portfolio."""

from __future__ import annotations
import json
import secrets
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import urlparse

from job_system.application.dashboard import DashboardService
from job_system.application.portfolio import PROFILE


def source_html(workflow):
    sections = []
    for name in ["examples/jobs.html", "examples/shortlist.html"]:
        text = (workflow.root / name).read_text(encoding="utf-8")
        body = re.search(r"<body[^>]*>(.*)</body>", text, re.S)
        text = body.group(1) if body else text
        sections.append(re.sub(r'data-job-id="([^"]+)"', r'id="\1" data-job-id="\1"', text))
    return (
        "<!doctype html><html lang='en'><meta charset='utf-8'><title>Fictional vacancy sources</title><body><h1>Fictional vacancy sources</h1><p>Self-authored examples used in the dashboard demo.</p>"
        + "".join(sections)
        + "</body></html>"
    )


def create_server(workflow, port=8765):
    service = DashboardService(workflow.root, workflow.settings.data_dir, workflow.database)
    profile = workflow.assessment.ensure_profile(PROFILE)
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def send(self, body, media="application/json; charset=utf-8", status=200):
            raw = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            self.wfile.write(raw)

        def valid_host(self):
            return self.headers.get("Host", "") in {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }

        def do_GET(self):
            if not self.valid_host():
                return self.send('{"error":"Invalid host"}', status=403)
            path = urlparse(self.path).path
            if path in {"/api/v1/dashboard", "/api/v1/dashboard-state"}:
                value = service.snapshot(profile.profile_id)
                return self.send(
                    json.dumps({"dashboard": value} if path.endswith("-state") else value)
                )
            if path == "/demo-source.html":
                return self.send(source_html(workflow), "text/html; charset=utf-8")
            assets = {
                "/": "index.html",
                "/index.html": "index.html",
                "/app.js": "app.js",
                "/styles.css": "styles.css",
                "/demo-data.js": "demo-data.js",
            }
            if path not in assets:
                return self.send('{"error":"Not found"}', status=404)
            name = assets[path]
            content = (
                files("job_system.web.static")
                .joinpath(name)
                .read_text(encoding="utf-8")
                .replace("__CSRF_TOKEN__", token)
            )
            media = (
                "text/html"
                if name.endswith(".html")
                else "text/css"
                if name.endswith(".css")
                else "text/javascript"
            )
            self.send(content, media + "; charset=utf-8")

        def do_POST(self):
            origin = self.headers.get("Origin")
            if (
                not self.valid_host()
                or self.headers.get("X-Dashboard-Token") != token
                or (
                    origin
                    and origin
                    not in {
                        f"http://127.0.0.1:{self.server.server_port}",
                        f"http://localhost:{self.server.server_port}",
                    }
                )
            ):
                return self.send('{"error":"Request denied"}', status=403)
            if self.path != "/api/v1/reviews":
                return self.send('{"error":"Not found"}', status=404)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 65536:
                    raise ValueError("Invalid body size")
                body = json.loads(self.rfile.read(length))
                result = service.set_review(
                    body["group_id"], profile.profile_id, body["status"], body.get("note")
                )
                self.send(json.dumps(result))
            except (KeyError, TypeError, ValueError, RuntimeError) as error:
                self.send(json.dumps({"error": str(error)}), status=400)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_dashboard(workflow, port=8765):
    server = create_server(workflow, port)
    print(f"Dashboard: http://127.0.0.1:{server.server_port}/ (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
