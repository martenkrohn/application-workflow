"""Local configuration for storage and collection paths."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """Raised before operational work starts when configuration is unsafe."""


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    data_dir: Path
    database_path: Path
    lock_path: Path
    loaded_files: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        payload = {
            "data_dir": self.data_dir.relative_to(self.project_root).as_posix(),
            "database_path": self.database_path.relative_to(
                self.project_root
            ).as_posix(),
            "lock_path": self.lock_path.relative_to(self.project_root).as_posix(),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _inside(root: Path, candidate: Path, label: str) -> Path:
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise ConfigurationError(f"{label} must stay inside the project root")
    return resolved


def load_settings(project_root: Path, local_file: Path | None = None) -> Settings:
    """Load defaults, tracked config, optional local config, then environment overrides."""
    root = project_root.resolve()
    data: dict[str, Any] = {
        "project": {"data_dir": "var"},
        "database": {"path": "var/db/jobs.sqlite3"},
        "collection": {"lock_path": "var/locks/daily.lock"},
    }
    loaded: list[str] = []
    candidates = [root / "config" / "project.toml"]
    candidates.append(local_file or root / "config" / "project.local.toml")
    for path in candidates:
        if path.is_file():
            with path.open("rb") as handle:
                data = _merge(data, tomllib.load(handle))
            loaded.append(str(path.resolve()))

    env_data_dir = os.environ.get("JOB_SYSTEM_DATA_DIR")
    if env_data_dir:
        data["project"]["data_dir"] = env_data_dir

    data_dir = _inside(root, root / str(data["project"]["data_dir"]), "data_dir")
    database_path = _inside(root, root / str(data["database"]["path"]), "database.path")
    lock_path = _inside(root, root / str(data["collection"]["lock_path"]), "lock_path")
    if not database_path.is_relative_to(data_dir) or not lock_path.is_relative_to(
        data_dir
    ):
        raise ConfigurationError("database and lock paths must stay inside data_dir")
    return Settings(root, data_dir, database_path, lock_path, tuple(loaded))
