"""A real parser for a deliberately small, fictional HTML source contract.

No network access is performed. A private adapter can implement the same SourceAdapter
interface and map its website-specific fields to the neutral JSON record used here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from job_system.domain.models import SourceItem


class _Jobs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[dict[str, str]] = []
        self.current: dict[str, str] | None = None
        self.field: str | None = None
        self.field_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "article" and attributes.get("data-job-id"):
            if self.current is not None:
                raise ValueError("nested job articles are not supported")
            self.current = {"external_id": attributes["data-job-id"] or ""}
        if self.current is None:
            return
        if self.field is not None:
            if tag not in {"br", "hr", "img", "input", "meta", "link"}:
                self.field_depth += 1
            if tag in {"p", "br", "li", "h3"}:
                self.parts.append("\n")
        elif attributes.get("data-field"):
            self.field = attributes["data-field"]
            self.field_depth = 1
            self.parts = []

    def handle_endtag(self, tag: str) -> None:
        if self.field is not None:
            self.field_depth -= 1
            if self.field_depth == 0:
                assert self.current is not None
                self.current[self.field] = "\n".join(
                    " ".join(line.split())
                    for line in "".join(self.parts).splitlines()
                    if line.strip()
                )
                self.field = None
            elif tag in {"p", "li", "h3"}:
                self.parts.append("\n")
        if tag == "article" and self.current is not None:
            self.records.append(self.current)
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.field is not None:
            self.parts.append(data)


class LocalHTMLSource:
    def __init__(
        self,
        path: Path,
        name: str = "demo-html",
        origin: str = "https://careers.example.invalid",
    ) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError(
                "origin must be an absolute HTTPS origin without credentials"
            )
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("origin must not contain a path, query, or fragment")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
            raise ValueError("source name must be a lowercase slug")
        self.path, self.name, self.origin = path, name, origin.rstrip("/")

    def collect(self) -> Iterable[SourceItem]:
        parser = _Jobs()
        parser.feed(self.path.read_text(encoding="utf-8"))
        parser.close()
        if parser.current is not None:
            raise ValueError("incomplete HTML job article")
        seen = set()
        for record in parser.records:
            identity = record["external_id"]
            if not re.fullmatch(r"[a-zA-Z0-9-]+", identity) or identity in seen:
                raise ValueError("job IDs must be unique slugs")
            seen.add(identity)
            for field in ("title", "employer", "locations", "description"):
                if not record.get(field):
                    raise ValueError(f"job {identity} is missing {field}")
            detail = {
                key: value for key, value in record.items() if key != "external_id"
            }
            detail["locations"] = [
                part.strip() for part in record["locations"].split(";")
            ]
            payload = {"external_id": identity, "detail": detail}
            yield SourceItem(
                identity,
                f"{self.origin}/jobs/{identity}",
                "application/json",
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            )
