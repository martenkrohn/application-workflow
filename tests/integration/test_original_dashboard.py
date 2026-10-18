import json
import re
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

import pytest

from job_system.web.server import create_server


@contextmanager
def running_dashboard(workflow):
    server = create_server(workflow, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base) as response:
            html = response.read().decode()
            assert (
                "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            )
        token = re.search(r'name="dashboard-token" content="([^"]+)"', html)[1]

        def request(path, data=None, headers=None):
            options = {"X-Dashboard-Token": token, "Content-Type": "application/json"}
            options.update(headers or {})
            req = urllib.request.Request(
                base + path,
                data=json.dumps(data).encode() if data is not None else None,
                headers=options,
            )
            with urllib.request.urlopen(req) as response:
                return json.load(response)

        yield request, html
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_original_views_and_human_decisions(workflow):
    workflow.demo()
    with running_dashboard(workflow) as (request, html):
        for view in ("Quick review", "Assessment queue", "Detailed review"):
            assert view in html
        state = request("/api/v1/dashboard-state")
        assert state["dashboard"]["counts"] == {"quick": 3, "detailed": 3}
        row = state["dashboard"]["detailed"][0]
        assert row["summary"].startswith("Simulated")
        assert row["strengths"] and row["score_components"]
        request("/api/v1/reviews", {"group_id": row["group_id"], "status": "dismissed"})
        assert (
            next(
                v
                for v in workflow.vacancies()
                if v["external_id"] == row["sources"][0]["external_id"]
            )["human_status"]
            == "dismissed"
        )
        with pytest.raises(urllib.error.HTTPError) as rejected:
            request(
                "/api/v1/reviews",
                {"group_id": row["group_id"], "status": "interested"},
                {"Origin": "https://external.example.invalid"},
            )
        assert rejected.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as rejected:
            request("/api/v1/dashboard", headers={"Host": "external.example.invalid"})
        assert rejected.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as rejected:
            request(
                "/api/v1/reviews",
                {"group_id": row["group_id"], "status": "interested"},
                {"X-Dashboard-Token": "wrong"},
            )
        assert rejected.value.code == 403


def test_manual_queue_preview_and_exact_agent_handoff(workflow):
    workflow.demo()
    with running_dashboard(workflow) as (request, _):
        request(
            "/api/v1/assessments",
            {"source": "demo-html", "external_id": "research-coordinator"},
        )
        preview = request("/api/v1/assessment-queue/preview", {"total": 2})
        assert len(preview["rows"]) == 2
        assert any(
            row["external_id"] == "research-coordinator" and row["origin"] == "manual"
            for row in preview["rows"]
        )
        result = request(
            "/api/v1/assessment-queue/prepare", {"revision": preview["revision"]}
        )
        assert result["prepared"] == 2 and not result["waiting"]
        paths = workflow.batch_paths(result["batches"][0]["batch_id"])
        manifest_path = workflow.root / paths["manifest"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["expected_item_count"] == 2
        assert (manifest_path.parent / "HANDOFF.md").is_file()
        assert not (workflow.root / paths["output"]).exists()
        # A stale browser tab cannot prepare the same selection again.
        with pytest.raises(urllib.error.HTTPError):
            request(
                "/api/v1/assessment-queue/prepare", {"revision": preview["revision"]}
            )


def test_application_workspace_and_status_move_are_isolated(workflow):
    workflow.demo()
    with running_dashboard(workflow) as (request, _):
        row = request("/api/v1/dashboard")["detailed"][0]
        workspace = request(
            "/api/v1/workspaces",
            {"group_id": row["group_id"], "slug": "example-analyst"},
        )
        path = workflow.settings.data_dir / workspace["relative_path"]
        assert path.is_dir()
        assert (path / "assessment.json").is_file()
        assert "Do not submit" in (path / "application.md").read_text(encoding="utf-8")
        result = request(
            "/api/v1/reviews", {"group_id": row["group_id"], "status": "applied"}
        )
        moved = workflow.settings.data_dir / result["workspace_path"]
        assert not path.exists() and moved.is_dir()
        assert moved.parent.name == "02_applied"
        assert (
            (moved / "STATUS.md")
            .read_text(encoding="utf-8")
            .strip()
            .endswith("applied")
        )
        with pytest.raises(urllib.error.HTTPError):
            request(
                "/api/v1/workspaces", {"group_id": row["group_id"], "slug": "../escape"}
            )
