# Validation record

Validated on 18 October 2026, using Python 3.13 on Windows.

| Check | Observed result |
| --- | --- |
| Automated tests | 44 passed: transferred collection/storage tests and portfolio boundary tests |
| Default demonstration | Three fictional HTML vacancies collected, normalized, assessed with simulated outputs and imported; two fictional mail proposals stored |
| Repeated collection | Existing records retained; unchanged observations recognised |
| Human selection | Exact selected item count; no agent or output automatically created |
| Assessment validation | Bad identities, duplicate output, invalid citations, inconsistent score bands, modified inputs and profile snapshots rejected |
| Human decisions | Import does not change the human decision; explicit decisions survive refresh |
| Independent workspaces | Separate datasets and handoff commands; unfinished work is not overwritten by the demo |
| Private adapter boundary | An importable test adapter factory successfully collected neutral records |
| Mail proposals | Input hashes, classification, confidence and actual quotations checked; incomplete batches refused |
| Local HTTP API | Same-origin mutations and loopback host enforcement checked |
| Browser, 390px / 1280px | Selection, handoff, validate/import, persisted decision and mail tabs passed; no JS errors or horizontal overflow |
| Packaging | Wheel built without network; installed in a fresh venv; installed CLI demo passed |
| Fresh checkout export | The exact 85 selected repository files run the demo with isolated Python; no private working files required |
| Repository content | Current files and all preparation commits checked for production portal names, credential patterns and personal addresses/paths; no matches |
| Package resources | Dashboard assets and all migrated schema files available from the installed wheel |

No real website, production mailbox, model account, employer contact or application submission was used. No images were reviewed and no TeX compilation was performed for this task. The browser checks used DOM and behaviour only.

The test environment used pytest from an existing developer environment; runtime installation was separately checked in a fresh venv with no third-party runtime packages. GitHub Actions is configured to run the tests and demo on Ubuntu/Python 3.13 after upload; its returned status is separate from the local checks above.

Transfer hashes, synthetic runtime databases and detailed local validation evidence remain under ignored `local/` and `var/`, outside the repository history.
