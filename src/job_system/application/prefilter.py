"""Transparent deterministic vacancy eligibility and ordering policy."""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PrefilterConfigurationError(RuntimeError):
    pass


def normalized(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = text.encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).split())


def _phrase(text: str, value: str) -> bool:
    return f" {value} " in f" {text} "


@dataclass(frozen=True, slots=True)
class PrefilterPolicy:
    positive_phrases: tuple[str, ...]
    hard_phrases: tuple[str, ...]
    hard_prefixes: tuple[str, ...]
    conditional_negative_phrases: tuple[str, ...]
    priority_negative_phrases: tuple[str, ...]
    excluded_cities: tuple[str, ...]
    southern_states: tuple[str, ...]
    southern_postcode_prefixes: tuple[str, ...]
    southern_major_cities: tuple[str, ...]
    remote_phrases: tuple[str, ...]

    @classmethod
    def load(cls, project_root: Path) -> PrefilterPolicy:
        path = project_root / "config" / "vacancy-prefilter.toml"
        try:
            payload = tomllib.loads(path.read_text(encoding="utf-8"))
            professional = payload["professional"]
            location = payload["location"]
            values = {
                "positive_phrases": professional["positive_phrases"],
                "hard_phrases": professional["hard_phrases"],
                "hard_prefixes": professional["hard_prefixes"],
                "conditional_negative_phrases": professional[
                    "conditional_negative_phrases"
                ],
                "priority_negative_phrases": professional["priority_negative_phrases"],
                "excluded_cities": location["excluded_cities"],
                "southern_states": location["southern_states"],
                "southern_postcode_prefixes": location["southern_postcode_prefixes"],
                "southern_major_cities": location["southern_major_cities"],
                "remote_phrases": location["remote_phrases"],
            }
        except (OSError, KeyError, tomllib.TOMLDecodeError, TypeError) as error:
            raise PrefilterConfigurationError(
                f"invalid vacancy prefilter policy: {path}"
            ) from error
        cleaned: dict[str, tuple[str, ...]] = {}
        for name, raw in values.items():
            if (
                not isinstance(raw, list)
                or not raw
                or not all(isinstance(item, str) for item in raw)
            ):
                raise PrefilterConfigurationError(
                    f"prefilter setting {name} must be a string list"
                )
            cleaned[name] = tuple(
                dict.fromkeys(normalized(item) for item in raw if normalized(item))
            )
        return cls(**cleaned)


@dataclass(frozen=True, slots=True)
class PrefilterDecision:
    exclusion_reason: str | None
    role_band: int
    location_band: int
    matched_positive: tuple[str, ...]
    matched_negative: tuple[str, ...]
    matched_hard: tuple[str, ...]


class VacancyPrefilter:
    def __init__(self, policy: PrefilterPolicy) -> None:
        self.policy = policy

    def evaluate(self, packet: dict[str, Any]) -> PrefilterDecision:
        title = normalized(packet.get("title"))
        positive = tuple(
            value for value in self.policy.positive_phrases if _phrase(title, value)
        )
        hard = tuple(
            value for value in self.policy.hard_phrases if _phrase(title, value)
        )
        prefixes = tuple(
            value for value in self.policy.hard_prefixes if title.startswith(value)
        )
        conditional = tuple(
            value
            for value in self.policy.conditional_negative_phrases
            if _phrase(title, value)
        )
        negative = tuple(
            value
            for value in self.policy.priority_negative_phrases
            if _phrase(title, value)
        )
        matched_hard = (*hard, *prefixes)
        if matched_hard:
            return PrefilterDecision(
                "professional_hard", 3, 2, positive, negative, matched_hard
            )
        if conditional and not positive:
            return PrefilterDecision(
                "professional_conditional_mismatch",
                3,
                2,
                positive,
                (*negative, *conditional),
                (),
            )

        location_reason, location_band = self._location(packet)
        role_band = 3 if conditional or negative else (1 if positive else 2)
        return PrefilterDecision(
            location_reason,
            role_band,
            location_band,
            positive,
            (*negative, *conditional),
            (),
        )

    def _location(self, packet: dict[str, Any]) -> tuple[str | None, int]:
        title_and_evidence = normalized(
            " ".join(
                (
                    str(packet.get("title") or ""),
                    str(packet.get("available_fields") or ""),
                    str(packet.get("description_excerpt") or "")[:1000],
                )
            )
        )
        if any(
            _phrase(title_and_evidence, phrase) for phrase in self.policy.remote_phrases
        ):
            return None, 1

        locations = [
            normalized(location)
            for location in packet.get("locations", ())
            if normalized(location)
        ]
        if not locations:
            return None, 2
        categories = tuple(self._location_category(location) for location in locations)
        if "preferred" in categories:
            return None, 1
        if "southern_city" in categories:
            return None, 3
        if all(category == "outside_germany" for category in categories):
            return "outside_germany", 2
        excluded = {"munich", "southern_nonmetro"}
        if categories and all(category in excluded for category in categories):
            reason = (
                "location_munich"
                if all(item == "munich" for item in categories)
                else "location_southern_nonmetro"
            )
            return reason, 3
        return None, 2

    def _location_category(self, location: str) -> str:
        if any(_phrase(location, city) for city in self.policy.excluded_cities):
            return "munich"
        if any(_phrase(location, city) for city in self.policy.southern_major_cities):
            return "southern_city"
        postcode = re.search(r"(?:^|\s)([0-9]{5})(?:\s|$)", location)
        southern_postcode = bool(
            postcode
            and any(
                postcode.group(1).startswith(prefix)
                for prefix in self.policy.southern_postcode_prefixes
            )
        )
        if southern_postcode or any(
            _phrase(location, state) for state in self.policy.southern_states
        ):
            return "southern_nonmetro"
        if location in {"deutschland", "germany", "bundesweit"}:
            return "unknown"
        if re.search(
            r"(?:^|\s)(?:at|be|ch|dk|es|fr|it|lu|nl|no|pl|se|uk|us)$", location
        ):
            return "outside_germany"
        if "deutschland" in location or re.search(
            r"(?:^|\s)[0-9]{5}(?:\s|$)", location
        ):
            return "preferred"
        if re.search(r"(?:^|\s)de$", location):
            return "preferred"
        return "unknown"
