# Technical walkthrough

The [hosted demo](https://marten-application-workflow-demo.saltychair1.chatgpt.site) is the quickest way to inspect the result. This guide is optional.

## Explore the fictional dashboard locally

Use Python 3.13 from the repository root:

```sh
python run_demo.py demo
python run_demo.py dashboard serve
```

Open `http://127.0.0.1:8765/`. Search for a role, inspect Details, change Minimum fit to see weaker matches and save a vacancy to the shortlist. These are fictional examples; no model is invoked. Repeating the demo preserves human decisions and imported results.

## Run compact screening on a separate dataset

Install Codex CLI, sign in and ensure `codex` is on PATH. Availability of the pinned models depends on the user's account. This command uses the model account and may consume plan usage:

```sh
python run_demo.py --workspace review run
```

It collects bundled fictional HTML, runs GPT-6 Luna with Low reasoning on compact packets, validates and imports the output, and prepares detailed batches for promoted vacancies. It reuses pending screening work after interruption. Failed or invalid output stays local for inspection.

Open a prepared manifest's adjacent `HANDOFF.md` in Codex, choose GPT-6.1 Sol and start the task. The handoff instructs the agent to read assigned full evidence, write JSONL, validate and import. Then inspect the stored results:

```sh
python run_demo.py --workspace review dashboard serve
```

Low-level `assessment prepare --ids source:id`, `assessment validate BATCH_ID`, `assessment finish BATCH_ID` and `assessment instructions BATCH_ID` remain available for inspection and recovery. They do not start a model. Keep live adapters and authenticated sessions private; see [source adapters](source-adapters.md).

## Export the static preview

```sh
python run_demo.py export-dashboard --output demo
python -m http.server 8765 --directory demo
```

The exporter builds fresh fictional data in an isolated temporary directory. It never exports an existing runtime database. The preview supports filtering, sorting, details, dark mode and a browser-local shortlist without a backend.

## Tests

```sh
python -m pip install -e ".[dev]"
python -m pytest
```

Tests exercise evidence integrity, immutable inputs, validation, human decisions, screening orchestration and the dashboard boundary. A controlled test double replaces the model subprocess; no paid model calls or real source requests are needed.
