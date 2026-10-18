"""Small deterministic infrastructure services."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class UuidGenerator:
    def new(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"


class Sha256Hasher:
    def digest(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


def utc_text(value: datetime) -> str:
    """Serialize an aware timestamp consistently."""
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
