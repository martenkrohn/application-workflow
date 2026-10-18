"""Offline conversion, immutable version storage, validation and dataset export."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

from job_system.domain.clean_vacancies import (
    EXTRACTOR_VERSION,
    FIELDS,
    assessment_packet,
    encoded,
    normalize,
)
from job_system.infrastructure.database import Database
from job_system.infrastructure.services import SystemClock, utc_text


class CleaningError(RuntimeError):
    pass


def validate_document(document: dict[str, Any]) -> None:
    if document["quality"]["state"] not in {"ready", "needs_review"}:
        raise CleaningError("invalid quality state")
    if set(document["facts"]) != set(FIELDS):
        raise CleaningError("clean fact fields do not match the schema")
    block_ids = [b["id"] for b in document["blocks"]]
    if len(set(block_ids)) != len(block_ids):
        raise CleaningError("duplicate evidence block IDs")
    for name, fact in document["facts"].items():
        if fact["status"] not in {"unknown", "stated"}:
            raise CleaningError(f"{name} has invalid fact status")
        if (fact["value"] is None) != (fact["status"] == "unknown"):
            raise CleaningError(f"{name} has inconsistent unknown status")
        if fact["value"] is not None and not fact["provenance"]:
            raise CleaningError(f"{name} has no provenance")
        if fact["value"] is not None and not isinstance(fact["value"], str | list):
            raise CleaningError(f"{name} has an invalid value type")
        if isinstance(fact["value"], list) and not all(
            isinstance(v, str) for v in fact["value"]
        ):
            raise CleaningError(f"{name} contains a non-string array entry")
        for citation in fact["provenance"]:
            if len(citation["artifact_hash"]) != 64 or not citation["locator"]:
                raise CleaningError(f"{name} has invalid provenance")
            if citation.get("block_id") and citation["block_id"] not in block_ids:
                raise CleaningError(f"{name} refers to a missing block")
    for block in document["blocks"]:
        if (
            not block["text"]
            or not block["provenance"]["locator"]
            or len(block["provenance"]["artifact_hash"]) != 64
        ):
            raise CleaningError("empty evidence block or locator")
    if document["quality"]["state"] == "ready" and document["quality"]["reasons"]:
        raise CleaningError("ready record has unresolved quality reasons")


class CleaningService:
    def __init__(self, data_dir: Path, database: Database) -> None:
        self.data_dir, self.database = data_dir.resolve(), database

    def preview(self, row: dict[str, Any]) -> dict[str, Any]:
        path = (self.data_dir / str(row["relative_path"])).resolve()
        if not path.is_relative_to(self.data_dir) or not path.is_file():
            raise CleaningError("raw evidence path is missing or outside local data")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != row["artifact_hash"]:
            raise CleaningError("raw evidence integrity mismatch")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise CleaningError("observation must contain a JSON object")
        result = normalize(row, payload)
        validate_document(result)
        return result

    def ensure(self, row: dict[str, Any]) -> dict[str, Any]:
        # Cached records still require the original evidence to pass its hash check.
        document = self.preview(row)
        text = encoded(document).decode()
        document_hash = hashlib.sha256(text.encode()).hexdigest()
        timestamp = utc_text(SystemClock().now())
        with self.database.unit_of_work() as work:
            assert work.connection is not None
            work.connection.execute(
                "INSERT OR IGNORE INTO clean_vacancy_versions VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    document["version_id"],
                    document["source"],
                    document["external_id"],
                    document["schema_version"],
                    document["extractor_version"],
                    document["content_hash"],
                    document_hash,
                    document["quality"]["state"],
                    text,
                    timestamp,
                ),
            )
            work.connection.execute(
                "INSERT OR IGNORE INTO clean_observation_versions VALUES (?,?,?,?,?)",
                (
                    row["id"],
                    EXTRACTOR_VERSION,
                    document["version_id"],
                    encoded(
                        {
                            "artifact_hash": row["artifact_hash"],
                            "facts": document["facts"],
                            "blocks": document["blocks"],
                        }
                    ).decode(),
                    timestamp,
                ),
            )
            # Older observations prepared for an existing batch must not move the current pointer.
            newest = work.connection.execute(
                "SELECT id FROM source_observations WHERE source_name=? AND external_id=? "
                "ORDER BY first_seen_at DESC,id DESC LIMIT 1",
                (row["source_name"], row["external_id"]),
            ).fetchone()
            if newest is not None and newest["id"] == row["id"]:
                ready_id = (
                    document["version_id"]
                    if document["quality"]["state"] == "ready"
                    else None
                )
                work.connection.execute(
                    "INSERT INTO clean_vacancy_current VALUES (?,?,?,?,?) "
                    "ON CONFLICT(source_name,external_id) DO UPDATE SET "
                    "version_id=excluded.version_id,observation_id=excluded.observation_id,"
                    "last_ready_version_id=COALESCE(excluded.last_ready_version_id,"
                    "clean_vacancy_current.last_ready_version_id)",
                    (
                        document["source"],
                        document["external_id"],
                        document["version_id"],
                        ready_id,
                        row["id"],
                    ),
                )
        # Immutable versions keep their original provenance when equivalent raw inputs recur.
        return self.get(document["version_id"])

    def get(self, version_id: str) -> dict[str, Any]:
        with self.database.unit_of_work(read_only=True) as work:
            assert work.connection is not None
            row = work.connection.execute(
                "SELECT * FROM clean_vacancy_versions WHERE id=?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise CleaningError("clean vacancy version not found")
        return _verified_document(row["document_json"], row["document_hash"])

    def read_raw(self, row: dict[str, Any]) -> bytes:
        """Read and verify the original saved evidence."""
        path = (self.data_dir / str(row["relative_path"])).resolve()
        if not path.is_relative_to(self.data_dir) or not path.is_file():
            raise CleaningError("saved evidence is missing or outside local data")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != row["artifact_hash"]:
            raise CleaningError("raw evidence integrity mismatch")
        return body

    def current(self, row: dict[str, Any]) -> dict[str, Any]:
        """Read collected evidence; never normalize or publish a downstream candidate."""
        self.read_raw(row)
        version_id = row.get("clean_version_id")
        if not version_id:
            raise CleaningError(
                "vacancy has no current ready clean version; run collection"
            )
        document = self.get(str(version_id))
        if document["quality"]["state"] != "ready":
            raise CleaningError("vacancy is not ready")
        return document

    def build(
        self, sources: tuple[str, ...] | None = None, *, persist: bool = True
    ) -> dict[str, Any]:
        with self.database.unit_of_work() as work:
            rows = work.evidence.review(100000, sources)
        states: Counter[str] = Counter()
        by_source: dict[str, Counter[str]] = {}
        reasons: Counter[str] = Counter()
        errors = []
        raw_bytes = packet_bytes = 0
        for row in rows:
            try:
                document = self.ensure(row) if persist else self.preview(row)
                state = document["quality"]["state"]
                states[state] += 1
                by_source.setdefault(row["source_name"], Counter())[state] += 1
                reasons.update(document["quality"]["reasons"])
                if state == "ready":
                    raw_bytes += (self.data_dir / row["relative_path"]).stat().st_size
                    packet_bytes += len(encoded(assessment_packet(document)))
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                ET.ParseError,
                CleaningError,
            ) as error:
                states["failed"] += 1
                errors.append(
                    {
                        "observation_id": row["id"],
                        "source": row["source_name"],
                        "error": str(error),
                    }
                )
        return {
            "observations": len(rows),
            "states": dict(states),
            "by_source": {s: dict(c) for s, c in sorted(by_source.items())},
            "review_reasons": dict(reasons),
            "errors": errors,
            "ready_raw_bytes": raw_bytes,
            "ready_packet_bytes": packet_bytes,
            "measurement": "UTF-8 bytes, not model tokens",
        }

    def export(
        self, output: Path, sources: tuple[str, ...] | None = None
    ) -> dict[str, Any]:
        path = output.resolve()
        if not path.is_relative_to(self.data_dir):
            raise CleaningError("dataset export must stay inside ignored local data")
        with self.database.unit_of_work() as work:
            rows = work.normalization.ready_observations()
        documents = [
            self.current(row)
            for row in rows
            if sources is None or row["source_name"] in sources
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(b"".join(encoded(d) + b"\n" for d in documents))
        temporary.replace(path)
        return {"records": len(documents), "path": str(path)}


def _verified_document(text: str, document_hash: str) -> dict[str, Any]:
    if hashlib.sha256(text.encode()).hexdigest() != document_hash:
        raise CleaningError("clean version integrity mismatch")
    document: dict[str, Any] = json.loads(text)
    validate_document(document)
    return document


class CleaningReadBatch(CleaningService):
    """Bulk-read and reuse verified evidence during one dashboard build only.

    Never retain this object across requests or assessment preparation callbacks.
    Callers receive separate document copies, just as with ordinary get() reads.
    """

    def __init__(
        self, data_dir: Path, database: Database, rows: tuple[dict[str, Any], ...]
    ) -> None:
        super().__init__(data_dir, database)
        self._versions: dict[str, dict[str, Any]] = {}
        self._documents: dict[str, dict[str, Any]] = {}
        self._raw: dict[tuple[str, str], bytes] = {}
        versions = sorted(
            {
                str(row["clean_version_id"])
                for row in rows
                if row.get("clean_version_id")
            }
        )
        if versions:
            with database.unit_of_work(read_only=True) as work:
                assert work.connection is not None
                for offset in range(0, len(versions), 500):
                    chunk = versions[offset : offset + 500]
                    parameters = ",".join("?" for _ in chunk)
                    records = work.connection.execute(
                        "SELECT id,document_json,document_hash FROM clean_vacancy_versions "
                        f"WHERE id IN ({parameters})",
                        chunk,
                    ).fetchall()
                    self._versions.update(
                        (str(record["id"]), dict(record)) for record in records
                    )

    def get(self, version_id: str) -> dict[str, Any]:
        if version_id not in self._documents:
            row = self._versions.get(version_id)
            if row is None:
                raise CleaningError("clean vacancy version not found")
            self._documents[version_id] = _verified_document(
                row["document_json"], row["document_hash"]
            )
        return deepcopy(self._documents[version_id])

    def read_raw(self, row: dict[str, Any]) -> bytes:
        key = (str(row["relative_path"]), str(row["artifact_hash"]))
        if key not in self._raw:
            self._raw[key] = super().read_raw(row)
        return self._raw[key]
