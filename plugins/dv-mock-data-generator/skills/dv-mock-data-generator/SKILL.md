---
name: dv-mock-data-generator
description: Generate small/medium/large volumes of realistic mock data for Dataverse/Power Apps tables that respects existing schema, required fields, picklist values, relationships (FK dependency order), and business rules. Uses a reusable seed email with +alias uniqueness for any email column. Always previews a dry run and requires explicit user approval before committing writes. Use when the user asks to generate, seed, populate, or create mock/test/sample/demo data for one or more Dataverse tables.
---

# Skill: Dataverse Mock Data Generator

This is an orchestration skill for the official Microsoft
[`microsoft/Dataverse-skills`](https://github.com/microsoft/Dataverse-skills) package. It must use:

| Microsoft skill | Ownership in this workflow |
|---|---|
| **dv-overview** | Tool routing and cross-cutting safety rules; load first |
| **dv-connect** | Tool setup, delegated user authentication, environment discovery/listing/selection, connection verification |
| **dv-metadata** | Table columns, constraints, choices, alternate keys, and relationships |
| **dv-query** | Existing/reference records and read-only checks needed to model realistic values |
| **dv-data** | Approved creates, dependency-ordered bulk operations, SDK/auth helper, and rollback |

Do not reimplement any of those responsibilities. The local scripts only render/validate the Excel approval
artifact and translate its stable cross-sheet references into the structures consumed by **dv-data**.
See [`references/microsoft-dataverse-skills-integration.md`](references/microsoft-dataverse-skills-integration.md).

If these skills are unavailable, stop and install Microsoft's package rather than using a substitute:

```powershell
npx skills add microsoft/Dataverse-skills -s "*" -g
```

## Minimal-typing trigger convention

Tell the user (once, e.g. in onboarding or the first time this skill fires) they can invoke this with a
one-line shorthand instead of a full sentence:

```
testdata: <size|count> <table1>[,<table2>,...] [seed=<email>] [env=<name>]
testdata: <size|count> solution=<unique-name> [seed=<email>] [env=<name>]
```

Examples:
- `testdata: small account,contact`
- `testdata: medium contact seed=jane.doe@contoso.com`
- `testdata: 40 cr123_project`

Parsing rules:
- First token after `testdata:` is size or an integer count. Size words map to counts (confirm once per
  session, then remember): **small = 25, medium = 250, large = 2500**. A bare integer is used as-is per table.
- Remaining comma-separated tokens (until a `key=value`) are table logical names.
- `seed=` sets/overrides the seed email for this run. `env=` overrides the active Dataverse environment.
- `solution=` expands all table components from the live solution into one combined workbook.
- The shorthand never bypasses startup checks, dependency consent, or final write approval. Missing setup
  values are collected together rather than through a sequence of separate prompts.

If the user writes a full natural-language request instead of the shorthand, extract the same fields from
prose and proceed identically — do not require the shorthand syntax, just prefer suggesting it going
forward.

## Mandatory prerequisites

Do not inspect metadata, generate a workbook, or write records until **all** of these are complete:

1. Required dependencies have passed the read-only dependency check.
2. The current delegated user/environment has been shown and the user has chosen to reuse or change it.
3. The user has interactively authenticated with their own delegated Microsoft account.
4. The user has explicitly selected or confirmed one environment.
5. The selected environment has been verified by both `pac org who --environment <url>` and
   `dataverse org who --json`.
6. The user has supplied a valid seed email address, even if the currently selected tables do not expose an
   email column. It is mandatory input for every generation session.
7. The workbook output path has been confirmed. Default to the current user's Desktop.
8. The user has answered whether a repository containing Dataverse plugin/custom-process code should be used
   as generation context. The repository is optional; asking is mandatory.

Collect prerequisites 2, 6, 7, 8, record counts, existing-record choices, and any already-known lookup
target preferences in **one setup form** whenever the request does not already provide them. Do not ask
separate connection, repository, path, count, and existing-record questions. If the request already
contains every required value and selects the currently verified environment, proceed without a redundant
setup prompt.

Follow [`references/authentication-and-environment-selection.md`](references/authentication-and-environment-selection.md)
and the official **dv-connect** skill. Service principals, client secrets, and certificates are not allowed
for this workflow: all reads and writes must execute as the signed-in user.

Small/medium/large remain fixed at **25/250/2500**; a bare integer overrides the count.

## User-visible progress

Keep the user informed whenever the workflow changes phase, becomes blocked, or waits for a decision. Use
the same eight-step model as the helper scripts:

| Step | Phase | Explain to the user |
|---|---|---|
| 1 | Prerequisites | Which dependencies are being checked and why generation cannot start without them |
| 2 | Connection | Which delegated user/environment is being verified and why the target must be explicit |
| 3 | Run setup | Which tables/counts, seed email, repository context, existing-record choices, and output path are being prepared |
| 4 | Schema discovery | That live writable metadata, constraints, keys, and relationships are being inspected |
| 5 | Record generation | That meaningful values and dependency links are being generated in memory only |
| 6 | Workbook validation | That quality, types, choices, keys, and lookups are being checked offline |
| 7 | Approval | That writes are paused while the exact dry-run workbook is reviewed |
| 8 | Dataverse apply | Which table is currently being probed/created and why dependency order matters |

Each phase update must state **current step**, **status** (`running`, `complete`, `waiting`, or `blocked`),
**what is happening and why**, and **what happens next**. Use the helper-script format:
`[Step N/8 - Phase] STATUS: explanation Next: next action`.

Do not emit repetitive updates for every internal command. Update only on phase changes, meaningful
sub-steps such as beginning a new table during apply, blockers, and user decision points. The agent must
provide steps 2 and 3 because those are conversational; the local scripts provide steps 1 and 4-8.

## Agentic flow

1. **Check dependencies without changing the machine.** Run `scripts/check_dependencies.py --json`.
   - If everything is present, continue silently.
   - If anything is missing, show the missing items and ask one consent question with these choices:
     **Install for me**, **I will install manually**, or **Cancel**.
   - Install only after explicit **Install for me** consent. For manual installation, print the exact
     commands and stop until the user says it is complete. Never treat the original test-data request as
     installation consent.
2. **Check the current connection before changing it.** Load dv-overview, then use dv-connect to run
   `dataverse auth who` and `pac org who`. If both resolve to the same user/environment, show the account,
   environment name, and URL and ask: **Use this connection** or **Change user/environment**.
   - Announce step 2 as running before verification, waiting when user selection is required, blocked when
     authentication fails, and complete after the exact delegated account/environment is confirmed.
   - Reuse only after explicit confirmation.
   - If change is requested (or no valid current connection exists), follow dv-connect's delegated
     authentication and environment discovery. Return the environment list in the consolidated setup form.
     Never auto-select the active/default/first environment.
3. **Capture mandatory selection and email.** After the user chooses/confirms an environment and supplies the seed
   email, complete **dv-connect** for that exact environment. Its `dataverse auth who`,
   `dataverse org who`, `pac org who`, and `python scripts/auth.py --check` verification must all pass.
4. **Capture repository process context in the consolidated setup form.** Ask whether there is a repository containing Dataverse plugin,
   workflow, custom API, web-resource, or process code that should guide the generated values and
   calculations?"* Offer **Use current repository**, **Provide another repository/path**, or **No repository
   context**.
   - If provided, inspect it read-only. Do not build or execute repository code.
   - Search plugin handlers, workflow activities, custom APIs, client scripts, flow definitions, constants,
     tests, and registration metadata for defaults, allowed values, conditional requirements, calculations,
     status transitions, and lookup constraints.
   - Correlate discovered logical names with live dv-metadata; do not assume repository code is deployed.
   - Convert confirmed findings to the safe declarative format in
     [`references/repository-process-context.md`](references/repository-process-context.md), show a short
     extracted-rule summary, and ask the user to confirm those rules before generation.
5. **Confirm the workbook path in the same setup form.** Resolve the Windows known-folder Desktop and
   propose a filename such as `testdata-contact-100-20261016-1225.xlsx`. Do not generate the workbook until
   the destination is confirmed. `--output` may be omitted only after the user accepts the Desktop default.
6. **Parse the data request** (shorthand or prose) into table list and per-table count. Environment and seed
   email always come from the verified dv-connect selection, never from defaults or unverified prompt text.
   - Treat repository context, workbook destination, seed email, table counts, and existing-record choices
     as step 3. Keep step 3 waiting while any mandatory answer is outstanding, then mark it complete with a
     concise run configuration summary.
7. **Discover schema & relationships live from the selected Power Apps environment** with
   `scripts/discover_testdata_workbook.py` — see
   [`references/schema-and-dependency-discovery.md`](references/schema-and-dependency-discovery.md):
   table/field descriptions, required columns, attribute types, valid local/global Picklist option values,
   alternate keys, and ManyToOne lookup
   relationships to other tables (both other requested tables and tables outside the request). Never use a
   static/sample plan as the user's approval workbook. The environment is the source of metadata on every
   run, but generation logic must not contain environment-specific table, column, value, or record
   assumptions.
8. **Resolve relationship order.** Topologically sort requested tables by FK dependency (parents before
   children). A single-target required lookup to a table not explicitly requested is added recursively as a
   fully generated implicit dependency. Record the source table, lookup, and dependency path and show them
   in the workbook and final summary. Required polymorphic lookups still require an explicit target. Optional
   lookups are filled when their target table was explicitly selected or a user-confirmed repository rule
   selects a    target present in live relationship metadata. Aggregate every unresolved polymorphic target and external
   dependency into one decision form instead of failing and prompting one lookup at a time. Preserve optional
   cyclic relationships as approved deferred updates; report required cycles together when they cannot be
   satisfied safely.
9. **Generate records in memory only** (no writes yet) using the schema-driven `fake()` dispatch pattern
   from dv-data's sample-data-generation reference, extended with:
   - Picklist columns use the live default when valid, otherwise choose from the **actual** local/global
     OptionSet values and all available localized labels (never a placeholder integer).
   - Metadata-declared email columns get a **seed-based alias** — see
     [`references/email-aliasing-and-safety.md`](references/email-aliasing-and-safety.md).
   - Contact/person tables get deterministic human first and last names. Product tables get coherent
     product names, variants, product numbers, descriptions, prices, and URLs. Other name/title fields use
     readable organization, project, or role names; never emit generic `Test First Name 1`-style labels.
     Name values are loaded from `references/seed-names/*.txt` by `scripts/seed_name_loader.py`, including
     middle names when the table exposes a middle-name column.
   - Lookup columns for parent tables already generated in this run are bound via `@odata.bind` to the
     newly-generated parent GUIDs once parents are committed (see step 6) — never fabricated GUIDs.
   - Respect uniqueness: keep alternate-key attribute groups intact. Enforce single-column keys directly and
     composite keys by tuple, adjusting a writable string component when needed.
   - Apply user-confirmed repository defaults, allowed values, templates, and safe calculations before the
     workbook is rendered.
   - Use live descriptions, labels, types, formats, relationships, choice defaults, and localized choice
     labels to infer semantics.
     Generate coherent row-level values rather than independently reusing one organization name across
     unrelated fields.
   - Build and satisfy a metadata-driven quality profile for each table: all writable required fields and
     the primary name, plus representative semantic groups such as contactability, address, descriptive,
     product, transaction, organization, or relationship data. Do not select every unknown custom column by
     default; choose a small representative fallback unless `--all-writable-columns` was explicitly used.
10. **Create the Excel preview** as part of `scripts/discover_testdata_workbook.py`. The workbook is the canonical
   suggested-data plan and must be returned to the user before approval. See
   [`references/excel-preview-format.md`](references/excel-preview-format.md):
   - One worksheet per Dataverse table.
   - A stable `__row_key` on every suggested record.
   - Lookup cells contain the related record's `__row_key` and are clickable hyperlinks to the exact row
     on the related worksheet. This makes parent/child relationships understandable without GUIDs.
   - Required columns are highlighted; lookup columns use a distinct color; choice fields use Excel data
     validation where the valid option list fits Excel's inline-list limit.
   - A hidden `_Metadata` sheet carries table/entity-set/navigation-property metadata used by the validator
     and approved apply script.
   - The metadata also carries column-selection reasons, table inclusion provenance, warnings, and the
     meaningful-data quality profile.
11. **Dry-run preview — always shown, never skipped.** Run
    `scripts/summarize_testdata_workbook.py` and place its concise output immediately before the approval
    question:
   - Per table: table name, record count, dependency order position.
   - 3-5 sample generated records per table (full field values, not truncated), including the exact
     email-alias values that will be used.
   - Return/attach the generated `.xlsx` file to the user in the conversation as the confirmation artifact;
     do not merely retain it locally or present it as an example for the operator.
   - The user may edit it before approval. If edited,
     run `scripts/validate_testdata_workbook.py` and show any errors before asking again.
   - Total record count across all tables and an explicit list of any lookups that will be bound to
     parent records created in the same run.
   - Requested versus implicitly added tables, the reason for every implicit dependency, per-table
     completeness statistics, warnings, and whether relationships use generated or existing records.
   - End with a direct approval question, e.g.: *"Create these 275 records (25 accounts, 250 contacts)
     in `contoso-dev`? (yes / no / adjust)"*
12. **One hard stop for approval.** Do **not** call any create/upsert operation until the user replies with an
   explicit affirmative (`yes`, `go`, `approve`, etc.). `no`/edits go back to step 4 with adjustments. This
   gate applies once to the combined workbook for every run, including repeat invocations later in the same
   session. Never split a solution or multi-table request into separately approved workbooks merely to work
   around lookup ambiguity; collect the decisions and regenerate one workbook.
13. **Commit in dependency order** with `scripts/apply_testdata_workbook.py --approved YES` — parent tables
   first (parallel across tables at the same level per
   dv-data's multi-table-fk-import pattern), building source-index -> GUID maps, then child tables bound via
   `@odata.bind`. Probe with the single-record SDK path. Use `CreateMultiple` for supported tables and fall
   back only on the explicit unsupported-operation response. Persist every created row for partial resume,
   apply approved lifecycle actions before dependents, then apply deferred cyclic lookups.
14. **Write a manifest** of every created record (table + GUID) for easy rollback — see
   [`references/approval-and-rollback.md`](references/approval-and-rollback.md). Print the manifest path
   and a summary (counts per table + environment URL to view the records).

## Hard rules

- Never create/update/delete a record before the user has explicitly approved the previewed dry run.
- Never request separate write approvals for subsets of one requested solution/run. Produce one combined
  approval artifact unless a Dataverse rule discovered after approval requires values or actions to change.
- Never install a dependency without a separate, explicit installation consent response.
- Never replace or change a valid current user/environment without first asking whether to reuse it.
- Never skip the repository-context question, and never execute untrusted repository code while extracting
  process rules.
- Never proceed without a verified dv-connect user session, an explicitly selected environment, and a valid
  seed email supplied by the user.
- Never use service-principal, client-secret, or certificate authentication for this workflow.
- Never auto-select an environment. Always return the authenticated user's environment list and wait for
  their explicit selection.
- Never invent a parent record's GUID — only bind to GUIDs this run actually created or that the user
  explicitly supplied.
- Never use a real personal domain for generated emails beyond the one seed address the user supplied —
  see the aliasing reference for the exact transform.
- Skip `createdon`, `modifiedon`, `ownerid`, `statecode`, `statuscode` and any Lookup the user hasn't
  resolved, same as dv-data's sample-data-generation safety rules.
- Never tailor generation logic to the currently connected environment. Only live metadata, confirmed
  declarative business rules/repository context, and table relationships may influence profiles and values.
- Never silently omit a required column with an unsupported attribute type. Block on `SystemRequired`;
  surface `ApplicationRequired` as an approval warning that the retained probe must verify.
- Never accept a technically valid but bare-bones workbook row. Revalidate the stored quality profile,
  option values, alternate-key uniqueness, lengths, and numeric bounds before approval and apply.
- If a `describe`/create probe reveals a plugin-enforced business rule (400 error naming a field or
  constraint not visible in metadata), add it to the preview as a discovered rule and regenerate before
  asking for approval again — don't silently retry past business-rule failures.

## Included scripts

Install once with `python -m pip install -r scripts/requirements.txt`.

```powershell
# 1. Read-only dependency audit; ask before installing anything reported here
python scripts\check_dependencies.py --json

# 2-3. Owned by dv-connect:
# show current user/environment and ask reuse/change; if changing, authenticate, list, select, verify.

# 4. Ask whether repository plugin/custom-process context should be used.
# If yes, inspect it read-only and create a confirmed context-rules.json.

# 5. Propose the Desktop path and ask whether to use it or choose another folder.

# 6. After connection, seed email, repository context, and destination are confirmed:
python scripts\discover_testdata_workbook.py `
  --environment-url https://selected.crm.dynamics.com `
  --seed-email user@contoso.com --solution ContosoDemo --size small `
  --lookup-targets lead.customerid=account,opportunity.customerid=account `
  --context-rules context-rules.json `
  --output "$HOME\Desktop\suggested-test-data.xlsx" --plan-output testdata-plan.json

# 6. Validate and print the concise confirmation summary
python scripts\validate_testdata_workbook.py --workbook suggested-test-data.xlsx
python scripts\summarize_testdata_workbook.py --workbook suggested-test-data.xlsx

# 7. Only after explicit user approval; user/environment are rechecked before any write
python scripts\apply_testdata_workbook.py `
  --environment-url https://selected.crm.dynamics.com `
  --seed-email user@contoso.com --workbook suggested-test-data.xlsx --approved YES
```

`build_testdata_workbook.py` remains an internal/offline renderer for the live metadata snapshot produced
by discovery; it is not the normal user entry point.
