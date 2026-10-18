from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_system.infrastructure.config import ConfigurationError, load_settings
from job_system.infrastructure.services import Sha256Hasher, UuidGenerator, utc_text


def test_configuration_layers_and_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "project.toml").write_text(
        "[project]\ndata_dir='var'\n[database]\npath='var/db/main.sqlite3'\n"
        "[collection]\nlock_path='var/locks/run.lock'\n",
        encoding="utf-8",
    )
    local = config / "project.local.toml"
    monkeypatch.setenv("JOB_SYSTEM_DATA_DIR", "local-data")
    local.write_text(
        "[database]\npath='local-data/db/main.sqlite3'\n"
        "[collection]\nlock_path='local-data/locks/run.lock'\n",
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert settings.data_dir == tmp_path / "local-data"
    assert settings.database_path == tmp_path / "local-data/db/main.sqlite3"
    assert settings.lock_path == tmp_path / "local-data/locks/run.lock"
    assert len(settings.loaded_files) == 2
    assert len(settings.fingerprint) == 64


@pytest.mark.parametrize(
    "contents,message",
    [
        (
            "[project]\ndata_dir='../outside'\n",
            "data_dir must stay inside the project root",
        ),
        (
            "[database]\npath='database.sqlite3'\n",
            "database and lock paths must stay inside data_dir",
        ),
    ],
)
def test_unsafe_configuration_is_rejected(
    tmp_path: Path, contents: str, message: str
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "project.toml").write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigurationError, match=message):
        load_settings(tmp_path)


def test_identity_hashing_and_timestamp_services() -> None:
    expected = bytes.fromhex(
        "ee8250fb 76e094b3 4b471f13 a73dbbe5 1d1ae142 e9df59d7 c0d31ec2 0f0a0a8e"
    ).hex()
    assert Sha256Hasher().digest(b"evidence") == expected
    assert UuidGenerator().new("run").startswith("run_")
    assert utc_text(datetime(2026, 1, 2, 3, 4, tzinfo=UTC)) == "2026-01-02T03:04:00Z"
    with pytest.raises(ValueError, match="timezone-aware"):
        utc_text(datetime(2026, 1, 2))
