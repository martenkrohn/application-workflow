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
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
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


def test_dashboard_ends_with_assessed_vacancies_and_saves_human_choice(workflow):
    workflow.demo()
    with running_dashboard(workflow) as (request, html):
        assert "Assessed vacancies" in html
        assert "Assessment queue" not in html and "Start application" not in html
        state = request("/api/v1/dashboard")
        assert state["counts"] == {"quick": 0, "detailed": 6}
        row = state["detailed"][0]
        assert row["strengths"] and row["score_components"]
        request("/api/v1/reviews", {"group_id": row["group_id"], "status": "interested"})
        assert (
            next(
                v
                for v in workflow.vacancies()
                if v["external_id"] == row["sources"][0]["external_id"]
            )["human_status"]
            == "interested"
        )
        for path, body in [("/api/v1/workspaces", {}), ("/api/v1/assessment-queue/prepare", {})]:
            with pytest.raises(urllib.error.HTTPError) as rejected:
                request(path, body)
            assert rejected.value.code == 404


def test_dashboard_denies_external_origin_host_and_wrong_token(workflow):
    workflow.demo()
    with running_dashboard(workflow) as (request, _):
        row = request("/api/v1/dashboard")["detailed"][0]
        for headers in [
            {"Origin": "https://external.example.invalid"},
            {"X-Dashboard-Token": "wrong"},
            {"Host": "external.example.invalid"},
        ]:
            with pytest.raises(urllib.error.HTTPError) as rejected:
                request(
                    "/api/v1/reviews",
                    {"group_id": row["group_id"], "status": "interested"},
                    headers,
                )
            assert rejected.value.code == 403
