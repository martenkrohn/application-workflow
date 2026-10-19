# Human, Python and Codex

| Responsibility | Owner |
| --- | --- |
| Source sign-in and any renewed verification | Human, in the private working setup |
| Collection, deduplication, clean versions and storage | Python and SQLite |
| Compact first-pass screening | Codex CLI, GPT-6 Luna, Low reasoning |
| Detailed batch preparation | Python |
| Start the detailed assessment | Human, in Codex with GPT-6.1 Sol |
| Read assigned evidence and return structured assessments | Codex agent |
| Validate identities, hashes, scores and evidence references; persist results | Python, invoked as part of the agent task |
| Review fit, uncertainty and the shortlist | Human, in the dashboard |

Both stages use an agent environment. Their model, context, scope and initiation differ. A more capable model and more complete evidence support deeper review; they do not guarantee correctness.

The prepared handoff fixes the input set and output path. Vacancy text is untrusted data. The task does not authorize browsing, expanding the set or contacting anyone. The detailed task completes assessment, validation and import after its manual start, returning unresolved problems for inspection.

The hosted dashboard uses illustrative assessments and never runs a model. The optional local `run` command invokes the signed-in Codex CLI for real quick screening; its token usage depends on the user's Codex access and plan. No API key or authentication state is stored in this repository.
