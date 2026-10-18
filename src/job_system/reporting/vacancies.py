"""Provider-neutral preview boundary for prepared vacancy evidence."""

import hashlib
import json
from pathlib import Path
from typing import Any

from job_system.domain.clean_vacancies import normalize


def observation_screening_packet(data_dir: Path, row: dict[str, Any]) -> dict[str, Any]:
    root = data_dir.resolve()
    path = (root / str(row["relative_path"])).resolve()
    if not path.is_relative_to(root):
        raise ValueError("evidence must stay inside the data directory")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != row["artifact_hash"]:
        raise ValueError("evidence integrity mismatch")
    document = normalize(row, json.loads(content))
    facts = document["facts"]
    return {
        "source": document["source"],
        "external_id": document["external_id"],
        "source_url": document["source_url"],
        "title": facts["title"]["value"],
        "employer": facts["employer"]["value"],
        "locations": facts["locations"]["value"] or [],
        "description_excerpt": "\n".join(b["text"] for b in document["blocks"]),
        "detail_available": document["quality"]["state"] == "ready",
        "fetch_state": None,
    }
