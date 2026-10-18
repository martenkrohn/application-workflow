from pathlib import Path

import pytest

from job_system.infrastructure.artifacts import (
    ArtifactIntegrityError,
    FileArtifactStore,
)
from job_system.infrastructure.locking import FileRunLock, RunAlreadyLocked
from job_system.infrastructure.services import Sha256Hasher


def test_artifacts_are_content_addressed_and_immutable(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path, Sha256Hasher())

    first = store.put(b"same evidence")
    second = store.put(b"same evidence")

    assert first.created is True
    assert second.created is False
    assert first.content_hash == second.content_hash
    artifact_path = tmp_path / first.relative_path
    artifact_path.write_bytes(b"tampered")
    with pytest.raises(ArtifactIntegrityError, match="content mismatch"):
        store.put(b"same evidence")


def test_run_lock_excludes_overlap_and_releases(tmp_path: Path) -> None:
    lock_path = tmp_path / "locks" / "daily.lock"

    with FileRunLock(lock_path):
        assert lock_path.is_file()
        with (
            pytest.raises(RunAlreadyLocked, match="already exists"),
            FileRunLock(lock_path),
        ):
            pass

    assert not lock_path.exists()
    with FileRunLock(lock_path):
        assert lock_path.exists()
