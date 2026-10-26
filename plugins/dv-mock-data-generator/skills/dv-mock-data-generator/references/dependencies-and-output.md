# Dependencies and Workbook Destination

## Consent-gated dependencies

Run `scripts/check_dependencies.py --json` at startup. It is read-only and reports missing CLIs/modules.
Its JSON includes the current step, status, explanation, and next action without adding non-JSON output.

If anything is missing, present the list and ask one question with:

- **Install for me** — run the official dv-connect installation steps and `pip install -r
  scripts/requirements.txt`.
- **I will install manually** — print exact commands and stop until the user confirms completion.
- **Cancel** — end without installing or generating data.

Never infer installation consent from a `testdata:` request.

## Workbook destination

Resolve the current user's Windows Desktop known folder and propose a timestamped filename. Ask whether to
use that path or choose another folder before generating the workbook.

`discover_testdata_workbook.py` defaults to Desktop when `--output` is omitted, but the agent must still ask
the user first. The default exists for script safety/convenience, not to bypass confirmation.

## Repository process context

Before confirming the output path, ask whether plugin/custom-process repository context should be used.
See `repository-process-context.md`. This question is mandatory, but providing a repository is optional.

## Approval summary

Run `scripts/summarize_testdata_workbook.py` after validation. Show its short summary immediately before the
approval prompt: target environment, record counts per table, relationships, email-alias behavior, workbook
path, and the explicit statement that no records have been written yet.

The summary must also show:

- Which tables were explicitly requested and which were added automatically.
- The exact source table and lookup that caused each implicit dependency.
- Whether each relationship uses records generated in this run or explicitly selected existing records.
- Per-table meaningful-field completeness statistics.
- Three complete sample rows per table, including lookup keys and email aliases.
- Discovery warnings and skipped optional relationships.

The first summary line must mark step 7 as `WAITING` and explain that no writes can happen until the user
approves this exact validated workbook. If the workbook is edited, return to step 6 and explain why
revalidation is required.
