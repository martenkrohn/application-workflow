"""Structured vacancy extraction and grouping helpers used by the LID."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from contextlib import suppress
from copy import deepcopy
from datetime import datetime
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, cast

from job_system.domain.clean_vacancies import normalize


def observation_previews(
    data_dir: Path, rows: tuple[dict[str, Any], ...]
) -> tuple[dict[str, Any], ...]:
    return tuple((_preview(data_dir, row) for row in rows))


def discovery_visible_rows(
    data_dir: Path, rows: tuple[dict[str, Any], ...]
) -> tuple[dict[str, Any], ...]:
    return rows


def observation_screening_packet(data_dir: Path, row: dict[str, Any]) -> dict[str, Any]:
    root = data_dir.resolve()
    path = (root / str(row["relative_path"])).resolve()
    if not path.is_relative_to(root):
        raise ValueError("evidence must stay inside the data directory")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != row["artifact_hash"]:
        raise ValueError("evidence integrity mismatch")
    document = normalize(row, json.loads(content))
    facts = document["facts"]
    return {
        "source": document["source"],
        "external_id": document["external_id"],
        "source_url": document["source_url"],
        "title": facts["title"]["value"],
        "employer": facts["employer"]["value"],
        "locations": facts["locations"]["value"] or [],
        "description_excerpt": "\n".join((b["text"] for b in document["blocks"])),
        "detail_available": document["quality"]["state"] == "ready",
        "fetch_state": None,
    }


def _clean(value: str) -> str:
    text = unescape(unescape(value))
    text = re.sub("<(script|style)\\b[^>]*>.*?</\\1>", " ", text, flags=re.I | re.S)
    return " ".join(re.sub("<[^>]+>", " ", text).split())


def _preview(
    data_dir: Path,
    row: dict[str, Any],
    document: dict[str, Any] | None = None,
    *,
    content: bytes | None = None,
) -> dict[str, Any]:
    if content is None:
        content = (data_dir / str(row["relative_path"])).resolve().read_bytes()
    if hashlib.sha256(content).hexdigest() != row["artifact_hash"]:
        raise RuntimeError(f"artifact integrity failure for observation {row['id']}")
    title = employer = publication = deadline = None
    locations: list[str] = []
    coordinates = None
    if str(row["media_type"]) == "application/json":
        payload = json.loads(content)
        if isinstance(payload, dict):
            raw_detail, raw_search = (payload.get("detail"), payload.get("search"))
            detail = raw_detail if isinstance(raw_detail, dict) else payload
            search = raw_search if isinstance(raw_search, dict) else payload
            title = _first(
                detail, "stellenangebotsTitel", "adtitle", "title", "Stellenbezeichnung"
            ) or _first(
                search, "stellenangebotsTitel", "adtitle", "title", "stellenbezeichnung"
            )
            employer = _first(
                detail, "arbeitgeber", "name", "firma", "StellenangebotBehoerde"
            ) or _first(search, "firma", "name", "behoerdeName")
            raw_locations = (
                detail.get("arbeitsorte")
                or search.get("stellenlokationen")
                or detail.get("locations")
                or search.get("einsatzorte")
            )
            locations, coordinates = (
                _locations(raw_locations),
                _coordinates(raw_locations),
            )
            publication = _first(
                detail,
                "aktuelleVeroeffentlichungsdatum",
                "ersteVeroeffentlichungsdatum",
                "publication_date",
                "startdate",
                "DatumOeffentlichAusschreiben",
            ) or _first(search, "publication_date", "startdate", "datumErstellt")
            deadline = _first(
                detail,
                "bewerbungsfrist",
                "deadline_date",
                "enddate",
                "DatumBewerbungsfrist",
            ) or _first(search, "deadline_date", "enddate", "bewerbungsfrist")
            feed_xml = payload.get("feed_item_xml")
            if isinstance(feed_xml, str):
                feed = _feed(feed_xml)
                title, employer = (feed["title"] or title, feed["employer"] or employer)
                locations = [feed["location"]] if feed["location"] else locations
                publication, deadline = (
                    feed["publication_date"] or publication,
                    feed["application_deadline"] or deadline,
                )
            facts = (document if document is not None else normalize(row, payload))[
                "facts"
            ]
            locations = facts["locations"]["value"] or locations
            title = facts["title"]["value"] or title
            employer = facts["employer"]["value"] or employer
            deadline = facts["application_deadline"]["value"] or deadline
    elif "xml" in str(row["media_type"]):
        title = _xml(ET.fromstring(content), "title")
    return {
        "observation_id": row["id"],
        "source": row["source_name"],
        "external_id": row["external_id"],
        "title": title,
        "employer": employer,
        "locations": locations,
        "coordinates": coordinates,
        "source_url": row["source_url"],
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "publication_date": publication,
        "application_deadline": deadline,
    }


def _feed(content: str) -> dict[str, str | None]:
    root = ET.fromstring(content)
    plain = re.sub(
        "<[^>]+>",
        "",
        re.sub(
            "<br\\s*/?>",
            "\n",
            unescape(unescape(_xml(root, "description") or "")),
            flags=re.I,
        ),
    )

    def labeled(name: str) -> str | None:
        match = re.search(f"(?:^|\\n)\\s*{re.escape(name)}:\\s*([^\\n]+)", plain)
        return " ".join(match.group(1).split()) if match else None

    publication = _xml(root, "pubdate")
    if publication:
        with suppress(TypeError, ValueError):
            publication = parsedate_to_datetime(publication).date().isoformat()
    deadline = labeled("Bewerbungsfrist")
    if deadline:
        with suppress(ValueError):
            deadline = (
                datetime.strptime(deadline.split()[0], "%d.%m.%Y").date().isoformat()
            )
    return {
        "title": _xml(root, "title"),
        "employer": labeled("Arbeitgeber"),
        "location": labeled("Ort"),
        "publication_date": publication,
        "application_deadline": deadline,
        "description": " ".join(plain.split()),
    }


def assessment_groups(
    rows: tuple[dict[str, Any], ...],
) -> tuple[tuple[dict[str, Any], ...], ...]:
    vacancies = [json.loads(row["result_json"])["vacancy"] for row in rows]
    groups: list[list[int]] = []
    buckets: dict[tuple[str, str], list[list[int]]] = {}
    for index, vacancy in enumerate(vacancies):
        title = _normal(_value(vacancy, "title"))
        deadline = _value(vacancy, "application_deadline")
        if not title or not deadline:
            groups.append([index])
            continue
        candidates = buckets.setdefault((title, str(deadline)), [])
        for group in candidates:
            if any((_same(vacancy, vacancies[member]) for member in group)):
                group.append(index)
                break
        else:
            group = [index]
            candidates.append(group)
            groups.append(group)
    for group in groups:
        group.sort(key=lambda index: _rank(rows[index], vacancies[index]), reverse=True)
    groups.sort(key=lambda group: group[0])
    return tuple((tuple((rows[index] for index in group)) for group in groups))


def merged_assessment_result(group: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    merged = cast(dict[str, Any], deepcopy(json.loads(group[0]["result_json"])))
    target = merged["vacancy"]
    for member in group[1:]:
        for name, candidate in json.loads(member["result_json"])["vacancy"].items():
            current = target.get(name)
            if not isinstance(candidate, dict) or candidate.get("value") in (
                None,
                "",
                [],
            ):
                continue
            if not isinstance(current, dict) or current.get("value") in (None, "", []):
                target[name] = deepcopy(candidate)
            elif isinstance(current.get("value"), list) and isinstance(
                candidate.get("value"), list
            ):
                values = [*current["value"], *candidate["value"]]
                current["value"] = list(dict.fromkeys((str(value) for value in values)))
                evidence = [
                    *current.get("evidence", []),
                    *candidate.get("evidence", []),
                ]
                current["evidence"] = list(
                    {
                        (
                            str(item.get("artifact_hash", "")),
                            str(item.get("locator", "")),
                        ): item
                        for item in evidence
                    }.values()
                )
    return merged


def _same(lv: dict[str, Any], rv: dict[str, Any]) -> bool:
    lt, rt = (_normal(_value(lv, "title")), _normal(_value(rv, "title")))
    deadline = _value(lv, "application_deadline")
    return bool(
        lt
        and lt == rt
        and deadline
        and (deadline == _value(rv, "application_deadline"))
        and (_similar(_value(lv, "employer"), _value(rv, "employer")) >= 0.82)
        and (_similar(_value(lv, "locations"), _value(rv, "locations")) >= 0.75)
    )


def _rank(row: dict[str, Any], vacancy: dict[str, Any]) -> tuple[int, int, int, str]:
    complete = sum(
        (
            isinstance(field, dict) and field.get("value") not in (None, "", [])
            for field in vacancy.values()
        )
    )
    return (
        int(row["suitability_score"]),
        int(row["assessment_confidence"]),
        complete,
        str(row["updated_at"]),
    )


def _value(vacancy: dict[str, Any], name: str) -> Any:
    field = vacancy.get(name)
    return field.get("value") if isinstance(field, dict) else None


def _normal(value: object) -> str:
    if isinstance(value, list):
        value = " ".join((str(item) for item in value))
    return (
        " ".join(re.findall("[\\w]+", unicodedata.normalize("NFKC", value).casefold()))
        if isinstance(value, str)
        else ""
    )


def _similar(left: object, right: object) -> float:
    left, right = (_normal(left), _normal(right))
    return SequenceMatcher(None, left, right).ratio() if left and right else 0.0


def _first(payload: dict[str, Any], *keys: str) -> str | None:
    return next(
        (
            value.strip()
            for key in keys
            if isinstance((value := payload.get(key)), str) and value.strip()
        ),
        None,
    )


def _locations(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    output = []
    for entry in value:
        if isinstance(entry, str) and entry.strip():
            output.append(entry.strip())
            continue
        if not isinstance(entry, dict):
            continue
        raw = entry.get("adresse")
        address = raw if isinstance(raw, dict) else entry
        primary = " ".join(
            (
                item
                for item in (
                    _first(address, "plz", "zip"),
                    _first(address, "ort", "city"),
                )
                if item
            )
        )
        parts = [
            item.replace("_", " ").title()
            if not item.isupper() or len(item) > 3
            else item
            for item in (
                primary,
                _first(address, "region", "state"),
                _first(address, "land", "country_code"),
            )
            if item
        ]
        if parts:
            output.append(", ".join(dict.fromkeys(parts)))
    return output


def _coordinates(value: object) -> dict[str, float] | None:
    if not isinstance(value, list):
        return None
    for entry in value:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("adresse")
        candidates = [entry, raw] if isinstance(raw, dict) else [entry]
        for item in candidates:
            latitude, longitude = (
                item.get("breite", item.get("latitude", item.get("lat"))),
                item.get("laenge", item.get("longitude", item.get("lon"))),
            )
            try:
                if latitude is not None and longitude is not None:
                    return {
                        "latitude": float(str(latitude).replace(",", ".")),
                        "longitude": float(str(longitude).replace(",", ".")),
                    }
            except ValueError:
                continue
    return None


def _xml(root: ET.Element, name: str) -> str | None:
    return next(
        (
            element.text.strip()
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1].lower() == name and element.text
        ),
        None,
    )
