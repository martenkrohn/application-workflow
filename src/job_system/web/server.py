"""Connect the original dashboard to the isolated portfolio workspace."""

from http.server import ThreadingHTTPServer

from job_system.application.dashboard import DashboardService
from job_system.application.portfolio import PROFILE, PortfolioWorkflow
from job_system.web.dashboard import DashboardServer


def create_server(workflow: PortfolioWorkflow, port: int = 8765) -> ThreadingHTTPServer:
    profile = workflow.assessment.ensure_profile(PROFILE)
    # Retain any human choices made with the earlier portfolio interface.
    with workflow.database.unit_of_work() as work:
        assert work.connection is not None
        for row in work.connection.execute(
            "SELECT * FROM portfolio_decisions"
        ).fetchall():
            group_id = work.reviews.ensure_group(
                ((row["source"], row["external_id"]),),
                workflow.ids.new("vgrp"),
                row["updated_at"],
            )
            if (
                work.reviews.decision(group_id, profile.profile_id)["updated_at"]
                is None
            ):
                work.reviews.set_decision(
                    group_id, profile.profile_id, row["status"], None, row["updated_at"]
                )
    service = DashboardService(
        workflow.root, workflow.settings.data_dir, workflow.database
    )
    dashboard = DashboardServer(
        service,
        profile.profile_id,
        port=port,
        prepare_batches=lambda pairs: workflow.prepare(
            [
                {"source": source, "external_id": external_id}
                for source, external_id in pairs
            ]
        ),
    )
    return dashboard.httpd


def serve_dashboard(workflow: PortfolioWorkflow, port: int = 8765) -> None:
    server = create_server(workflow, port)
    print(
        f"Dashboard: http://127.0.0.1:{server.server_port}/ (Ctrl+C to stop)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
