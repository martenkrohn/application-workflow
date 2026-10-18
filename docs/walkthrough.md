# Recruiter walkthrough

From the repository root, with Python 3.13:

```sh
python run_demo.py demo
python run_demo.py dashboard serve
```

Open http://127.0.0.1:8765/. This is the original project's dashboard with six fictional vacancies. Three detailed assessments and three quick-review priorities are simulated. The collection, evidence, preparation, validation and persistence services really run.

1. Start in **Detailed review**. Open **Details** for Research Data Analyst. Read its strengths, gaps, missing information and extracted facts. Set **Minimum fit** to `0` to compare the unsuitable senior engineering role. **Source** opens the fictional source text locally.
2. Open **Quick review**. The two higher-priority roles pass to detailed review; the lower-priority coordinator demonstrates that a human can override a recommendation. Click **Queue detailed review** on that role.
3. Open **Assessment queue**, enter total `2` and click **Preview selection**. The manual choice stays included alongside a suggested role. Change checkboxes and **Apply selection changes**, or leave the selection intact. **Prepare batches** writes bounded tasks without calling an agent.
4. Inspect the resulting local batch folder under `var/ai/input/` (or `var/workspaces/NAME/ai/input/`). `HANDOFF.md` gives exact input, output and validation commands. Prepared tasks survive restarting the dashboard. The queue lists pending batch IDs.
5. Return to **Detailed review**, click **Start application**, and choose a slug such as `example-research-analyst`. Open the generated folder under `var/applications/01_active/`. It contains the assessment, an application brief, a cover-letter draft and fictional materials guidance. Nothing is submitted.

The three views, table layout, filtering, sorting, column resizing, dark mode and dialogs come from the original working project. The portfolio replaces private profile data, source adapters and document scaffolding. It does not require a mailbox or model account.

## Try a real agent handoff in a separate dataset

```sh
python run_demo.py --workspace review collect
python run_demo.py --workspace review dashboard serve
```

All three vacancies in this dataset begin unassessed. Manually queue one in **Quick review**, preview total `1` in **Assessment queue**, then prepare it. Open the generated `HANDOFF.md` with your chosen agent. After the agent writes the assigned JSONL output:

```sh
python run_demo.py --workspace review assessment validate BATCH_ID
python run_demo.py --workspace review assessment finish BATCH_ID
```

Replace `BATCH_ID` with the displayed ID, then **Refresh data**. Invalid outputs are rejected before import. Preparing a batch never starts an agent; importing never decides to apply.

## Synthetic mail demonstration

Mail classification is a separate CLI example, outside the original dashboard's three views:

```sh
python run_demo.py --workspace mail-review mail prepare
```

Give the returned manifest and `prompts/mail-review.md` to an agent. After its output:

```sh
python run_demo.py --workspace mail-review mail finish
python run_demo.py --workspace mail-review status
```

Only classification proposals are stored. No account is contacted and no application status is changed automatically.
