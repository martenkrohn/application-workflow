"""Content-addressed immutable raw artifact storage."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from job_system.application.ports import ContentHasher
from job_system.domain.models import StoredArtifact


class ArtifactIntegrityError(RuntimeError):
    """Raised when content-addressed evidence was modified after storage."""


class FileArtifactStore:
    def __init__(self, data_dir: Path, hasher: ContentHasher) -> None:
        self.data_dir = data_dir
        self.root = data_dir / "raw" / "sha256"
        self.hasher = hasher

    def put(self, content: bytes) -> StoredArtifact:
        content_hash = self.hasher.digest(content)
        target = self.root / content_hash[:2] / content_hash
        relative_path = target.relative_to(self.data_dir).as_posix()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if self.hasher.digest(target.read_bytes()) != content_hash:
                raise ArtifactIntegrityError(
                    f"artifact content mismatch: {relative_path}"
                )
            return StoredArtifact(content_hash, relative_path, len(content), False)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix="artifact-", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
                created = True
            except FileExistsError:
                created = False
            if self.hasher.digest(target.read_bytes()) != content_hash:
                raise ArtifactIntegrityError(
                    f"artifact content mismatch: {relative_path}"
                )
            return StoredArtifact(content_hash, relative_path, len(content), created)
        finally:
            temporary.unlink(missing_ok=True)
