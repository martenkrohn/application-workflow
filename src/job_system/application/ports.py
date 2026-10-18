"""Application-facing interfaces."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol

from job_system.domain.models import SourceItem, StoredArtifact


class Clock(Protocol):
    def now(self) -> datetime: ...


class IdGenerator(Protocol):
    def new(self, prefix: str) -> str: ...


class ContentHasher(Protocol):
    def digest(self, content: bytes) -> str: ...


class SourceAdapter(Protocol):
    @property
    def name(self) -> str: ...

    def collect(self) -> Iterable[SourceItem]: ...


class RawArtifactStore(Protocol):
    def put(self, content: bytes) -> StoredArtifact: ...
