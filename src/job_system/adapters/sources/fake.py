"""Deterministic fake adapters used to prove the operational core."""

from __future__ import annotations

import json
from collections.abc import Iterable

from job_system.domain.models import SourceItem


class FakeSourceError(RuntimeError):
    pass


class FakeSource:
    def __init__(self, name: str, *, fail: bool = False) -> None:
        self._name = name
        self.fail = fail

    @property
    def name(self) -> str:
        return self._name

    def collect(self) -> Iterable[SourceItem]:
        if self.fail:
            raise FakeSourceError(f"deliberate failure from fake source {self.name}")
        payload = {
            "external_id": f"{self.name}-001",
            "title": f"Example vacancy from {self.name}",
            "source": self.name,
            "description": (
                "Responsibilities: Analyse economic datasets and prepare research reports. "
                "Develop statistical models and communicate evidence to the policy team. "
                "Requirements: Economics degree and interest in quantitative methods. "
                "We offer a full-time analytical role with training and development opportunities."
            ),
        }
        yield SourceItem(
            external_id=payload["external_id"],
            source_url=f"https://example.invalid/{self.name}/jobs/001",
            media_type="application/json",
            content=json.dumps(payload, sort_keys=True).encode(),
        )
