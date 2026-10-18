"""Preview the existing fake source without accounts, network access, or private data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running directly from a checkout without downloading build dependencies.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from job_system.adapters.sources.fake import FakeSource


def main() -> None:
    items = []
    for item in FakeSource("portfolio-demo").collect():
        items.append(
            {
                "external_id": item.external_id,
                "source_url": item.source_url,
                "media_type": item.media_type,
                "content": json.loads(item.content),
            }
        )
    print(json.dumps({"synthetic": True, "vacancies": items}, indent=2))


if __name__ == "__main__":
    main()
