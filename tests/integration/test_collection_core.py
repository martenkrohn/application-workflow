import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from job_system.adapters.sources.fake import FakeSource
from job_system.application.collection import CollectionService
from job_system.domain.models import RunStatus
from job_system.infrastructure.artifacts import FileArtifactStore
from job_system.infrastructure.config import Settings
from job_system.infrastructure.database import Database
from job_system.infrastructure.services import Sha256Hasher, SystemClock, UuidGenerator


def settings_for(root: Path) -> Settings:
    data = root / "var"
    return Settings(
        root,
        data,
        data / "db" / "jobs.sqlite3",
        data / "locks" / "daily.lock",
        (),
    )


def service_for(settings: Settings) -> CollectionService:
    database = Database(settings.database_path)
    database.migrate()
    return CollectionService(
        settings,
        database,
        FileArtifactStore(settings.data_dir, Sha256Hasher()),
        SystemClock(),
        UuidGenerator(),
    )


def scalar(database: Path, query: str) -> int:
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(query).fetchone()
    assert row is not None
    return int(row[0])


def test_repeated_collection_is_idempotent(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    service = service_for(settings)

    first = service.run([FakeSource("alpha"), FakeSource("beta")])
    second = service.run([FakeSource("alpha"), FakeSource("beta")])

    assert first.status == RunStatus.SUCCEEDED
    assert second.status == RunStatus.SUCCEEDED
    assert scalar(settings.database_path, "SELECT COUNT(*) FROM collection_runs") == 2
    assert scalar(settings.database_path, "SELECT COUNT(*) FROM source_observations") == 2
    assert scalar(settings.database_path, "SELECT COUNT(*) FROM raw_artifacts") == 2
    assert scalar(settings.database_path, "SELECT COUNT(*) FROM artifact_retrievals") == 4
    assert sum(item.observations_unchanged for item in second.sources) == 2


def test_collection_reports_source_progress(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    started: list[str] = []
    finished: list[str] = []

    outcome = service_for(settings).run(
        [FakeSource("alpha"), FakeSource("beta")],
        on_source_start=started.append,
        on_source_finish=lambda item: finished.append(item.source_name),
    )

    assert outcome.status == RunStatus.SUCCEEDED
    assert started == ["alpha", "beta"]
    assert finished == ["alpha", "beta"]


def test_source_failure_is_partial_and_recoverable(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    service = service_for(settings)

    partial = service.run([FakeSource("alpha"), FakeSource("beta", fail=True)])
    recovered = service.run([FakeSource("alpha"), FakeSource("beta")])

    assert partial.status == RunStatus.PARTIAL
    assert partial.sources[1].error_code == "FakeSourceError"
    assert recovered.status == RunStatus.SUCCEEDED
    assert scalar(settings.database_path, "SELECT COUNT(*) FROM source_observations") == 2
    summary = json.loads((settings.data_dir / partial.summary_path).read_text(encoding="utf-8"))
    assert summary["status"] == "partial"
    assert "local logs" in summary["recovery"]
    assert "jobs collect" in summary["recovery"]
    log = (settings.data_dir / "logs" / f"{partial.run_id}.jsonl").read_text(encoding="utf-8")
    assert '"operation":"source_finished"' in log
    with closing(sqlite3.connect(settings.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        alpha = connection.execute(
            "SELECT * FROM source_health WHERE source_name='alpha'"
        ).fetchone()
        beta = connection.execute("SELECT * FROM source_health WHERE source_name='beta'").fetchone()
    assert alpha is not None and dict(alpha)["state"] == "healthy"
    assert beta is not None and dict(beta)["state"] == "healthy"
    assert dict(beta)["consecutive_failures"] == 0
    assert dict(beta)["last_success_at"] is not None
    assert dict(beta)["checkpoint"] == recovered.sources[1].checkpoint


def test_source_health_retains_checkpoint_when_later_attempt_fails(
    tmp_path: Path,
) -> None:
    settings = settings_for(tmp_path)
    service = service_for(settings)

    succeeded = service.run([FakeSource("alpha")])
    failed = service.run([FakeSource("alpha", fail=True)])

    with Database(settings.database_path).unit_of_work() as work:
        health = work.source_health.get("alpha")
    assert failed.status == RunStatus.FAILED
    assert health[0]["state"] == "degraded"
    assert health[0]["consecutive_failures"] == 1
    assert health[0]["checkpoint"] == succeeded.sources[0].checkpoint


def test_all_sources_failing_marks_run_failed(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    outcome = service_for(settings).run(
        [FakeSource("alpha", fail=True), FakeSource("beta", fail=True)]
    )

    assert outcome.status == RunStatus.FAILED


def test_read_only_transaction_does_not_reserve_writer_lock(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_path)
    database.migrate()
    with database.unit_of_work() as writer:
        writer.runs.create("pending", "1", "fingerprint", "2026-01-01T00:00:00Z")
        with database.unit_of_work(read_only=True) as reader:
            assert reader.connection is not None
            assert (
                reader.connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0] == 0
            )
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.connection.execute("DELETE FROM collection_runs")
    with database.unit_of_work(read_only=True) as reader:
        assert reader.connection is not None
        assert reader.connection.execute("SELECT count(*) FROM collection_runs").fetchone()[0] == 1


def test_migrations_backup_and_rollback(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    database = Database(settings.database_path)
    assert database.migrate() > 0
    assert database.migrate() == 0
    with closing(database.connect()) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"source_observations", "vacancy_screenings", "job_evaluations"} <= tables
        assert (
            not {
                "application_workspaces",
                "application_emails",
                "email_assessments",
                "assessment_queue_drafts",
            }
            & tables
        )
    with pytest.raises(RuntimeError), database.unit_of_work() as work:
        work.runs.create("run_rollback", "1", "fingerprint", "2026-01-01T00:00:00Z")
        raise RuntimeError("rollback")
    with closing(database.connect()) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM collection_runs WHERE id='run_rollback'"
            ).fetchone()[0]
            == 0
        )

    service_for(settings).run([FakeSource("alpha")])
    backup = settings.data_dir / "backups" / "jobs.sqlite3"
    database.backup(backup)
    database.verify_backup(backup)
    restored = settings.data_dir / "restore-smoke" / "jobs.sqlite3"
    database.restore(backup, restored)
    assert scalar(restored, "SELECT COUNT(*) FROM collection_runs") == 1
    with pytest.raises(FileExistsError, match="already exists"):
        database.restore(backup, restored)
    with pytest.raises(FileNotFoundError):
        database.verify_backup(settings.data_dir / "backups" / "missing.sqlite3")
