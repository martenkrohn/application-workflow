"""Profile-specific agent assessment over complete per-vacancy evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_system.application.cleaning import CleaningService
from job_system.application.ports import Clock, IdGenerator
from job_system.application.prefilter import (
    PrefilterDecision,
    PrefilterPolicy,
    VacancyPrefilter,
)
from job_system.domain.clean_vacancies import (
    EXTRACTOR_VERSION,
    assessment_packet,
    legacy_fields,
)
from job_system.domain.location_policy import LocationPolicy, location_packet
from job_system.domain.queue_policy import older_than_backlog_window, received_at
from job_system.infrastructure.database import Database
from job_system.infrastructure.services import utc_text
from job_system.reporting.vacancies import observation_screening_packet

PROMPT_ID = "vacancy-assessment"
PROMPT_VERSION = 1
CLEAN_PROMPT_VERSION = 3
SCREENING_PROMPT_ID = "vacancy-screening"
SCREENING_WAVE_SIZE = 50
SCREENING_PROMOTION_SCORE = 75
VACANCY_BATCH_ATTEMPTS = 3
DEFAULT_PROFILE = Path("examples/profiles/demo/profile-v1.md")
FIELD_STATUSES = frozenset({"stated", "inferred", "unknown", "not_stated", "not_applicable"})
VACANCY_FIELDS = (
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
LIST_FIELDS = frozenset(
    {
        "locations",
        "responsibilities",
        "mandatory_requirements",
        "preferred_qualifications",
        "education_requirements",
        "experience_requirements",
        "methods_tools",
        "languages_required",
        "occupational_categories",
        "contact_details",
        "keywords",
    }
)
DATE_FIELDS = frozenset(
    {"contract_end_date", "start_date", "publication_date", "application_deadline"}
)
EVALUATION_KEYS = frozenset(
    {
        "suitability_score",
        "assessment_confidence",
        "recommendation",
        "summary",
        "strengths",
        "gaps",
        "deal_breakers",
        "missing_information",
        "score_components",
    }
)


class AssessmentError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ValidationResult:
    batch_id: str
    valid_count: int
    rejected_count: int
    errors: tuple[dict[str, Any], ...]
    valid_records: tuple[dict[str, Any], ...]


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _damaged_output_text(value: object) -> bool:
    if isinstance(value, dict):
        return any((_damaged_output_text(item) for item in value.values()))
    if isinstance(value, list):
        return any((_damaged_output_text(item) for item in value))
    if not isinstance(value, str):
        return False
    text = re.sub("https?://\\S+", "", value)
    return "�" in text or bool(re.search("[^\\W\\d_]\\?[^\\W\\d_]", text))


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f"{path.name}-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


class NoAssessmentCandidates(AssessmentError):
    """Raised when every collected observation is already assessed for the profile."""


def _vacancy_value(vacancy: dict[str, Any], name: str) -> Any:
    return vacancy[name]["value"]


def _vacancy_list(vacancy: dict[str, Any], name: str) -> list[str]:
    value = _vacancy_value(vacancy, name)
    return value if isinstance(value, list) else []


COMPONENTS = frozenset(
    {"role_fit", "education", "methods_tools", "experience", "practical_conditions"}
)
RECORD_KEYS = frozenset(
    {
        "batch_id",
        "work_item_id",
        "observation_id",
        "input_hash",
        "prompt_id",
        "prompt_version",
        "model_id",
        "status",
        "profile_id",
        "profile_version",
        "profile_hash",
        "vacancy",
        "evaluation",
        "suspicious_content",
    }
)
MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "â‚", "ðŸ", "�")


def _contains_mojibake(value: object) -> bool:
    if isinstance(value, str):
        return any((marker in value for marker in MOJIBAKE_MARKERS))
    if isinstance(value, dict):
        return any((_contains_mojibake(item) for item in value.values()))
    if isinstance(value, list):
        return any((_contains_mojibake(item) for item in value))
    return False


@dataclass(frozen=True, slots=True)
class ProfileDocument:
    profile_name: str
    version: int
    profile_id: str
    relative_path: str
    content_hash: str
    content: str

    @classmethod
    def load(cls, project_root: Path, relative_path: Path = DEFAULT_PROFILE) -> ProfileDocument:
        path = (project_root / relative_path).resolve()
        if not path.is_relative_to(project_root.resolve()) or not path.is_file():
            raise AssessmentError(f"screening profile not found inside project: {relative_path}")
        match = re.fullmatch("profile-v([1-9][0-9]*)", path.stem)
        if match is None or path.parent == project_root.resolve():
            raise AssessmentError("profile path must end in <name>/profile-v<number>.md")
        content_bytes = path.read_bytes()
        name = path.parent.name.lower()
        version = int(match.group(1))
        return cls(
            name,
            version,
            f"profile_{name}_v{version}",
            path.relative_to(project_root.resolve()).as_posix(),
            _sha256(content_bytes),
            content_bytes.decode("utf-8"),
        )


SCREENING_RECORD_KEYS = frozenset(
    {
        "batch_id",
        "work_item_id",
        "observation_id",
        "input_hash",
        "prompt_id",
        "prompt_version",
        "model_id",
        "status",
        "profile_id",
        "profile_version",
        "profile_hash",
        "suitability_score",
        "assessment_confidence",
        "decision",
        "summary",
        "suspicious_content",
    }
)


class ScreeningService:
    """Compact first-pass assessment used to rank every collected vacancy."""

    def __init__(
        self,
        project_root: Path,
        data_dir: Path,
        database: Database,
        clock: Clock,
        ids: IdGenerator,
        *,
        clean_input: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.data_dir = data_dir.resolve()
        self.database = database
        self.clock = clock
        self.ids = ids
        self.clean_input = clean_input
        self.cleaner = CleaningService(self.data_dir, self.database)
        self._packet_cache: dict[str, dict[str, Any]] = {}
        self._preview_only = False
        self.prefilter = VacancyPrefilter(PrefilterPolicy.load(self.project_root))
        self.location_policy = LocationPolicy.load(self.project_root)
        recovery_path = self.data_dir / "datasets/location-policy-recovery.json"
        recovery = (
            json.loads(recovery_path.read_text(encoding="utf-8")) if recovery_path.exists() else []
        )
        self._location_recovery = {
            (entry["source"], entry["external_id"], entry["clean_version_id"]) for entry in recovery
        }

    def prepare(
        self,
        source_names: tuple[str, ...],
        limit: int = SCREENING_WAVE_SIZE,
        profile_path: Path = DEFAULT_PROFILE,
        *,
        cycle_id: str | None = None,
    ) -> dict[str, Any]:
        if limit <= 0 or limit > SCREENING_WAVE_SIZE:
            raise AssessmentError(
                f"screening batch limit must be between 1 and {SCREENING_WAVE_SIZE}"
            )
        prompt_version = (6 if self.location_policy.enabled else 4) if self.clean_input else 2
        profile = AssessmentService(
            self.project_root, self.data_dir, self.database, self.clock, self.ids
        ).ensure_profile(profile_path)
        with self.database.unit_of_work() as work:
            available = work.normalization.screening_candidates(source_names, 2147483647)
            handled = work.normalization.handled_candidate_observations()
        ordered, selection = self._select_candidates(available, handled)
        candidates = ordered[:limit]
        if not candidates:
            raise NoAssessmentCandidates("no new or changed observations need quick screening")
        selected_bands = {
            f"L{location}R{role}": 0 for location in range(1, 4) for role in range(1, 4)
        }
        for candidate in candidates:
            selected_decision = self.prefilter.evaluate(self._packet(candidate))
            selected_bands[f"L{selected_decision.location_band}R{selected_decision.role_band}"] += 1
        cycle_id = cycle_id or self.ids.new("screening")
        batch_id = self.ids.new("aiw")
        relative_directory = Path("ai") / "batches" / batch_id
        directory = self.data_dir / relative_directory
        output_relative = Path("ai") / "staging" / batch_id / "results.jsonl"
        data_relative = self.data_dir.relative_to(self.project_root)
        manifest_items: list[dict[str, str]] = []
        prepared_items: list[dict[str, str]] = []
        shared_profile = None
        if self.clean_input:
            snapshot = _json_bytes(
                {
                    "profile_id": profile.profile_id,
                    "version": profile.version,
                    "content_hash": profile.content_hash,
                    "content": profile.content,
                }
            )
            _write_atomic(directory / "profile.json", snapshot)
            shared_profile = {"path": "profile.json", "hash": _sha256(snapshot)}
        for candidate in candidates:
            artifact_path = (self.data_dir / str(candidate["relative_path"])).resolve()
            if not artifact_path.is_relative_to(self.data_dir) or not artifact_path.is_file():
                raise AssessmentError(f"invalid evidence artifact for {candidate['id']}")
            content = artifact_path.read_bytes()
            if _sha256(content) != candidate["artifact_hash"]:
                raise AssessmentError(f"invalid evidence artifact for {candidate['id']}")
            work_item_id = self.ids.new("aiwitem")
            item = {
                "batch_id": batch_id,
                "work_item_id": work_item_id,
                "observation_id": candidate["id"],
                "source_name": candidate["source_name"],
                "external_id": candidate["external_id"],
                "artifact_hash": candidate["artifact_hash"],
                "profile": {
                    "profile_id": profile.profile_id,
                    "profile_name": profile.profile_name,
                    "version": profile.version,
                    "content_hash": profile.content_hash,
                    "content": profile.content,
                },
                "screening_packet": self._packet(candidate),
            }
            if self.clean_input:
                item.pop("profile")
                item["profile_hash"] = profile.content_hash
                item["screening_packet"] = self._prepared_packet(
                    self.cleaner.get(self._packet(candidate)["version_id"]),
                    prompt_version,
                )
            item_bytes = _json_bytes(item)
            input_hash = _sha256(item_bytes)
            relative_item = Path("items") / f"{work_item_id}.json"
            _write_atomic(directory / relative_item, item_bytes)
            manifest_items.append(
                {
                    "work_item_id": work_item_id,
                    "observation_id": str(candidate["id"]),
                    "path": relative_item.as_posix(),
                    "input_hash": input_hash,
                }
            )
            prepared_items.append(
                {
                    "id": work_item_id,
                    "observation_id": str(candidate["id"]),
                    "input_hash": input_hash,
                }
            )
        manifest: dict[str, Any] = {
            "batch_id": batch_id,
            "cycle_id": cycle_id,
            "task": SCREENING_PROMPT_ID,
            "prompt_id": SCREENING_PROMPT_ID,
            "prompt_version": prompt_version,
            "created_at": utc_text(self.clock.now()),
            "profile_id": profile.profile_id,
            "profile_version": profile.version,
            "profile_hash": profile.content_hash,
            "profile_path": profile.relative_path,
            "source_names": list(source_names),
            "expected_item_count": len(manifest_items),
            "items": manifest_items,
            "allowed_output_path": (data_relative / output_relative).as_posix(),
        }
        if shared_profile:
            manifest["profile_document"] = shared_profile
        if self.location_policy.enabled:
            manifest["quick_location_policy"] = {
                "hash": self.location_policy.fingerprint,
                "groups_in_priority_order": list(self.location_policy.labels),
                "approved_places": {
                    city: self.location_policy.labels[group]
                    for city, (group, _) in self.location_policy.cities.items()
                },
                "admitted_work_items": {
                    item["work_item_id"]: {
                        "group": self.location_policy.label(
                            self.location_policy.classify(self._packet(candidate))
                        )
                        if self.location_policy.classify(self._packet(candidate)).group is not None
                        else None,
                        "reason": self.location_policy.classify(self._packet(candidate)).reason,
                    }
                    for item, candidate in zip(manifest_items, candidates, strict=True)
                },
            }
        manifest_bytes = _json_bytes(manifest)
        manifest_hash = _sha256(manifest_bytes)
        _write_atomic(directory / "manifest.json", manifest_bytes)
        instruction = f"Quick-screening batch {batch_id} is ready.\nUse prompts/{SCREENING_PROMPT_ID}/v{prompt_version}/prompt.md\nInput: {(data_relative / relative_directory / 'manifest.json').as_posix()}\nOutput: {(data_relative / output_relative).as_posix()}\nThen run: jobs screening finish {batch_id}\n".encode()
        _write_atomic(directory / "instructions.txt", instruction)
        with self.database.unit_of_work() as work:
            work.normalization.create_batch(
                {
                    "id": batch_id,
                    "prompt_id": SCREENING_PROMPT_ID,
                    "prompt_version": prompt_version,
                    "manifest_hash": manifest_hash,
                    "created_at": manifest["created_at"],
                    "input_directory": relative_directory.as_posix(),
                    "output_path": output_relative.as_posix(),
                    "cycle_id": cycle_id,
                },
                tuple(prepared_items),
            )
        return {
            "batch_id": batch_id,
            "cycle_id": cycle_id,
            "profile_id": profile.profile_id,
            "item_count": len(prepared_items),
            "manifest_hash": manifest_hash,
            "excluded": selection["exclusions"],
            "selection": selection,
            "selected_bands": selected_bands,
            "instructions": instruction.decode().splitlines(),
        }

    def pool_summary(
        self,
        source_names: tuple[str, ...],
        profile_path: Path = DEFAULT_PROFILE,
        *,
        preview_limit: int | None = None,
    ) -> dict[str, Any]:
        profile = AssessmentService(
            self.project_root, self.data_dir, self.database, self.clock, self.ids
        ).ensure_profile(profile_path)
        with self.database.unit_of_work() as work:
            available = work.normalization.screening_candidates(source_names, 2147483647)
            handled = work.normalization.handled_candidate_observations()
        ordered, summary = self._select_candidates(available, handled)
        if preview_limit is not None:
            summary["selected_preview"] = self._queue_statistics(ordered[: max(0, preview_limit)])
        return summary | {"profile_id": profile.profile_id}

    def location_preview(
        self,
        source_names: tuple[str, ...],
        profile_path: Path = DEFAULT_PROFILE,
        limit: int = 30,
    ) -> dict[str, Any]:
        """Read saved facts without registering profiles, cleaning versions or AI batches."""
        ProfileDocument.load(self.project_root, profile_path)
        with self.database.unit_of_work() as work:
            rows = work.evidence.review(2147483647, source_names)
            candidates = work.normalization.screening_candidates(source_names, 2147483647)
            handled = work.normalization.handled_candidate_observations()
        counts = dict.fromkeys(
            (*self.location_policy.labels, "Outside scope", "Needs clarification"), 0
        )
        examples: dict[str, list[dict[str, Any]]] = {label: [] for label in counts}
        self._preview_only = True
        try:
            remote = 0
            for row in rows:
                packet = self._packet(row)
                decision = self.location_policy.classify(packet)
                label = (
                    self.location_policy.labels[decision.group]
                    if decision.group is not None
                    else "Needs clarification"
                    if decision.reason == "location_needs_clarification"
                    else "Outside scope"
                )
                counts[label] += 1
                remote += int(decision.full_remote)
                if len(examples[label]) < 10:
                    examples[label].append(
                        {
                            "source": row["source_name"],
                            "external_id": row["external_id"],
                            "title": packet.get("title"),
                            "locations": packet.get("locations"),
                            "reason": decision.reason,
                        }
                    )
            policy = self.location_policy
            try:
                self.location_policy = LocationPolicy({})
                _, baseline = self._select_candidates(candidates, handled)
            finally:
                self.location_policy = policy
            ordered, summary = self._select_candidates(candidates, handled)
            selected = [
                {
                    "source": row["source_name"],
                    "external_id": row["external_id"],
                    "title": self._packet(row).get("title"),
                    "locations": self._packet(row).get("locations"),
                    "group": self.location_policy.label(
                        self.location_policy.classify(self._packet(row))
                    ),
                }
                for row in ordered[:limit]
            ]
            return {
                "saved_vacancies": len(rows),
                "classifications": counts,
                "confirmed_full_remote": remote,
                "examples": examples,
                "quick_screening_pool": summary,
                "previous_policy_eligible_count": baseline["eligible_count"],
                "selected_limit": limit,
                "first_selected": selected,
                "network_requests": 0,
                "database_changes": 0,
                "location_policy_hash": self.location_policy.fingerprint,
            }
        finally:
            self._preview_only = False
            self._packet_cache.clear()

    def audit(
        self, source_names: tuple[str, ...], profile_path: Path = DEFAULT_PROFILE
    ) -> dict[str, Any]:
        profile = AssessmentService(
            self.project_root, self.data_dir, self.database, self.clock, self.ids
        ).ensure_profile(profile_path)
        with self.database.unit_of_work() as work:
            history = work.normalization.screening_history(profile.profile_id)
        rules: dict[str, dict[str, Any]] = {}
        for row in history:
            packet = self._packet(row)
            decision = self.prefilter.evaluate(packet)
            matches = [
                *((f"hard:{value}", "hard_exclude") for value in decision.matched_hard),
                *(
                    (
                        f"negative:{value}",
                        "hard_exclude"
                        if decision.exclusion_reason == "professional_conditional_mismatch"
                        and value in self.prefilter.policy.conditional_negative_phrases
                        else "R3",
                    )
                    for value in decision.matched_negative
                ),
            ]
            for name, action in matches:
                entry = rules.setdefault(
                    name,
                    {
                        "action": action,
                        "historical_matches": 0,
                        "score_50_plus": 0,
                        "score_75_plus": 0,
                        "samples": [],
                    },
                )
                score = int(row["suitability_score"])
                entry["historical_matches"] += 1
                entry["score_50_plus"] += int(score >= 50)
                entry["score_75_plus"] += int(score >= 75)
                if len(entry["samples"]) < 5:
                    entry["samples"].append(
                        {
                            "score": score,
                            "title": packet.get("title"),
                            "locations": packet.get("locations"),
                        }
                    )
        return {
            "profile_id": profile.profile_id,
            "historical_items": len(history),
            "current_pool": self.pool_summary(source_names, profile_path),
            "rules": dict(sorted(rules.items())),
        }

    def validate(self, batch_id: str) -> ValidationResult:
        batch, items, manifest = self._load_batch(batch_id)
        output = self._data_path(str(batch["output_path"]))
        if not output.is_file():
            raise AssessmentError(f"staged output not found: {batch['output_path']}")
        expected = {str(item["id"]): item for item in items}
        manifest_items = {str(item["work_item_id"]): item for item in manifest["items"]}
        valid: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        seen: set[str] = set()
        for line_number, line in enumerate(output.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append({"line": line_number, "errors": [f"invalid JSON: {error}"]})
                continue
            record_errors = self._validate_record(
                record, batch, expected, manifest_items, manifest, seen
            )
            if record_errors:
                errors.append(
                    {
                        "line": line_number,
                        "work_item_id": record.get("work_item_id")
                        if isinstance(record, dict)
                        else None,
                        "errors": record_errors,
                    }
                )
            else:
                valid.append(record)
                seen.add(str(record["work_item_id"]))
        missing = sorted(
            set(expected) - seen - {str(error.get("work_item_id")) for error in errors}
        )
        errors.extend(
            (
                {
                    "line": None,
                    "work_item_id": item_id,
                    "errors": ["missing result record"],
                }
                for item_id in missing
            )
        )
        invalid_ids = {error.get("work_item_id") for error in errors}
        valid = [record for record in valid if record["work_item_id"] not in invalid_ids]
        with self.database.unit_of_work() as work:
            for record in valid:
                if expected[str(record["work_item_id"])]["status"] != "imported":
                    work.normalization.set_item_status(str(record["work_item_id"]), "validated")
            if batch["status"] != "imported":
                work.normalization.set_batch_status(
                    batch_id, "validated" if not errors else "partial"
                )
        return ValidationResult(batch_id, len(valid), len(errors), tuple(errors), tuple(valid))

    def cancel_batch(self, batch_id: str, reason: str) -> dict[str, Any]:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise AssessmentError("screening cancellation reason must not be empty")
        with self.database.unit_of_work() as work:
            return work.normalization.cancel_assessment_batch(
                batch_id,
                utc_text(self.clock.now()),
                normalized_reason,
                SCREENING_PROMPT_ID,
            )

    def import_batch(self, batch_id: str, *, allow_partial: bool = False) -> dict[str, Any]:
        validation = self.validate(batch_id)
        if validation.rejected_count and (not allow_partial):
            raise AssessmentError("cannot import a screening batch with invalid records")
        batch, items, manifest = self._load_batch(batch_id)
        expected = {item["id"]: item for item in items}
        directory = self._data_path(str(batch["input_directory"]))
        paths = {
            str(item["work_item_id"]): directory / str(item["path"]) for item in manifest["items"]
        }
        timestamp = utc_text(self.clock.now())
        imported = 0
        unchanged = 0
        with self.database.unit_of_work() as work:
            for record in validation.valid_records:
                if expected[str(record["work_item_id"])]["status"] == "imported":
                    unchanged += 1
                    continue
                item = json.loads(paths[str(record["work_item_id"])].read_text(encoding="utf-8"))
                screening = {
                    "id": self.ids.new("screen"),
                    "observation_id": record["observation_id"],
                    "source_name": item["source_name"],
                    "external_id": item["external_id"],
                    "artifact_hash": item["artifact_hash"],
                    "profile_id": record["profile_id"],
                    "screening_cycle_id": manifest["cycle_id"],
                    "input_hash": record["input_hash"],
                    "suitability_score": record["suitability_score"],
                    "assessment_confidence": record["assessment_confidence"],
                    "decision": record["decision"],
                    "summary": record["summary"],
                    "result_json": json.dumps(record, ensure_ascii=False, sort_keys=True),
                    "prompt_id": record["prompt_id"],
                    "prompt_version": record["prompt_version"],
                    "model_id": record["model_id"],
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                changed = work.normalization.upsert_screening(screening)
                imported += int(changed)
                unchanged += int(not changed)
                work.normalization.set_item_status(str(record["work_item_id"]), "imported")
            work.normalization.set_batch_status(
                batch_id, "partial" if validation.rejected_count else "imported"
            )
            assert work.connection is not None
        return {
            "batch_id": batch_id,
            "profile_id": manifest["profile_id"],
            "screening_cycle_id": manifest["cycle_id"],
            "rejected": validation.rejected_count,
            "source_names": tuple((str(value) for value in manifest["source_names"])),
            "profile_path": str(manifest["profile_path"]),
            "imported": imported,
            "unchanged": unchanged,
        }

    def _validate_record(
        self,
        record: object,
        batch: dict[str, Any],
        expected: dict[str, dict[str, Any]],
        manifest_items: dict[str, dict[str, Any]],
        manifest: dict[str, Any],
        seen: set[str],
    ) -> list[str]:
        if not isinstance(record, dict):
            return ["result must be a JSON object"]
        errors: list[str] = []
        required = SCREENING_RECORD_KEYS | (
            {"clean_version_id", "exclusion_basis"} if batch["prompt_version"] >= 3 else set()
        )
        if batch["prompt_version"] >= 6:
            required |= {"location_assessment"}
        if set(record) != required:
            errors.append(
                f"result fields do not exactly match vacancy-screening schema v{batch['prompt_version']}"
            )
        if _contains_mojibake(record) or _damaged_output_text(record):
            errors.append("result text appears to contain incorrectly decoded UTF-8 (mojibake)")
        item_id = record.get("work_item_id")
        if not isinstance(item_id, str) or item_id not in expected:
            return ["unknown work_item_id"]
        if item_id in seen:
            return ["duplicate work_item_id"]
        checks = {
            "batch_id": batch["id"],
            "observation_id": expected[item_id]["observation_id"],
            "input_hash": expected[item_id]["input_hash"],
            "prompt_id": SCREENING_PROMPT_ID,
            "prompt_version": batch["prompt_version"],
            "profile_id": manifest["profile_id"],
            "profile_version": manifest["profile_version"],
            "profile_hash": manifest["profile_hash"],
        }
        for key, value in checks.items():
            if record.get(key) != value:
                errors.append(f"{key} does not match the prepared batch")
        item_path = self._data_path(str(batch["input_directory"])) / manifest_items[item_id]["path"]
        if _sha256(item_path.read_bytes()) != expected[item_id]["input_hash"]:
            return [*errors, "prepared input changed after batch creation"]
        for name in ("suitability_score", "assessment_confidence"):
            value = record.get(name)
            minimum = 1 if name == "suitability_score" and batch["prompt_version"] >= 4 else 0
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or (not minimum <= value <= 100)
            ):
                errors.append(f"{name} must be an integer from {minimum} to 100")
        if record.get("decision") not in {"promote", "deprioritize"}:
            errors.append("decision is invalid")
        score = record.get("suitability_score")
        if (
            record.get("decision") == "deprioritize"
            and isinstance(score, int)
            and (score >= SCREENING_PROMOTION_SCORE)
        ):
            errors.append(f"deprioritize requires a score below {SCREENING_PROMOTION_SCORE}")
        if (
            record.get("decision") == "promote"
            and isinstance(score, int)
            and (score < SCREENING_PROMOTION_SCORE)
        ):
            errors.append(f"promote requires a score of at least {SCREENING_PROMOTION_SCORE}")
        if record.get("status") != "completed":
            errors.append("status must be completed")
        if not isinstance(record.get("model_id"), str) or not record["model_id"]:
            errors.append("model_id must be a non-empty string")
        if not isinstance(record.get("summary"), str) or not record["summary"].strip():
            errors.append("summary must be a non-empty string")
        if not isinstance(record.get("suspicious_content"), bool):
            errors.append("suspicious_content must be boolean")
        if batch["prompt_version"] >= 3:
            item = json.loads(item_path.read_bytes())
            if record.get("clean_version_id") != item["screening_packet"]["version_id"]:
                errors.append("clean version does not match the prepared screening item")
            doc = self.cleaner.get(item["screening_packet"]["version_id"])
            needs_review = (
                manifest["quick_location_policy"]["admitted_work_items"][item_id]["group"] is None
                if batch["prompt_version"] >= 6
                else None
            )
            if (
                self._prepared_packet(doc, batch["prompt_version"], needs_review)
                != item["screening_packet"]
            ):
                errors.append("screening packet differs from immutable clean version")
            basis = record.get("exclusion_basis")
            if record.get("decision") == "promote" and basis is not None:
                errors.append("promoted record must have null exclusion_basis")
            if record.get("decision") == "deprioritize":
                if basis not in {"title", "location"}:
                    errors.append("deprioritization requires a title or location mismatch")
                elif not item["screening_packet"].get(
                    "title" if basis == "title" else "locations"
                ) and (basis != "location" or batch["prompt_version"] < 6):
                    errors.append("missing title/location cannot justify deprioritization")
                elif (
                    basis == "location"
                    and batch["prompt_version"] < 6
                    and (not self._explicit_onsite(item["screening_packet"]))
                ):
                    errors.append(
                        "location rejection requires explicit on-site attendance evidence"
                    )
            if batch["prompt_version"] >= 6:
                errors.extend(self._validate_location_assessment(record, item, manifest))
        return errors

    def _validate_location_assessment(
        self, record: dict[str, Any], item: dict[str, Any], manifest: dict[str, Any]
    ) -> list[str]:
        review = record.get("location_assessment")
        if not isinstance(review, dict) or set(review) != {
            "status",
            "group",
            "summary",
            "evidence_ids",
        }:
            return ["location_assessment fields are invalid"]
        errors = []
        status = review["status"]
        if not isinstance(status, str):
            return ["invalid location assessment status"]
        groups = manifest["quick_location_policy"]["groups_in_priority_order"]
        if status not in {"matched", "outside_scope", "unresolved"}:
            errors.append("invalid location assessment status")
        if (
            status == "matched"
            and review["group"] not in groups
            or (status != "matched" and review["group"] is not None)
        ):
            errors.append("location group does not match location status")
        if not isinstance(review["summary"], str) or not review["summary"].strip():
            errors.append("location assessment needs an explanation")
        packet = item["screening_packet"]
        evidence = {entry["id"]: entry["text"] for entry in packet["location_evidence"]}
        citations = review["evidence_ids"]
        if not isinstance(citations, list) or any(
            (not isinstance(value, str) or value not in evidence for value in citations)
        ):
            errors.append("location assessment cites unknown evidence")
            citations = []
        if len(citations) != len(set(citations)):
            errors.append("duplicate location evidence citations")
        if status in {"matched", "outside_scope"} and (not citations):
            errors.append("resolved location assessment requires saved evidence")
        admission = manifest["quick_location_policy"]["admitted_work_items"][item["work_item_id"]]
        if admission["group"] is not None and (
            status != "matched" or review["group"] != admission["group"]
        ):
            errors.append("confirmed policy location must retain its admitted group")
        if record.get("exclusion_basis") == "location" and status != "outside_scope":
            errors.append("uncertain or accepted location cannot justify rejection")
        if status == "outside_scope":
            cited = " ".join((evidence[value] for value in citations))
            has_onsite, conflicting = self._location_attendance_evidence(cited, evidence)
            if not has_onsite or conflicting or packet["location_evidence_incomplete"]:
                errors.append("location rejection requires complete cited workplace evidence")
            if record.get("decision") != "deprioritize":
                errors.append("outside-scope location must be explicitly deprioritized")
        if status == "unresolved" and record.get("exclusion_basis") == "location":
            errors.append("unresolved location must remain available for review")
        return errors

    @staticmethod
    def _location_attendance_evidence(cited: str, evidence: dict[str, str]) -> tuple[bool, bool]:
        fixed_days = "\\b(?:[1-6]|one|two|three|four|five|six|ein(?:en|e)?|zwei|drei|vier|fünf|sechs)\\s+(?:tage?|days?)\\s+(?:pro\\s+woche\\s+|per\\s+week\\s+)?(?:vor ort|on[- ]site|in (?:the )?office|office|im büro)\\b"
        required_presence = "\\bon[- ]site\\s+presence\\s+(?:is\\s+)?required\\b|\\b(?:mandatory|required)\\s+on[- ]site\\s+(?:presence|attendance)\\b"
        attendance = f"(?:{fixed_days}|{required_presence})"
        optional = "\\b(?:optional|freiwillig|bis zu|up to|keine präsenzpflicht|ohne präsenzpflicht|no (?:mandatory )?(?:on[- ]site|office)|not required|nicht erforderlich)\\b"
        required = bool(re.search(attendance, cited, re.I)) and (
            not re.search(optional, cited, re.I)
        )
        onsite = bool(re.search("vor ort|on[- ]site|in person|präsenzpflicht", cited, re.I))
        all_text = " ".join(evidence.values())
        if re.search(optional, all_text, re.I):
            return (False, True)
        full_remote = re.search(
            "fully remote|full remote|100\\s*%?\\s*(?:remote|homeoffice)|vollständig (?:remote|homeoffice)",
            all_text,
            re.I,
        )
        if required:
            labels = {"locations"}
            policy = evidence.get("remote_policy", "")
            if policy == evidence.get("locations") or re.fullmatch("hybrid", policy.strip(), re.I):
                labels.add("remote_policy")
            conflicting = bool(full_remote) or any(
                (
                    re.search("remote|homeoffice|hybrid|mobil", value, re.I)
                    and key not in labels
                    and (not re.fullmatch("\\s*Working Pattern:\\s*Hybrid\\s*", value, re.I))
                    and (not re.search(attendance, value, re.I))
                    for key, value in evidence.items()
                )
            )
            return (True, conflicting)
        return (
            onsite,
            bool(re.search("remote|homeoffice|hybrid|mobil", all_text, re.I)),
        )

    def _prepared_packet(
        self,
        document: dict[str, Any],
        prompt_version: int,
        needs_review: bool | None = None,
    ) -> dict[str, Any]:
        packet = self._thin_packet(document)
        if prompt_version < 6:
            return packet
        if needs_review is None:
            needs_review = self.location_policy.classify(location_packet(document)).group is None
        evidence = [
            {
                "id": field,
                "text": "; ".join(value) if isinstance(value, list) else str(value),
            }
            for field in ("title", "locations", "remote_policy")
            if (value := packet.get(field))
        ]
        incomplete = False
        if needs_review:
            relevant = [
                block
                for block in document["blocks"]
                if re.search(
                    "remote|homeoffice|hybrid|mobil|vor ort|on.site|in person|präsenz|standort|dienstort|arbeitsort|arbeitsplatz|büro|workplace|office|location|metropol|rhein.main",
                    block["text"],
                    re.I,
                )
            ]
            incomplete = len(relevant) > 8 or any((len(block["text"]) > 1200 for block in relevant))
            evidence.extend(
                ({"id": block["id"], "text": block["text"][:1200]} for block in relevant[:8])
            )
        packet["location_evidence"] = evidence
        packet["location_evidence_incomplete"] = incomplete
        packet["location_review_required"] = needs_review
        return packet

    def _load_batch(
        self, batch_id: str
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], dict[str, Any]]:
        with self.database.unit_of_work() as work:
            batch = work.normalization.batch(batch_id)
            items = work.normalization.batch_items(batch_id)
        if batch is None or batch["prompt_id"] != SCREENING_PROMPT_ID:
            raise AssessmentError(f"screening batch not found: {batch_id}")
        if batch["status"] == "cancelled":
            raise AssessmentError(f"screening batch is cancelled: {batch_id}")
        manifest_path = self._data_path(str(batch["input_directory"])) / "manifest.json"
        content = manifest_path.read_bytes()
        if _sha256(content) != batch["manifest_hash"]:
            raise AssessmentError("batch manifest changed after preparation")
        manifest = json.loads(content)
        if batch["prompt_version"] >= 3:
            shared = manifest["profile_document"]
            path = (manifest_path.parent / shared["path"]).resolve()
            if not path.is_relative_to(manifest_path.parent):
                raise AssessmentError("shared screening profile path escapes the batch")
            raw = path.read_bytes()
            if _sha256(raw) != shared["hash"]:
                raise AssessmentError("shared screening profile changed after preparation")
            if _sha256(json.loads(raw)["content"].encode()) != manifest["profile_hash"]:
                raise AssessmentError("shared screening profile does not match the profile")
        with self.database.unit_of_work() as work:
            registered = work.normalization.profile_version(str(manifest["profile_id"]))
        if registered is None or (
            registered["version"] != manifest["profile_version"]
            or registered["content_hash"] != manifest["profile_hash"]
            or registered["relative_path"] != manifest["profile_path"]
        ):
            raise AssessmentError(
                f"batch profile {manifest['profile_id']} does not match the immutable profile registry"
            )
        return (batch, items, manifest)

    def _data_path(self, relative: str) -> Path:
        path = (self.data_dir / relative).resolve()
        if not path.is_relative_to(self.data_dir):
            raise AssessmentError("assessment path escapes data_dir")
        return path

    @staticmethod
    def _thin_packet(document: dict[str, Any]) -> dict[str, Any]:
        facts = document["facts"]
        arrangement_blocks = [
            b
            for b in document["blocks"]
            if re.search(
                "remote|homeoffice|mobiles arbeiten|work location|on.site|vor ort|präsenz",
                b["text"],
                re.I,
            )
        ]
        snippets = [
            {"id": b["id"], "text": b["text"]} for b in arrangement_blocks if len(b["text"]) <= 160
        ][:3]
        return {
            "version_id": document["version_id"],
            **{
                name: facts[name]["value"]
                for name in (
                    "title",
                    "employer",
                    "locations",
                    "remote_policy",
                    "working_time",
                )
            },
            "work_arrangement_evidence": snippets,
            "work_arrangement_incomplete": len(snippets) < len(arrangement_blocks),
        }

    @staticmethod
    def _explicit_onsite(packet: dict[str, Any]) -> bool:
        if packet.get("work_arrangement_incomplete"):
            return False
        texts = [
            packet.get("remote_policy") or "",
            *(b["text"] for b in packet.get("work_arrangement_evidence", [])),
        ]
        if any((re.search("remote|homeoffice|hybrid|mobil", text, re.I) for text in texts)):
            return False
        return any(
            (
                re.fullmatch(
                    "work location:\\s*in person|on[- ]site|vor ort|präsenzpflicht",
                    text.strip(),
                    re.I,
                )
                for text in texts
            )
        )

    def _packet(self, row: dict[str, Any]) -> dict[str, Any]:
        if not self.clean_input:
            return observation_screening_packet(self.data_dir, row)
        key = str(row["id"])
        if key in self._packet_cache:
            return self._packet_cache[key]
        if self._preview_only:
            with self.database.unit_of_work(read_only=True) as work:
                assert work.connection is not None
                version = work.connection.execute(
                    "SELECT version_id FROM clean_observation_versions WHERE observation_id=? AND extractor_version=?",
                    (key, EXTRACTOR_VERSION),
                ).fetchone()
            doc = self.cleaner.get(version["version_id"]) if version else self.cleaner.preview(row)
        else:
            doc = self.cleaner.current(row)
        packet = self._thin_packet(doc)
        if self.location_policy.enabled:
            packet.update(location_packet(doc))
            packet["work_arrangement_incomplete"] = False
        packet.update(
            {
                "locations": packet["locations"] or [],
                "source": doc["source"],
                "external_id": doc["external_id"],
                "publication_date": doc["facts"]["publication_date"]["value"],
                "application_deadline": doc["facts"]["application_deadline"]["value"],
                "quality_reasons": doc["quality"]["reasons"],
                "fetch_state": doc["quality"]["availability"],
                "available_fields": {
                    "remote_policy": packet["remote_policy"],
                    "work_arrangement_evidence": packet["work_arrangement_evidence"],
                },
                "description_excerpt": "",
            }
        )
        self._packet_cache[key] = packet
        return packet

    def _select_candidates(
        self,
        candidates: tuple[dict[str, Any], ...],
        handled: tuple[dict[str, Any], ...],
    ) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
        exclusions: defaultdict[str, int] = defaultdict(int)
        handled_keys = {
            key
            for candidate in handled
            if (key := self._exact_duplicate_key(self._packet(candidate)))
        }
        prepared: list[tuple[dict[str, Any], dict[str, Any], PrefilterDecision, int, int]] = []
        for candidate in candidates:
            if (
                older_than_backlog_window(
                    str(candidate.get("discovered_at", candidate["first_seen_at"])),
                    self.clock.now(),
                )
                and (
                    str(candidate["source_name"]),
                    str(candidate["external_id"]),
                    str(candidate.get("clean_version_id", "")),
                )
                not in self._location_recovery
            ):
                exclusions["older_than_7_days"] += 1
                continue
            packet = self._packet(candidate)
            location = self.location_policy.classify(packet)
            if not location.admitted:
                exclusions[location.reason] += 1
                continue
            reason = self._exclusion_reason(candidate, packet)
            if reason:
                exclusions[reason] += 1
                continue
            duplicate_key = self._exact_duplicate_key(packet)
            if duplicate_key and duplicate_key in handled_keys:
                exclusions["exact_duplicate"] += 1
                continue
            decision = self.prefilter.evaluate(packet)
            if (
                self.location_policy.enabled
                and decision.exclusion_reason
                and (
                    decision.exclusion_reason.startswith("location_")
                    or decision.exclusion_reason == "outside_germany"
                )
            ):
                decision = PrefilterDecision(
                    None,
                    decision.role_band,
                    decision.location_band,
                    decision.matched_positive,
                    decision.matched_negative,
                    decision.matched_hard,
                )
            if self.clean_input and (
                decision.exclusion_reason == "professional_conditional_mismatch"
                or (
                    decision.exclusion_reason
                    and (
                        decision.exclusion_reason.startswith("location_")
                        or decision.exclusion_reason == "outside_germany"
                    )
                    and (not self._explicit_onsite(packet))
                )
            ):
                decision = PrefilterDecision(
                    None,
                    decision.role_band,
                    decision.location_band,
                    decision.matched_positive,
                    decision.matched_negative,
                    decision.matched_hard,
                )
            if decision.exclusion_reason:
                exclusions[decision.exclusion_reason] += 1
                continue
            priority = self._priority(packet)
            richness = sum(
                (
                    bool(packet.get(field))
                    for field in (
                        "title",
                        "employer",
                        "locations",
                        "publication_date",
                        "description_excerpt",
                    )
                )
            )
            prepared.append((candidate, packet, decision, priority, richness))
        prepared.sort(
            key=lambda value: (
                str(value[0]["source_name"]),
                str(value[0]["external_id"]),
            )
        )
        prepared.sort(key=lambda value: value[4], reverse=True)
        prepared.sort(key=lambda value: value[3], reverse=True)
        prepared.sort(key=lambda value: value[2].role_band)
        prepared.sort(
            key=lambda value: (
                parsed.timestamp()
                if (parsed := received_at(str(value[0]["first_seen_at"]))) is not None
                else float("-inf")
            ),
            reverse=True,
        )
        prepared.sort(
            key=lambda value: self.location_policy.source_rank(str(value[0]["source_name"]))
        )
        prepared.sort(
            key=lambda value: self.location_policy.rank(self.location_policy.classify(value[1]))
        )
        unique: list[tuple[dict[str, Any], dict[str, Any], PrefilterDecision, int, int]] = []
        seen_keys = set(handled_keys)
        for value in prepared:
            duplicate_key = self._exact_duplicate_key(value[1])
            if duplicate_key and duplicate_key in seen_keys:
                exclusions["exact_duplicate"] += 1
                continue
            if duplicate_key:
                seen_keys.add(duplicate_key)
            unique.append(value)
        ordered = tuple((value[0] for value in unique))
        bands = {f"L{location}R{role}": 0 for location in range(1, 4) for role in range(1, 4)}
        for _, _, decision, _, _ in unique:
            bands[f"L{decision.location_band}R{decision.role_band}"] += 1
        statistics = self._queue_statistics(ordered)
        return (
            ordered,
            {
                "eligible_count": len(ordered),
                "location_policy_hash": self.location_policy.fingerprint,
                "exclusions": dict(sorted(exclusions.items())),
                "bands": bands,
                **statistics,
                "limit_previews": {
                    str(limit): self._queue_statistics(ordered[:limit])
                    for limit in sorted({min(limit, len(ordered)) for limit in (10, 30, 50, 100)})
                    if limit > 0
                },
            },
        )

    def automatic_exclusion_reason(self, candidate: dict[str, Any]) -> str | None:
        """Explain policy exclusion using the same rules as automatic selection.

        Duplicate visibility and existing/queued assessments are handled by the caller.
        """
        _, summary = self._select_candidates((candidate,), ())
        return next(iter(summary["exclusions"]), None)

    def _queue_statistics(self, ordered: tuple[dict[str, Any], ...]) -> dict[str, Any]:
        sources = sorted(
            {str(row["source_name"]) for row in ordered},
            key=lambda source: (self.location_policy.source_rank(source), source),
        )
        labels = (*self.location_policy.labels, "Location unresolved")
        locations = {label: 0 for label in labels}
        location_sources = {label: dict.fromkeys(sources, 0) for label in labels}
        ages = dict.fromkeys(
            ("0-24 hours", "1-3 days", "3-7 days", "Recovered backlog", "Unknown age"),
            0,
        )
        dates = []
        full_remote = 0
        for row in ordered:
            decision = self.location_policy.classify(self._packet(row))
            label = self.location_policy.label(decision)
            full_remote += int(decision.full_remote)
            locations[label] += 1
            location_sources[label][str(row["source_name"])] += 1
            parsed = received_at(str(row["first_seen_at"]))
            if parsed is None:
                ages["Unknown age"] += 1
            else:
                dates.append(parsed)
                days = (self.clock.now() - parsed).total_seconds() / 86400
                ages[
                    "0-24 hours"
                    if days <= 1
                    else "1-3 days"
                    if days <= 3
                    else "3-7 days"
                    if days <= 7
                    else "Recovered backlog"
                ] += 1
        return {
            "count": len(ordered),
            "confirmed_full_remote": full_remote,
            "by_location": locations,
            "location_sources": location_sources,
            "by_source": {
                source: sum((row["source_name"] == source for row in ordered)) for source in sources
            },
            "by_age": ages,
            "oldest_received_at": utc_text(min(dates)) if dates else None,
            "newest_received_at": utc_text(max(dates)) if dates else None,
        }

    def _exclusion_reason(self, candidate: dict[str, Any], packet: dict[str, Any]) -> str | None:
        if packet.get("fetch_state") == "unavailable":
            return "vacancy_unavailable"
        if self.clean_input:
            if packet.get("fetch_state") == "blocked":
                return "awaiting_verification"
            reasons = set(packet.get("quality_reasons", []))
            if reasons:
                return "clean_data_needs_review"
        if packet.get("detail_available") is False:
            return "awaiting_vacancy_detail"
        deadline = packet.get("application_deadline")
        if isinstance(deadline, str):
            try:
                if date.fromisoformat(deadline[:10]) < self.clock.now().date():
                    return "expired"
            except ValueError:
                pass
        return None

    @staticmethod
    def _normalized(value: object) -> str:
        text = unicodedata.normalize("NFKD", str(value or ""))
        return " ".join(re.sub("[^a-z0-9]+", " ", text.lower()).split())

    @classmethod
    def _exact_duplicate_key(
        cls, packet: dict[str, Any]
    ) -> tuple[str, str, tuple[str, ...]] | None:
        title = cls._normalized(packet.get("title"))
        employer = cls._normalized(packet.get("employer"))
        locations = tuple(
            sorted(
                (cls._normalized(location) for location in packet.get("locations", ()) if location)
            )
        )
        return (employer, title, locations) if employer and title and locations else None

    @classmethod
    def _priority(cls, packet: dict[str, Any]) -> int:
        title = cls._normalized(packet.get("title"))
        signals = (
            "economist",
            "volkswirt",
            "okonom",
            "research analyst",
            "policy analyst",
            "data analyst",
            "economic consultant",
            "business analyst",
            "business analysis",
            "business analytics",
            "business intelligence",
            "controlling",
            "controller",
            "operations analyst",
            "operations analysis",
            "operations analytics",
            "process analyst",
            "process analysis",
            "reporting analyst",
            "forecasting",
            "wissenschaftlicher mitarbeiter",
        )
        return int(any((signal in title for signal in signals)))


class AssessmentService:
    def __init__(
        self,
        project_root: Path,
        data_dir: Path,
        database: Database,
        clock: Clock,
        ids: IdGenerator,
        *,
        clean_input: bool = True,
    ) -> None:
        self.project_root = project_root.resolve()
        self.data_dir = data_dir.resolve()
        self.database = database
        self.clock = clock
        self.ids = ids
        self.clean_input = clean_input

    def prepare(
        self,
        source_names: tuple[str, ...],
        limit: int = 25,
        profile_path: Path = DEFAULT_PROFILE,
        *,
        identities: tuple[tuple[str, str], ...] | None = None,
    ) -> dict[str, Any]:
        if limit <= 0 or limit > 25:
            raise AssessmentError("assessment batch limit must be between 1 and 25")
        if identities is not None and (not identities or len(identities) > 25):
            raise AssessmentError("selected assessment batch must contain between 1 and 25 jobs")
        profile = self.ensure_profile(profile_path)
        with self.database.unit_of_work() as work:
            candidates = (
                work.normalization.assessment_candidates_by_identity(identities)
                if identities is not None
                else work.normalization.assessment_candidates(
                    source_names, 100000 if self.clean_input else limit
                )
            )
        if not candidates:
            raise NoAssessmentCandidates("no new or changed observations need profile assessment")
        clean_documents: dict[str, dict[str, Any]] = {}
        skipped: list[dict[str, Any]] = []
        prompt_version = CLEAN_PROMPT_VERSION if self.clean_input else PROMPT_VERSION
        if self.clean_input:
            cleaner = CleaningService(self.data_dir, self.database)
            location_policy = LocationPolicy.load(self.project_root)
            ready_candidates = []
            for candidate in candidates:
                document = cleaner.current(candidate)
                reason = None
                if (
                    location_policy.enabled
                    and location_policy.strict_locations_only
                    and (not location_policy.classify(location_packet(document)).admitted)
                ):
                    reason = "location_outside_scope"
                elif document["quality"]["state"] != "ready":
                    reason = ", ".join(document["quality"]["reasons"])
                else:
                    with self.database.unit_of_work() as work:
                        assert work.connection is not None
                        assessed = work.connection.execute(
                            "SELECT 1 FROM clean_assessment_history WHERE version_id=? LIMIT 1",
                            (document["version_id"],),
                        ).fetchone()
                    if assessed:
                        reason = "unchanged clean version already assessed"
                if reason:
                    skipped.append(
                        {
                            "source": candidate["source_name"],
                            "external_id": candidate["external_id"],
                            "reason": reason,
                        }
                    )
                    if identities is not None:
                        raise AssessmentError(
                            f"Selected vacancy is not ready for assessment: {reason}"
                        )
                    continue
                clean_documents[candidate["id"]] = document
                ready_candidates.append(candidate)
                if len(ready_candidates) == limit:
                    break
            candidates = tuple(ready_candidates)
            if not candidates:
                raise NoAssessmentCandidates(
                    f"no ready clean vacancies; {len(skipped)} records need fetching/review or already have an assessment for this clean version"
                )
        batch_id = self.ids.new("aiw")
        relative_directory = Path("ai") / "batches" / batch_id
        directory = self.data_dir / relative_directory
        output_relative = Path("ai") / "staging" / batch_id / "results.jsonl"
        data_relative = self.data_dir.relative_to(self.project_root)
        workspace_directory = data_relative / relative_directory
        workspace_output = data_relative / output_relative
        prepared_items: list[dict[str, str]] = []
        manifest_items: list[dict[str, str]] = []
        shared_profile = None
        if self.clean_input:
            profile_bytes = _json_bytes(
                {
                    "profile_id": profile.profile_id,
                    "version": profile.version,
                    "content_hash": profile.content_hash,
                    "content": profile.content,
                }
            )
            _write_atomic(directory / "profile.json", profile_bytes)
            shared_profile = {"path": "profile.json", "hash": _sha256(profile_bytes)}
        for candidate in candidates:
            artifact_path = (self.data_dir / str(candidate["relative_path"])).resolve()
            if not artifact_path.is_relative_to(self.data_dir) or not artifact_path.is_file():
                raise AssessmentError(f"missing evidence artifact for {candidate['id']}")
            content = artifact_path.read_bytes()
            if _sha256(content) != candidate["artifact_hash"]:
                raise AssessmentError(f"evidence hash mismatch for {candidate['id']}")
            work_item_id = self.ids.new("aiwitem")
            item = {
                "batch_id": batch_id,
                "work_item_id": work_item_id,
                "observation_id": candidate["id"],
                "source_name": candidate["source_name"],
                "external_id": candidate["external_id"],
                "source_url": candidate["source_url"],
                "profile": {
                    "profile_id": profile.profile_id,
                    "profile_name": profile.profile_name,
                    "version": profile.version,
                    "content_hash": profile.content_hash,
                    "content": profile.content,
                },
                "evidence": [
                    {
                        "artifact_hash": candidate["artifact_hash"],
                        "role": "complete_vacancy_record",
                        "media_type": candidate["media_type"],
                        "content": content.decode("utf-8", errors="replace"),
                    }
                ],
            }
            if self.clean_input:
                document = clean_documents[candidate["id"]]
                item = {
                    "batch_id": batch_id,
                    "work_item_id": work_item_id,
                    "observation_id": candidate["id"],
                    "source_name": candidate["source_name"],
                    "external_id": candidate["external_id"],
                    "raw_artifact_hash": candidate["artifact_hash"],
                    "profile_hash": profile.content_hash,
                    "vacancy": assessment_packet(document),
                }
            item_bytes = _json_bytes(item)
            input_hash = _sha256(item_bytes)
            relative_item = Path("items") / f"{work_item_id}.json"
            _write_atomic(directory / relative_item, item_bytes)
            manifest_items.append(
                {
                    "work_item_id": work_item_id,
                    "observation_id": candidate["id"],
                    "path": relative_item.as_posix(),
                    "input_hash": input_hash,
                }
            )
            prepared_items.append(
                {
                    "id": work_item_id,
                    "observation_id": candidate["id"],
                    "input_hash": input_hash,
                }
            )
        manifest = {
            "batch_id": batch_id,
            "task": PROMPT_ID,
            "prompt_id": PROMPT_ID,
            "prompt_version": prompt_version,
            "created_at": utc_text(self.clock.now()),
            "profile_id": profile.profile_id,
            "profile_version": profile.version,
            "profile_hash": profile.content_hash,
            "profile_path": profile.relative_path,
            "expected_item_count": len(manifest_items),
            "items": manifest_items,
            "allowed_output_path": workspace_output.as_posix(),
        }
        if shared_profile:
            manifest["profile_document"] = shared_profile
        manifest_bytes = _json_bytes(manifest)
        manifest_hash = _sha256(manifest_bytes)
        _write_atomic(directory / "manifest.json", manifest_bytes)
        instruction = f"Assessment batch {batch_id} is ready.\nUse prompts/{PROMPT_ID}/v{prompt_version}/prompt.md\nInput: {(workspace_directory / 'manifest.json').as_posix()}\nOutput: {workspace_output.as_posix()}\nValidate: jobs assessment validate {batch_id}\nAfter validation succeeds, import: jobs assessment finish {batch_id}\n".encode()
        _write_atomic(directory / "instructions.txt", instruction)
        batch = {
            "id": batch_id,
            "prompt_id": PROMPT_ID,
            "prompt_version": prompt_version,
            "manifest_hash": manifest_hash,
            "created_at": manifest["created_at"],
            "input_directory": relative_directory.as_posix(),
            "output_path": output_relative.as_posix(),
        }
        with self.database.unit_of_work() as work:
            work.normalization.create_batch(batch, tuple(prepared_items))
            if self.clean_input:
                assert work.connection is not None
                for prepared_item in prepared_items:
                    work.connection.execute(
                        "INSERT INTO clean_assessment_items VALUES (?,?)",
                        (
                            prepared_item["id"],
                            clean_documents[prepared_item["observation_id"]]["version_id"],
                        ),
                    )
        return {
            "batch_id": batch_id,
            "profile_id": profile.profile_id,
            "profile_hash": profile.content_hash,
            "item_count": len(prepared_items),
            "manifest_hash": manifest_hash,
            "instructions": instruction.decode().splitlines(),
            "skipped": skipped,
        }

    def ensure_profile(self, profile_path: Path = DEFAULT_PROFILE) -> ProfileDocument:
        profile = ProfileDocument.load(self.project_root, profile_path)
        registered_at = utc_text(self.clock.now())
        with self.database.unit_of_work() as work:
            work.normalization.register_profile(
                {
                    "id": profile.profile_id,
                    "profile_name": profile.profile_name,
                    "version": profile.version,
                    "content_hash": profile.content_hash,
                    "relative_path": profile.relative_path,
                    "created_at": registered_at,
                }
            )
        return profile

    def profile_status(self, profile_path: Path = DEFAULT_PROFILE) -> dict[str, Any]:
        profile = ProfileDocument.load(self.project_root, profile_path)
        with self.database.unit_of_work() as work:
            registered = work.normalization.profile_version(profile.profile_id)
        if registered is None:
            return {
                "status": "unregistered",
                "profile_id": profile.profile_id,
                "profile_version": profile.version,
                "path": profile.relative_path,
                "current_hash": profile.content_hash,
                "registered_hash": None,
                "matches": None,
            }
        matches = (
            registered["profile_name"] == profile.profile_name
            and registered["version"] == profile.version
            and (registered["relative_path"] == profile.relative_path)
            and (registered["content_hash"] == profile.content_hash)
        )
        return {
            "status": "matching" if matches else "changed",
            "profile_id": profile.profile_id,
            "profile_version": profile.version,
            "path": profile.relative_path,
            "current_hash": profile.content_hash,
            "registered_hash": registered["content_hash"],
            "registered_path": registered["relative_path"],
            "matches": matches,
        }

    def cancel_batch(self, batch_id: str, reason: str) -> dict[str, Any]:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise AssessmentError("assessment cancellation reason must not be empty")
        with self.database.unit_of_work() as work:
            return work.normalization.cancel_assessment_batch(
                batch_id, utc_text(self.clock.now()), normalized_reason
            )

    def validate(self, batch_id: str) -> ValidationResult:
        batch, items, manifest = self._load_batch(batch_id)
        output = self._data_path(str(batch["output_path"]))
        if not output.is_file():
            raise AssessmentError(f"staged output not found: {batch['output_path']}")
        expected = {str(item["id"]): item for item in items}
        manifest_items = {str(item["work_item_id"]): item for item in manifest["items"]}
        errors: list[dict[str, Any]] = []
        valid: list[dict[str, Any]] = []
        seen: set[str] = set()
        for line_number, line in enumerate(output.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append({"line": line_number, "errors": [f"invalid JSON: {error}"]})
                continue
            record_errors = self._validate_record(
                record, batch, expected, manifest_items, manifest, seen
            )
            if record_errors:
                errors.append(
                    {
                        "line": line_number,
                        "work_item_id": record.get("work_item_id")
                        if isinstance(record, dict)
                        else None,
                        "errors": record_errors,
                    }
                )
            else:
                if batch["prompt_version"] in {2, CLEAN_PROMPT_VERSION}:
                    record = dict(record)
                    record["vacancy"] = legacy_fields(
                        CleaningService(self.data_dir, self.database).get(
                            record["clean_version_id"]
                        )
                    )
                valid.append(record)
                seen.add(str(record["work_item_id"]))
        missing = sorted(set(expected) - seen - {str(e.get("work_item_id")) for e in errors})
        errors.extend(
            (
                {
                    "line": None,
                    "work_item_id": item_id,
                    "errors": ["missing result record"],
                }
                for item_id in missing
            )
        )
        report = {
            "batch_id": batch_id,
            "valid_count": len(valid),
            "rejected_count": len(errors),
            "errors": errors,
        }
        _write_atomic(
            self.data_dir / "ai" / "rejected" / batch_id / "validation.json",
            _json_bytes(report),
        )
        rejected_ids = {
            str(error["work_item_id"]) for error in errors if error.get("work_item_id") in expected
        }
        with self.database.unit_of_work() as work:
            for record in valid:
                work.normalization.set_item_status(str(record["work_item_id"]), "validated")
            for item_id in rejected_ids:
                work.normalization.set_item_status(item_id, "rejected")
            work.normalization.set_batch_status(batch_id, "validated" if not errors else "partial")
        return ValidationResult(batch_id, len(valid), len(errors), tuple(errors), tuple(valid))

    def import_batch(self, batch_id: str) -> dict[str, Any]:
        validation = self.validate(batch_id)
        batch, _, manifest = self._load_batch(batch_id)
        timestamp = utc_text(self.clock.now())
        imported = 0
        unchanged = 0
        item_paths = {
            str(item["work_item_id"]): self._data_path(str(batch["input_directory"]))
            / str(item["path"])
            for item in manifest["items"]
        }
        item_documents = {
            item_id: json.loads(path.read_text(encoding="utf-8"))
            for item_id, path in item_paths.items()
        }
        profile = {
            "id": manifest["profile_id"],
            "profile_name": str(manifest["profile_id"]).removeprefix("profile_").rsplit("_v", 1)[0],
            "version": manifest["profile_version"],
            "content_hash": manifest["profile_hash"],
            "relative_path": manifest["profile_path"],
            "created_at": timestamp,
        }
        with self.database.unit_of_work() as work:
            work.normalization.register_profile(profile)
            for record in validation.valid_records:
                item = item_documents[str(record["work_item_id"])]
                if batch["prompt_version"] in {2, CLEAN_PROMPT_VERSION}:
                    assert work.connection is not None
                    previous = work.connection.execute(
                        "SELECT result_json FROM clean_assessment_history WHERE work_item_id=?",
                        (record["work_item_id"],),
                    ).fetchone()
                    record_json = json.dumps(record, ensure_ascii=False, sort_keys=True)
                    if previous is not None and previous["result_json"] != record_json:
                        raise AssessmentError(
                            "An imported clean assessment result cannot be rewritten"
                        )
                    work.connection.execute(
                        "INSERT OR IGNORE INTO clean_assessment_history VALUES (?,?,?,?,?,?,?)",
                        (
                            record["work_item_id"],
                            record["clean_version_id"],
                            record["profile_id"],
                            record["prompt_version"],
                            record["input_hash"],
                            record_json,
                            timestamp,
                        ),
                    )
                vacancy = record["vacancy"]
                evaluation = record["evaluation"]
                job = {
                    "id": self.ids.new("job"),
                    "observation_id": record["observation_id"],
                    "input_hash": record["input_hash"],
                    "source_name": item["source_name"],
                    "external_id": item["external_id"],
                    "title": _vacancy_value(vacancy, "title"),
                    "employer": _vacancy_value(vacancy, "employer"),
                    "locations_json": json.dumps(
                        _vacancy_list(vacancy, "locations"), ensure_ascii=False
                    ),
                    "remote_policy": _vacancy_value(vacancy, "remote_policy"),
                    "employment_type": _vacancy_value(vacancy, "employment_type"),
                    "seniority": _vacancy_value(vacancy, "seniority"),
                    "description_summary": _vacancy_value(vacancy, "description_summary"),
                    "requirements_json": json.dumps(
                        _vacancy_list(vacancy, "mandatory_requirements"),
                        ensure_ascii=False,
                    ),
                    "application_url": _vacancy_value(vacancy, "application_url"),
                    "publication_date": _vacancy_value(vacancy, "publication_date"),
                    "application_deadline": _vacancy_value(vacancy, "application_deadline"),
                    "language": _vacancy_value(vacancy, "language"),
                    "keywords_json": json.dumps(
                        _vacancy_list(vacancy, "keywords"), ensure_ascii=False
                    ),
                    "confidence": evaluation["assessment_confidence"] / 100,
                    "uncertainty_notes_json": json.dumps(
                        evaluation["missing_information"], ensure_ascii=False
                    ),
                    "result_json": json.dumps(record, ensure_ascii=False, sort_keys=True),
                    "prompt_id": record["prompt_id"],
                    "prompt_version": record["prompt_version"],
                    "model_id": record["model_id"],
                    "created_at": timestamp,
                    "updated_at": timestamp,
                    "working_time": _vacancy_value(vacancy, "working_time"),
                    "contract_type": _vacancy_value(vacancy, "contract_type"),
                    "contract_end_date": _vacancy_value(vacancy, "contract_end_date"),
                    "start_date": _vacancy_value(vacancy, "start_date"),
                    "salary": _vacancy_value(vacancy, "salary"),
                    "responsibilities_json": json.dumps(
                        _vacancy_list(vacancy, "responsibilities"), ensure_ascii=False
                    ),
                    "mandatory_requirements_json": json.dumps(
                        _vacancy_list(vacancy, "mandatory_requirements"),
                        ensure_ascii=False,
                    ),
                    "preferred_qualifications_json": json.dumps(
                        _vacancy_list(vacancy, "preferred_qualifications"),
                        ensure_ascii=False,
                    ),
                    "education_requirements_json": json.dumps(
                        _vacancy_list(vacancy, "education_requirements"),
                        ensure_ascii=False,
                    ),
                    "experience_requirements_json": json.dumps(
                        _vacancy_list(vacancy, "experience_requirements"),
                        ensure_ascii=False,
                    ),
                    "methods_tools_json": json.dumps(
                        _vacancy_list(vacancy, "methods_tools"), ensure_ascii=False
                    ),
                    "languages_required_json": json.dumps(
                        _vacancy_list(vacancy, "languages_required"), ensure_ascii=False
                    ),
                    "occupational_categories_json": json.dumps(
                        _vacancy_list(vacancy, "occupational_categories"),
                        ensure_ascii=False,
                    ),
                    "application_method": _vacancy_value(vacancy, "application_method"),
                    "contact_details_json": json.dumps(
                        _vacancy_list(vacancy, "contact_details"), ensure_ascii=False
                    ),
                    "reference_number": _vacancy_value(vacancy, "reference_number"),
                    "validity_status": _vacancy_value(vacancy, "validity_status"),
                }
                job_id, _ = work.normalization.upsert_assessed_job(job)
                evaluation_row = {
                    "id": self.ids.new("eval"),
                    "job_id": job_id,
                    "observation_id": record["observation_id"],
                    "source_name": item["source_name"],
                    "external_id": item["external_id"],
                    "artifact_hash": item["raw_artifact_hash"]
                    if "raw_artifact_hash" in item
                    else item["evidence"][0]["artifact_hash"],
                    "profile_id": record["profile_id"],
                    "input_hash": record["input_hash"],
                    "suitability_score": evaluation["suitability_score"],
                    "assessment_confidence": evaluation["assessment_confidence"],
                    "recommendation": evaluation["recommendation"],
                    "summary": evaluation["summary"],
                    "strengths_json": json.dumps(evaluation["strengths"], ensure_ascii=False),
                    "gaps_json": json.dumps(evaluation["gaps"], ensure_ascii=False),
                    "deal_breakers_json": json.dumps(
                        evaluation["deal_breakers"], ensure_ascii=False
                    ),
                    "missing_information_json": json.dumps(
                        evaluation["missing_information"], ensure_ascii=False
                    ),
                    "result_json": json.dumps(record, ensure_ascii=False, sort_keys=True),
                    "prompt_id": record["prompt_id"],
                    "prompt_version": record["prompt_version"],
                    "model_id": record["model_id"],
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                changed = work.normalization.upsert_evaluation(evaluation_row)
                imported += int(changed)
                unchanged += int(not changed)
                work.normalization.set_item_status(str(record["work_item_id"]), "imported")
            work.normalization.set_batch_status(
                batch_id, "imported" if validation.rejected_count == 0 else "partial"
            )
        return {
            "batch_id": batch_id,
            "profile_id": manifest["profile_id"],
            "imported": imported,
            "unchanged": unchanged,
            "rejected": validation.rejected_count,
        }

    def _validate_record(
        self,
        record: object,
        batch: dict[str, Any],
        expected: dict[str, dict[str, Any]],
        manifest_items: dict[str, dict[str, Any]],
        manifest: dict[str, Any],
        seen: set[str],
    ) -> list[str]:
        if batch["prompt_version"] in {2, CLEAN_PROMPT_VERSION}:
            return self._validate_clean_record(
                record, batch, expected, manifest_items, manifest, seen
            )
        if not isinstance(record, dict):
            return ["result must be a JSON object"]
        errors: list[str] = []
        if set(record) != RECORD_KEYS:
            errors.append("result fields do not exactly match vacancy-assessment schema v1")
        if _contains_mojibake(record) or _damaged_output_text(record):
            errors.append("result text appears to contain incorrectly decoded UTF-8 (mojibake)")
        item_id = record.get("work_item_id")
        if not isinstance(item_id, str) or item_id not in expected:
            return ["unknown work_item_id"]
        if item_id in seen:
            return ["duplicate work_item_id"]
        checks = {
            "batch_id": batch["id"],
            "observation_id": expected[item_id]["observation_id"],
            "input_hash": expected[item_id]["input_hash"],
            "prompt_id": PROMPT_ID,
            "prompt_version": batch["prompt_version"],
            "profile_id": manifest["profile_id"],
            "profile_version": manifest["profile_version"],
            "profile_hash": manifest["profile_hash"],
        }
        for key, expected_value in checks.items():
            if record.get(key) != expected_value:
                errors.append(f"{key} does not match the prepared batch")
        item_path = self._data_path(str(batch["input_directory"])) / manifest_items[item_id]["path"]
        if _sha256(item_path.read_bytes()) != expected[item_id]["input_hash"]:
            return [*errors, "prepared input changed after batch creation"]
        if record.get("status") not in {"completed", "insufficient_evidence"}:
            errors.append("status must be completed or insufficient_evidence")
        if not isinstance(record.get("model_id"), str) or not record["model_id"]:
            errors.append("model_id must be a non-empty string")
        if not isinstance(record.get("suspicious_content"), bool):
            errors.append("suspicious_content must be boolean")
        item_document = json.loads(item_path.read_text(encoding="utf-8"))
        allowed_hashes = {entry["artifact_hash"] for entry in item_document["evidence"]}
        vacancy = record.get("vacancy")
        if not isinstance(vacancy, dict):
            errors.append("vacancy must be an object")
        else:
            if set(vacancy) != set(VACANCY_FIELDS):
                errors.append("vacancy fields do not exactly match schema v1")
            for name in VACANCY_FIELDS:
                errors.extend(self._validate_field(name, vacancy.get(name), allowed_hashes))
            url = vacancy.get("application_url", {}).get("value")
            if url is not None and (
                not isinstance(url, str)
                or urlparse(url).scheme != "https"
                or (not urlparse(url).netloc)
            ):
                errors.append("application_url.value must be an absolute HTTPS URL or null")
            for name in DATE_FIELDS:
                value = vacancy.get(name, {}).get("value")
                if isinstance(value, str):
                    try:
                        date.fromisoformat(value)
                    except ValueError:
                        errors.append(f"{name}.value must be an ISO YYYY-MM-DD date")
        errors.extend(self._validate_evaluation(record.get("evaluation")))
        return errors

    def _validate_clean_record(
        self,
        record: object,
        batch: dict[str, Any],
        expected: dict[str, dict[str, Any]],
        manifest_items: dict[str, dict[str, Any]],
        manifest: dict[str, Any],
        seen: set[str],
    ) -> list[str]:
        if not isinstance(record, dict):
            return ["result must be a JSON object"]
        errors = []
        required = RECORD_KEYS - {"vacancy"} | {"clean_version_id", "citations"}
        if set(record) != required:
            errors.append("result fields do not exactly match vacancy-assessment schema v2")
        if _contains_mojibake(record) or _damaged_output_text(record):
            errors.append("result contains incorrectly decoded UTF-8")
        item_id = record.get("work_item_id")
        if not isinstance(item_id, str) or item_id not in expected:
            return [*errors, "unknown work_item_id"]
        if item_id in seen:
            return [*errors, "duplicate work_item_id"]
        path = self._data_path(str(batch["input_directory"])) / manifest_items[item_id]["path"]
        content = path.read_bytes()
        if _sha256(content) != expected[item_id]["input_hash"]:
            return [*errors, "prepared input changed after batch creation"]
        item = json.loads(content)
        for key, value in {
            "batch_id": batch["id"],
            "work_item_id": item_id,
            "observation_id": expected[item_id]["observation_id"],
            "input_hash": expected[item_id]["input_hash"],
            "prompt_id": PROMPT_ID,
            "prompt_version": batch["prompt_version"],
            "profile_id": manifest["profile_id"],
            "profile_version": manifest["profile_version"],
            "profile_hash": manifest["profile_hash"],
            "clean_version_id": item["vacancy"]["version_id"],
        }.items():
            if record.get(key) != value:
                errors.append(f"{key} does not match the prepared batch")
        if record.get("status") not in {"completed", "insufficient_evidence"}:
            errors.append("invalid result status")
        if not isinstance(record.get("model_id"), str) or not record["model_id"]:
            errors.append("model_id must be a non-empty string")
        if not isinstance(record.get("suspicious_content"), bool):
            errors.append("suspicious_content must be boolean")
        cleaner = CleaningService(self.data_dir, self.database)
        document = cleaner.get(item["vacancy"]["version_id"])
        if (
            assessment_packet(document) != item["vacancy"]
            or document["quality"]["state"] != "ready"
        ):
            errors.append("prepared clean vacancy does not match its immutable ready version")
        with self.database.unit_of_work() as work:
            assert work.connection is not None
            binding = work.connection.execute(
                "SELECT version_id FROM clean_assessment_items WHERE work_item_id=?",
                (item_id,),
            ).fetchone()
        if binding is None or binding["version_id"] != item["vacancy"]["version_id"]:
            errors.append("assessment item has an invalid clean version binding")
        errors.extend(self._validate_evaluation(record.get("evaluation")))
        if batch["prompt_version"] == 3:
            evaluation = record.get("evaluation")
            if isinstance(evaluation, dict):
                score = evaluation.get("suitability_score")
                if isinstance(score, int) and (not isinstance(score, bool)):
                    recommendation = (
                        "high"
                        if score >= 85
                        else "medium"
                        if score >= 70
                        else "low"
                        if score >= 25
                        else "reject"
                    )
                    if evaluation.get("recommendation") != recommendation:
                        errors.append("recommendation does not match the suitability score band")
                    if evaluation.get("deal_breakers") and score >= 25:
                        errors.append("confirmed deal breakers require a reject score below 25")
        allowed = {b["id"] for b in item["vacancy"]["blocks"]}
        allowed |= {"f." + name for name in item["vacancy"]["facts"]}
        claims = set()
        citations = record.get("citations")
        if not isinstance(citations, list):
            errors.append("citations must be an array")
        else:
            for citation in citations:
                if not isinstance(citation, dict) or set(citation) != {"claim", "refs"}:
                    errors.append("citation must contain claim and refs")
                    continue
                claim, refs = (citation["claim"], citation["refs"])
                if not isinstance(claim, str) or not claim:
                    errors.append("citation claim must be a non-empty string")
                elif (
                    isinstance(refs, list)
                    and refs
                    and all((isinstance(ref, str) and ref in allowed for ref in refs))
                ):
                    claims.add(claim)
                else:
                    errors.append("citation refers to missing vacancy evidence")
        evaluation = record.get("evaluation")
        if isinstance(evaluation, dict):
            for name in ("strengths", "gaps", "deal_breakers"):
                values = evaluation.get(name)
                if isinstance(values, list):
                    for claim in values:
                        if isinstance(claim, str) and claim not in claims:
                            errors.append(
                                f"evaluation.{name} claim requires a valid evidence citation"
                            )
        return errors

    @staticmethod
    def _validate_field(name: str, field: object, allowed_hashes: set[str]) -> list[str]:
        if not isinstance(field, dict):
            return [f"vacancy.{name} must be a field object"]
        errors: list[str] = []
        if set(field) != {"value", "status", "evidence"}:
            errors.append(f"vacancy.{name} fields do not exactly match schema v1")
        value = field.get("value")
        status = field.get("status")
        evidence = field.get("evidence")
        if status not in FIELD_STATUSES:
            errors.append(f"vacancy.{name}.status is invalid")
        if name in LIST_FIELDS:
            if value is not None and (
                not isinstance(value, list) or not all((isinstance(item, str) for item in value))
            ):
                errors.append(f"vacancy.{name}.value must be a string array or null")
        elif value is not None and (not isinstance(value, str)):
            errors.append(f"vacancy.{name}.value must be a string or null")
        if status in {"unknown", "not_stated", "not_applicable"} and value is not None:
            errors.append(f"vacancy.{name}.value must be null for status {status}")
        if not isinstance(evidence, list):
            return [*errors, f"vacancy.{name}.evidence must be an array"]
        for citation in evidence:
            if (
                not isinstance(citation, dict)
                or set(citation) != {"artifact_hash", "locator"}
                or citation.get("artifact_hash") not in allowed_hashes
                or (not isinstance(citation.get("locator"), str))
                or (not citation["locator"])
            ):
                errors.append(f"vacancy.{name} has an invalid evidence citation")
        if value is not None and (not evidence):
            errors.append(f"vacancy.{name} requires evidence for every non-null value")
        return errors

    @staticmethod
    def _validate_evaluation(evaluation: object) -> list[str]:
        if not isinstance(evaluation, dict):
            return ["evaluation must be an object"]
        errors: list[str] = []
        if set(evaluation) != EVALUATION_KEYS:
            errors.append("evaluation fields do not exactly match schema v1")
        for name in ("suitability_score", "assessment_confidence"):
            value = evaluation.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or (not 0 <= value <= 100):
                errors.append(f"evaluation.{name} must be an integer from 0 to 100")
        if evaluation.get("recommendation") not in {"high", "medium", "low", "reject"}:
            errors.append("evaluation.recommendation is invalid")
        if not isinstance(evaluation.get("summary"), str) or not evaluation.get("summary"):
            errors.append("evaluation.summary must be a non-empty string")
        for name in ("strengths", "gaps", "deal_breakers", "missing_information"):
            value = evaluation.get(name)
            if not isinstance(value, list) or not all((isinstance(item, str) for item in value)):
                errors.append(f"evaluation.{name} must be a string array")
        components = evaluation.get("score_components")
        if not isinstance(components, dict) or set(components) != COMPONENTS:
            errors.append("evaluation.score_components fields do not exactly match schema v1")
        else:
            for name, component in components.items():
                if not isinstance(component, dict) or set(component) != {
                    "score",
                    "rationale",
                }:
                    errors.append(f"evaluation.score_components.{name} is invalid")
                    continue
                score = component["score"]
                if score is not None and (
                    not isinstance(score, int) or isinstance(score, bool) or (not 0 <= score <= 100)
                ):
                    errors.append(f"evaluation.score_components.{name}.score is invalid")
                if not isinstance(component["rationale"], str):
                    errors.append(f"evaluation.score_components.{name}.rationale must be a string")
        return errors

    def _load_batch(
        self, batch_id: str
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], dict[str, Any]]:
        with self.database.unit_of_work() as work:
            batch = work.normalization.batch(batch_id)
            items = work.normalization.batch_items(batch_id)
        if batch is None or batch["prompt_id"] != PROMPT_ID:
            raise AssessmentError(f"assessment batch not found: {batch_id}")
        if batch["status"] == "cancelled":
            raise AssessmentError(f"assessment batch is cancelled: {batch_id}")
        manifest_path = self._data_path(str(batch["input_directory"])) / "manifest.json"
        content = manifest_path.read_bytes()
        if _sha256(content) != batch["manifest_hash"]:
            raise AssessmentError("batch manifest changed after preparation")
        manifest = json.loads(content)
        if batch["prompt_version"] in {2, CLEAN_PROMPT_VERSION}:
            profile_document = manifest.get("profile_document")
            if not isinstance(profile_document, dict):
                raise AssessmentError("clean assessment has no shared profile snapshot")
            shared_path = (manifest_path.parent / profile_document["path"]).resolve()
            if not shared_path.is_relative_to(manifest_path.parent):
                raise AssessmentError("shared profile path escapes the batch")
            shared_bytes = shared_path.read_bytes()
            if _sha256(shared_bytes) != profile_document["hash"]:
                raise AssessmentError("shared profile snapshot changed after preparation")
            shared = json.loads(shared_bytes)
            if _sha256(shared["content"].encode()) != manifest["profile_hash"]:
                raise AssessmentError(
                    "shared profile content does not match the registered profile"
                )
        with self.database.unit_of_work() as work:
            registered = work.normalization.profile_version(str(manifest["profile_id"]))
        if registered is None:
            raise AssessmentError(f"batch profile is not registered: {manifest['profile_id']}")
        if (
            registered["version"] != manifest["profile_version"]
            or registered["content_hash"] != manifest["profile_hash"]
            or registered["relative_path"] != manifest["profile_path"]
        ):
            raise AssessmentError(
                f"batch profile {manifest['profile_id']} does not match the immutable profile registry: registered hash {registered['content_hash']}, batch hash {manifest['profile_hash']}; cancel this batch and prepare a replacement with a new profile version"
            )
        return (batch, items, manifest)

    def _data_path(self, relative: str) -> Path:
        path = (self.data_dir / relative).resolve()
        if not path.is_relative_to(self.data_dir):
            raise AssessmentError("assessment path escapes data_dir")
        return path

    def _batch_item_path(self, batch_id: str, item_id: str) -> Path:
        batch, _, manifest = self._load_batch(batch_id)
        match: dict[str, Any] = next(
            (item for item in manifest["items"] if item["work_item_id"] == item_id)
        )
        return self._data_path(str(batch["input_directory"])) / str(match["path"])
