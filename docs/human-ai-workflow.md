# Human, agent and application

The central design is a human-in-the-loop pipeline. The human, agent and Python application have distinct responsibilities.

| Role | Responsibility |
| --- | --- |
| Human | Sets the profile, chooses the exact scope, reviews judgements, imports results and makes application decisions |
| Application | Collects and preserves evidence, prepares immutable work packages, validates structured output and persists state |
| Agent | Interprets only the assigned evidence, explains fit and gaps, cites evidence and returns the specified result |

## Executable cycle

1. Collect the fictional HTML vacancies into a local SQLite dataset.
2. Choose explicit source/job identities in the dashboard or CLI.
3. Prepare a bounded assessment batch (1–25 jobs), with profile snapshot, manifest, input hashes and work items.
4. Open HANDOFF.md in a capable agent environment. It identifies the exact files, schema and allowed output path.
5. Let the agent create JSONL results; do not change the immutable inputs.
6. Validate the output. Repair rejected results and revalidate; preserve unresolved work for inspection.
7. The human explicitly imports the complete validated batch and reviews it in the dashboard.
8. A separate human action changes the application decision. No application or message is sent.

This review edition deliberately uses explicit import. The original working project also includes automated imports within bounded tasks and some automatic lifecycle transitions. Human-in-the-loop does not imply that every technical operation in the original project was manually approved.

## Agent independence

The file contract is usable by an agent with local read/write access; no particular model SDK or agent executable is embedded. The agent environment must provide the required file and command capabilities. Different products have not all been independently tested, so their interchangeability is not claimed.

The original product-specific runner is not included. There is no model API call in the checkout. A deterministic example-results command exists only for the bundled fictional jobs and labels its output `simulated-example/no-model`. This provides a reproducible demonstration without claiming an actual agent ran.

## What validation proves

The retained validator checks expected item identities, input hashes, registered profile version, ready clean versions, structured fields, score bands and existing evidence references. The portfolio wrapper refuses incomplete or partly rejected batches. Mail results are separately bound to their message hash and exact quotations.

Those checks do not establish that a recommendation is substantively correct or fair. The human must interpret uncertain or missing evidence. Vacancy and email content is untrusted input, not a source of new instructions for the agent.

See [the review guide](review-guide.md) for commands and the fictional mail handoff.
