# Architecture and provenance

## Working layers

| Layer | Responsibility | Origin |
| --- | --- | --- |
| Domain | Source items, quality checks, immutable clean vacancy documents | Transferred; website-specific branches and field aliases removed |
| Collection | Per-source attempts, artifacts, observations, checkpoints and failure recovery | Existing collection service |
| Infrastructure | SQLite repositories, 20 migrations, artifact hashes, locking and local configuration | Existing implementation; personal mock address and provider priorities removed |
| Assessment | Profile snapshots, bounded manifests, input identities, citations, validation and persistent evaluations | Existing detailed-assessment service; neutral profile default, provider-specific fetch-state integration removed |
| Portfolio orchestration | Exact human selection, strict whole-batch import, deterministic simulation and decisions | Added for the review edition |
| Source adapter | Parser for a self-authored HTML fixture | Added; same original SourceAdapter/SourceItem boundary |
| Mail demonstration | Fictional message handoff, identity and quotation validation, stored proposals | Added; uses the classification categories of the original workflow, not its production mailbox connector |
| Dashboard | Quick review, assessment queue, detailed review, filters, dialogs and table interactions | Original frontend, dashboard service, queue service and grouping; personal labels and source-specific rules removed |
| Application workspaces | Human starts a folder, collaborates on documents, tracks decisions and moves folders by status | Original decision/move lifecycle; private document scaffolding replaced with fictional Markdown materials |
| CLI | Reproducible demo and explicit prepare, validate and import commands | Small entry point added for the portfolio |

This is an adapted runnable slice, not a copy of the complete private project. The private project remains unchanged. File-level hashes and transfer notes are retained locally outside version control.

## Source normalization

A source adapter parses its own upstream format and emits JSON in neutral field names. The retained normalizer turns those values and description lines into facts, evidence blocks and immutable versions. Provider names are no longer used for extracting facts or ranking collection candidates.

The standard source item carries its stable identity, public URL, media type and raw bytes. The core hashes and stores the emitted bytes. For the local HTML adapter these are the extracted neutral records; they are not a byte-for-byte HTTP-response archive. No live request is made by that adapter.

## Assessment handoff

The person chooses identities explicitly. The existing AssessmentService freezes the fictional profile and selected clean versions into a manifest and item files. Every item is bound to the batch, observation, input hash, profile version and clean vacancy version. A separate HANDOFF.md explains the agent task and the human import step.

The agent writes results in JSONL. The application checks identity, immutability, score/recommendation consistency and valid evidence references. The portfolio wrapper requires the complete batch to pass before importing. The human's application decision is stored separately from the recommendation.

A modified-input validation path was corrected in the review edition: hash mismatches now return a validation error before attempting to read fields from the changed item.

## Storage and packaging

The database schema is retained to keep the original storage contracts and migration sequence. Some tables support original workflows not exposed by this edition. New portfolio decisions and mail proposals have separate tables; generated data is never committed. Independent named workspaces keep review attempts separate without deletion.

Runtime code uses only Python's standard library. The installed package includes migrations and dashboard assets. Example content, prompts and project configuration remain in the checkout, which is the working directory for CLI usage.

## Intentional limits

- No bundled website-specific scraper, browser automation or live mailbox connector.
- No automatic model execution, scheduler or multi-provider runner.
- No claim of a universal HTML parser or complete parity with the production workflow.
- Validation establishes contract consistency, not the truth of an agent judgement.
- The local dashboard is a single-user review tool and binds only to loopback.

See [source adapters](source-adapters.md) and [human/agent interaction](human-ai-workflow.md).

## Dashboard preservation

The portfolio uses the working project's `web/static/index.html`, `app.js` and `styles.css`, `web/dashboard.py`, `application/dashboard.py` and `application/assessment_queue.py`. It keeps the original three-view review flow. Adaptations are limited to neutral labels, fictional source links, isolated runtime paths, agent-neutral handoff wording and local HTTP safeguards. Private folder discovery is omitted; newly created demo folders are registered directly.

Quick priorities are labelled presets, not validated agent outputs. Detailed example assessments pass through the normal preparation/validation/import contract. Mail proposals remain a separate CLI demonstration and are not presented as an original dashboard feature.
