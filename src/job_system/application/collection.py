"""Recoverable Stage 1 collection orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable

from job_system import __version__
from job_system.application.cleaning import CleaningService
from job_system.application.ports import (
    Clock,
    IdGenerator,
    RawArtifactStore,
    SourceAdapter,
)
from job_system.domain.models import (
    AttemptStatus,
    RunOutcome,
    RunStatus,
    SourceOutcome,
    StoredArtifact,
)
from job_system.infrastructure.config import Settings
from job_system.infrastructure.database import Database
from job_system.infrastructure.locking import FileRunLock
from job_system.infrastructure.observability import JsonLogger, write_summary
from job_system.infrastructure.services import utc_text


class CollectionService:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        artifacts: RawArtifactStore,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self.settings = settings
        self.database = database
        self.artifacts = artifacts
        self.clock = clock
        self.ids = ids

    def run(
        self,
        sources: Iterable[SourceAdapter],
        *,
        on_source_start: Callable[[str], None] | None = None,
        on_source_finish: Callable[[SourceOutcome], None] | None = None,
        source_checkpoint: Callable[[str, Callable[[], SourceOutcome]], SourceOutcome]
        | None = None,
    ) -> RunOutcome:
        with FileRunLock(self.settings.lock_path):
            return self._run_locked(
                tuple(sources), on_source_start, on_source_finish, source_checkpoint
            )

    def _run_locked(
        self,
        sources: tuple[SourceAdapter, ...],
        on_source_start: Callable[[str], None] | None,
        on_source_finish: Callable[[SourceOutcome], None] | None,
        source_checkpoint: Callable[[str, Callable[[], SourceOutcome]], SourceOutcome]
        | None = None,
    ) -> RunOutcome:
        run_id = self.ids.new("run")
        started_at = utc_text(self.clock.now())
        logger = JsonLogger(self.settings.data_dir / "logs" / f"{run_id}.jsonl", run_id)
        with self.database.unit_of_work() as work:
            work.runs.create(run_id, __version__, self.settings.fingerprint, started_at)
        logger.event(
            started_at, "INFO", "collection", "run_started", source_count=len(sources)
        )

        outcomes: list[SourceOutcome] = []
        for source in sources:
            if on_source_start:
                on_source_start(source.name)

            def collect_source(current: SourceAdapter = source) -> SourceOutcome:
                return self._run_source(run_id, current, logger)

            source_outcome = (
                source_checkpoint(source.name, collect_source)
                if source_checkpoint
                else collect_source()
            )
            outcomes.append(source_outcome)
            if on_source_finish:
                on_source_finish(source_outcome)
        succeeded = sum(item.status == AttemptStatus.SUCCEEDED for item in outcomes)
        if succeeded == len(outcomes):
            status = RunStatus.SUCCEEDED
        elif succeeded:
            status = RunStatus.PARTIAL
        else:
            status = RunStatus.FAILED
        relative_summary = f"runs/{run_id}/summary.json"
        outcome = RunOutcome(run_id, status, tuple(outcomes), relative_summary)
        write_summary(self.settings.data_dir, outcome)
        finished_at = utc_text(self.clock.now())
        with self.database.unit_of_work() as work:
            work.runs.finish(run_id, status, finished_at, relative_summary)
        logger.event(finished_at, "INFO", "collection", "run_finished", status=status)
        return outcome

    def _run_source(
        self, run_id: str, source: SourceAdapter, logger: JsonLogger
    ) -> SourceOutcome:
        attempt_id = self.ids.new("attempt")
        started_at = utc_text(self.clock.now())
        with self.database.unit_of_work() as work:
            work.runs.start_attempt(attempt_id, run_id, source.name, started_at)
        logger.event(
            started_at,
            "INFO",
            "collection",
            "source_started",
            source_name=source.name,
            attempt_id=attempt_id,
        )
        try:
            outcome = self._persist_items(run_id, source)
        except Exception as error:  # adapters are isolated at this boundary
            outcome = SourceOutcome(
                source.name,
                AttemptStatus.FAILED,
                error_code=type(error).__name__,
                error_message=str(error),
            )
        finished_at = utc_text(self.clock.now())
        with self.database.unit_of_work() as work:
            work.runs.finish_attempt(attempt_id, finished_at, outcome)
            work.source_health.record(source.name, finished_at, outcome)
        logger.event(
            finished_at,
            "ERROR" if outcome.status == AttemptStatus.FAILED else "INFO",
            "collection",
            "source_finished",
            source_name=source.name,
            status=outcome.status,
            error_code=outcome.error_code,
        )
        return outcome

    def _persist_items(self, run_id: str, source: SourceAdapter) -> SourceOutcome:
        created_observations = 0
        unchanged_observations = 0
        created_artifacts = 0
        unchanged_artifacts = 0
        checkpoint_items: list[str] = []
        retrieval_cache: dict[tuple[str, str], StoredArtifact] = {}
        try:
            for item in source.collect():
                seen_at = utc_text(self.clock.now())
                artifact = self.artifacts.put(item.content)
                created_artifacts += int(artifact.created)
                unchanged_artifacts += int(not artifact.created)
                with self.database.unit_of_work() as work:
                    work.evidence.record_artifact(artifact, item.media_type, seen_at)
                    if not item.evidence_payloads:
                        retrieval_url = item.retrieval_url or item.source_url
                        retrieval_key = (artifact.content_hash, retrieval_url)
                        if retrieval_key not in retrieval_cache:
                            work.evidence.record_retrieval(
                                self.ids.new("retrieval"),
                                artifact.content_hash,
                                source.name,
                                run_id,
                                seen_at,
                                retrieval_url,
                            )
                            retrieval_cache[retrieval_key] = artifact
                    created, observation_id = work.evidence.record_observation(
                        self.ids.new("obs"),
                        source.name,
                        item.external_id,
                        artifact,
                        item.source_url,
                        seen_at,
                        run_id,
                    )
                    work.evidence.link_observation_artifact(
                        observation_id, artifact.content_hash, "observation"
                    )
                    for payload in item.evidence_payloads:
                        payload_hash = hashlib.sha256(payload.content).hexdigest()
                        retrieval_key = (payload_hash, payload.retrieval_url)
                        supporting = retrieval_cache.get(retrieval_key)
                        if supporting is None:
                            supporting = self.artifacts.put(payload.content)
                            created_artifacts += int(supporting.created)
                            unchanged_artifacts += int(not supporting.created)
                            work.evidence.record_artifact(
                                supporting, payload.media_type, seen_at
                            )
                            work.evidence.record_retrieval(
                                self.ids.new("retrieval"),
                                supporting.content_hash,
                                source.name,
                                run_id,
                                seen_at,
                                payload.retrieval_url,
                            )
                            retrieval_cache[retrieval_key] = supporting
                        work.evidence.link_observation_artifact(
                            observation_id, supporting.content_hash, payload.role
                        )
                created_observations += int(created)
                unchanged_observations += int(not created)
                checkpoint_items.append(f"{item.external_id}:{artifact.content_hash}")
                with self.database.unit_of_work() as work:
                    assert work.connection is not None
                    row = work.connection.execute(
                        "SELECT o.*,ra.relative_path,ra.media_type FROM source_observations o "
                        "JOIN raw_artifacts ra ON ra.content_hash=o.artifact_hash WHERE o.id=?",
                        (observation_id,),
                    ).fetchone()
                assert row is not None
                CleaningService(self.settings.data_dir, self.database).ensure(dict(row))
        except Exception as error:
            # Successful observations are already committed and cleaned before the next request.
            return SourceOutcome(
                source.name,
                AttemptStatus.FAILED,
                created_observations,
                unchanged_observations,
                created_artifacts,
                unchanged_artifacts,
                error_code=type(error).__name__,
                error_message=str(error),
            )
        checkpoint_input = "\n".join(sorted(checkpoint_items)).encode()
        checkpoint = hashlib.sha256(checkpoint_input).hexdigest()
        return SourceOutcome(
            source.name,
            AttemptStatus.SUCCEEDED,
            created_observations,
            unchanged_observations,
            created_artifacts,
            unchanged_artifacts,
            checkpoint=checkpoint,
        )
