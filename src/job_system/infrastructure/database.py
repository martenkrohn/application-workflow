"""SQLite migrations, repositories, unit of work, and backup verification."""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from importlib.resources import files
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

from job_system.domain.clean_vacancies import EXTRACTOR_VERSION
from job_system.domain.models import (
    AttemptStatus,
    RunStatus,
    SourceOutcome,
    StoredArtifact,
)

# Every downstream candidate query joins this same current, assessable dataset.
CURRENT_READY_JOIN = (
    "JOIN clean_vacancy_current cc ON cc.source_name=ranked.source_name "
    "AND cc.external_id=ranked.external_id AND cc.observation_id=ranked.id "
    "JOIN clean_vacancy_versions cv ON cv.id=cc.version_id "
    f"AND cv.extractor_version='{EXTRACTOR_VERSION}' AND cv.quality_state='ready' "
    "AND COALESCE(json_extract(cv.document_json,'$.quality.availability'),'unknown') "
    "!='unavailable' "
    "AND COALESCE(date(substr(json_extract(cv.document_json,"
    "'$.facts.application_deadline.value'),1,10))>=date('now'),1) "
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        if read_only:
            connection = sqlite3.connect(
                f"{self.path.resolve().as_uri()}?mode=ro", uri=True, timeout=10
            )
            connection.execute("PRAGMA query_only = ON")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if not read_only:
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def migrate(self) -> int:
        connection = self.connect()
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            applied = {
                str(row[0])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            count = 0
            migration_root = files("job_system.infrastructure.migrations")
            for migration in sorted(
                migration_root.iterdir(), key=lambda item: item.name
            ):
                if not migration.name.endswith(".sql") or migration.name in applied:
                    continue
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version) VALUES (?)",
                    (migration.name,),
                )
                count += 1
            connection.commit()
            return count
        finally:
            connection.close()

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        source = self.connect()
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()
        temporary.replace(destination)

    @staticmethod
    def verify_backup(path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(path)
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()
            if result is None or result[0] != "ok":
                raise RuntimeError(f"backup integrity check failed: {result}")
            connection.execute("SELECT COUNT(*) FROM collection_runs").fetchone()
        finally:
            connection.close()

    @classmethod
    def restore(cls, backup: Path, destination: Path) -> None:
        """Restore a verified snapshot to a new path without overwriting existing data."""
        cls.verify_backup(backup)
        if destination.exists():
            raise FileExistsError(f"restore destination already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        source = sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            target.commit()
        finally:
            target.close()
            source.close()
        temporary.replace(destination)
        cls.verify_backup(destination)

    def unit_of_work(self, *, read_only: bool = False) -> UnitOfWork:
        return UnitOfWork(self, read_only=read_only)


class RunRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def create(
        self, run_id: str, version: str, fingerprint: str, started_at: str
    ) -> None:
        self.connection.execute(
            "INSERT INTO collection_runs "
            "(id, command_version, configuration_fingerprint, started_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, version, fingerprint, started_at, RunStatus.RUNNING),
        )

    def finish(
        self, run_id: str, status: RunStatus, finished_at: str, summary_path: str
    ) -> None:
        self.connection.execute(
            "UPDATE collection_runs SET status=?, finished_at=?, summary_path=? WHERE id=?",
            (status, finished_at, summary_path, run_id),
        )

    def start_attempt(
        self, attempt_id: str, run_id: str, source_name: str, started_at: str
    ) -> None:
        self.connection.execute(
            "INSERT INTO source_attempts (id, run_id, source_name, started_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (attempt_id, run_id, source_name, started_at, AttemptStatus.RUNNING),
        )

    def finish_attempt(
        self, attempt_id: str, finished_at: str, outcome: SourceOutcome
    ) -> None:
        self.connection.execute(
            "UPDATE source_attempts SET finished_at=?, status=?, observations_created=?, "
            "observations_unchanged=?, artifacts_created=?, artifacts_unchanged=?, "
            "error_code=?, error_message=?, checkpoint=? WHERE id=?",
            (
                finished_at,
                outcome.status,
                outcome.observations_created,
                outcome.observations_unchanged,
                outcome.artifacts_created,
                outcome.artifacts_unchanged,
                outcome.error_code,
                outcome.error_message,
                outcome.checkpoint,
                attempt_id,
            ),
        )

    def get(self, run_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM collection_runs WHERE id=?", (run_id,)
        ).fetchone()
        return dict(row) if row is not None else None


class EvidenceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def record_artifact(
        self, artifact: StoredArtifact, media_type: str, created_at: str
    ) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO raw_artifacts "
            "(content_hash, relative_path, size, media_type, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                artifact.content_hash,
                artifact.relative_path,
                artifact.size,
                media_type,
                created_at,
            ),
        )

    def record_retrieval(
        self,
        retrieval_id: str,
        artifact_hash: str,
        source_name: str,
        run_id: str,
        retrieved_at: str,
        source_url: str,
    ) -> None:
        self.connection.execute(
            "INSERT INTO artifact_retrievals VALUES (?, ?, ?, ?, ?, ?)",
            (
                retrieval_id,
                artifact_hash,
                source_name,
                run_id,
                retrieved_at,
                source_url,
            ),
        )

    def record_observation(
        self,
        observation_id: str,
        source_name: str,
        external_id: str,
        artifact: StoredArtifact,
        source_url: str,
        seen_at: str,
        run_id: str,
    ) -> tuple[bool, str]:
        existing = self.connection.execute(
            "SELECT id FROM source_observations "
            "WHERE source_name=? AND external_id=? AND content_hash=?",
            (source_name, external_id, artifact.content_hash),
        ).fetchone()
        if existing is not None:
            self.connection.execute(
                "UPDATE source_observations SET last_seen_at=?, last_run_id=? WHERE id=?",
                (seen_at, run_id, existing[0]),
            )
            return False, str(existing[0])
        self.connection.execute(
            "INSERT INTO source_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation_id,
                source_name,
                external_id,
                artifact.content_hash,
                artifact.content_hash,
                source_url,
                seen_at,
                seen_at,
                run_id,
            ),
        )
        return True, observation_id

    def link_observation_artifact(
        self, observation_id: str, artifact_hash: str, role: str
    ) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO observation_artifacts "
            "(observation_id, artifact_hash, role) VALUES (?, ?, ?)",
            (observation_id, artifact_hash, role),
        )

    def review(
        self, limit: int, source_names: tuple[str, ...] | None = None
    ) -> tuple[dict[str, Any], ...]:
        parameters: list[Any] = []
        source_clause = ""
        if source_names is not None:
            if not source_names:
                return ()
            placeholders = ",".join("?" for _ in source_names)
            source_clause = f"AND ranked.source_name IN ({placeholders}) "
            parameters.extend(source_names)
        parameters.append(limit)
        rows = self.connection.execute(
            "WITH ranked AS ("
            "SELECT so.*, ROW_NUMBER() OVER ("
            "PARTITION BY source_name, external_id ORDER BY first_seen_at DESC, id DESC"
            ") AS version_rank FROM source_observations so WHERE NOT EXISTS ("
            "SELECT 1 FROM source_identity_aliases sa WHERE sa.source_name=so.source_name "
            "AND sa.alias=so.external_id)) "
            "SELECT ranked.*, ra.relative_path, ra.media_type "
            "FROM ranked JOIN raw_artifacts ra ON ra.content_hash=ranked.artifact_hash "
            "WHERE ranked.version_rank=1 "
            f"{source_clause}"
            "ORDER BY ranked.first_seen_at DESC, ranked.source_name, ranked.external_id LIMIT ?",
            parameters,
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def selected_observations(
        self, identities: tuple[tuple[str, str], ...]
    ) -> tuple[dict[str, Any], ...]:
        selected: list[dict[str, Any]] = []
        for source_name, external_id in identities:
            row = self.connection.execute(
                "SELECT so.*, ra.relative_path, ra.media_type FROM source_observations so "
                "JOIN raw_artifacts ra ON ra.content_hash=so.artifact_hash "
                "WHERE so.source_name=? AND so.external_id=? "
                "ORDER BY so.first_seen_at DESC, so.id DESC LIMIT 1",
                (source_name, external_id),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"selected vacancy is no longer available: {source_name}:{external_id}"
                )
            selected.append(dict(row))
        return tuple(selected)


class NormalizationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ready_observations(self) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "WITH ranked AS (SELECT so.*,ROW_NUMBER() OVER (PARTITION BY source_name,"
            "external_id ORDER BY first_seen_at DESC,id DESC) AS version_rank "
            "FROM source_observations so WHERE NOT EXISTS (SELECT 1 FROM "
            "source_identity_aliases a WHERE a.source_name=so.source_name "
            "AND a.alias=so.external_id)) "
            "SELECT ranked.*,ra.relative_path,ra.media_type,cv.id AS clean_version_id "
            "FROM ranked JOIN raw_artifacts ra ON ra.content_hash=ranked.artifact_hash "
            f"{CURRENT_READY_JOIN} WHERE ranked.version_rank=1"
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def create_batch(
        self,
        batch: dict[str, Any],
        items: tuple[dict[str, str], ...],
    ) -> None:
        self.connection.execute(
            "INSERT INTO ai_work_batches ("
            "id, prompt_id, prompt_version, manifest_hash, created_at, status, "
            "input_directory, output_path, item_count, cycle_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                batch["id"],
                batch["prompt_id"],
                batch["prompt_version"],
                batch["manifest_hash"],
                batch["created_at"],
                "prepared",
                batch["input_directory"],
                batch["output_path"],
                len(items),
                batch.get("cycle_id"),
            ),
        )
        self.connection.executemany(
            "INSERT INTO ai_work_items VALUES (?, ?, ?, ?, 'prepared')",
            (
                (item["id"], batch["id"], item["observation_id"], item["input_hash"])
                for item in items
            ),
        )

    def batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM ai_work_batches WHERE id=?", (batch_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def batch_items(self, batch_id: str) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT * FROM ai_work_items WHERE batch_id=? ORDER BY id", (batch_id,)
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def batches(self, limit: int) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT * FROM ai_work_batches ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def set_batch_status(self, batch_id: str, status: str) -> None:
        self.connection.execute(
            "UPDATE ai_work_batches SET status=? WHERE id=?", (status, batch_id)
        )

    def set_item_status(self, item_id: str, status: str) -> None:
        self.connection.execute(
            "UPDATE ai_work_items SET status=? WHERE id=?", (status, item_id)
        )

    def cancel_assessment_batch(
        self,
        batch_id: str,
        cancelled_at: str,
        reason: str,
        prompt_id: str = "vacancy-assessment",
    ) -> dict[str, Any]:
        batch = self.connection.execute(
            "SELECT * FROM ai_work_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if batch is None or batch["prompt_id"] != prompt_id:
            raise RuntimeError(f"{prompt_id} batch not found: {batch_id}")
        if (
            batch["status"] == "imported"
            or self.connection.execute(
                "SELECT 1 FROM ai_work_items WHERE batch_id=? AND status='imported' LIMIT 1",
                (batch_id,),
            ).fetchone()
            is not None
        ):
            raise RuntimeError(f"cannot cancel imported assessment batch: {batch_id}")
        if batch["status"] == "cancelled":
            return {
                "batch_id": batch_id,
                "status": "cancelled",
                "cancelled_at": batch["cancelled_at"],
                "reason": batch["cancellation_reason"],
                "cancelled_items": 0,
            }
        cursor = self.connection.execute(
            "UPDATE ai_work_items SET status='cancelled' WHERE batch_id=? AND status<>'imported'",
            (batch_id,),
        )
        self.connection.execute(
            "UPDATE ai_work_batches SET status='cancelled', cancelled_at=?, "
            "cancellation_reason=? WHERE id=?",
            (cancelled_at, reason, batch_id),
        )
        return {
            "batch_id": batch_id,
            "status": "cancelled",
            "cancelled_at": cancelled_at,
            "reason": reason,
            "cancelled_items": cursor.rowcount,
        }

    def assessment_candidates(
        self, source_names: tuple[str, ...], limit: int
    ) -> tuple[dict[str, Any], ...]:
        """Completed detailed assessments belong to the vacancy across all profiles."""
        if not source_names:
            return ()
        placeholders = ",".join("?" for _ in source_names)
        rows = self.connection.execute(
            "WITH ranked AS ("
            "SELECT so.*, MIN(first_seen_at) OVER (PARTITION BY source_name, external_id) "
            "AS discovered_at, ROW_NUMBER() OVER ("
            "PARTITION BY source_name, external_id ORDER BY first_seen_at DESC, id DESC"
            ") AS version_rank FROM source_observations so WHERE NOT EXISTS ("
            "SELECT 1 FROM source_identity_aliases sa WHERE sa.source_name=so.source_name "
            "AND sa.alias=so.external_id)) "
            "SELECT ranked.*, ra.relative_path, ra.media_type, cv.id AS clean_version_id "
            "FROM ranked JOIN raw_artifacts ra ON ra.content_hash=ranked.artifact_hash "
            f"{CURRENT_READY_JOIN}"
            "WHERE ranked.version_rank=1 "
            "AND julianday(ranked.discovered_at)>=julianday('now','-7 days') "
            f"AND ranked.source_name IN ({placeholders}) "
            "AND NOT EXISTS (SELECT 1 FROM job_evaluations je "
            "WHERE je.source_name=ranked.source_name AND je.external_id=ranked.external_id) "
            "AND NOT EXISTS (SELECT 1 FROM ai_work_items wi "
            "JOIN ai_work_batches wb ON wb.id=wi.batch_id "
            "WHERE wi.observation_id=ranked.id AND wb.prompt_id='vacancy-assessment' "
            "AND wi.status IN ('prepared','validated')) "
            "ORDER BY "
            "ranked.first_seen_at DESC, ranked.source_name, ranked.external_id LIMIT ?",
            (*source_names, limit),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def assessment_candidates_by_identity(
        self, identities: tuple[tuple[str, str], ...]
    ) -> tuple[dict[str, Any], ...]:
        candidates: list[dict[str, Any]] = []
        for source_name, external_id in identities:
            row = self.connection.execute(
                "WITH ranked AS ("
                "SELECT so.*, MIN(first_seen_at) OVER (PARTITION BY source_name, external_id) "
                "AS discovered_at, ROW_NUMBER() OVER ("
                "PARTITION BY source_name, external_id ORDER BY first_seen_at DESC, id DESC"
                ") AS version_rank FROM source_observations so WHERE NOT EXISTS ("
                "SELECT 1 FROM source_identity_aliases sa WHERE sa.source_name=so.source_name "
                "AND sa.alias=so.external_id)) "
                "SELECT ranked.*, ra.relative_path, ra.media_type, cv.id AS clean_version_id "
                "FROM ranked JOIN raw_artifacts ra ON ra.content_hash=ranked.artifact_hash "
                f"{CURRENT_READY_JOIN}"
                "WHERE ranked.version_rank=1 AND ranked.source_name=? AND ranked.external_id=? "
                "AND NOT EXISTS (SELECT 1 FROM job_evaluations je "
                "WHERE je.source_name=ranked.source_name AND je.external_id=ranked.external_id) "
                "AND NOT EXISTS (SELECT 1 FROM ai_work_items wi JOIN ai_work_batches wb "
                "ON wb.id=wi.batch_id WHERE wi.observation_id=ranked.id "
                "AND wb.prompt_id='vacancy-assessment' "
                "AND wi.status IN ('prepared','validated'))",
                (source_name, external_id),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    "selected vacancy is not ready, already assessed, queued, or unavailable: "
                    f"{source_name}:{external_id}"
                )
            candidates.append(dict(row))
        return tuple(candidates)

    def screening_candidates(
        self,
        source_names: tuple[str, ...],
        limit: int,
    ) -> tuple[dict[str, Any], ...]:
        """Only new evidence needs screening; profile changes never invalidate results."""
        if not source_names:
            return ()
        placeholders = ",".join("?" for _ in source_names)
        parameters: tuple[Any, ...] = (*source_names,)
        parameters += (limit,)
        rows = self.connection.execute(
            "WITH ranked AS ("
            "SELECT so.*, MIN(first_seen_at) OVER (PARTITION BY source_name, external_id) "
            "AS discovered_at, ROW_NUMBER() OVER ("
            "PARTITION BY source_name, external_id ORDER BY first_seen_at DESC, id DESC"
            ") AS version_rank FROM source_observations so WHERE NOT EXISTS ("
            "SELECT 1 FROM source_identity_aliases sa WHERE sa.source_name=so.source_name "
            "AND sa.alias=so.external_id)) "
            "SELECT ranked.*, ra.relative_path, ra.media_type, cv.id AS clean_version_id "
            "FROM ranked JOIN raw_artifacts ra ON ra.content_hash=ranked.artifact_hash "
            f"{CURRENT_READY_JOIN}"
            "WHERE ranked.version_rank=1 "
            f"AND ranked.source_name IN ({placeholders}) "
            "AND NOT EXISTS (SELECT 1 FROM job_evaluations je "
            "WHERE je.source_name=ranked.source_name "
            "AND je.external_id=ranked.external_id) "
            "AND NOT EXISTS (SELECT 1 FROM vacancy_screenings vs "
            "WHERE vs.source_name=ranked.source_name "
            "AND vs.external_id=ranked.external_id "
            "AND (vs.observation_id=ranked.id OR EXISTS (SELECT 1 FROM "
            "clean_observation_versions cov WHERE cov.observation_id=vs.observation_id "
            "AND cov.version_id=cv.id))) "
            "AND NOT EXISTS (SELECT 1 FROM ai_work_items wi "
            "JOIN ai_work_batches wb ON wb.id=wi.batch_id "
            "JOIN source_observations queued ON queued.id=wi.observation_id "
            "WHERE queued.source_name=ranked.source_name "
            "AND queued.external_id=ranked.external_id AND wb.prompt_id='vacancy-screening' "
            "AND wi.status IN ('prepared','validated') "
            "AND NOT EXISTS (SELECT 1 FROM problem_batches pb WHERE pb.batch_id=wb.id)) "
            "AND NOT EXISTS (SELECT 1 FROM problem_items pi WHERE pi.state='open' "
            "AND (pi.observation_id=ranked.id OR pi.version_id=cv.id)) "
            "AND NOT EXISTS (SELECT 1 FROM ai_work_items wi "
            "JOIN ai_work_batches wb ON wb.id=wi.batch_id "
            "JOIN source_observations queued ON queued.id=wi.observation_id "
            "WHERE queued.source_name=ranked.source_name "
            "AND queued.external_id=ranked.external_id "
            "AND wb.prompt_id='vacancy-assessment' "
            "AND wi.status IN ('prepared','validated')) "
            "ORDER BY ranked.first_seen_at DESC, ranked.source_name, ranked.external_id LIMIT ?",
            parameters,
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def handled_candidate_observations(self) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "WITH ranked AS ("
            "SELECT so.*, ROW_NUMBER() OVER ("
            "PARTITION BY source_name, external_id ORDER BY first_seen_at DESC, id DESC"
            ") AS version_rank FROM source_observations so WHERE NOT EXISTS ("
            "SELECT 1 FROM source_identity_aliases sa WHERE sa.source_name=so.source_name "
            "AND sa.alias=so.external_id)) "
            "SELECT ranked.*, cv.id AS clean_version_id, ra.relative_path, ra.media_type "
            "FROM ranked JOIN raw_artifacts ra ON ra.content_hash=ranked.artifact_hash "
            + CURRENT_READY_JOIN
            + "WHERE ranked.version_rank=1 AND ("
            "EXISTS (SELECT 1 FROM vacancy_screenings vs "
            "WHERE vs.source_name=ranked.source_name AND vs.external_id=ranked.external_id "
            "AND (vs.observation_id=ranked.id OR EXISTS (SELECT 1 FROM "
            "clean_observation_versions cov WHERE cov.observation_id=vs.observation_id "
            "AND cov.version_id=cv.id))) "
            "OR EXISTS (SELECT 1 FROM job_evaluations je "
            "WHERE je.source_name=ranked.source_name AND je.external_id=ranked.external_id))",
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def upsert_screening(self, screening: dict[str, Any]) -> bool:
        existing = self.connection.execute(
            "SELECT id, input_hash FROM vacancy_screenings "
            "WHERE source_name=? AND external_id=? AND profile_id=?",
            (
                screening["source_name"],
                screening["external_id"],
                screening["profile_id"],
            ),
        ).fetchone()
        changed = existing is None or existing["input_hash"] != screening["input_hash"]
        if existing is not None:
            screening["id"] = str(existing["id"])
        self.connection.execute(
            "INSERT INTO vacancy_screenings (id, observation_id, source_name, external_id, "
            "artifact_hash, profile_id, screening_cycle_id, input_hash, suitability_score, "
            "assessment_confidence, "
            "decision, summary, result_json, prompt_id, prompt_version, model_id, created_at, "
            "updated_at) VALUES (:id, :observation_id, :source_name, :external_id, "
            ":artifact_hash, :profile_id, :screening_cycle_id, :input_hash, :suitability_score, "
            ":assessment_confidence, :decision, :summary, :result_json, :prompt_id, "
            ":prompt_version, :model_id, :created_at, :updated_at) "
            "ON CONFLICT(source_name, external_id, profile_id) DO UPDATE SET "
            "observation_id=excluded.observation_id, artifact_hash=excluded.artifact_hash, "
            "screening_cycle_id=excluded.screening_cycle_id, "
            "input_hash=excluded.input_hash, suitability_score=excluded.suitability_score, "
            "assessment_confidence=excluded.assessment_confidence, decision=excluded.decision, "
            "summary=excluded.summary, result_json=excluded.result_json, "
            "prompt_id=excluded.prompt_id, prompt_version=excluded.prompt_version, "
            "model_id=excluded.model_id, updated_at=excluded.updated_at",
            screening,
        )
        return changed

    def pending_screening_batches(self, cycle_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) FROM ai_work_batches WHERE prompt_id='vacancy-screening' "
            "AND cycle_id=? AND status NOT IN ('imported','cancelled')",
            (cycle_id,),
        ).fetchone()
        return int(row[0])

    def screening_history(self, profile_id: str) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT so.*, ra.relative_path, ra.media_type, vs.suitability_score, "
            "vs.assessment_confidence FROM vacancy_screenings vs "
            "JOIN source_observations so ON so.id=vs.observation_id "
            "JOIN raw_artifacts ra ON ra.content_hash=so.artifact_hash "
            "WHERE vs.profile_id=? ORDER BY vs.suitability_score, so.source_name, so.external_id",
            (profile_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def screening_dashboard(
        self, profile_id: str | None, limit: int | None = 5000
    ) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "WITH ranked AS ("
            "SELECT so.*, MIN(first_seen_at) OVER (PARTITION BY source_name, external_id) "
            "AS discovered_at, ROW_NUMBER() OVER ("
            "PARTITION BY source_name, external_id ORDER BY first_seen_at DESC, id DESC"
            ") AS version_rank FROM source_observations so WHERE NOT EXISTS ("
            "SELECT 1 FROM source_identity_aliases sa WHERE sa.source_name=so.source_name "
            "AND sa.alias=so.external_id)) "
            "SELECT ranked.*, ra.relative_path, ra.media_type, cv.id AS clean_version_id, "
            "vs.suitability_score AS screening_score, "
            "vs.assessment_confidence AS screening_confidence, "
            "vs.decision AS screening_decision, vs.summary AS screening_summary, "
            "vs.profile_id AS screening_profile_id, "
            "json_extract(vs.result_json,'$.location_assessment') AS location_assessment_json, "
            "CASE WHEN EXISTS (SELECT 1 FROM ai_work_items wi JOIN ai_work_batches wb "
            "ON wb.id=wi.batch_id JOIN source_observations queued ON queued.id=wi.observation_id "
            "WHERE queued.source_name=ranked.source_name AND queued.external_id=ranked.external_id "
            "AND wb.prompt_id='vacancy-screening' AND wi.status IN ('prepared','validated')) "
            "THEN 1 ELSE 0 END AS screening_queued "
            ", CASE WHEN EXISTS (SELECT 1 FROM ai_work_items wi "
            "JOIN ai_work_batches wb ON wb.id=wi.batch_id "
            "JOIN source_observations queued ON queued.id=wi.observation_id "
            "WHERE queued.source_name=ranked.source_name "
            "AND queued.external_id=ranked.external_id "
            "AND wb.prompt_id='vacancy-assessment' "
            "AND wi.status IN ('prepared','validated')) THEN 1 ELSE 0 END AS assessment_queued "
            "FROM ranked JOIN raw_artifacts ra ON ra.content_hash=ranked.artifact_hash "
            f"{CURRENT_READY_JOIN}"
            "LEFT JOIN vacancy_screenings vs ON vs.id=(SELECT candidate.id "
            "FROM vacancy_screenings candidate WHERE candidate.source_name=ranked.source_name "
            "AND candidate.external_id=ranked.external_id "
            "AND (? IS NULL OR candidate.profile_id=?) "
            "AND (candidate.observation_id=ranked.id OR EXISTS (SELECT 1 FROM "
            "clean_observation_versions cov WHERE cov.observation_id=candidate.observation_id "
            "AND cov.version_id=cv.id)) "
            "ORDER BY candidate.updated_at DESC, candidate.id DESC LIMIT 1) "
            "WHERE ranked.version_rank=1 "
            "ORDER BY COALESCE(vs.suitability_score, -1) DESC, ranked.first_seen_at DESC LIMIT ?",
            (profile_id, profile_id, -1 if limit is None else limit),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def register_profile(self, profile: dict[str, Any]) -> None:
        existing = self.connection.execute(
            "SELECT profile_name, version, content_hash, relative_path "
            "FROM profile_versions WHERE id=?",
            (profile["id"],),
        ).fetchone()
        if existing is not None:
            if (
                existing["profile_name"] != profile["profile_name"]
                or existing["version"] != profile["version"]
                or existing["content_hash"] != profile["content_hash"]
                or existing["relative_path"] != profile["relative_path"]
            ):
                next_version = int(existing["version"]) + 1
                raise RuntimeError(
                    f"profile {profile['id']} is immutable: registered path "
                    f"{existing['relative_path']} has hash {existing['content_hash']}, but "
                    f"{profile['relative_path']} has hash {profile['content_hash']}; restore the "
                    f"registered file or create profile-v{next_version}.md"
                )
            return
        self.connection.execute(
            "INSERT INTO profile_versions "
            "(id, profile_name, version, content_hash, relative_path, created_at) "
            "VALUES (:id, :profile_name, :version, :content_hash, :relative_path, :created_at)",
            profile,
        )

    def profile_version(self, profile_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM profile_versions WHERE id=?", (profile_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def upsert_assessed_job(self, job: dict[str, Any]) -> tuple[str, bool]:
        existing = self.connection.execute(
            "SELECT id, input_hash FROM canonical_jobs WHERE source_name=? AND external_id=?",
            (job["source_name"], job["external_id"]),
        ).fetchone()
        changed = existing is None or existing["input_hash"] != job["input_hash"]
        if existing is not None:
            job["id"] = str(existing["id"])
        self.connection.execute(
            "INSERT INTO canonical_jobs ("
            "id, observation_id, input_hash, source_name, external_id, title, employer, "
            "locations_json, remote_policy, employment_type, seniority, description_summary, "
            "requirements_json, application_url, publication_date, application_deadline, "
            "language, keywords_json, confidence, uncertainty_notes_json, result_json, "
            "prompt_id, prompt_version, model_id, created_at, updated_at, working_time, "
            "contract_type, contract_end_date, start_date, salary, responsibilities_json, "
            "mandatory_requirements_json, preferred_qualifications_json, "
            "education_requirements_json, experience_requirements_json, methods_tools_json, "
            "languages_required_json, occupational_categories_json, application_method, "
            "contact_details_json, reference_number, validity_status) VALUES ("
            ":id, :observation_id, :input_hash, :source_name, :external_id, :title, :employer, "
            ":locations_json, :remote_policy, :employment_type, :seniority, "
            ":description_summary, :requirements_json, :application_url, :publication_date, "
            ":application_deadline, :language, :keywords_json, :confidence, "
            ":uncertainty_notes_json, :result_json, :prompt_id, :prompt_version, :model_id, "
            ":created_at, :updated_at, :working_time, :contract_type, :contract_end_date, "
            ":start_date, :salary, :responsibilities_json, :mandatory_requirements_json, "
            ":preferred_qualifications_json, :education_requirements_json, "
            ":experience_requirements_json, :methods_tools_json, :languages_required_json, "
            ":occupational_categories_json, :application_method, :contact_details_json, "
            ":reference_number, :validity_status) "
            "ON CONFLICT(source_name, external_id) DO UPDATE SET "
            "observation_id=excluded.observation_id, input_hash=excluded.input_hash, "
            "title=excluded.title, employer=excluded.employer, "
            "locations_json=excluded.locations_json, remote_policy=excluded.remote_policy, "
            "employment_type=excluded.employment_type, seniority=excluded.seniority, "
            "description_summary=excluded.description_summary, "
            "requirements_json=excluded.requirements_json, "
            "application_url=excluded.application_url, publication_date=excluded.publication_date, "
            "application_deadline=excluded.application_deadline, language=excluded.language, "
            "keywords_json=excluded.keywords_json, confidence=excluded.confidence, "
            "uncertainty_notes_json=excluded.uncertainty_notes_json, "
            "result_json=excluded.result_json, prompt_id=excluded.prompt_id, "
            "prompt_version=excluded.prompt_version, model_id=excluded.model_id, "
            "updated_at=excluded.updated_at, working_time=excluded.working_time, "
            "contract_type=excluded.contract_type, contract_end_date=excluded.contract_end_date, "
            "start_date=excluded.start_date, salary=excluded.salary, "
            "responsibilities_json=excluded.responsibilities_json, "
            "mandatory_requirements_json=excluded.mandatory_requirements_json, "
            "preferred_qualifications_json=excluded.preferred_qualifications_json, "
            "education_requirements_json=excluded.education_requirements_json, "
            "experience_requirements_json=excluded.experience_requirements_json, "
            "methods_tools_json=excluded.methods_tools_json, "
            "languages_required_json=excluded.languages_required_json, "
            "occupational_categories_json=excluded.occupational_categories_json, "
            "application_method=excluded.application_method, "
            "contact_details_json=excluded.contact_details_json, "
            "reference_number=excluded.reference_number, validity_status=excluded.validity_status",
            job,
        )
        return str(job["id"]), changed

    def upsert_evaluation(self, evaluation: dict[str, Any]) -> bool:
        existing = self.connection.execute(
            "SELECT id, input_hash FROM job_evaluations "
            "WHERE source_name=? AND external_id=? AND profile_id=?",
            (
                evaluation["source_name"],
                evaluation["external_id"],
                evaluation["profile_id"],
            ),
        ).fetchone()
        changed = existing is None or existing["input_hash"] != evaluation["input_hash"]
        if existing is not None:
            evaluation["id"] = str(existing["id"])
        self.connection.execute(
            "INSERT INTO job_evaluations (id, job_id, observation_id, source_name, external_id, "
            "artifact_hash, profile_id, input_hash, suitability_score, assessment_confidence, "
            "recommendation, summary, strengths_json, gaps_json, deal_breakers_json, "
            "missing_information_json, result_json, prompt_id, prompt_version, model_id, "
            "review_status, created_at, updated_at) VALUES ("
            ":id, :job_id, :observation_id, :source_name, :external_id, :artifact_hash, "
            ":profile_id, :input_hash, :suitability_score, :assessment_confidence, "
            ":recommendation, :summary, :strengths_json, :gaps_json, :deal_breakers_json, "
            ":missing_information_json, :result_json, :prompt_id, :prompt_version, :model_id, "
            "'new', :created_at, :updated_at) "
            "ON CONFLICT(source_name, external_id, profile_id) DO UPDATE SET "
            "job_id=excluded.job_id, observation_id=excluded.observation_id, "
            "artifact_hash=excluded.artifact_hash, input_hash=excluded.input_hash, "
            "suitability_score=excluded.suitability_score, "
            "assessment_confidence=excluded.assessment_confidence, "
            "recommendation=excluded.recommendation, summary=excluded.summary, "
            "strengths_json=excluded.strengths_json, gaps_json=excluded.gaps_json, "
            "deal_breakers_json=excluded.deal_breakers_json, "
            "missing_information_json=excluded.missing_information_json, "
            "result_json=excluded.result_json, prompt_id=excluded.prompt_id, "
            "prompt_version=excluded.prompt_version, model_id=excluded.model_id, "
            "review_status='new', updated_at=excluded.updated_at",
            evaluation,
        )
        return changed

    def evaluations(
        self, profile_id: str | None, limit: int | None, only_new: bool = False
    ) -> tuple[dict[str, Any], ...]:
        status_clause = " AND je.review_status='new'" if only_new else ""
        rows = self.connection.execute(
            "SELECT je.*, cj.title, cj.employer, cj.locations_json, "
            "cj.application_deadline, cj.application_url, so.source_url, so.first_seen_at "
            "FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY source_name, external_id "
            "ORDER BY updated_at DESC, id DESC) AS assessment_rank FROM job_evaluations "
            "WHERE (? IS NULL OR profile_id=?)) je "
            "JOIN canonical_jobs cj ON cj.id=je.job_id "
            "JOIN source_observations so ON so.id=je.observation_id "
            "WHERE je.assessment_rank=1" + status_clause + " "
            "ORDER BY je.suitability_score DESC, "
            "CASE WHEN cj.application_deadline IS NULL THEN 1 ELSE 0 END, "
            "cj.application_deadline, je.updated_at DESC LIMIT ?",
            (profile_id, profile_id, -1 if limit is None else limit),
        ).fetchall()
        return tuple(dict(row) for row in rows)


class SourceHealthRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def record(
        self, source_name: str, attempted_at: str, outcome: SourceOutcome
    ) -> None:
        succeeded = outcome.status == AttemptStatus.SUCCEEDED
        item_count = outcome.observations_created + outcome.observations_unchanged
        self.connection.execute(
            "INSERT INTO source_health "
            "(source_name, state, last_attempt_at, last_success_at, consecutive_failures, "
            "last_item_count, last_error_code, last_error_message, checkpoint) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(source_name) DO UPDATE SET "
            "state=excluded.state, last_attempt_at=excluded.last_attempt_at, "
            "last_success_at=CASE WHEN excluded.state='healthy' "
            "THEN excluded.last_success_at ELSE source_health.last_success_at END, "
            "consecutive_failures=CASE WHEN excluded.state='healthy' THEN 0 "
            "ELSE source_health.consecutive_failures + 1 END, "
            "last_item_count=excluded.last_item_count, "
            "last_error_code=excluded.last_error_code, "
            "last_error_message=excluded.last_error_message, "
            "checkpoint=COALESCE(excluded.checkpoint, source_health.checkpoint)",
            (
                source_name,
                "healthy" if succeeded else "degraded",
                attempted_at,
                attempted_at if succeeded else None,
                0 if succeeded else 1,
                item_count,
                outcome.error_code,
                outcome.error_message,
                outcome.checkpoint,
            ),
        )

    def get(self, source_name: str | None = None) -> tuple[dict[str, Any], ...]:
        if source_name is None:
            rows = self.connection.execute(
                "SELECT * FROM source_health ORDER BY source_name"
            ).fetchall()
        else:
            rows = self.connection.execute(
                "SELECT * FROM source_health WHERE source_name=?", (source_name,)
            ).fetchall()
        return tuple(dict(row) for row in rows)


class ReviewRepository:
    """Persist logical vacancy groups and profile-specific human decisions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def ensure_group(
        self,
        members: tuple[tuple[str, str], ...],
        proposed_id: str,
        timestamp: str,
    ) -> str:
        if not members:
            raise ValueError("a vacancy group requires at least one member")
        clauses = " OR ".join("(source_name=? AND external_id=?)" for _ in members)
        parameters = tuple(value for member in members for value in member)
        rows = self.connection.execute(
            f"SELECT DISTINCT group_id FROM vacancy_group_members WHERE {clauses}",
            parameters,
        ).fetchall()
        existing = sorted(str(row["group_id"]) for row in rows)
        group_id = existing[0] if existing else proposed_id
        if not existing:
            self.connection.execute(
                "INSERT INTO vacancy_groups(id, created_at, updated_at) VALUES (?, ?, ?)",
                (group_id, timestamp, timestamp),
            )
        for secondary in existing[1:]:
            self._merge_group(group_id, secondary)
        self.connection.executemany(
            "INSERT OR IGNORE INTO vacancy_group_members"
            "(group_id, source_name, external_id, added_at) VALUES (?, ?, ?, ?)",
            (
                (group_id, source, external_id, timestamp)
                for source, external_id in members
            ),
        )
        self.connection.execute(
            "UPDATE vacancy_groups SET updated_at=? WHERE id=?", (timestamp, group_id)
        )
        return group_id

    def group_for_members(self, members: tuple[tuple[str, str], ...]) -> str | None:
        """Find an established group without writing or merging groups."""
        if not members:
            return None
        clauses = " OR ".join("(source_name=? AND external_id=?)" for _ in members)
        rows = self.connection.execute(
            f"SELECT source_name,external_id,group_id FROM vacancy_group_members WHERE {clauses}",
            tuple(value for member in members for value in member),
        ).fetchall()
        groups = {str(row["group_id"]) for row in rows}
        if len(rows) == len(set(members)) and len(groups) == 1:
            return groups.pop()
        return None

    def _merge_group(self, primary: str, secondary: str) -> None:
        decisions = self.connection.execute(
            "SELECT profile_id, status, note, decided_at, updated_at "
            "FROM vacancy_review_decisions WHERE group_id IN (?, ?) "
            "ORDER BY profile_id, group_id",
            (primary, secondary),
        ).fetchall()
        by_profile: dict[str, list[sqlite3.Row]] = {}
        for decision in decisions:
            by_profile.setdefault(str(decision["profile_id"]), []).append(decision)
        for profile_decisions in by_profile.values():
            choices = {
                (str(item["status"]), item["note"]) for item in profile_decisions
            }
            if len(choices) > 1:
                raise RuntimeError(
                    "cannot merge vacancy groups with conflicting review decisions"
                )
        self.connection.execute(
            "INSERT OR IGNORE INTO vacancy_review_decisions"
            "(group_id, profile_id, status, note, decided_at, updated_at) "
            "SELECT ?, profile_id, status, note, decided_at, updated_at "
            "FROM vacancy_review_decisions WHERE group_id=?",
            (primary, secondary),
        )
        self.connection.execute(
            "DELETE FROM vacancy_review_decisions WHERE group_id=?", (secondary,)
        )
        self.connection.execute(
            "UPDATE vacancy_group_members SET group_id=? WHERE group_id=?",
            (primary, secondary),
        )
        self.connection.execute("DELETE FROM vacancy_groups WHERE id=?", (secondary,))

    def decision(self, group_id: str, profile_id: str | None) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT status, note, decided_at, updated_at FROM vacancy_review_decisions "
            "WHERE group_id=? AND (? IS NULL OR profile_id=?) "
            "ORDER BY updated_at DESC, profile_id DESC LIMIT 1",
            (group_id, profile_id, profile_id),
        ).fetchone()
        if row is None:
            return {
                "status": "new",
                "note": None,
                "decided_at": None,
                "updated_at": None,
            }
        return dict(row)

    def set_decision(
        self,
        group_id: str,
        profile_id: str,
        status: str,
        note: str | None,
        timestamp: str,
    ) -> bool:
        exists = self.connection.execute(
            "SELECT 1 FROM vacancy_groups WHERE id=?", (group_id,)
        ).fetchone()
        if exists is None:
            return False
        self.connection.execute(
            "INSERT INTO vacancy_review_decisions"
            "(group_id, profile_id, status, note, decided_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(group_id, profile_id) DO UPDATE SET "
            "status=excluded.status, note=excluded.note, "
            "decided_at=excluded.decided_at, updated_at=excluded.updated_at",
            (group_id, profile_id, status, note, timestamp, timestamp),
        )
        return True

    def list_decisions(self, profile_id: str) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT vg.id AS group_id, COALESCE(vrd.status, 'new') AS status, "
            "vrd.note, vrd.decided_at, COUNT(vgm.external_id) AS source_records, "
            "MIN(cj.title) AS title, MIN(cj.employer) AS employer "
            "FROM vacancy_groups vg "
            "JOIN vacancy_group_members vgm ON vgm.group_id=vg.id "
            "LEFT JOIN vacancy_review_decisions vrd "
            "ON vrd.group_id=vg.id AND vrd.profile_id=? "
            "LEFT JOIN canonical_jobs cj ON cj.source_name=vgm.source_name "
            "AND cj.external_id=vgm.external_id "
            "GROUP BY vg.id, vrd.status, vrd.note, vrd.decided_at "
            "ORDER BY CASE COALESCE(vrd.status, 'new') "
            "WHEN 'interested' THEN 0 WHEN 'deferred' THEN 1 WHEN 'new' THEN 2 "
            "WHEN 'applied' THEN 3 WHEN 'invited' THEN 4 ELSE 5 END, title",
            (profile_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def workspace(self, group_id: str, profile_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT application_id, contact_email, submission_route, sender_email, is_mock, "
            "relative_path, slug, created_at "
            "FROM application_workspaces "
            "WHERE group_id=? AND profile_id=?",
            (group_id, profile_id),
        ).fetchone()
        return dict(row) if row is not None else None

    def record_workspace(
        self,
        group_id: str,
        profile_id: str,
        relative_path: str,
        slug: str,
        application_id: str,
        contact_email: str | None,
        submission_route: str,
        sender_email: str | None,
        created_at: str,
    ) -> None:
        self.connection.execute(
            "INSERT INTO application_workspaces"
            "(group_id, profile_id, relative_path, slug, application_id, "
            "contact_email, submission_route, sender_email, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                group_id,
                profile_id,
                relative_path,
                slug,
                application_id,
                contact_email,
                submission_route,
                sender_email,
                created_at,
            ),
        )

    def workspace_by_application_id(self, application_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT group_id, profile_id, relative_path, slug, application_id, contact_email, "
            "submission_route, sender_email, is_mock, created_at FROM application_workspaces "
            "WHERE application_id=?",
            (application_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def workspace_by_contact_email(self, contact_email: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT group_id, profile_id, relative_path, slug, application_id, contact_email, "
            "submission_route, sender_email, is_mock, created_at FROM application_workspaces "
            "WHERE lower(contact_email)=lower(?)",
            (contact_email.strip(),),
        ).fetchone()
        return dict(row) if row is not None else None

    def set_workspace_contact_email(
        self, application_id: str, contact_email: str
    ) -> None:
        self.connection.execute(
            "UPDATE application_workspaces SET contact_email=? WHERE application_id=?",
            (contact_email, application_id),
        )

    def set_workspace_submission(
        self,
        application_id: str,
        submission_route: str,
        sender_email: str | None,
    ) -> None:
        self.connection.execute(
            "UPDATE application_workspaces SET submission_route=?, sender_email=? "
            "WHERE application_id=?",
            (submission_route, sender_email, application_id),
        )

    def record_status_event(
        self,
        application_id: str,
        group_id: str,
        profile_id: str,
        previous_status: str,
        new_status: str,
        source: str,
        reason: str | None,
        timestamp: str,
        gmail_message_id: str | None = None,
    ) -> None:
        self.connection.execute(
            "INSERT INTO application_status_events "
            "(application_id, group_id, profile_id, previous_status, new_status, source, "
            "gmail_message_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                application_id,
                group_id,
                profile_id,
                previous_status,
                new_status,
                source,
                gmail_message_id,
                reason,
                timestamp,
            ),
        )

    def status_events(self, application_id: str) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT * FROM application_status_events WHERE application_id=? "
            "ORDER BY created_at, id",
            (application_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def register_mock_workspace(self, profile_id: str, timestamp: str) -> bool:
        existing = self.workspace_by_application_id("00")
        if existing is not None:
            if not existing["is_mock"]:
                raise RuntimeError(
                    "application ID 00 is already used by a non-mock workspace"
                )
            return False
        group_id = "vgrp_mock_email_workflow"
        self.connection.execute(
            "INSERT OR IGNORE INTO vacancy_groups(id, created_at, updated_at) VALUES (?, ?, ?)",
            (group_id, timestamp, timestamp),
        )
        self.connection.execute(
            "INSERT INTO vacancy_review_decisions "
            "(group_id, profile_id, status, note, decided_at, updated_at) "
            "VALUES (?, ?, 'applied', ?, ?, ?)",
            (group_id, profile_id, "Reserved mock application", timestamp, timestamp),
        )
        self.connection.execute(
            "INSERT INTO application_workspaces "
            "(group_id, profile_id, relative_path, slug, application_id, contact_email, "
            "submission_route, sender_email, created_at, is_mock) "
            "VALUES (?, ?, ?, 'mock', '00', ?, 'unknown', NULL, ?, 1)",
            (
                group_id,
                profile_id,
                "applications/02_applied/00_mock",
                "applicant+mock@example.invalid",
                timestamp,
            ),
        )
        self.record_status_event(
            "00",
            group_id,
            profile_id,
            "new",
            "applied",
            "manual",
            "Reserved mock application setup",
            timestamp,
        )
        return True

    def update_workspace_path(
        self,
        group_id: str,
        profile_id: str,
        relative_path: str,
        slug: str | None = None,
    ) -> None:
        if slug is None:
            self.connection.execute(
                "UPDATE application_workspaces SET relative_path=? "
                "WHERE group_id=? AND profile_id=?",
                (relative_path, group_id, profile_id),
            )
        else:
            self.connection.execute(
                "UPDATE application_workspaces SET relative_path=?, slug=? "
                "WHERE group_id=? AND profile_id=?",
                (relative_path, slug, group_id, profile_id),
            )

    def workspaces(self, profile_id: str | None) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT group_id, relative_path, slug, application_id, contact_email, "
            "submission_route, sender_email, is_mock, created_at "
            "FROM application_workspaces "
            "WHERE (? IS NULL OR profile_id=?) AND is_mock=0 ORDER BY created_at DESC",
            (profile_id, profile_id),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def group_records(
        self, group_id: str, profile_id: str
    ) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT vg.id AS group_id, COALESCE(vrd.status, 'new') AS review_status, "
            "vrd.note AS review_note, vgm.source_name, vgm.external_id, "
            "cj.title, cj.employer, cj.application_deadline, cj.application_url, "
            "so.source_url, "
            "je.id AS evaluation_id, je.suitability_score, je.assessment_confidence "
            "FROM vacancy_groups vg "
            "JOIN vacancy_group_members vgm ON vgm.group_id=vg.id "
            "LEFT JOIN vacancy_review_decisions vrd "
            "ON vrd.group_id=vg.id AND vrd.profile_id=? "
            "LEFT JOIN canonical_jobs cj ON cj.source_name=vgm.source_name "
            "AND cj.external_id=vgm.external_id "
            "LEFT JOIN source_observations so ON so.id=cj.observation_id "
            "LEFT JOIN job_evaluations je ON je.source_name=vgm.source_name "
            "AND je.external_id=vgm.external_id AND je.profile_id=? "
            "WHERE vg.id=? "
            "ORDER BY je.suitability_score DESC, je.assessment_confidence DESC, "
            "vgm.source_name, vgm.external_id",
            (profile_id, profile_id, group_id),
        ).fetchall()
        return tuple(dict(row) for row in rows)


class EmailRepository:
    """Persist immutable Gmail messages and the incremental scan cursor."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def known_message_ids(self, message_ids: tuple[str, ...]) -> frozenset[str]:
        if not message_ids:
            return frozenset()
        placeholders = ",".join("?" for _ in message_ids)
        rows = self.connection.execute(
            f"SELECT gmail_message_id FROM application_emails "
            f"WHERE gmail_message_id IN ({placeholders})",
            message_ids,
        ).fetchall()
        return frozenset(str(row[0]) for row in rows)

    def aliases(self) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT application_id, contact_email, relative_path, is_mock "
            "FROM application_workspaces "
            "WHERE contact_email IS NOT NULL ORDER BY application_id"
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def add_message(self, message: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO application_emails "
            "(gmail_message_id, gmail_thread_id, application_id, sender, recipients, subject, "
            "received_at, raw_artifact_path, raw_artifact_hash, ingested_at, routing_status, "
            "routing_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                message["gmail_message_id"],
                message["gmail_thread_id"],
                message["application_id"],
                message["sender"],
                message["recipients"],
                message["subject"],
                message["received_at"],
                message["raw_artifact_path"],
                message["raw_artifact_hash"],
                message["ingested_at"],
                message["routing_status"],
                message["routing_reason"],
            ),
        )

    def sync_state(self, account_address: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM email_sync_state WHERE account_address=?", (account_address,)
        ).fetchone()
        return dict(row) if row is not None else None

    def save_sync_state(
        self,
        account_address: str,
        scanned_at: str,
        result: str,
        history_id: str | None = None,
    ) -> None:
        self.connection.execute(
            "INSERT INTO email_sync_state "
            "(account_address, last_successful_scan_at, gmail_history_id, last_scan_result, "
            "last_error, updated_at) VALUES (?, ?, ?, ?, NULL, ?) "
            "ON CONFLICT(account_address) DO UPDATE SET "
            "last_successful_scan_at=excluded.last_successful_scan_at, "
            "gmail_history_id=excluded.gmail_history_id, "
            "last_scan_result=excluded.last_scan_result, last_error=NULL, "
            "updated_at=excluded.updated_at",
            (account_address, scanned_at, history_id, result, scanned_at),
        )

    def list_messages(self, limit: int = 100) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT ae.*, CASE WHEN EXISTS (SELECT 1 FROM email_assessments ea "
            "WHERE ea.gmail_message_id=ae.gmail_message_id) THEN 1 ELSE 0 END AS assessed "
            "FROM application_emails ae ORDER BY ae.ingested_at DESC, ae.gmail_message_id LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def assessment_candidates(
        self, prompt_id: str, prompt_version: int, limit: int
    ) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT ae.* FROM application_emails ae "
            "WHERE ae.routing_status IN ('routed', 'unmatched') "
            "AND NOT EXISTS (SELECT 1 FROM email_assessments ea "
            "WHERE ea.gmail_message_id=ae.gmail_message_id AND ea.prompt_id=? "
            "AND ea.prompt_version=?) "
            "AND NOT EXISTS (SELECT 1 FROM email_assessment_items eai "
            "JOIN email_assessment_batches eab ON eab.id=eai.batch_id "
            "WHERE eai.gmail_message_id=ae.gmail_message_id "
            "AND eab.prompt_id=? AND eab.prompt_version=? "
            "AND eai.status IN ('prepared', 'validated') "
            "AND NOT EXISTS (SELECT 1 FROM problem_batches pb WHERE pb.batch_id=eab.id)) "
            "AND NOT EXISTS (SELECT 1 FROM problem_items pi WHERE pi.state='open' "
            "AND pi.gmail_message_id=ae.gmail_message_id) "
            "ORDER BY ae.received_at, ae.gmail_message_id LIMIT ?",
            (prompt_id, prompt_version, prompt_id, prompt_version, limit),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def route_from_thread(self, message_id: str, thread_id: str) -> str | None:
        rows = self.connection.execute(
            "SELECT DISTINCT COALESCE(ae.application_id, ea.deterministic_application_id, "
            "ea.suggested_application_id) AS application_id FROM application_emails ae "
            "LEFT JOIN email_assessments ea ON ea.gmail_message_id=ae.gmail_message_id "
            "AND ea.confidence>=90 AND ea.suspicious_content=0 "
            "AND ea.classification NOT IN ('unrelated', 'ambiguous') "
            "WHERE ae.gmail_thread_id=? AND ae.gmail_message_id<>? "
            "AND ae.routing_status NOT IN ('multiple', 'failed')",
            (thread_id, message_id),
        ).fetchall()
        ids = {
            str(row["application_id"])
            for row in rows
            if row["application_id"] is not None
        }
        if len(ids) != 1:
            return None
        application_id = ids.pop()
        cursor = self.connection.execute(
            "UPDATE application_emails SET application_id=?,routing_status='routed',"
            "routing_reason=? "
            "WHERE gmail_message_id=? AND routing_status='unmatched' AND application_id IS NULL",
            (
                application_id,
                "Unambiguous application match from saved Gmail thread",
                message_id,
            ),
        )
        return application_id if cursor.rowcount == 1 else None

    def application_contexts(self) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT aw.application_id, aw.contact_email, aw.submission_route, aw.is_mock, "
            "aw.relative_path, COALESCE(vrd.status, 'new') AS current_status, "
            "CASE WHEN aw.is_mock=1 THEN 'Mock email lifecycle test' "
            "ELSE MIN(cj.title) END AS title, "
            "CASE WHEN aw.is_mock=1 THEN 'Mock Employer' "
            "ELSE MIN(cj.employer) END AS employer, "
            "(SELECT MIN(ase.created_at) FROM application_status_events ase "
            "WHERE ase.application_id=aw.application_id AND ase.new_status='applied') "
            "AS submitted_at "
            "FROM application_workspaces aw "
            "LEFT JOIN vacancy_review_decisions vrd ON vrd.group_id=aw.group_id "
            "AND vrd.profile_id=aw.profile_id "
            "LEFT JOIN vacancy_group_members vgm ON vgm.group_id=aw.group_id "
            "LEFT JOIN canonical_jobs cj ON cj.source_name=vgm.source_name "
            "AND cj.external_id=vgm.external_id "
            "GROUP BY aw.application_id ORDER BY aw.application_id"
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def dashboard_messages(self) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT ae.gmail_message_id, ae.gmail_thread_id, ae.application_id, ae.sender, "
            "ae.recipients, ae.subject, ae.received_at, ae.routing_status, ae.routing_reason, "
            "ea.suggested_application_id, ea.classification, ea.confidence, ea.reason, "
            "ea.evidence_json, ea.suspicious_content, ea.assessed_at, ea.applied_transition_at, "
            "COALESCE(ea.deterministic_application_id, ea.suggested_application_id, "
            "ae.application_id) AS assessed_application_id "
            "FROM application_emails ae LEFT JOIN email_assessments ea "
            "ON ea.gmail_message_id=ae.gmail_message_id AND ea.prompt_id='email-assessment' "
            "AND ea.prompt_version=(SELECT MAX(latest.prompt_version) "
            "FROM email_assessments latest WHERE latest.gmail_message_id=ae.gmail_message_id "
            "AND latest.prompt_id='email-assessment') "
            "ORDER BY ae.received_at DESC, ae.gmail_message_id"
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def create_assessment_batch(
        self,
        batch: dict[str, Any],
        items: tuple[dict[str, str], ...],
    ) -> None:
        self.connection.execute(
            "INSERT INTO email_assessment_batches "
            "(id, prompt_id, prompt_version, manifest_hash, created_at, status, "
            "input_directory, output_path, item_count) "
            "VALUES (?, ?, ?, ?, ?, 'prepared', ?, ?, ?)",
            (
                batch["id"],
                batch["prompt_id"],
                batch["prompt_version"],
                batch["manifest_hash"],
                batch["created_at"],
                batch["input_directory"],
                batch["output_path"],
                len(items),
            ),
        )
        self.connection.executemany(
            "INSERT INTO email_assessment_items "
            "(id, batch_id, gmail_message_id, input_hash, status) "
            "VALUES (?, ?, ?, ?, 'prepared')",
            (
                (item["id"], batch["id"], item["gmail_message_id"], item["input_hash"])
                for item in items
            ),
        )

    def assessment_batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM email_assessment_batches WHERE id=?", (batch_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def assessment_batch_items(self, batch_id: str) -> tuple[dict[str, Any], ...]:
        rows = self.connection.execute(
            "SELECT * FROM email_assessment_items WHERE batch_id=? ORDER BY id",
            (batch_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def set_assessment_item_status(self, item_id: str, status: str) -> None:
        self.connection.execute(
            "UPDATE email_assessment_items SET status=? WHERE id=?", (status, item_id)
        )

    def set_assessment_status(self, batch_id: str, status: str) -> None:
        self.connection.execute(
            "UPDATE email_assessment_batches SET status=? WHERE id=?",
            (status, batch_id),
        )

    def save_assessment(self, assessment: dict[str, Any]) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO email_assessments "
            "(gmail_message_id, prompt_id, prompt_version, input_hash, "
            "deterministic_application_id, suggested_application_id, classification, "
            "confidence, reason, evidence_json, proposed_status, suspicious_content, model_id, "
            "result_json, assessed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                assessment["gmail_message_id"],
                assessment["prompt_id"],
                assessment["prompt_version"],
                assessment["input_hash"],
                assessment["deterministic_application_id"],
                assessment["suggested_application_id"],
                assessment["classification"],
                assessment["confidence"],
                assessment["reason"],
                assessment["evidence_json"],
                assessment["proposed_status"],
                assessment["suspicious_content"],
                assessment["model_id"],
                assessment["result_json"],
                assessment["assessed_at"],
            ),
        )
        return cursor.rowcount == 1

    def assessment(
        self, gmail_message_id: str, prompt_id: str, prompt_version: int
    ) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM email_assessments WHERE gmail_message_id=? "
            "AND prompt_id=? AND prompt_version=?",
            (gmail_message_id, prompt_id, prompt_version),
        ).fetchone()
        return dict(row) if row is not None else None

    def mark_transition_applied(
        self,
        gmail_message_id: str,
        prompt_id: str,
        prompt_version: int,
        timestamp: str,
    ) -> None:
        cursor = self.connection.execute(
            "UPDATE email_assessments SET applied_transition_at=? "
            "WHERE gmail_message_id=? AND prompt_id=? AND prompt_version=? "
            "AND applied_transition_at IS NULL",
            (timestamp, gmail_message_id, prompt_id, prompt_version),
        )
        if cursor.rowcount != 1:
            raise RuntimeError(
                f"email transition was already applied: {gmail_message_id}"
            )


class UnitOfWork(AbstractContextManager["UnitOfWork"]):
    def __init__(self, database: Database, *, read_only: bool = False) -> None:
        self.database = database
        self.read_only = read_only
        self.connection: sqlite3.Connection | None = None
        self.runs: RunRepository
        self.evidence: EvidenceRepository
        self.source_health: SourceHealthRepository
        self.normalization: NormalizationRepository
        self.reviews: ReviewRepository
        self.emails: EmailRepository

    def __enter__(self) -> Self:
        self.connection = (
            self.database.connect(read_only=True)
            if self.read_only
            else self.database.connect()
        )
        try:
            self.connection.execute("BEGIN" if self.read_only else "BEGIN IMMEDIATE")
        except BaseException:
            self.connection.close()
            raise
        self.runs = RunRepository(self.connection)
        self.evidence = EvidenceRepository(self.connection)
        self.source_health = SourceHealthRepository(self.connection)
        self.normalization = NormalizationRepository(self.connection)
        self.reviews = ReviewRepository(self.connection)
        self.emails = EmailRepository(self.connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        assert self.connection is not None
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()
        return False
