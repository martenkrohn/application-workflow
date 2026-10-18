"""Dependency-free loopback HTTP server for the local dashboard."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import webbrowser
from contextlib import suppress
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlsplit

from job_system.application.assessment_queue import AssessmentQueueService, PrepareBatch
from job_system.application.dashboard import DashboardService


class DashboardServer:
    def __init__(
        self,
        service: DashboardService,
        profile_id: str,
        host: str = "127.0.0.1",
        port: int = 8765,
        prepare_batches: PrepareBatch | None = None,
    ) -> None:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("the dashboard may only bind to the local computer")
        self.service = service
        self.profile_id = profile_id
        self.token = secrets.token_urlsafe(24)
        self.assessment_queue = AssessmentQueueService(service, profile_id)
        self.prepare_batches = prepare_batches
        self.httpd = ThreadingHTTPServer((host, port), self._handler())

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        displayed_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else str(host)
        return f"http://{displayed_host}:{port}/"

    def serve(self, open_browser: bool = True) -> None:
        if open_browser:
            webbrowser.open(self.url)
        self.httpd.serve_forever()

    def close(self) -> None:
        self.httpd.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        service = self.service
        default_profile = self.profile_id
        token = self.token
        assessment_queue = self.assessment_queue
        prepare_batches = self.prepare_batches

        class Handler(BaseHTTPRequestHandler):
            server_version = "JobDashboard/1"

            def _allowed_request(self) -> bool:
                port = self.server.server_port
                return self.headers.get("Host") in {
                    f"127.0.0.1:{port}",
                    f"localhost:{port}",
                }

            def do_GET(self) -> None:
                if not self._allowed_request():
                    self._error(HTTPStatus.FORBIDDEN, "loopback host required")
                    return
                request = urlsplit(self.path)
                if request.path == "/":
                    content = _asset("index.html").replace("__CSRF_TOKEN__", token)
                    self._send(content.encode(), "text/html; charset=utf-8")
                    return
                if request.path in {"/app.js", "/styles.css"}:
                    media = (
                        "text/javascript"
                        if request.path.endswith(".js")
                        else "text/css"
                    )
                    self._send(
                        _asset(request.path[1:]).encode(), f"{media}; charset=utf-8"
                    )
                    return
                if request.path == "/demo-source":
                    fixtures = [
                        service.project_root / "examples" / name
                        for name in ("jobs.html", "shortlist.html")
                    ]
                    sections = [
                        re.search(
                            r"<body>(.*?)</body>",
                            path.read_text(encoding="utf-8"),
                            re.S,
                        )[1]
                        for path in fixtures
                    ]
                    content = (
                        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Fictional vacancy sources</title></head><body>'
                        + "\n".join(sections).replace('data-job-id="', 'id="')
                        + "</body></html>"
                    )
                    self._send(content.encode(), "text/html; charset=utf-8")
                    return
                if request.path == "/api/v1/health":
                    self._json({"status": "ok"})
                    return
                if request.path == "/api/v1/dashboard-state":
                    try:
                        self._json(assessment_queue.dashboard_state())
                    except sqlite3.OperationalError as error:
                        self._database_error(error)
                    except (OSError, RuntimeError, ValueError) as error:
                        self._error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                if request.path == "/api/v1/assessment-queue":
                    try:
                        self._json(assessment_queue.state())
                    except sqlite3.OperationalError as error:
                        self._database_error(error)
                    except (OSError, RuntimeError, ValueError) as error:
                        self._error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                if request.path == "/api/v1/dashboard":
                    profile = parse_qs(request.query).get(
                        "profile_id", [default_profile]
                    )[0]
                    try:
                        self._json(service.snapshot(profile))
                    except sqlite3.OperationalError as error:
                        self._database_error(error)
                    except (OSError, RuntimeError, ValueError) as error:
                        self._error(HTTPStatus.BAD_REQUEST, str(error))
                    return
                self._error(HTTPStatus.NOT_FOUND, "not found")

            def do_POST(self) -> None:
                origin = self.headers.get("Origin")
                if not self._allowed_request() or (
                    origin is not None
                    and origin
                    not in {
                        f"http://127.0.0.1:{self.server.server_port}",
                        f"http://localhost:{self.server.server_port}",
                    }
                ):
                    self._error(
                        HTTPStatus.FORBIDDEN, "same-origin local request required"
                    )
                    return
                if self.headers.get("X-Dashboard-Token") != token:
                    self._error(HTTPStatus.FORBIDDEN, "invalid dashboard token")
                    return
                try:
                    body = self._body()
                    if self.path == "/api/v1/reviews":
                        result = service.set_review(
                            _required(body, "group_id"),
                            str(body.get("profile_id") or default_profile),
                            _required(body, "status"),
                            body.get("note")
                            if isinstance(body.get("note"), str)
                            else None,
                        )
                    elif self.path == "/api/v1/workspaces":
                        result = service.create_workspace(
                            _required(body, "group_id"),
                            str(body.get("profile_id") or default_profile),
                            _required(body, "slug"),
                        )
                    elif self.path == "/api/v1/assessments":
                        result = assessment_queue.manual(
                            (_required(body, "source"), _required(body, "external_id")),
                            True,
                        )
                    elif self.path == "/api/v1/assessment-queue/remove":
                        result = assessment_queue.manual(
                            (_required(body, "source"), _required(body, "external_id")),
                            False,
                        )
                    elif self.path == "/api/v1/assessment-queue/preview":
                        result = assessment_queue.preview(_integer(body, "total"))
                    elif self.path == "/api/v1/assessment-queue/select":
                        if type(body.get("selected")) is not bool:
                            raise ValueError("selected must be a boolean")
                        result = assessment_queue.select(
                            (_required(body, "source"), _required(body, "external_id")),
                            body["selected"],
                            _integer(body, "revision"),
                        )
                    elif self.path == "/api/v1/assessment-queue/select-many":
                        items = body.get("changes")
                        if not isinstance(items, list) or not items:
                            raise ValueError("changes must be a non-empty list")
                        changes = {}
                        for item in items:
                            if (
                                not isinstance(item, dict)
                                or type(item.get("selected")) is not bool
                            ):
                                raise ValueError(
                                    "Each change must have a boolean selected value"
                                )
                            key = (
                                _required(item, "source"),
                                _required(item, "external_id"),
                            )
                            if key in changes:
                                raise ValueError("Duplicate vacancy in changes")
                            changes[key] = item["selected"]
                        result = assessment_queue.select_many(
                            changes, _integer(body, "revision")
                        )
                    elif self.path == "/api/v1/assessment-queue/fill":
                        result = assessment_queue.fill(_integer(body, "revision"))
                    elif self.path == "/api/v1/assessment-queue/prepare":
                        if prepare_batches is None:
                            raise ValueError(
                                "Batch preparation is unavailable in this server."
                            )
                        result = assessment_queue.prepare(
                            _integer(body, "revision"), prepare_batches
                        )
                    else:
                        self._error(HTTPStatus.NOT_FOUND, "not found")
                        return
                    self._json(result)
                except sqlite3.OperationalError as error:
                    self._database_error(error)
                except sqlite3.IntegrityError:
                    self._error(
                        HTTPStatus.CONFLICT,
                        "This change conflicts with an existing database record. Refresh the dashboard and try again.",
                    )
                except (KeyError, OSError, RuntimeError, ValueError) as error:
                    self._error(HTTPStatus.BAD_REQUEST, str(error))

            def _database_error(self, error: sqlite3.OperationalError) -> None:
                if "locked" in str(error).lower() or "busy" in str(error).lower():
                    self._error(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        "The database is busy saving changes. Please refresh in a moment.",
                    )
                else:
                    self._error(
                        HTTPStatus.INTERNAL_SERVER_ERROR, "The database request failed."
                    )

            def _body(self) -> dict[str, Any]:
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError as error:
                    raise ValueError("invalid content length") from error
                if length < 1 or length > 65536:
                    raise ValueError("request body must be between 1 byte and 64 KiB")
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError("request body must be a JSON object")
                return value

            def _json(self, value: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
                self._send(
                    json.dumps(value, ensure_ascii=False, default=str).encode(),
                    "application/json; charset=utf-8",
                    status,
                )

            def _error(self, status: HTTPStatus, message: str) -> None:
                self._json({"error": message}, status)

            def _send(
                self,
                content: bytes,
                media_type: str,
                status: HTTPStatus = HTTPStatus.OK,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", media_type)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
                )
                self.end_headers()
                with suppress(
                    BrokenPipeError, ConnectionResetError, ConnectionAbortedError
                ):
                    self.wfile.write(content)

            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler


def _asset(name: str) -> str:
    return files("job_system.web.static").joinpath(name).read_text(encoding="utf-8")


def _required(value: dict[str, Any], name: str) -> str:
    field = value.get(name)
    if not isinstance(field, str) or not field.strip():
        raise ValueError(f"{name} is required")
    return field.strip()


def _integer(value: dict[str, Any], name: str) -> int:
    field = value.get(name)
    if type(field) is not int or field < 0:
        raise ValueError(f"{name} must be a non-negative whole number")
    return field
