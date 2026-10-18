# Neutral source adapters

The public-facing edition describes an extensible collection workflow, not a scraper that accepts any website unchanged. The original site-specific adapters and configurations remain private.

## Existing contract

The original `SourceAdapter` protocol in `application/ports.py` requires:

```python
@property
def name(self) -> str: ...

def collect(self) -> Iterable[SourceItem]: ...
```

Each `SourceItem` has an external ID, source URL, media type and bytes. This boundary already existed in the working project. The bundled local adapter implements it with no network requests.

## Neutral payload

This edition's normalizer accepts JSON with a `detail` object and neutral field names:

```json
{
  "external_id": "role-001",
  "detail": {
    "title": "Research Analyst",
    "employer": "Example Organisation",
    "locations": ["Berlin", "Remote"],
    "description": "Complete description with responsibilities and requirements...",
    "remote_policy": "Remote possible"
  }
}
```

`description` or `description_text` holds the complete description; `content` is also accepted. Missing facts remain unknown. The quality checks require a title and sufficient description evidence; a short summary is not promoted to ready evidence merely to make a demonstration pass.

The source adapter is responsible for translating its upstream fields and layouts into this contract. The shared normalizer no longer branches on concrete provider names. It does not guess how arbitrary APIs, email cards or login flows work.

## Local HTML demonstration

`examples/jobs.html` is entirely self-authored. An `<article data-job-id="...">` contains elements labelled with `data-field="title"`, `employer`, `locations`, `description`, and optional neutral fields. Nested paragraphs and headings are preserved in the description. Locations use a semicolon separator. IDs are unique stable slugs.

`config/source.toml` selects the local fixture, neutral source name and fictional HTTPS origin. The configured file must stay inside the checkout. `example.invalid` URLs label the fixture; they are never fetched.

## Private extension

A locally importable adapter can be connected without committing it:

```python
# my_private_adapter.py, outside this repository or installed privately
from job_system.domain.models import SourceItem

class MySource:
    name = "private-source"

    def collect(self):
        # Read an authorised source, then emit neutral SourceItem records.
        ...

def create_source(project_root):
    return MySource()
```

```sh
python run_demo.py collect --adapter my_private_adapter:create_source
```

The factory runs trusted local Python code. This is an implemented import boundary, not an automatic plugin marketplace or a sandbox. The adapter must own its access policy, allowed hosts, small request budget, delays and stop conditions. Website-specific IDs, selectors, authentication and response formats remain its responsibility. Secrets and browser sessions belong outside tracked files.

## Publication scope

No concrete production domains, sender patterns, website-specific fixtures, scraped vacancies or historical private Git objects are included. Only fictional examples are shipped. Future adapters need their own parsing tests; replacing domains in copied code would not establish generality.
