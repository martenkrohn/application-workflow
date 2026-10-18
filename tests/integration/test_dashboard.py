import json
import threading
import urllib.error
import urllib.request

from job_system.web.server import create_server


def test_dashboard_local_api_and_explicit_decisions(workflow) -> None:
    workflow.demo()
    server = create_server(workflow, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/api/state") as response:
            state = json.load(response)
        assert len(state["vacancies"]) == 3
        with urllib.request.urlopen(base + "/") as response:
            assert b"Human" in response.read()
            assert (
                "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
            )
        body = json.dumps(
            {
                "source": "demo-html",
                "external_id": "research-analyst",
                "status": "interested",
            }
        ).encode()
        for origin, status in [("https://external.example.invalid", 403), (base, 200)]:
            request = urllib.request.Request(
                base + "/api/decision",
                data=body,
                headers={"Origin": origin, "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(request) as response:
                    assert response.status == status
            except urllib.error.HTTPError as error:
                assert error.code == status
        request = urllib.request.Request(
            base + "/api/state", headers={"Host": "external.example.invalid"}
        )
        try:
            urllib.request.urlopen(request)
            raise AssertionError("external Host must be rejected")
        except urllib.error.HTTPError as error:
            assert error.code == 403
        assert (
            next(
                v
                for v in workflow.vacancies()
                if v["external_id"] == "research-analyst"
            )["human_status"]
            == "interested"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
