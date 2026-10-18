"""Deterministic vacancy facts and complete evidence blocks, without profile inference."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SCHEMA_VERSION = 1
EXTRACTOR_VERSION = "portfolio-neutral-1"
MAX_ASSESSMENT_CHARACTERS = 40000
FIELDS = (
    "title",
    "employer",
    "locations",
    "remote_policy",
    "working_time",
    "employment_type",
    "contract_type",
    "contract_end_date",
    "start_date",
    "salary",
    "seniority",
    "description_summary",
    "responsibilities",
    "mandatory_requirements",
    "preferred_qualifications",
    "education_requirements",
    "experience_requirements",
    "methods_tools",
    "languages_required",
    "occupational_categories",
    "application_url",
    "application_method",
    "contact_details",
    "reference_number",
    "publication_date",
    "application_deadline",
    "language",
    "keywords",
    "validity_status",
)
DATE_FIELDS = {
    "contract_end_date",
    "start_date",
    "publication_date",
    "application_deadline",
}
SECTIONS = {
    "aufgaben": "duties",
    "ihre aufgaben": "duties",
    "deine aufgaben": "duties",
    "your responsibilities": "duties",
    "responsibilities": "duties",
    "zwingende voraussetzungen": "mandatory_requirements",
    "minimum requirements": "mandatory_requirements",
    "required qualifications": "mandatory_requirements",
    "preferred qualifications": "preferred_qualifications",
    "wünschenswert": "preferred_qualifications",
    "ihr profil": "requirements",
    "dein profil": "requirements",
    "your profile": "requirements",
    "qualifications": "requirements",
    "anforderungen": "requirements",
    "wir bieten": "conditions",
    "wir bieten ihnen": "conditions",
    "benefits": "conditions",
    "what we offer": "conditions",
    "unser angebot": "conditions",
    "ihre vorteile": "conditions",
    "bewerbung": "application",
    "application": "application",
    "how to apply": "application",
    "einleitung": "introduction",
    "über uns": "introduction",
    "about us": "introduction",
}


def encoded(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def clean_lines(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", unescape(text))
    text = re.sub("[\\u034f\\u200b-\\u200f\\u2060\\ufeff\\u00ad]", "", text)
    return [line for raw in text.splitlines() if (line := " ".join(raw.split()))]


def public_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
        ):
            return None
        parameters = [
            (k, v)
            for k, v in parse_qsl(parts.query)
            if not re.search("utm_|tracking|token|sig|lipi|trk|refid|mid|eid", k, re.I)
        ]
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(parameters), "")
        )
    except ValueError:
        return None


class VacancyHTML(HTMLParser):
    """Keep paragraph/list boundaries; drop explicitly non-vacancy document elements."""

    def __init__(self, *, focus: bool = False) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[tuple[str, bool, bool]] = []
        self.focus = focus
        self.focus_found = False
        self.links: list[tuple[str, str]] = []
        self.anchor: tuple[str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        selected = not self.focus or any((s[2] for s in self.stack))
        if self.focus and (
            tag in {"main", "article"} or attributes.get("id") == "main"
        ):
            selected = True
            self.focus_found = True
        blocked = (
            any((s[1] for s in self.stack))
            or tag in {"script", "style", "button", "svg", "noscript", "head"}
            or any(
                (
                    word in (attributes.get("class") or "").split()
                    for word in {
                        "page-functions",
                        "newsletter",
                        "additionalOffer",
                        "c-share",
                        "breadcrumb",
                    }
                )
            )
        )
        if tag not in {"br", "img", "meta", "link", "input", "hr", "wbr"}:
            self.stack.append((tag, blocked, selected))
        if selected and (not blocked):
            if tag in {
                "p",
                "div",
                "br",
                "li",
                "tr",
                "dt",
                "dd",
                "h1",
                "h2",
                "h3",
                "h4",
                "section",
            }:
                self.parts.append("\n")
            if tag == "a":
                self.anchor = (attributes.get("href") or "", [])

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.anchor:
            self.links.append((self.anchor[0], " ".join(self.anchor[1])))
            self.anchor = None
        matching = next(
            (i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i][0] == tag),
            None,
        )
        if matching is not None:
            selected = self.stack[matching][2] and (not self.stack[matching][1])
            del self.stack[matching:]
            if selected and tag in {
                "p",
                "div",
                "li",
                "tr",
                "dt",
                "dd",
                "h1",
                "h2",
                "h3",
                "h4",
                "section",
            }:
                self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not any((s[1] for s in self.stack)) and (
            not self.focus or any((s[2] for s in self.stack))
        ):
            self.parts.append(data)
            if self.anchor:
                self.anchor[1].append(data)

    def lines(self) -> list[str]:
        return clean_lines("".join(self.parts))


def description_lines(
    text: str, *, focus: bool = False
) -> tuple[list[str], list[tuple[str, str]], bool]:
    if re.search("<(?:p|div|html|br|ul|li|h[1-6]|section)\\b", text, re.I):
        parser = VacancyHTML(focus=focus)
        parser.feed(text)
        return (parser.lines(), parser.links, not focus or parser.focus_found)
    return (clean_lines(text), [], True)


def normalize(row: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    source, digest = (str(row["source_name"]), str(row["artifact_hash"]))
    facts: dict[str, Any] = {
        name: {"value": None, "status": "unknown", "provenance": []} for name in FIELDS
    }
    warnings: list[str] = []
    problems: list[str] = []
    conflicts: list[dict[str, Any]] = []
    raw_search, raw_detail = (payload.get("search"), payload.get("detail"))
    search: dict[str, Any] = raw_search if isinstance(raw_search, dict) else payload
    detail: dict[str, Any] = raw_detail if isinstance(raw_detail, dict) else {}

    def fact(
        name: str, value: object, path: str, *, method: str = "structured"
    ) -> None:
        if value is None or value == "" or value == []:
            return
        if isinstance(value, str):
            value = " ".join(clean_lines(value)).strip()
            if value.upper() in {
                "KEINE_ANGABEN",
                "KEINE ANGABEN",
                "NOT_SPECIFIED",
                "N/A",
            }:
                return
        if name == "application_url":
            value = public_url(value)
            if value is None:
                warnings.append("application_url_not_public_https")
                return
        if name in DATE_FIELDS:
            value = iso_date(value)
            if value is None:
                warnings.append(f"unparsed_date:{name}")
                return
        if not isinstance(value, str | list):
            warnings.append(f"unsupported_fact_type:{name}")
            return
        citation = {"artifact_hash": digest, "locator": path, "method": method}
        old = facts[name]
        if old["value"] is not None and old["value"] != value:
            conflicts.append(
                {
                    "field": name,
                    "values": [old["value"], value],
                    "provenance": [*old["provenance"], citation],
                }
            )
            return
        facts[name] = {"value": value, "status": "stated", "provenance": [citation]}

    containers = [
        (detail, "/detail"),
        (search, "/search" if "search" in payload else ""),
    ]
    mappings = {
        "title": ("title",),
        "employer": ("employer",),
        "working_time": ("working_time",),
        "remote_policy": ("remote_policy",),
        "contract_type": ("contract_type",),
        "publication_date": ("publication_date",),
        "application_deadline": ("application_deadline",),
        "salary": ("salary",),
        "application_url": ("application_url",),
        "reference_number": ("reference_number",),
    }
    for name, keys in mappings.items():
        for container, prefix in containers:
            key = next(
                (k for k in keys if container.get(k) not in (None, "", [])), None
            )
            if key:
                fact(name, container[key], prefix + "/" + key)
                break
    for container, prefix in containers:
        key = next((k for k in ("locations",) if container.get(k)), None)
        if key:
            fact(
                "locations",
                location_names(container[key]),
                prefix + "/" + key,
                method="deterministic",
            )
            evidence = container.get("location_evidence")
            if isinstance(evidence, dict) and all(
                (
                    isinstance(evidence.get(k), str) and evidence[k]
                    for k in ("artifact_hash", "locator", "quote", "method")
                )
            ):
                facts["locations"]["provenance"] = [dict(evidence)]
            break
    description, locator = ("", "")
    for container, prefix in [(detail, "/detail"), (payload, "")]:
        key = next(
            (
                k
                for k in ("description_text", "content", "description")
                if isinstance(container.get(k), str) and container[k].strip()
            ),
            None,
        )
        if key:
            description, locator = (container[key], prefix + "/" + key)
            break
    lines, links, focused = description_lines(description, focus=False)
    if not focused:
        problems.append("unrecognized_html_layout")
    if facts["application_url"]["value"] is None:
        for url, label in links:
            if re.fullmatch(
                "jetzt bewerben!?|bewerben|apply now|apply", label.strip(), re.I
            ) and public_url(url):
                fact("application_url", url, locator, method="deterministic")
                break
    blocks: list[dict[str, Any]] = []
    section = "unclassified"
    for number, line in enumerate(lines, 1):
        section = SECTIONS.get(line.casefold().rstrip(":"), section)
        blocks.append(
            {
                "id": f"b{number:03d}",
                "section": section,
                "text": line,
                "provenance": {
                    "artifact_hash": digest,
                    "locator": locator,
                    "clean_line": number,
                    "quote": line,
                },
            }
        )
    text = "\n".join((b["text"] for b in blocks))
    if not facts["title"]["value"]:
        problems.append("missing_title")
    if len(text) < 200:
        problems.append("insufficient_description")
    if re.search(
        "just a moment|verify you are human|additional verification required|access denied",
        text[:1800],
        re.I,
    ):
        problems.append("verification_content")
    if re.search(
        "(?:description truncated|text truncated|\\[truncated\\])", text, re.I
    ):
        problems.append("truncation_marker")
    if len(text) > MAX_ASSESSMENT_CHARACTERS:
        problems.append("assessment_budget_exceeded")
    if "�" in text or any((x in text for x in ("Ã¤", "Ã¶", "Ã¼"))):
        problems.append("encoding_problem")
    if conflicts:
        problems.append("conflicting_facts")
    if not facts["employer"]["value"]:
        warnings.append("employer_unknown")
    availability = (
        "unavailable" if payload.get("fetch_state") == "unavailable" else "unknown"
    )
    if availability == "unavailable":
        problems.append("vacancy_unavailable")
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "source": source,
        "external_id": str(row["external_id"]),
        "source_url": public_url(row["source_url"]),
        "facts": facts,
        "blocks": blocks,
        "quality": {
            "state": "needs_review" if problems else "ready",
            "reasons": sorted(set(problems)),
            "warnings": sorted(set(warnings)),
            "unknown_fields": [n for n in FIELDS if facts[n]["value"] is None],
            "conflicts": conflicts,
            "availability": availability,
            "description_characters": len(text),
            "raw_description_characters": len(description),
        },
    }
    semantic = {
        "schema_version": SCHEMA_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "source": source,
        "external_id": document["external_id"],
        "facts": {
            n: {"value": facts[n]["value"], "status": facts[n]["status"]}
            for n in FIELDS
        },
        "blocks": [
            {"id": b["id"], "text": b["text"], "section": b["section"]} for b in blocks
        ],
        "quality": {
            k: document["quality"][k]
            for k in ("state", "reasons", "warnings", "availability")
        },
    }
    content_hash = hashlib.sha256(encoded(semantic)).hexdigest()
    document["content_hash"] = content_hash
    document["version_id"] = "cv_" + content_hash
    return document


def iso_date(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    for candidate in (value[:10], value):
        try:
            return date.fromisoformat(candidate).isoformat()
        except ValueError:
            pass
    try:
        return datetime.strptime(value.strip()[:10], "%d.%m.%Y").date().isoformat()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (ValueError, TypeError):
        return None


def location_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        value = [value]
    result = []
    for location in value:
        if isinstance(location, str):
            result.append(location)
        elif isinstance(location, dict):
            data = location.get("adresse", location)
            if not isinstance(data, dict):
                continue
            parts = [
                str(data[k])
                for k in (
                    "PLZ",
                    "plz",
                    "postal_code",
                    "Ort",
                    "ort",
                    "city",
                    "country_code",
                )
                if data.get(k)
            ]
            if parts:
                result.append(" ".join(dict.fromkeys(parts)))
    return list(dict.fromkeys(result))


def assessment_packet(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "version_id": document["version_id"],
        "source": document["source"],
        "external_id": document["external_id"],
        "source_url": document["source_url"],
        "facts": {
            n: f["value"]
            for n, f in document["facts"].items()
            if f["value"] is not None
            and n
            not in {
                "responsibilities",
                "mandatory_requirements",
                "preferred_qualifications",
            }
        },
        "blocks": [{k: b[k] for k in ("id", "text")} for b in document["blocks"]],
        "unknown_fields": document["quality"]["unknown_fields"],
        "warnings": document["quality"]["warnings"],
    }


def legacy_fields(document: dict[str, Any]) -> dict[str, Any]:
    return {
        n: {
            "value": f["value"],
            "status": f["status"],
            "evidence": [
                {"artifact_hash": e["artifact_hash"], "locator": e["locator"]}
                for e in f["provenance"]
            ],
        }
        for n, f in document["facts"].items()
    }
