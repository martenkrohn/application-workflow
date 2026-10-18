"""Profile-specific agent assessment over complete per-vacancy evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from job_system.application.cleaning import CleaningService
from job_system.application.ports import Clock, IdGenerator
from job_system.domain.clean_vacancies import (
    assessment_packet,
    legacy_fields,
)
from job_system.domain.location_policy import LocationPolicy, location_packet
from job_system.infrastructure.database import Database
from job_system.infrastructure.services import utc_text

PROMPT_ID = "vacancy-assessment"
PROMPT_VERSION = 1
CLEAN_PROMPT_VERSION = 3
SCREENING_PROMPT_ID = "vacancy-screening"
SCREENING_WAVE_SIZE = 50
SCREENING_PROMOTION_SCORE = 75
VACANCY_BATCH_ATTEMPTS = 3
DEFAULT_PROFILE = Path("examples/profiles/demo/profile-v1.md")
FIELD_STATUSES = frozenset(
    {"stated", "inferred", "unknown", "not_stated", "not_applicable"}
)
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
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


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
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}-", dir=path.parent
    )
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
    def load(
        cls, project_root: Path, relative_path: Path = DEFAULT_PROFILE
    ) -> ProfileDocument:
        path = (project_root / relative_path).resolve()
        if not path.is_relative_to(project_root.resolve()) or not path.is_file():
            raise AssessmentError(
                f"screening profile not found inside project: {relative_path}"
            )
        match = re.fullmatch("profile-v([1-9][0-9]*)", path.stem)
        if match is None or path.parent == project_root.resolve():
            raise AssessmentError(
                "profile path must end in <name>/profile-v<number>.md"
            )
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
            raise AssessmentError(
                "selected assessment batch must contain between 1 and 25 jobs"
            )
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
            raise NoAssessmentCandidates(
                "no new or changed observations need profile assessment"
            )
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
                    and (
                        not location_policy.classify(location_packet(document)).admitted
                    )
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
            if (
                not artifact_path.is_relative_to(self.data_dir)
                or not artifact_path.is_file()
            ):
                raise AssessmentError(
                    f"missing evidence artifact for {candidate['id']}"
                )
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
                            clean_documents[prepared_item["observation_id"]][
                                "version_id"
                            ],
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
        for line_number, line in enumerate(
            output.read_text(encoding="utf-8").splitlines(), 1
        ):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                errors.append(
                    {"line": line_number, "errors": [f"invalid JSON: {error}"]}
                )
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
        missing = sorted(
            set(expected) - seen - {str(e.get("work_item_id")) for e in errors}
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
            str(error["work_item_id"])
            for error in errors
            if error.get("work_item_id") in expected
        }
        with self.database.unit_of_work() as work:
            for record in valid:
                work.normalization.set_item_status(
                    str(record["work_item_id"]), "validated"
                )
            for item_id in rejected_ids:
                work.normalization.set_item_status(item_id, "rejected")
            work.normalization.set_batch_status(
                batch_id, "validated" if not errors else "partial"
            )
        return ValidationResult(
            batch_id, len(valid), len(errors), tuple(errors), tuple(valid)
        )

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
            "profile_name": str(manifest["profile_id"])
            .removeprefix("profile_")
            .rsplit("_v", 1)[0],
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
                    "description_summary": _vacancy_value(
                        vacancy, "description_summary"
                    ),
                    "requirements_json": json.dumps(
                        _vacancy_list(vacancy, "mandatory_requirements"),
                        ensure_ascii=False,
                    ),
                    "application_url": _vacancy_value(vacancy, "application_url"),
                    "publication_date": _vacancy_value(vacancy, "publication_date"),
                    "application_deadline": _vacancy_value(
                        vacancy, "application_deadline"
                    ),
                    "language": _vacancy_value(vacancy, "language"),
                    "keywords_json": json.dumps(
                        _vacancy_list(vacancy, "keywords"), ensure_ascii=False
                    ),
                    "confidence": evaluation["assessment_confidence"] / 100,
                    "uncertainty_notes_json": json.dumps(
                        evaluation["missing_information"], ensure_ascii=False
                    ),
                    "result_json": json.dumps(
                        record, ensure_ascii=False, sort_keys=True
                    ),
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
                    "strengths_json": json.dumps(
                        evaluation["strengths"], ensure_ascii=False
                    ),
                    "gaps_json": json.dumps(evaluation["gaps"], ensure_ascii=False),
                    "deal_breakers_json": json.dumps(
                        evaluation["deal_breakers"], ensure_ascii=False
                    ),
                    "missing_information_json": json.dumps(
                        evaluation["missing_information"], ensure_ascii=False
                    ),
                    "result_json": json.dumps(
                        record, ensure_ascii=False, sort_keys=True
                    ),
                    "prompt_id": record["prompt_id"],
                    "prompt_version": record["prompt_version"],
                    "model_id": record["model_id"],
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
                changed = work.normalization.upsert_evaluation(evaluation_row)
                imported += int(changed)
                unchanged += int(not changed)
                work.normalization.set_item_status(
                    str(record["work_item_id"]), "imported"
                )
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
            errors.append(
                "result fields do not exactly match vacancy-assessment schema v1"
            )
        if _contains_mojibake(record) or _damaged_output_text(record):
            errors.append(
                "result text appears to contain incorrectly decoded UTF-8 (mojibake)"
            )
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
        item_path = (
            self._data_path(str(batch["input_directory"]))
            / manifest_items[item_id]["path"]
        )
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
                errors.extend(
                    self._validate_field(name, vacancy.get(name), allowed_hashes)
                )
            url = vacancy.get("application_url", {}).get("value")
            if url is not None and (
                not isinstance(url, str)
                or urlparse(url).scheme != "https"
                or (not urlparse(url).netloc)
            ):
                errors.append(
                    "application_url.value must be an absolute HTTPS URL or null"
                )
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
            errors.append(
                "result fields do not exactly match vacancy-assessment schema v2"
            )
        if _contains_mojibake(record) or _damaged_output_text(record):
            errors.append("result contains incorrectly decoded UTF-8")
        item_id = record.get("work_item_id")
        if not isinstance(item_id, str) or item_id not in expected:
            return [*errors, "unknown work_item_id"]
        if item_id in seen:
            return [*errors, "duplicate work_item_id"]
        path = (
            self._data_path(str(batch["input_directory"]))
            / manifest_items[item_id]["path"]
        )
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
            errors.append(
                "prepared clean vacancy does not match its immutable ready version"
            )
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
                        errors.append(
                            "recommendation does not match the suitability score band"
                        )
                    if evaluation.get("deal_breakers") and score >= 25:
                        errors.append(
                            "confirmed deal breakers require a reject score below 25"
                        )
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
    def _validate_field(
        name: str, field: object, allowed_hashes: set[str]
    ) -> list[str]:
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
                not isinstance(value, list)
                or not all((isinstance(item, str) for item in value))
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
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or (not 0 <= value <= 100)
            ):
                errors.append(f"evaluation.{name} must be an integer from 0 to 100")
        if evaluation.get("recommendation") not in {"high", "medium", "low", "reject"}:
            errors.append("evaluation.recommendation is invalid")
        if not isinstance(evaluation.get("summary"), str) or not evaluation.get(
            "summary"
        ):
            errors.append("evaluation.summary must be a non-empty string")
        for name in ("strengths", "gaps", "deal_breakers", "missing_information"):
            value = evaluation.get(name)
            if not isinstance(value, list) or not all(
                (isinstance(item, str) for item in value)
            ):
                errors.append(f"evaluation.{name} must be a string array")
        components = evaluation.get("score_components")
        if not isinstance(components, dict) or set(components) != COMPONENTS:
            errors.append(
                "evaluation.score_components fields do not exactly match schema v1"
            )
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
                    not isinstance(score, int)
                    or isinstance(score, bool)
                    or (not 0 <= score <= 100)
                ):
                    errors.append(
                        f"evaluation.score_components.{name}.score is invalid"
                    )
                if not isinstance(component["rationale"], str):
                    errors.append(
                        f"evaluation.score_components.{name}.rationale must be a string"
                    )
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
                raise AssessmentError(
                    "shared profile snapshot changed after preparation"
                )
            shared = json.loads(shared_bytes)
            if _sha256(shared["content"].encode()) != manifest["profile_hash"]:
                raise AssessmentError(
                    "shared profile content does not match the registered profile"
                )
        with self.database.unit_of_work() as work:
            registered = work.normalization.profile_version(str(manifest["profile_id"]))
        if registered is None:
            raise AssessmentError(
                f"batch profile is not registered: {manifest['profile_id']}"
            )
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
