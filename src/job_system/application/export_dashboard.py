"""Export a fixture-only browser demo; production data cannot enter this export."""

import json
import shutil
import tempfile
from importlib.resources import files
from pathlib import Path

from job_system.application.dashboard import DashboardService
from job_system.application.portfolio import PROFILE, PortfolioWorkflow
from job_system.web.server import source_html


def export_dashboard(root: Path, destination: Path) -> dict:
    root = root.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    # Build a fresh isolated database from the tracked fictional fixtures. Never
    # read an existing runtime database when preparing the hosted preview.
    with tempfile.TemporaryDirectory(prefix="workflow-demo-") as temporary:
        project = Path(temporary)
        for folder in ["examples", "config", "prompts"]:
            shutil.copytree(root / folder, project / folder)
        workflow = PortfolioWorkflow(project)
        workflow.demo()
        profile = workflow.assessment.ensure_profile(PROFILE)
        data = DashboardService(project, workflow.settings.data_dir, workflow.database).snapshot(
            profile.profile_id
        )
        for row in data["detailed"]:
            if any(source["source"] != "demo-html" for source in row["sources"]):
                raise ValueError("Only fictional data may be exported")
            # Stable demo identities preserve browser-only choices after rebuilds.
            row["group_id"] = "demo-" + row["sources"][0]["external_id"]
            row["assessment_profile_id"] = "fictional-demo-profile"
        assets = files("job_system.web.static")
        for name in ["index.html", "app.js", "styles.css"]:
            (destination / name).write_bytes(assets.joinpath(name).read_bytes())
        (destination / "demo-data.js").write_text(
            "window.DEMO_DASHBOARD = "
            + json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
            + ";\n",
            encoding="utf-8",
        )
        (destination / "demo-source.html").write_text(source_html(workflow), encoding="utf-8")
    return {
        "directory": str(destination),
        "vacancies": len(data["detailed"]),
        "fictional_only": True,
    }
