"""Human-reviewed selection, kept separate from prepared AI work batches."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from threading import RLock
from typing import Any

from job_system.application.assessment import SCREENING_PROMOTION_SCORE
from job_system.application.cleaning import CleaningReadBatch
from job_system.application.dashboard import DashboardReadContext, DashboardService
from job_system.domain.location_policy import LocationPolicy, location_packet
from job_system.infrastructure.services import SystemClock, utc_text

PrepareBatch = Callable[[tuple[tuple[str, str], ...]], dict[str, Any]]


def identity(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row["source"]), str(row["external_id"]))


class AssessmentQueueService:
    def __init__(self, dashboard: DashboardService, profile_id: str) -> None:
        self.dashboard = dashboard
        self.profile_id = profile_id
        self.lock = RLock()

    def _read(self) -> dict[str, Any]:
        with self.dashboard.database.unit_of_work(read_only=True) as work:
            assert work.connection is not None
            row = work.connection.execute(
                "SELECT draft_json FROM assessment_queue_drafts WHERE profile_id=?",
                (self.profile_id,),
            ).fetchone()
        return (
            json.loads(row[0])
            if row
            else {
                "manual": [],
                "rows": [],
                "total": 0,
                "previewed": False,
                "revision": 0,
                "excluded": [],
            }
        )

    def _save(self, draft: dict[str, Any]) -> None:
        draft["revision"] += 1
        with self.dashboard.database.unit_of_work() as work:
            assert work.connection is not None
            work.connection.execute(
                "INSERT INTO assessment_queue_drafts VALUES (?,?,?) ON CONFLICT(profile_id) DO UPDATE SET draft_json=excluded.draft_json, updated_at=excluded.updated_at",
                (self.profile_id, json.dumps(draft), utc_text(SystemClock().now())),
            )

    def _pool(
        self,
        snapshot: dict[str, Any] | None = None,
        *,
        context: DashboardReadContext | None = None,
    ) -> list[dict[str, Any]]:
        if context is None:
            context = DashboardReadContext()
        if snapshot is None:
            snapshot = self.dashboard.snapshot(self.profile_id, context=context)
        policy = LocationPolicy.load(self.dashboard.project_root)
        with self.dashboard.database.unit_of_work(read_only=True) as work:
            rows = (
                context.rows
                if context.cleaner is not None
                else work.normalization.screening_dashboard(None, None)
            )
            workspaces = {row["group_id"] for row in work.reviews.workspaces(None)}
        evidence = {str(row["id"]): row for row in rows}
        cleaner = context.cleaner or CleaningReadBatch(
            self.dashboard.data_dir, self.dashboard.database, rows
        )
        pool = []
        for item in snapshot["quick"]:
            if item["assessment_queued"] or item["group_id"] in workspaces:
                continue
            row = evidence[str(item["observation_id"])]
            packet: dict[str, Any] = {"locations": item["locations"]}
            try:
                document = cleaner.current(row)
                packet = location_packet(document)
            except (OSError, RuntimeError, ValueError):
                continue
            decision = policy.classify(packet)
            location_review = item.get("location_assessment") or {}
            reviewed_group = location_review.get("group")
            if (
                policy.enabled
                and policy.strict_locations_only
                and (not decision.admitted)
            ):
                location_rank = 99
                location_group = "Outside scope"
            elif (
                location_review.get("status") == "matched"
                and reviewed_group in policy.labels
            ):
                location_rank = policy.labels.index(reviewed_group)
                location_group = str(reviewed_group)
            else:
                location_rank = policy.rank(decision)
                location_group = policy.label(decision)
            pool.append(
                {
                    **item,
                    "location_rank": location_rank,
                    "location_group": location_group,
                    "source_rank": policy.source_rank(item["source"]),
                    "clean_version_id": document["version_id"],
                    "waiting_reason": None,
                }
            )
        pool.sort(key=lambda row: (row["source"], row["external_id"]))
        pool.sort(key=lambda row: row["source_rank"])
        pool.sort(key=lambda row: row["first_seen_at"], reverse=True)
        pool.sort(
            key=lambda row: (
                row["location_rank"],
                -(row["fit"] if row["fit"] is not None else -1),
            )
        )
        return pool

    def _pending(self) -> list[dict[str, Any]]:
        with self.dashboard.database.unit_of_work(read_only=True) as work:
            batches = work.normalization.batches(10000)
        pending = []
        for batch in reversed(batches):
            if batch["prompt_id"] != "vacancy-assessment" or batch["status"] not in {
                "prepared",
                "partial",
                "validated",
            }:
                continue
            path = (self.dashboard.data_dir / batch["input_directory"]).resolve()
            if not path.is_relative_to(self.dashboard.data_dir):
                raise ValueError("batch path is outside local data")
            manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            pending.append(
                {
                    "batch_id": batch["id"],
                    "status": batch["status"],
                    "item_count": batch["item_count"],
                    "profile_id": manifest["profile_id"],
                }
            )
        return pending

    def _state(
        self, draft: dict[str, Any], pool: list[dict[str, Any]]
    ) -> dict[str, Any]:
        current = {identity(row): row for row in pool}
        rows = []
        for saved in draft["rows"]:
            fresh = current.get(identity(saved))
            reason = saved.get("preparation_error")
            if fresh is None:
                reason = "No longer available for selection; refresh the preview."
            elif fresh["observation_id"] != saved["observation_id"]:
                reason = "Evidence changed after preview; refresh the preview before preparation."
            elif fresh["clean_version_id"] != saved.get("clean_version_id"):
                reason = "Clean evidence changed after preview; refresh the preview."
            rows.append(
                {
                    **saved,
                    "waiting_reason": reason or (fresh or saved).get("waiting_reason"),
                }
            )
        manual = {tuple(value) for value in draft["manual"]}
        candidates = [row for row in pool if self._suggestible(row)]
        return {
            **draft,
            "rows": rows,
            "manual_rows": [row for row in pool if identity(row) in manual],
            "unavailable_manual": [list(key) for key in manual if key not in current],
            "available": len(candidates),
            "by_location": dict(Counter((row["location_group"] for row in candidates))),
            "pending_batches": self._pending(),
        }

    @staticmethod
    def _suggestible(row: dict[str, Any]) -> bool:
        return (
            row["fit"] is not None
            and row["fit"] >= SCREENING_PROMOTION_SCORE
            and (row["location_rank"] != 99)
        )

    def state(self) -> dict[str, Any]:
        with self.lock:
            return self._state(self._read(), self._pool())

    def dashboard_state(self) -> dict[str, Any]:
        """Build review data once and derive the queue from the same fresh snapshot."""
        with self.lock:
            context = DashboardReadContext()
            snapshot = self.dashboard.snapshot(self.profile_id, context=context)
            return {
                "dashboard": snapshot,
                "queue": self._state(
                    self._read(), self._pool(snapshot, context=context)
                ),
            }

    def manual(self, key: tuple[str, str], add: bool) -> dict[str, Any]:
        with self.lock:
            draft, pool = (self._read(), self._pool())
            keys = {identity(row): row for row in pool}
            if add and key not in keys:
                raise ValueError(
                    "Vacancy is not ready, already prepared, assessed, dismissed, or unavailable."
                )
            draft["manual"] = [
                value for value in draft["manual"] if tuple(value) != key
            ]
            draft["rows"] = [row for row in draft["rows"] if identity(row) != key]
            draft["excluded"] = [
                v for v in draft.get("excluded", []) if tuple(v) != key
            ]
            if add:
                draft["manual"].append(list(key))
                if draft["previewed"]:
                    draft["rows"].insert(
                        0, {**keys[key], "origin": "manual", "selected": True}
                    )
            elif draft["previewed"]:
                draft["excluded"].append(list(key))
            self._save(draft)
            return self._state(draft, pool)

    def preview(self, total: int) -> dict[str, Any]:
        if type(total) is not int or total < 0:
            raise ValueError("Enter a non-negative whole number.")
        with self.lock:
            draft, pool = (self._read(), self._pool())
            manual = {tuple(value) for value in draft["manual"]}
            if total < len(manual):
                raise ValueError(
                    f"{len(manual)} manually queued vacancies exceed this total. Increase the total or remove manual choices first."
                )
            rows = [
                {**row, "origin": "manual", "selected": True}
                for row in pool
                if identity(row) in manual
            ]
            if len(rows) != len(manual):
                raise ValueError(
                    "A manual choice is unavailable. Remove it before previewing."
                )
            draft.update(rows=rows, total=total, previewed=True, excluded=[])
            self._fill(draft, pool)
            self._save(draft)
            return self._state(draft, pool)

    def _fill(self, draft: dict[str, Any], pool: list[dict[str, Any]]) -> None:
        seen = {identity(row) for row in draft["rows"]}
        seen.update((tuple(value) for value in draft.get("excluded", [])))
        remaining = max(
            0, draft["total"] - sum((row["selected"] for row in draft["rows"]))
        )
        for row in pool:
            if remaining and identity(row) not in seen and self._suggestible(row):
                draft["rows"].append({**row, "origin": "suggested", "selected": True})
                remaining -= 1

    def fill(self, revision: int) -> dict[str, Any]:
        with self.lock:
            draft, pool = (self._read(), self._pool())
            self._check_revision(draft, revision)
            self._fill(draft, pool)
            self._save(draft)
            return self._state(draft, pool)

    @staticmethod
    def _check_revision(draft: dict[str, Any], revision: int) -> None:
        if not draft["previewed"] or revision != draft["revision"]:
            raise ValueError(
                "Selection changed in another tab. Refresh before continuing."
            )

    def select(
        self, key: tuple[str, str], selected: bool, revision: int
    ) -> dict[str, Any]:
        return self.select_many({key: selected}, revision)

    def select_many(
        self, changes: dict[tuple[str, str], bool], revision: int
    ) -> dict[str, Any]:
        with self.lock:
            draft = self._read()
            self._check_revision(draft, revision)
            keys = {identity(row) for row in draft["rows"]}
            if not changes or not changes.keys() <= keys:
                raise ValueError("Every vacancy must be in the preview.")
            if any((type(selected) is not bool for selected in changes.values())):
                raise ValueError("selected must be a boolean")
            pool = self._pool()
            for row in draft["rows"]:
                key = identity(row)
                if key in changes:
                    row["selected"] = changes[key]
            self._save(draft)
            return self._state(draft, pool)

    def prepare(self, revision: int, prepare_batch: PrepareBatch) -> dict[str, Any]:
        with self.lock:
            draft, pool = (self._read(), self._pool())
            self._check_revision(draft, revision)
            for row in draft["rows"]:
                row.pop("preparation_error", None)
            state = self._state(draft, pool)
            selected = [row for row in state["rows"] if row["selected"]]
            if not selected:
                raise ValueError(
                    "Select at least one vacancy before preparing batches."
                )
            if len(selected) > draft["total"]:
                raise ValueError(
                    "Selection exceeds the target. Adjust it before preparation."
                )
            ready = [row for row in selected if not row["waiting_reason"]]
            waiting = [row for row in selected if row["waiting_reason"]]
            batches = []
            completed: set[tuple[str, str]] = set()
            for offset in range(0, len(ready), 25):
                chunk = ready[offset : offset + 25]
                try:
                    batch = prepare_batch(tuple((identity(row) for row in chunk)))
                except (OSError, RuntimeError, ValueError) as error:
                    for row in ready[offset:]:
                        row["waiting_reason"] = f"Preparation stopped: {error}"
                        waiting.append(row)
                    break
                batches.append(batch)
                completed.update((identity(row) for row in chunk))
                draft["manual"] = [
                    v for v in draft["manual"] if tuple(v) not in completed
                ]
                draft["rows"] = [
                    row for row in draft["rows"] if identity(row) not in completed
                ]
                self._save(draft)
            errors = {
                identity(row): row["waiting_reason"]
                for row in waiting
                if row["waiting_reason"].startswith("Preparation stopped:")
            }
            for row in draft["rows"]:
                if identity(row) in errors:
                    row["preparation_error"] = errors[identity(row)]
            self._save(draft)
            context = DashboardReadContext()
            snapshot = self.dashboard.snapshot(self.profile_id, context=context)
            return {
                "batches": batches,
                "prepared": len(completed),
                "waiting": waiting,
                "queue": self._state(draft, self._pool(snapshot, context=context)),
                "dashboard": snapshot,
            }
