import json
from pathlib import Path

import pytest

from job_system.adapters.sources.local_html import LocalHTMLSource
from job_system.application.mail_demo import (
    finish_mail,
    input_digest,
    prepare_mail,
    validate_mail,
)


def test_adapter_preserves_nested_description_and_stable_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "page.html"
    path.write_text(
        '<article data-job-id="one"><h2 data-field="title">Analyst</h2><p data-field="employer">Example</p><p data-field="locations">Berlin; Remote</p><div data-field="description"><h3>Responsibilities</h3><p>Analyse <strong>economic</strong> data.</p></div></article>',
        encoding="utf-8",
    )
    item = list(LocalHTMLSource(path).collect())[0]
    data = json.loads(item.content)
    assert item.external_id == "one" and data["detail"]["locations"] == [
        "Berlin",
        "Remote",
    ]
    assert "Analyse economic data." in data["detail"]["description"]
    assert item.content == list(LocalHTMLSource(path).collect())[0].content


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.invalid",
        "https://user:secret@example.invalid",
        "https://example.invalid/path",
    ],
)
def test_adapter_rejects_bad_origin(tmp_path: Path, origin: str) -> None:
    with pytest.raises(ValueError):
        LocalHTMLSource(tmp_path / "page.html", origin=origin)


def test_duplicate_ids_fail_instead_of_merging(tmp_path: Path) -> None:
    article = '<article data-job-id="one"><p data-field="title">A</p><p data-field="employer">B</p><p data-field="locations">C</p><p data-field="description">D</p></article>'
    path = tmp_path / "page.html"
    path.write_text(article * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        list(LocalHTMLSource(path).collect())


@pytest.mark.parametrize("fault", ["hash", "quote", "confidence", "classification"])
def test_mail_contract_rejects_unbound_or_unsupported_results(fault: str) -> None:
    message = {"id": "mail-1", "body": "We invite you to an interview."}
    result = {
        "message_id": "mail-1",
        "input_hash": input_digest(message),
        "classification": "invitation",
        "confidence": 90,
        "reason": "An invitation.",
        "evidence": [message["body"]],
        "model_id": "test-agent",
    }
    if fault == "hash":
        result["input_hash"] = "0" * 64
    elif fault == "quote":
        result["evidence"] = ["Invented text"]
    elif fault == "confidence":
        result["confidence"] = True
    else:
        result["classification"] = "send-reply"
    with pytest.raises(ValueError):
        validate_mail(message, result)


def test_mail_rejects_incomplete_results_atomically(workflow) -> None:
    package = prepare_mail(workflow.root)
    entry = package["messages"][0]
    message = entry["message"]
    result = {
        "message_id": message["id"],
        "input_hash": entry["input_hash"],
        "classification": "invitation",
        "confidence": 90,
        "reason": "Invitation.",
        "evidence": [message["body"]],
        "model_id": "test-agent",
    }
    (workflow.root / "var/mail-handoff/results.jsonl").write_text(
        json.dumps(result) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="incomplete"):
        finish_mail(workflow)
    assert workflow.mail_reviews() == []


def test_mail_rejects_changed_inputs(workflow) -> None:
    workflow.demo()
    path = workflow.root / "var/mail-handoff/manifest.json"
    package = json.loads(path.read_text(encoding="utf-8"))
    package["messages"][0]["message"]["body"] = "Changed"
    path.write_text(json.dumps(package), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        finish_mail(workflow)
