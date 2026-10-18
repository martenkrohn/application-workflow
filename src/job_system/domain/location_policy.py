"""Shared geographic admission policy for fetching and quick screening only."""

from __future__ import annotations

import hashlib
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from job_system.domain.queue_policy import (
    LOCATION_GROUPS,
    location_priority,
    source_priority,
)

GROUPS = (
    "1 Primary / full remote",
    "2A Surroundings",
    "2B Easy return",
    "3 Large cities",
)


def _normal(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


@dataclass(frozen=True)
class LocationDecision:
    group: int | None
    reason: str
    city: str | None = None
    full_remote: bool = False

    @property
    def status(self) -> str:
        if self.group is not None:
            return "matched"
        return (
            "outside_scope" if self.reason == "location_outside_scope" else "unresolved"
        )

    @property
    def admitted(self) -> bool:
        return self.status != "outside_scope"


class LocationPolicy:
    def __init__(self, settings: dict[str, Any], fingerprint: str = "legacy") -> None:
        self.enabled = bool(settings.get("enabled", False))
        self.strict_locations_only = bool(settings.get("strict_locations_only", False))
        self.fingerprint = fingerprint
        self.labels = GROUPS if self.enabled else LOCATION_GROUPS
        if self.enabled and self.strict_locations_only:
            self.labels = ("1 Primary", *GROUPS[1:])
        self.cities: dict[str, tuple[int, str]] = {}
        for group, field in enumerate(
            ("primary", "surroundings", "easy_return", "large_cities")
        ):
            for city in settings.get(field, []):
                key = _normal(city)
                if key in self.cities:
                    raise ValueError(f"location appears in multiple groups: {city}")
                self.cities[key] = (group, city)
        for alias, city in settings.get("aliases", {}).items():
            if _normal(city) not in self.cities:
                raise ValueError(f"unknown location alias target: {city}")
            self.cities[_normal(alias)] = self.cities[_normal(city)]
        for workplace, city in settings.get("metropolitan_workplaces", {}).items():
            if _normal(city) not in self.cities:
                raise ValueError(f"unknown metropolitan workplace target: {city}")
            self.cities[_normal(workplace)] = self.cities[_normal(city)]
        self.preferred_sources = set(settings.get("preferred_sources", []))

    @classmethod
    def load(cls, root: Path) -> LocationPolicy:
        path = root / "config/location-policy.toml"
        if not path.exists():
            return cls({})
        raw = path.read_bytes()
        return cls(tomllib.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest())

    def source_rank(self, source: str) -> int:
        if not self.enabled:
            return source_priority(source)
        return 0 if source in self.preferred_sources else 1

    def rank(self, decision: LocationDecision) -> int:
        return decision.group if decision.group is not None else len(self.labels)

    def label(self, decision: LocationDecision) -> str:
        return (
            self.labels[decision.group]
            if decision.group is not None
            else "Location unresolved"
        )

    @staticmethod
    def _place(value: str) -> str:
        normal = _normal(value)
        normal = re.sub(r"\b\d{5}\b", "", normal)
        normal = re.sub(
            r"\b(deutschland|germany|de|hybrid|remote|homeoffice|vor ort|on site|in person)\b",
            "",
            normal,
        )
        normal = " ".join(normal.split())
        return re.sub(
            r"\s+(hh|sh|ni|nw|he|bw|by|be|bb|hb|mv|rp|sl|sn|st|th|westfalen|"
            r"nordrhein westfalen|bayern|baden wurttemberg|niedersachsen|schleswig holstein|"
            r"sachsen anhalt|sachsen|brandenburg|hessen|mecklenburg vorpommern)$",
            "",
            normal,
        )

    def classify(self, packet: dict[str, Any]) -> LocationDecision:
        if not self.enabled:
            return LocationDecision(
                location_priority(packet.get("locations")), "legacy"
            )
        locations = packet.get("locations") or []
        if isinstance(locations, str):
            locations = [locations]
        locations = [
            value for value in locations if isinstance(value, str) and value.strip()
        ]
        text = _normal(
            " ".join(
                [
                    str(packet.get("title") or ""),
                    str(packet.get("remote_policy") or ""),
                    *locations,
                    *(
                        str(block.get("text", ""))
                        for block in packet.get("work_arrangement_evidence", [])
                    ),
                ]
            )
        )
        remote = re.search(
            r"fully remote|full remote|100\s*(?:remote|homeoffice)|"
            r"vollstandig (?:remote|homeoffice)",
            text,
        )
        germany = re.search(
            r"\b(deutschland|deutschlandweit|germany|weltweit|worldwide|europa|europe)\b",
            text,
        ) or any(re.search(r"\bde$", _normal(value)) for value in locations)
        restricted = re.search(
            r"\b(us|usa|uk|united states|united kingdom) only\b", text
        )
        onsite = re.search(r"\b(vor ort|prasenzpflicht|on site|in person)\b", text)
        if re.search(
            r"keine prasenzpflicht|ohne prasenzpflicht|not.*on site|no on site|nicht.*vor ort",
            text,
        ):
            onsite = None
        negated = re.search(
            r"not fully remote|not full remote|nicht.*(?:100|vollstandig).*remote", text
        )
        hybrid = re.search(r"\bhybrid\b", text)
        incomplete = packet.get("work_arrangement_incomplete", False)
        if (
            not self.strict_locations_only
            and remote
            and germany
            and not (restricted or onsite or negated or hybrid or incomplete)
        ):
            return LocationDecision(0, "confirmed_full_remote", full_remote=True)
        remote_possible = re.search(
            r"\b(remote|homeoffice|hybrid|mobiles arbeiten)\b", text
        )
        uncertain = (
            not locations or bool(remote_possible) or bool(incomplete) or not onsite
        )
        found = []
        for value in locations:
            raw = _normal(value)
            if re.search(r"\b(at|ch|fr|us|uk|usa|united states|united kingdom)$", raw):
                continue
            # Regions and nearby workplaces are possibilities, not confirmed city workplaces.
            if re.search(
                r"\b(metropolregion|metropolitan|landkreis|district|region|bei|near)\b|"
                r"rhein main|berlin brandenburg",
                raw,
            ):
                uncertain = True
                continue
            normal = self._place(value)
            if normal in self.cities:
                found.append(self.cities[normal])
                continue
            parts = re.split(r"[,;/]|\s+(?:und|oder|and|or)\s+", value, flags=re.I)
            if len(parts) > 1 and not re.search(r"rhein main", raw):
                matches = [
                    self.cities[self._place(part)]
                    for part in parts
                    if self._place(part) in self.cities
                ]
                if matches:
                    found.extend(matches)
                    continue
            if (
                not normal
                or normal
                in {
                    "frankfurt",
                    "bundesweit",
                    "europa",
                    "europe",
                    "weltweit",
                    "worldwide",
                }
                or re.search(r"rhein main|berlin brandenburg|unknown|unbekannt", raw)
                or any(
                    re.search(rf"\b{re.escape(city)}\b", normal) for city in self.cities
                )
            ):
                uncertain = True
        if found:
            group, city = min(found)
            return LocationDecision(group, "approved_location", city)
        return LocationDecision(
            None,
            "location_needs_clarification"
            if uncertain and not self.strict_locations_only
            else "location_outside_scope",
        )


def location_packet(document: dict[str, Any]) -> dict[str, Any]:
    """Reuse cleaned facts/evidence without changing clean records or assessment inputs."""
    return {
        "title": document["facts"]["title"]["value"],
        "locations": document["facts"]["locations"]["value"] or [],
        "remote_policy": document["facts"]["remote_policy"]["value"],
        "work_arrangement_evidence": [
            b
            for b in document["blocks"]
            if re.search(
                r"remote|homeoffice|hybrid|mobil|vor ort|on.site|in person|präsenz",
                b["text"],
                re.I,
            )
        ],
    }
