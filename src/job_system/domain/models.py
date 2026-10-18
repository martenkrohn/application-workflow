"""Domain values used by the Stage 1 operational core."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class EvidenceRole(StrEnum):
    DISCOVERY = "discovery"
    DETAIL = "detail"


@dataclass(frozen=True, slots=True)
class EvidencePayload:
    """One unmodified HTTP response supporting a source observation."""

    role: EvidenceRole
    media_type: str
    content: bytes
    retrieval_url: str


@dataclass(frozen=True, slots=True)
class SourceItem:
    """Stable source fragment and its unmodified retrieval evidence."""

    external_id: str
    source_url: str
    media_type: str
    content: bytes
    retrieval_url: str | None = None
    evidence_payloads: tuple[EvidencePayload, ...] = ()


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    content_hash: str
    relative_path: str
    size: int
    created: bool


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    source_name: str
    status: AttemptStatus
    observations_created: int = 0
    observations_unchanged: int = 0
    artifacts_created: int = 0
    artifacts_unchanged: int = 0
    error_code: str | None = None
    error_message: str | None = None
    checkpoint: str | None = None


@dataclass(frozen=True, slots=True)
class RunOutcome:
    run_id: str
    status: RunStatus
    sources: tuple[SourceOutcome, ...]
    summary_path: str
