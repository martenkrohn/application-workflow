"""Structured local logs and deterministic run summaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from job_system.domain.models import RunOutcome


class JsonLogger:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(
        self, timestamp: str, severity: str, module: str, operation: str, **data: Any
    ) -> None:
        record = {
            "timestamp": timestamp,
            "severity": severity,
            "run_id": self.run_id,
            "module": module,
            "operation": operation,
            **data,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            )


def write_summary(data_dir: Path, outcome: RunOutcome) -> Path:
    path = data_dir / "runs" / outcome.run_id / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": outcome.run_id,
        "status": outcome.status,
        "sources": [
            {
                "source_name": item.source_name,
                "status": item.status,
                "observations_created": item.observations_created,
                "observations_unchanged": item.observations_unchanged,
                "artifacts_created": item.artifacts_created,
                "artifacts_unchanged": item.artifacts_unchanged,
                "error_code": item.error_code,
                "error_message": item.error_message,
            }
            for item in outcome.sources
        ],
        "recovery": (
            "Inspect the saved collection summary and local logs; "
            "use `jobs collect` when ready to retry."
            if outcome.status != "succeeded"
            else "No recovery action required."
        ),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    temporary.replace(path)
    return path
