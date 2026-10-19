import json
from pathlib import Path

import pytest

from job_system.adapters.sources.local_html import LocalHTMLSource


def test_adapter_preserves_nested_description_and_stable_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "page.html"
    path.write_text(
        '<article data-job-id="one"><h2 data-field="title">Analyst</h2><p data-field="employer">Example</p><p data-field="locations">Berlin; Remote</p><div data-field="description"><h3>Responsibilities</h3><p>Analyse <strong>economic</strong> data.</p></div></article>',
        encoding="utf-8",
    )
    item = list(LocalHTMLSource(path).collect())[0]
    data = json.loads(item.content)
    assert item.external_id == "one" and data["detail"]["locations"] == [
        "Berlin",
        "Remote",
    ]
    assert "Analyse economic data." in data["detail"]["description"]
    assert item.content == list(LocalHTMLSource(path).collect())[0].content


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.invalid",
        "https://user:secret@example.invalid",
        "https://example.invalid/path",
    ],
)
def test_adapter_rejects_bad_origin(tmp_path: Path, origin: str) -> None:
    with pytest.raises(ValueError):
        LocalHTMLSource(tmp_path / "page.html", origin=origin)


def test_duplicate_ids_fail_instead_of_merging(tmp_path: Path) -> None:
    article = '<article data-job-id="one"><p data-field="title">A</p><p data-field="employer">B</p><p data-field="locations">C</p><p data-field="description">D</p></article>'
    path = tmp_path / "page.html"
    path.write_text(article * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        list(LocalHTMLSource(path).collect())
