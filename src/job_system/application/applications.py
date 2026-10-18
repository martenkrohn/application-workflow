from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from job_system.application.assessment import AssessmentError
from job_system.infrastructure.database import Database
from job_system.infrastructure.services import SystemClock, utc_text

WORKSPACE_DIRECTORIES = (
    "applications/01_active",
    "applications/02_applied",
    "applications/03_invited",
    "applications/04_rejected",
    "applications/05_abandoned",
)
STATUS_DIRECTORIES = {
    "applied": "applications/02_applied",
    "invited": "applications/03_invited",
    "rejected": "applications/04_rejected",
    "abandoned": "applications/05_abandoned",
}
ACTIVE_DIRECTORY = "applications/01_active"


class ApplicationWorkspaceService:
    def start(self, group_id: str, profile_id: str, slug: str) -> dict[str, Any]:
        """Use the real workspace lifecycle with fictional Markdown materials."""
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,79}", slug):
            raise AssessmentError(
                "application slug must contain 2-80 lowercase letters, digits, or hyphens"
            )
        with self.database.unit_of_work(read_only=True) as work:
            records = work.reviews.group_records(group_id, profile_id)
            tracked = work.reviews.workspace(group_id, profile_id)
            members = {(r["source_name"], r["external_id"]) for r in records}
            evaluations = tuple(
                row
                for row in work.normalization.evaluations(profile_id, None)
                if (row["source_name"], row["external_id"]) in members
            )
        if not evaluations:
            raise AssessmentError(
                "A detailed assessment is required before starting an application."
            )
        if tracked is not None:
            raise AssessmentError(
                "An application workspace already exists for this vacancy."
            )
        directories = self.ensure_directories()
        application_id = f"{self._next_index(directories):02d}"
        destination = directories[0] / f"{application_id}_{slug}"
        destination.mkdir()
        try:
            import json

            from job_system.reporting.vacancies import merged_assessment_result

            result = merged_assessment_result(evaluations)
            (destination / "assessment.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            title = result["vacancy"]["title"]["value"]
            (destination / "application.md").write_text(
                f"# {title}\n\nFictional demonstration application.\n\n"
                f"- Vacancy group: {group_id}\n- Profile: {profile_id}\n"
                "- Submission route: unknown\n\n"
                "Review assessment.json and the saved vacancy evidence. Verify requirements, "
                "draft a tailored cover letter using only supported claims, and return the draft "
                "to the human for approval. Do not submit applications or contact anyone.\n",
                encoding="utf-8",
            )
            (destination / "cover-letter.md").write_text(
                "# Cover letter draft\n\nAwaiting human and agent collaboration. No application has been sent.\n",
                encoding="utf-8",
            )
            self._write_material_links(destination)
            self._write_review_status(destination, "interested")
            relative = destination.relative_to(self.project_root).as_posix()
            with self.database.unit_of_work() as work:
                work.reviews.record_workspace(
                    group_id,
                    profile_id,
                    relative,
                    slug,
                    application_id,
                    None,
                    "unknown",
                    None,
                    utc_text(SystemClock().now()),
                )
                work.reviews.set_decision(
                    group_id,
                    profile_id,
                    "interested",
                    None,
                    utc_text(SystemClock().now()),
                )
        except BaseException:
            for child in destination.iterdir():
                child.unlink()
            destination.rmdir()
            raise
        return {
            "group_id": group_id,
            "profile_id": profile_id,
            "application_id": application_id,
            "title": title,
            "path": str(destination),
            "relative_path": relative,
        }

    @staticmethod
    def _write_material_links(destination: Path) -> None:
        (destination / "MATERIALS.md").write_text(
            "# Demonstration materials\n\nThis portfolio contains an invented applicant profile "
            "in examples/profiles/demo/profile-v1.md. No private CVs, contact details or certificates "
            "are included. Real use requires a private profile and human-approved documents.\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_review_status(destination: Path, status: str) -> None:
        (destination / "STATUS.md").write_text(
            f"# Human decision\n\n{status}\n", encoding="utf-8"
        )

    def __init__(self, project_root: Path, database: Database) -> None:
        self.project_root = project_root.resolve()
        self.database = database

    def mark_by_group(
        self,
        group_id: str,
        profile_id: str,
        status: str,
        *,
        source: str,
        note: str | None,
    ) -> dict[str, Any]:
        timestamp = utc_text(SystemClock().now())
        with self.database.unit_of_work() as work:
            current = work.reviews.decision(group_id, profile_id)
            tracked = work.reviews.workspace(group_id, profile_id)
            cleaned_note = current["note"] if note is None else note.strip() or None
            changed = work.reviews.set_decision(
                group_id, profile_id, status, cleaned_note, timestamp
            )
            if changed and tracked is not None and (current["status"] != status):
                work.reviews.record_status_event(
                    str(tracked["application_id"]),
                    group_id,
                    profile_id,
                    str(current["status"]),
                    status,
                    source,
                    cleaned_note,
                    timestamp,
                )
        if not changed:
            raise AssessmentError(f"vacancy review group not found: {group_id}")
        workspace_path = self.move_for_status(group_id, profile_id, status)
        return {
            "group_id": group_id,
            "status": status,
            "note": cleaned_note,
            "workspace_path": workspace_path,
        }

    def ensure_directories(self) -> tuple[Path, ...]:
        directories = tuple(
            (self.project_root / name for name in WORKSPACE_DIRECTORIES)
        )
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
        return directories

    def move_for_status(
        self, group_id: str, profile_id: str, status: str
    ) -> str | None:
        """Move a tracked workspace to the directory represented by its status."""
        with self.database.unit_of_work() as work:
            tracked = work.reviews.workspace(group_id, profile_id)
        if tracked is None:
            return None
        directories = {
            path.relative_to(self.project_root).as_posix(): path
            for path in self.ensure_directories()
        }
        target_directory = directories[STATUS_DIRECTORIES.get(status, ACTIVE_DIRECTORY)]
        source = (self.project_root / str(tracked["relative_path"])).resolve()
        if (
            not source.is_relative_to(self.project_root)
            or source.parent not in directories.values()
        ):
            raise AssessmentError(f"invalid application workspace path: {source}")
        if not source.is_dir():
            raise AssessmentError(f"application workspace not found: {source}")
        destination = target_directory / source.name
        if destination == source:
            self._write_material_links(source)
            self._write_review_status(source, status)
            return source.relative_to(self.project_root).as_posix()
        if destination.exists():
            raise AssessmentError(
                f"application workspace already exists: {destination}"
            )
        shutil.move(str(source), destination)
        relative_path = destination.relative_to(self.project_root).as_posix()
        try:
            self._write_material_links(destination)
            with self.database.unit_of_work() as work:
                work.reviews.update_workspace_path(group_id, profile_id, relative_path)
            self._write_review_status(destination, status)
        except BaseException:
            shutil.move(str(destination), source)
            self._write_material_links(source)
            raise
        return relative_path

    @staticmethod
    def _next_index(directories: tuple[Path, ...]) -> int:
        indexes = []
        for directory in directories:
            for path in directory.iterdir():
                if not path.is_dir():
                    continue
                match = re.match("^(\\d{2})_", path.name)
                if match is not None:
                    indexes.append(int(match.group(1)))
        index = max(indexes, default=0) + 1
        if index > 99:
            raise AssessmentError("application workspace index exceeds 99")
        return index
