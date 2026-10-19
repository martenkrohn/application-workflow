# Architecture

The workflow separates evidence, judgement and human decisions.

1. A source adapter emits records with stable identities and source evidence. The private setup handles source authentication interactively; this edition bundles a neutral local HTML adapter.
2. Collection stores content-addressed artifacts and retrieval observations. Normalization creates immutable clean versions linked to those observations.
3. Compact screening packets carry a shared profile and a reduced view of each vacancy. The included Codex runner pins `gpt-6-luna` and `model_reasoning_effort="low"`.
4. Valid screening output is imported into SQLite. Promoted, unassessed vacancies are automatically packaged into detailed batches.
5. The human starts the detailed handoff in Codex with `gpt-6.1-sol`. The task reads fuller evidence, returns the defined JSONL contract, validates and imports the completed batch.
6. The dashboard reads stored assessments and human shortlist decisions.

## Preserved implementation

The collection service, artifact store, SQL repositories, clean-data service, assessment validation and dashboard originated in the private working project. Source-specific extraction and priorities were removed. The compact runner, neutral fixtures, focused UI and fixture-only static export are adaptations for this portfolio.

The remaining schema migrations retain their original numbers. Fresh installations do not create email-review, application-folder or dashboard-queue tables. Existing local demo databases are left in place; retired tables are no longer accessed by this edition.

## Batch contracts

Manifests enumerate the exact work, profile version and input hashes. Shared profile content is stored once per batch. Each item has its own identity and content hash. Detailed results cite evidence blocks and are checked against the frozen input. Validation establishes structural consistency and traceability, not that a judgement is correct.

The quick runner supplies the compact package as data and uses a read-only Codex sandbox. It checks the selected model identity and imports only a complete valid batch. Failed work remains prepared for inspection and rerun. Detailed assessment retains the explicit human start; there is no background detailed-assessment runner.

## Hosted preview

`export-dashboard` creates a fresh temporary database exclusively from the tracked fictional fixtures, then exports reviewed results and the existing frontend. It does not load the user's runtime database. The hosted preview runs entirely in the browser; shortlist changes use browser storage. The local dashboard uses SQLite and loopback-only HTTP with token and origin checks for decisions.
