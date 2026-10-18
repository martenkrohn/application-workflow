import json
from pathlib import Path

import pytest

from job_system.application.assessment import AssessmentError
from job_system.application.cleaning import CleaningError, CleaningService
from job_system.application.portfolio import PortfolioWorkflow


def prepare(workflow: PortfolioWorkflow) -> str:
    workflow.collect()
    return workflow.prepare(
        [{"source": "demo-html", "external_id": "research-analyst"}]
    )["batch_id"]


def output(workflow: PortfolioWorkflow, batch: str) -> Path:
    return workflow.root / workflow.batch_paths(batch)["output"]


def test_demo_uses_original_services_and_is_idempotent(
    workflow: PortfolioWorkflow,
) -> None:
    first = workflow.demo()
    second = workflow.demo()
    assert first["vacancies"] == 3
    assert second["collection"]["sources"][0]["observations_unchanged"] == 3
    state = workflow.snapshot()
    assert len(state["batches"]) == 1 and len(state["email"]) == 2
    assert all(v["simulated"] for v in state["vacancies"])
    assert all(v["human_status"] == "new" for v in state["vacancies"])
    assert {
        v["assessment"]["evaluation"]["recommendation"] for v in state["vacancies"]
    } == {"high", "medium", "reject"}


def test_human_choice_creates_exactly_one_task_without_running_agent(
    workflow: PortfolioWorkflow,
) -> None:
    batch = prepare(workflow)
    path = workflow.root / workflow.batch_paths(batch)["manifest"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["expected_item_count"] == 1
    assert len(manifest["items"]) == 1
    assert not output(workflow, batch).exists()
    assert len(workflow.vacancies()) == 3
    assert all(v["assessment"] is None for v in workflow.vacancies())
    assert (path.parent / "HANDOFF.md").exists()


@pytest.mark.parametrize(
    "selection",
    [
        [],
        [{"source": "demo-html", "external_id": "unknown"}],
        [{"source": "demo-html", "external_id": "research-analyst"}] * 2,
    ],
)
def test_bad_selection_does_not_create_work(
    workflow: PortfolioWorkflow, selection: list
) -> None:
    workflow.collect()
    with pytest.raises(ValueError):
        workflow.prepare(selection)
    assert workflow.batches() == []


@pytest.mark.parametrize(
    "fault", ["identity", "citation", "duplicate", "band", "boolean-score"]
)
def test_invalid_agent_output_is_not_imported(
    workflow: PortfolioWorkflow, fault: str
) -> None:
    batch = prepare(workflow)
    workflow.simulate(batch)
    path = output(workflow, batch)
    record = json.loads(path.read_text(encoding="utf-8"))
    if fault == "identity":
        record["input_hash"] = "0" * 64
    elif fault == "citation":
        record["citations"][0]["refs"] = ["invented-block"]
    elif fault == "band":
        record["evaluation"]["recommendation"] = "reject"
    elif fault == "boolean-score":
        record["evaluation"]["suitability_score"] = True
    text = json.dumps(record) + "\n"
    path.write_text(text * 2 if fault == "duplicate" else text, encoding="utf-8")
    assert workflow.validate(batch)["rejected"] > 0
    with pytest.raises(AssessmentError):
        workflow.finish(batch)
    assert all(v["assessment"] is None for v in workflow.vacancies())


def test_changed_shared_profile_is_detected(workflow: PortfolioWorkflow) -> None:
    batch = prepare(workflow)
    workflow.simulate(batch)
    manifest = workflow.root / workflow.batch_paths(batch)["manifest"]
    (manifest.parent / "profile.json").write_text("{}", encoding="utf-8")
    with pytest.raises(AssessmentError):
        workflow.validate(batch)


def test_changed_prepared_item_is_detected(workflow: PortfolioWorkflow) -> None:
    batch = prepare(workflow)
    workflow.simulate(batch)
    manifest = workflow.root / workflow.batch_paths(batch)["manifest"]
    entry = json.loads(manifest.read_text(encoding="utf-8"))["items"][0]
    (manifest.parent / entry["path"]).write_text("{}", encoding="utf-8")
    assert workflow.validate(batch)["rejected"] > 0
    with pytest.raises(AssessmentError):
        workflow.finish(batch)


def test_example_results_do_not_replace_agent_output(
    workflow: PortfolioWorkflow,
) -> None:
    batch = prepare(workflow)
    workflow.simulate(batch)
    before = output(workflow, batch).read_bytes()
    with pytest.raises(FileExistsError):
        workflow.simulate(batch)
    assert output(workflow, batch).read_bytes() == before


def test_import_does_not_decide_and_decisions_survive_collection(
    workflow: PortfolioWorkflow,
) -> None:
    batch = prepare(workflow)
    workflow.simulate(batch)
    assert workflow.finish(batch)["imported"] == 1
    assert {v["human_status"] for v in workflow.vacancies()} == {"new"}
    workflow.decide("demo-html", "research-analyst", "interested")
    workflow.collect()
    analyst = next(
        v for v in workflow.vacancies() if v["external_id"] == "research-analyst"
    )
    assert analyst["human_status"] == "interested"
    assert workflow.finish(batch)["unchanged"] == 1


def test_unknown_or_invalid_decision_is_rejected(workflow: PortfolioWorkflow) -> None:
    workflow.collect()
    with pytest.raises(ValueError):
        workflow.decide("demo-html", "unknown", "interested")
    with pytest.raises(ValueError):
        workflow.decide("demo-html", "research-analyst", "send-now")


def test_raw_evidence_hash_is_checked(workflow: PortfolioWorkflow) -> None:
    workflow.collect()
    with workflow.database.unit_of_work(read_only=True) as work:
        row = work.evidence.review(1)[0]
    (workflow.settings.data_dir / row["relative_path"]).write_bytes(b"{}")
    with pytest.raises(CleaningError, match="integrity"):
        CleaningService(workflow.settings.data_dir, workflow.database).preview(row)


def test_fixture_cannot_escape_project(workflow: PortfolioWorkflow) -> None:
    (workflow.root / "config/source.toml").write_text(
        '[source]\nname="test"\nfixture="../secret.html"\norigin="https://example.invalid"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="inside"):
        workflow.collect()


def test_named_workspace_isolated_and_handoffs_keep_the_workspace(
    workflow: PortfolioWorkflow,
) -> None:
    workflow.demo()
    review = PortfolioWorkflow(workflow.root, "review")
    review.collect()
    assert all(v["assessment"] is None for v in review.vacancies())
    batch = review.prepare(
        [{"source": "demo-html", "external_id": "research-analyst"}]
    )["batch_id"]
    manifest = review.root / review.batch_paths(batch)["manifest"]
    handoff = (manifest.parent / "HANDOFF.md").read_text(encoding="utf-8")
    assert "--workspace review assessment validate" in handoff
    assert "var/workspaces/review/" in handoff
    assert len(workflow.batches()) == 1 and len(review.batches()) == 1
    with pytest.raises(ValueError, match="Unfinished"):
        review.demo()
    assert not output(review, batch).exists()
    review.simulate(batch)
    review.finish(batch)
    review.demo()
    assert len(review.mail_reviews()) == 2
    assert (review.settings.data_dir / "mail-handoff/results.jsonl").is_file()


def test_trusted_private_adapter_boundary(
    workflow: PortfolioWorkflow, monkeypatch
) -> None:
    import sys
    import types

    from job_system.adapters.sources.fake import FakeSource

    module = types.ModuleType("example_private_adapter")
    module.create_source = lambda root: FakeSource("local-extension")
    monkeypatch.setitem(sys.modules, "example_private_adapter", module)
    result = workflow.collect("example_private_adapter:create_source")
    assert result["sources"][0]["source_name"] == "local-extension"
    assert workflow.vacancies()[0]["source"] == "local-extension"
