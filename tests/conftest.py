import shutil
from pathlib import Path

import pytest

from job_system.application.portfolio import PortfolioWorkflow


@pytest.fixture
def workflow(tmp_path: Path) -> PortfolioWorkflow:
    project = Path(__file__).resolve().parents[1]
    for folder in ["examples", "prompts", "config"]:
        shutil.copytree(project / folder, tmp_path / folder)
    return PortfolioWorkflow(tmp_path)
