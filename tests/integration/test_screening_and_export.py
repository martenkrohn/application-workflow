import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_system.application.export_dashboard import export_dashboard
from job_system.application.portfolio import PROFILE
from job_system.infrastructure.screening_runner import run_screening


def fake_codex(command, *, cwd, input, **kwargs):
    assert command[command.index("--model") + 1] == "gpt-6-luna"
    assert 'model_reasoning_effort="low"' in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    package = json.loads(input.split("INPUT PACKAGE\n")[1])
    manifest = package["manifest"]
    records = []
    for entry in package["items"]:
        item = entry["item"]
        record = {
            key: manifest[key]
            for key in [
                "batch_id",
                "prompt_id",
                "prompt_version",
                "profile_id",
                "profile_version",
                "profile_hash",
            ]
        }
        record.update({key: item[key] for key in ["work_item_id", "observation_id"]})
        record.update(
            input_hash=entry["input_hash"],
            clean_version_id=item["screening_packet"]["version_id"],
            model_id="gpt-6-luna",
            status="completed",
            suitability_score=85,
            assessment_confidence=80,
            decision="promote",
            exclusion_basis=None,
            summary="Plausible title match; keep for detailed review.",
            suspicious_content=False,
        )
        assert "blocks" not in item["screening_packet"]
        records.append(record)
    response = Path(command[command.index("--output-last-message") + 1])
    response.write_text(json.dumps({"results": records}), encoding="utf-8")
    return SimpleNamespace(returncode=0)


def test_automatic_screening_and_manual_detailed_boundary(workflow, monkeypatch):
    import job_system.infrastructure.screening_runner as module

    monkeypatch.setattr(module.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(
        module,
        "run_screening",
        lambda workflow, batch: run_screening(workflow, batch, runner=fake_codex),
    )
    result = workflow.run()
    assert result["screening"] and result["detailed_batches"]
    assert all(v["assessment"] is None for v in workflow.vacancies())
    batch = result["detailed_batches"][0]
    assert not (workflow.root / batch["output"]).exists()
    handoff = (
        (workflow.root / batch["manifest"]).with_name("HANDOFF.md").read_text(encoding="utf-8")
    )
    assert "GPT-6.1 Sol" in handoff
    assert workflow.run()["screening"] == []
    assert workflow.run()["detailed_batches"] == []


def test_invalid_screening_never_imports_or_promotes(workflow, monkeypatch):
    monkeypatch.setattr(
        "job_system.infrastructure.screening_runner.shutil.which", lambda _: "codex"
    )
    workflow.collect()
    batch = workflow.screening.prepare(("demo-html",), profile_path=PROFILE)["batch_id"]

    def invalid(command, **kwargs):
        result = fake_codex(command, **kwargs)
        output = Path(command[command.index("--output-last-message") + 1])
        data = json.loads(output.read_text(encoding="utf-8"))
        data["results"][0]["input_hash"] = "0" * 64
        output.write_text(json.dumps(data), encoding="utf-8")
        return result

    with pytest.raises(ValueError, match="rejected"):
        run_screening(workflow, batch, runner=invalid)
    with workflow.database.unit_of_work(read_only=True) as work:
        assert all(
            row["screening_score"] is None
            for row in work.normalization.screening_dashboard(None, None)
        )


def test_static_export_uses_only_fresh_fixtures(workflow, tmp_path):
    (workflow.root / "var/private-marker.txt").write_text(
        "PRIVATE-MUST-NOT-EXPORT", encoding="utf-8"
    )
    result = export_dashboard(workflow.root, tmp_path / "static")
    assert result["vacancies"] == 6 and result["fictional_only"]
    text = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "static").iterdir())
    assert "PRIVATE-MUST-NOT-EXPORT" not in text
    assert "window.DEMO_DASHBOARD" in text
    assert "Assessment queue" not in text
