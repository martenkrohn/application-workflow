"""Structured read/write facade for the local vacancy dashboard."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from job_system.application.applications import ApplicationWorkspaceService
from job_system.application.assessment import ScreeningService
from job_system.application.cleaning import CleaningReadBatch
from job_system.domain.location_policy import LocationPolicy, location_packet
from job_system.domain.queue_policy import source_priority
from job_system.infrastructure.database import Database
from job_system.infrastructure.services import SystemClock, UuidGenerator, utc_text
from job_system.reporting.vacancies import (
    _preview,
    assessment_groups,
    merged_assessment_result,
)

REVIEW_STATUSES = frozenset(
    {
        "new",
        "interested",
        "deferred",
        "dismissed",
        "rejected",
        "applied",
        "invited",
        "abandoned",
    }
)
SCREENING_EXCLUSION_LABELS = {
    "older_than_7_days": "First discovered more than 7 days ago",
    "location_outside_scope": "Location outside your configured search area",
    "location_needs_clarification": "Location could not be matched to your search area",
    "professional_hard": "Title matches an excluded role",
    "professional_conditional_mismatch": "Title does not match your target professions",
    "outside_germany": "Location outside Germany",
    "location_munich": "Location excluded by your location rules",
    "location_southern_nonmetro": "Location excluded by your location rules",
    "vacancy_unavailable": "Vacancy unavailable",
    "awaiting_verification": "Waiting for website verification",
    "clean_data_needs_review": "Saved vacancy evidence needs review",
    "awaiting_vacancy_detail": "Waiting for a complete job description",
    "expired": "Application deadline has passed",
}


@dataclass
class DashboardReadContext:
    """Evidence shared within one read; create a new context for each fresh snapshot."""

    rows: tuple[dict[str, Any], ...] = ()
    cleaner: CleaningReadBatch | None = None


class DashboardService:
    """Expose persisted vacancy intelligence without generating report files."""

    def __init__(
        self,
        project_root: Path,
        data_dir: Path,
        database: Database,
        enabled_sources: frozenset[str] | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.data_dir = data_dir.resolve()
        self.database = database
        self.enabled_sources = enabled_sources
        self.database.migrate()

    def snapshot(
        self, profile_id: str, *, context: DashboardReadContext | None = None
    ) -> dict[str, Any]:
        self._backfill_workspaces(profile_id)
        with self.database.unit_of_work(read_only=True) as work:
            detailed_rows = work.normalization.evaluations(None, None, only_new=False)
            quick_rows = work.normalization.screening_dashboard(None, None)
            workspaces = {
                str(item["group_id"]): item for item in work.reviews.workspaces(None)
            }
        if self.enabled_sources:
            detailed_rows = tuple(
                (
                    row
                    for row in detailed_rows
                    if str(row["source_name"]) in self.enabled_sources
                )
            )
            quick_rows = tuple(
                (
                    row
                    for row in quick_rows
                    if str(row["source_name"]) in self.enabled_sources
                )
            )
        assessed = {
            (str(row["source_name"]), str(row["external_id"])) for row in detailed_rows
        }
        if context is None:
            context = DashboardReadContext()
        context.rows = quick_rows
        cleaner = context.cleaner = CleaningReadBatch(
            self.data_dir, self.database, quick_rows
        )
        screening = ScreeningService(
            self.project_root,
            self.data_dir,
            self.database,
            SystemClock(),
            UuidGenerator(),
        )
        screening.cleaner = cleaner
        policy = screening.location_policy
        location_buckets = (*policy.labels, "Location unresolved", "Outside scope")
        bucket_map = {}
        saved_rows = []
        for row in quick_rows:
            try:
                document = cleaner.current(row)
                row["preview"] = _preview(
                    self.data_dir, row, document, content=cleaner.read_raw(row)
                )
            except (OSError, RuntimeError, ValueError):
                continue
            review = json.loads(row["location_assessment_json"] or "null")
            bucket = _location_bucket(policy, location_packet(document), review)
            row["location_bucket"] = bucket
            bucket_map[str(row["source_name"]), str(row["external_id"])] = bucket
            saved_rows.append(row)
        quick_rows = tuple(saved_rows)
        coordinate_map = {
            (str(row["source_name"]), str(row["external_id"])): row["preview"][
                "coordinates"
            ]
            for row in quick_rows
            if row["preview"].get("coordinates") is not None
        }
        assessed_duplicate_keys = {
            key
            for row in quick_rows
            if (str(row["source_name"]), str(row["external_id"])) in assessed
            if (key := ScreeningService._exact_duplicate_key(row["preview"]))
        }
        quick_rows = tuple(
            row
            for row in quick_rows
            if (str(row["source_name"]), str(row["external_id"])) not in assessed
        )
        detailed = self._detailed(profile_id, detailed_rows, workspaces, coordinate_map)
        for item in detailed:
            buckets = [
                bucket_map[source["source"], source["external_id"]]
                for source in item.get("sources", [])
                if (source["source"], source["external_id"]) in bucket_map
            ]
            item["location_bucket"] = (
                min(buckets, key=location_buckets.index)
                if buckets
                else _location_bucket(
                    policy, {**item.get("vacancy", {}), "locations": item["locations"]}
                )
            )
        detailed.sort(key=lambda item: (-item["fit"], -item["confidence"]))
        seen = set()
        unique_detailed = []
        for item in detailed:
            key = ScreeningService._exact_duplicate_key(item)
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            unique_detailed.append(item)
        seen.update(assessed_duplicate_keys)
        detailed = [
            item
            for item in unique_detailed
            if item["workspace"] is None and item["review_status"] != "dismissed"
        ]
        unique_quick = []
        for row in sorted(
            quick_rows,
            key=lambda row: (
                row["screening_score"] is not None,
                bool(row["assessment_queued"]),
                str(row["first_seen_at"]),
                str(row["source_name"]),
                str(row["external_id"]),
            ),
            reverse=True,
        ):
            key = ScreeningService._exact_duplicate_key(row["preview"])
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            reason = (
                screening.automatic_exclusion_reason(row)
                if row["screening_score"] is None
                else None
            )
            row["screening_exclusion_reason"] = reason
            row["screening_exclusion_label"] = SCREENING_EXCLUSION_LABELS.get(
                reason or ""
            )
            row["screening_location_status"] = (
                screening.location_policy.classify(screening._packet(row)).status
                if row["screening_score"] is None
                else "matched"
            )
            unique_quick.append(row)
        quick = [
            item
            for item in self._quick(profile_id, tuple(unique_quick))
            if item["review_status"] != "dismissed"
        ]
        detailed.sort(key=lambda item: (-item["fit"], -item["confidence"]))
        quick.sort(
            key=lambda item: (
                -(item["fit"] if item["fit"] is not None else -1),
                -(item["confidence"] if item["confidence"] is not None else -1),
                source_priority(str(item["source"])),
            )
        )
        return {
            "profile_id": profile_id,
            "generated_at": utc_text(SystemClock().now()),
            "counts": {"detailed": len(detailed), "quick": len(quick)},
            "detailed": detailed,
            "quick": quick,
            "location_buckets": location_buckets,
        }

    def set_review(
        self, group_id: str, profile_id: str, status: str, note: str | None
    ) -> dict[str, Any]:
        if status not in REVIEW_STATUSES:
            raise ValueError(f"invalid review status: {status}")
        with self.database.unit_of_work() as work:
            current = work.reviews.decision(group_id, profile_id)
            cleaned_note = (
                current["note"]
                if note is None
                else note.strip()
                if note.strip()
                else None
            )
        return ApplicationWorkspaceService(self.data_dir, self.database).mark_by_group(
            group_id, profile_id, status, source="dashboard", note=cleaned_note
        )

    def create_workspace(
        self, group_id: str, profile_id: str, slug: str
    ) -> dict[str, Any]:
        return ApplicationWorkspaceService(self.data_dir, self.database).start(
            group_id, profile_id, slug
        )

    def _detailed(
        self,
        profile_id: str,
        rows: tuple[dict[str, Any], ...],
        workspaces: dict[str, dict[str, Any]],
        coordinate_map: dict[tuple[str, str], Any],
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        groups = assessment_groups(rows)
        reviews = self._review_states(
            tuple(
                (
                    tuple(
                        (
                            (str(row["source_name"]), str(row["external_id"]))
                            for row in group
                        )
                    )
                    for group in groups
                )
            )
        )
        for group in groups:
            members = tuple(
                ((str(row["source_name"]), str(row["external_id"])) for row in group)
            )
            group_id, decision = reviews[members]
            result = merged_assessment_result(group)
            vacancy = result["vacancy"]
            evaluation = result["evaluation"]
            deadline = _value(vacancy, "application_deadline")
            output.append(
                {
                    "group_id": group_id,
                    "assessment_profile_id": group[0]["profile_id"],
                    "fit": int(evaluation["suitability_score"]),
                    "confidence": int(evaluation["assessment_confidence"]),
                    "review_status": decision["status"],
                    "review_note": decision["note"],
                    "first_seen_at": min((str(row["first_seen_at"]) for row in group)),
                    "title": _value(vacancy, "title"),
                    "employer": _value(vacancy, "employer"),
                    "locations": _list_value(vacancy, "locations"),
                    "coordinates": next(
                        (
                            coordinate_map[identity]
                            for row in group
                            if (
                                identity := (
                                    str(row["source_name"]),
                                    str(row["external_id"]),
                                )
                            )
                            in coordinate_map
                        ),
                        None,
                    ),
                    "deadline": deadline,
                    "expired": _expired(deadline),
                    "application_url": _value(vacancy, "application_url"),
                    "source_url": group[0]["source_url"],
                    "summary": evaluation["summary"],
                    "gaps": evaluation["gaps"],
                    "strengths": evaluation["strengths"],
                    "deal_breakers": evaluation["deal_breakers"],
                    "missing_information": evaluation["missing_information"],
                    "score_components": evaluation["score_components"],
                    "vacancy": {name: _value(vacancy, name) for name in vacancy},
                    "sources": [
                        {
                            "source": row["source_name"],
                            "external_id": row["external_id"],
                            "profile_id": row["profile_id"],
                            "fit": row["suitability_score"],
                            "confidence": row["assessment_confidence"],
                        }
                        for row in group
                    ],
                    "workspace": workspaces.get(group_id),
                }
            )
        return output

    def _quick(
        self, profile_id: str, rows: tuple[dict[str, Any], ...]
    ) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        reviews = self._review_states(
            tuple(
                (((str(row["source_name"]), str(row["external_id"])),) for row in rows)
            )
        )
        for row in rows:
            preview = row["preview"]
            group_id, decision = reviews[
                (str(row["source_name"]), str(row["external_id"])),
            ]
            output.append(
                {
                    "group_id": group_id,
                    "review_status": decision["status"],
                    "observation_id": row["id"],
                    "assessment_profile_id": row["screening_profile_id"],
                    "source": row["source_name"],
                    "external_id": row["external_id"],
                    "fit": row["screening_score"],
                    "confidence": row["screening_confidence"],
                    "decision": row["screening_decision"],
                    "screening_exclusion_reason": row["screening_exclusion_reason"],
                    "screening_exclusion_label": row["screening_exclusion_label"],
                    "screening_location_status": row["screening_location_status"],
                    "location_assessment": json.loads(row["location_assessment_json"])
                    if row["location_assessment_json"]
                    else None,
                    "summary": row["screening_summary"],
                    "assessment_queued": bool(row["assessment_queued"]),
                    "screening_queued": bool(row["screening_queued"]),
                    "first_seen_at": row["first_seen_at"],
                    "title": preview["title"],
                    "employer": preview["employer"],
                    "locations": preview["locations"],
                    "location_bucket": row["location_bucket"],
                    "coordinates": preview.get("coordinates"),
                    "deadline": preview["application_deadline"],
                    "source_url": preview["source_url"],
                }
            )
        return output

    def _review_states(
        self, member_sets: tuple[tuple[tuple[str, str], ...], ...]
    ) -> dict[tuple[tuple[str, str], ...], tuple[str, dict[str, Any]]]:
        """Read established groups; acquire a writer lock only for new/merged groups."""
        states = {}
        missing = []
        with self.database.unit_of_work(read_only=True) as work:
            for members in member_sets:
                group_id = work.reviews.group_for_members(members)
                if group_id is None:
                    missing.append(members)
                else:
                    states[members] = (group_id, work.reviews.decision(group_id, None))
        if missing:
            timestamp = utc_text(SystemClock().now())
            with self.database.unit_of_work() as work:
                for members in missing:
                    group_id = work.reviews.ensure_group(
                        members, UuidGenerator().new("vgrp"), timestamp
                    )
                    states[members] = (group_id, work.reviews.decision(group_id, None))
        return states

    def _backfill_workspaces(self, profile_id: str) -> None:
        """Portfolio workspaces are registered at creation; no private folder discovery."""
        pass


def _location_bucket(
    policy: LocationPolicy, packet: dict[str, Any], review: dict[str, Any] | None = None
) -> str:
    if policy.enabled and policy.strict_locations_only:
        decision = policy.classify(packet)
        return policy.label(decision) if decision.admitted else "Outside scope"
    if review:
        if review.get("status") == "matched" and review.get("group") in policy.labels:
            return str(review["group"])
        if review.get("status") == "outside_scope":
            return "Outside scope"
    decision = policy.classify(packet)
    if decision.status == "outside_scope":
        return "Outside scope"
    return policy.label(decision)


def _value(vacancy: dict[str, Any], name: str) -> Any:
    field = vacancy.get(name)
    return field.get("value") if isinstance(field, dict) else None


def _list_value(vacancy: dict[str, Any], name: str) -> list[str]:
    value = _value(vacancy, name)
    return [str(item) for item in value] if isinstance(value, list) else []


def _expired(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value) < date.today()
    except ValueError:
        return False
