"""Run every portfolio command directly from a checkout, without package installation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from job_system.cli.main import main

raise SystemExit(main())
