"""Synthetic mail handoffs with evidence validation, without a mailbox connection."""

import hashlib
import json
from pathlib import Path
from typing import Any

from job_system.infrastructure.services import utc_text

CLASSIFICATIONS = {
    "invitation",
    "rejection",
    "action_required",
    "acknowledgement",
    "informational",
    "unrelated",
    "ambiguous",
}


def input_digest(message: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(message, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def validate_mail(message: dict[str, Any], result: dict[str, Any]) -> None:
    expected = {
        "message_id",
        "input_hash",
        "classification",
        "confidence",
        "reason",
        "evidence",
        "model_id",
    }
    if (
        set(result) != expected
        or result.get("message_id") != message["id"]
        or result.get("input_hash") != input_digest(message)
    ):
        raise ValueError("mail result is not bound to its input")
    if result.get("classification") not in CLASSIFICATIONS:
        raise ValueError("unknown mail classification")
    confidence = result.get("confidence")
    if type(confidence) is not int or not 0 <= confidence <= 100:
        raise ValueError("confidence must be an integer from 0 to 100")
    if not all(
        isinstance(result[k], str) and result[k].strip() for k in ["reason", "model_id"]
    ):
        raise ValueError("reason and model_id are required")
    evidence = result["evidence"]
    if (
        not isinstance(evidence, list)
        or not evidence
        or not all(
            isinstance(q, str) and q.strip() and q in message["body"] for q in evidence
        )
    ):
        raise ValueError("mail evidence must quote the supplied message")


def prepare_mail(root: Path, data_dir: Path | None = None) -> dict[str, Any]:
    messages = json.loads(
        (root / "examples/mail/messages.json").read_text(encoding="utf-8")
    )
    directory = (data_dir or root / "var") / "mail-handoff"
    directory.mkdir(parents=True, exist_ok=True)
    package = {
        "task": "classify-mail",
        "messages": [{"message": m, "input_hash": input_digest(m)} for m in messages],
        "output": (directory / "results.jsonl").relative_to(root).as_posix(),
        "instructions": "Follow prompts/mail-review.md. Classification is a proposal; do not change application states or send messages.",
    }
    existing = directory / "manifest.json"
    if (
        existing.exists()
        and json.loads(existing.read_text(encoding="utf-8")) != package
    ):
        raise ValueError(
            "a different mail handoff already exists; retain it and review before changing inputs"
        )
    (directory / "manifest.json").write_text(
        json.dumps(package, indent=2), encoding="utf-8"
    )
    return package


def finish_mail(workflow: Any) -> dict[str, int]:
    root = workflow.root
    package = json.loads(
        (workflow.settings.data_dir / "mail-handoff/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    current = json.loads(
        (root / "examples/mail/messages.json").read_text(encoding="utf-8")
    )
    expected = {m["id"]: m for m in current}
    if (
        len(expected) != len(current)
        or len(package["messages"]) != len(expected)
        or {entry["message"]["id"] for entry in package["messages"]} != set(expected)
    ):
        raise ValueError("mail selection does not match the fictional input package")
    if any(
        entry["input_hash"] != input_digest(entry["message"])
        or entry["message"] != expected.get(entry["message"]["id"])
        for entry in package["messages"]
    ):
        raise ValueError("mail inputs changed after preparation")
    records = [
        json.loads(line)
        for line in (workflow.settings.data_dir / "mail-handoff/results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    seen = set()
    for result in records:
        identity = result.get("message_id")
        if identity not in expected or identity in seen:
            raise ValueError("unknown or duplicate mail result")
        validate_mail(expected[identity], result)
        seen.add(identity)
    if seen != set(expected):
        raise ValueError("mail batch is incomplete")
    with workflow.database.unit_of_work() as work:
        for result in records:
            message = expected[result["message_id"]]
            saved = {
                **result,
                "subject": message["subject"],
                "sender": message["from"],
                "simulated": result["model_id"] == "simulated-example/no-model",
            }
            work.connection.execute(
                "INSERT INTO portfolio_mail_reviews VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET input_hash=excluded.input_hash,result_json=excluded.result_json,created_at=excluded.created_at",
                (
                    result["message_id"],
                    result["input_hash"],
                    json.dumps(saved),
                    utc_text(workflow.clock.now()),
                ),
            )
    return {
        "imported": len(records),
        "application_status_changes": 0,
        "messages_sent": 0,
    }


def demonstrate_mail(workflow: Any) -> None:
    package = prepare_mail(workflow.root, workflow.settings.data_dir)
    output = workflow.settings.data_dir / "mail-handoff/results.jsonl"
    if not output.exists():
        records = []
        for entry in package["messages"]:
            message = entry["message"]
            category = "invitation" if message["id"] == "mail-001" else "rejection"
            records.append(
                {
                    "message_id": message["id"],
                    "input_hash": entry["input_hash"],
                    "classification": category,
                    "confidence": 95,
                    "reason": "Simulated example, no model invoked.",
                    "evidence": [message["body"]],
                    "model_id": "simulated-example/no-model",
                }
            )
        output.write_text(
            "".join(json.dumps(row) + "\n" for row in records), encoding="utf-8"
        )
    finish_mail(workflow)
