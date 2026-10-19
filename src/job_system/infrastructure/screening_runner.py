"""Run the compact screening task in Codex; keep detailed review human-started."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

QUICK_MODEL = "gpt-6-luna"
QUICK_REASONING = "low"
DETAILED_MODEL = "gpt-6.1-sol"


def run_screening(workflow: Any, batch_id: str, *, runner: Callable = subprocess.run) -> dict:
    service = workflow.screening
    batch, _, manifest = service._load_batch(batch_id)
    directory = workflow.settings.data_dir / batch["input_directory"]
    output = workflow.settings.data_dir / batch["output_path"]
    response_path = output.with_name("codex-response.json")
    items = []
    for entry in manifest["items"]:
        path = (directory / entry["path"]).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("screening input escapes its prepared batch")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["input_hash"]:
            raise ValueError("screening input changed after preparation")
        items.append({"input_hash": entry["input_hash"], "item": json.loads(raw)})
    if not output.exists():
        executable = shutil.which("codex")
        if not executable:
            raise RuntimeError(
                "Install Codex CLI, sign in and put codex on PATH before running screening."
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        prompt = (workflow.root / "prompts/vacancy-screening/v4/prompt.md").read_text(
            encoding="utf-8"
        )
        package = {
            "manifest": manifest,
            "profile": json.loads((directory / "profile.json").read_text(encoding="utf-8")),
            "items": items,
        }
        command = [
            executable,
            "exec",
            "--model",
            QUICK_MODEL,
            "--config",
            f'model_reasoning_effort="{QUICK_REASONING}"',
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--output-schema",
            str(workflow.root / "prompts/vacancy-screening/v4/output.schema.json"),
            "--output-last-message",
            str(response_path),
            "-",
        ]
        result = runner(
            command,
            cwd=workflow.root,
            input=prompt + "\n\nINPUT PACKAGE\n" + json.dumps(package, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=1800,
        )
        if result.returncode:
            raise RuntimeError(
                f"Codex screening exited with code {result.returncode}; the prepared batch is preserved."
            )
        records = json.loads(response_path.read_text(encoding="utf-8"))["results"]
        if not all(row.get("model_id") == QUICK_MODEL for row in records):
            raise ValueError("screening model identity does not match the selected model")
        output.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8"
        )
    records = [
        json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not all(row.get("model_id") == QUICK_MODEL for row in records):
        raise ValueError("screening model identity does not match the selected model")
    validation = service.validate(batch_id)
    if validation.rejected_count or validation.valid_count != len(manifest["items"]):
        raise ValueError(f"Screening output rejected: {list(validation.errors)}")
    return service.import_batch(batch_id)
