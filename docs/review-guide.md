# Detailed review

The collection and quick-screening command prepares detailed batches. Start each adjacent `HANDOFF.md` manually in Codex with GPT-6.1 Sol. It supplies a frozen profile, item manifest, full evidence and output contract.

After that manual start, the task works through assigned vacancies, validates JSONL output and imports it through Python. Invalid records or changed inputs must be corrected before import. Final results appear in the dashboard.

See the [technical walkthrough](walkthrough.md) and [human/AI responsibilities](human-ai-workflow.md).
