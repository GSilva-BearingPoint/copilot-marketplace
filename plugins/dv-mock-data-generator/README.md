# Dataverse Mock Data Generator

An agentic **GitHub Copilot CLI skill** (`SKILL.md`) that generates realistic, schema-aware test/demo data
for Microsoft Dataverse / Power Apps tables, previews it as an editable Excel workbook, and only writes to
the environment after a human explicitly approves the preview.

It is **not** a standalone application you run directly — it is a skill definition plus a small set of
Python helper scripts that an AI agent (GitHub Copilot CLI, Claude Code, etc.) orchestrates on the user's
behalf, together with Microsoft's own official Dataverse skills.

---

## 1. What problem this solves

Populating Dataverse tables with believable sample data is tedious and risky to automate blindly because:

- Tables have **required columns, picklist/option-set values, alternate keys, and FK (lookup)
  relationships** that must be respected or the Dataverse Web API rejects the write.
- Child records need their parent's **GUID**, which doesn't exist until the parent is actually created.
- Email columns need values that are safe to use without spamming real third parties.
- Writes to a live business environment are **irreversible-feeling** and must never happen silently.

This tool solves all of that by: discovering the *live* schema from the target environment, generating
records in dependency order in memory, rendering them into a reviewable/edit-able Excel workbook, validating
any user edits, and only then committing — in the correct parent→child order — via the official Dataverse
SDK, with a rollback manifest written afterward.

---

## 2. High-level architecture

```
SKILL.md                     <- agent-facing instructions (the actual "skill")
scripts/                     <- Python helpers invoked by the agent
references/                  <- detailed behavior specs the agent must follow
examples/                    <- sample plan/rules JSON for illustration only
```

This repository is an **orchestration layer** on top of Microsoft's official
[`microsoft/Dataverse-skills`](https://github.com/microsoft/Dataverse-skills) package. It intentionally does
**not** reimplement authentication, metadata retrieval, querying, or record creation — those
responsibilities belong to Microsoft's skills:

| Microsoft skill | Responsibility in this workflow |
|---|---|
| **dv-overview** | Tool routing and cross-cutting safety rules (loaded first) |
| **dv-connect** | Dependency setup, delegated user sign-in, environment discovery/selection, connection verification |
| **dv-metadata** | Table columns, constraints, choice/option-set values, alternate keys, relationships |
| **dv-query** | Read-only lookups of existing/reference records to model realistic values |
| **dv-data** | Approved creates, dependency-ordered bulk operations, the SDK/auth helper (`auth.py`), and rollback primitives |

If those skills aren't installed, the agent is instructed to stop and install them rather than
substitute its own logic:

```powershell
npx skills add microsoft/Dataverse-skills -s "*" -g
```

(Marketplace equivalents: `/plugin install dataverse@awesome-copilot` for GitHub Copilot CLI, or
`/plugin install dataverse@claude-plugins-official` for Claude Code.)

The local Python scripts in this repo only do what the Microsoft skills don't:
- Render the discovered live schema + generated values into an editable `.xlsx` approval artifact.
- Give every proposed record a stable, human-readable pre-create key (`__row_key`) so lookups can be
  expressed before any GUID exists.
- Hyperlink lookup cells to the exact row on the related worksheet.
- Validate a user-edited workbook offline before re-confirming.
- Translate approved stable keys into real GUIDs as parents are actually created, then bind children via
  `@odata.bind`.

---

## 3. Why the design is the way it is (key reasoning)

- **Dry-run before write, always.** Live business data systems punish silent mistakes. The workbook preview
  + explicit approval gate (step in the agentic flow) is non-negotiable and re-asked on every run, even
  repeat invocations in the same session.
- **Excel, not just chat, as the approval artifact.** Spreadsheets let the user actually see/edit tens or
  thousands of rows, with highlighted required/lookup columns and real data-validation dropdowns for
  choice fields — far more reviewable than a chat transcript.
- **Stable row keys instead of fabricated GUIDs.** Dataverse GUIDs don't exist pre-creation. Using a stable
  `__row_key` (e.g. `TD-account-0001`) lets child-record lookups reference a specific not-yet-created parent
  row, with the real GUID substituted in at apply time.
- **Delegated (interactive user) authentication only.** Service principals/client secrets/certificates are
  explicitly rejected (`DELEGATED_AUTH_BLOCKERS` enforced in code) so that every created record is
  attributable to the real signed-in user, not an unattended identity — appropriate for a tool that writes
  to shared/shared-dev environments.
- **One real seed email, `+alias`'d per record.** Rather than generating fake/unreachable addresses, every
  generated email reuses one address the user actually controls with a unique `+id` suffix, so Outlook/Gmail
  deliver all test mail to the user's own inbox — never to a stranger's real address.
- **Schema discovered live, every run.** Static/sample plans are never used for a real request; the target
  environment is treated as the source of metadata since schemas evolve. The generator itself remains
  environment-independent: it contains no assumptions about the current organization's tables or records.
- **Dependency-ordered, FK-aware generation.** Tables are topologically sorted on ManyToOne relationships so
  parents are always generated/committed before children; single-target required lookups automatically add
  fully filled parent tables, and the exact reason is shown to the user.
- **Meaningful-data contract per table.** Writable required fields and primary names are combined with a
  representative metadata-derived profile. The same contract is stored in the workbook and rejects edited
  rows that become technically valid but too sparse to be useful.
- **Defensive discovery of undocumented business rules.** plugins/business rules can reject writes for
  reasons invisible in metadata. After approval, a single probe record is created per table first; a 400/409
  error is parsed to patch the generator and the full batch is created only after that succeeds.
- **Manifest-based rollback.** Every successful create is appended to a JSON manifest (table → GUIDs) so the
  user can cleanly delete everything from a run later, in reverse dependency order.

---

## 4. Requirements

### Software / accounts
- Python 3.10+ (scripts use modern typing syntax such as `str | Path`).
- An agent runtime capable of executing this skill (GitHub Copilot CLI, Claude Code, or similar) with the
  official `microsoft/Dataverse-skills` package installed.
- A Microsoft Entra (Azure AD) account with **delegated, interactive** access to at least one Dataverse /
  Power Apps environment (no service principal / client secret / certificate auth is accepted).
- `pac` CLI (Power Platform CLI) and the `dataverse` CLI (Node/npm-based), used by the Microsoft `dv-connect`
  skill for authentication and environment discovery.
- One real email address the user controls, used as the "seed" for all generated email values.

### Python package dependencies (`scripts/requirements.txt`)
```
openpyxl>=3.1,<4                       # build/read/validate the .xlsx approval workbook
PowerPlatform-Dataverse-Client>=1.1,<2 # Dataverse Web API SDK used for writes
azure-identity                         # Entra ID token acquisition
msal                                   # Microsoft Authentication Library
msal-extensions                        # secure token cache persistence for msal
```
Install with:
```powershell
python -m pip install -r skills/dv-mock-data-generator/scripts/requirements.txt
```
`scripts/check_dependencies.py --json` performs a **read-only** audit of these Python modules plus the
`pac`, `dataverse`, and `npm` CLIs, and reports what's missing without installing anything — the agent must
get explicit user consent before installing.

---

## 4a. Installation (for team members)

The skill calls its helper scripts by relative path, so install the **whole repository**, not just `SKILL.md`.

The skill lives in `skills/dv-mock-data-generator/` (with `SKILL.md`, `scripts/`, `references/`), and the repo
root has a plugin manifest, so it can be installed as a plugin:

**Plugin (recommended)**
```
/plugin install GSilva-BearingPoint/dataverse-mock-data-generator
```

**Personal skill (available in all projects)**
```powershell
git clone https://github.com/GSilva-BearingPoint/dataverse-mock-data-generator "$HOME\.copilot\dv-mock-data-generator-repo"
Copy-Item "$HOME\.copilot\dv-mock-data-generator-repo\skills\dv-mock-data-generator" "$HOME\.copilot\skills\" -Recurse
```

**Project skill (shared with everyone using one repo)**
Copy `skills\dv-mock-data-generator` into `<project>\.github\skills\`.

Then restart Copilot CLI (or run `/skills reload`) and confirm it appears in `/skills list`.

**Prerequisites**
1. Access to this GitHub repository.
2. Microsoft's Dataverse skills: `npx skills add microsoft/Dataverse-skills -s "*" -g`
   (or `/plugin install dataverse@awesome-copilot`).
3. Python dependencies: `python -m pip install -r skills\dv-mock-data-generator\scripts\requirements.txt`
4. `pac` and `dataverse` CLIs, plus a Microsoft Entra account with delegated access to a Dataverse environment.

**Updating:** run `git pull` inside the skill folder.

**Using it:** ask the agent in plain language, or use the shorthand
`testdata: small account,contact seed=you@company.com`.

---

## 5. Repository layout

```
SKILL.md                                        Agent-facing skill definition (routing, flow, hard rules)
scripts/
  check_dependencies.py                         Read-only audit of required CLIs/Python modules
  discover_testdata_workbook.py                 Entry point: live metadata discovery -> in-memory records -> workbook
  build_testdata_workbook.py                    Internal renderer: metadata+records -> .xlsx (not a direct entry point)
  validate_testdata_workbook.py                 Validates an edited workbook against its hidden metadata contract
  summarize_testdata_workbook.py                Prints the concise pre-approval summary (counts, relationships, env)
  apply_testdata_workbook.py                    Commits approved rows to Dataverse in dependency order; writes manifest
  quality_profile.py                            Shared per-table meaningful-data contract and completeness evaluation
  seed_name_loader.py                           Loads realistic first/middle/last name pools from references/seed-names
  testdata_common.py                            Shared helpers: email aliasing, dependency ordering, size->count map
  requirements.txt                              Python dependencies
references/
  microsoft-dataverse-skills-integration.md     How this tool composes with dv-overview/connect/metadata/query/data
  authentication-and-environment-selection.md   Required delegated-auth + environment-selection conversation sequence
  dependencies-and-output.md                    Dependency consent flow + workbook destination/repo-context questions
  schema-and-dependency-discovery.md             How required columns, option sets, keys, and FK order are discovered
  excel-preview-format.md                       Worksheet layout, highlighting, hidden _Metadata contract
  email-aliasing-and-safety.md                  The seed-email +alias transform and its safety rules
  repository-process-context.md                 How optional plugin/process repo context becomes context-rules.json
  approval-and-rollback.md                       The hard approval gate + rollback manifest format/usage
  seed-names/                                    Plain-text pools of first/middle/last names for realistic records
examples/
  account-contact-plan.json                     Illustrative dependency-ordered generation plan (not used at runtime)
  repository-context-rules.json                 Illustrative context-rules.json shape
```

---

## 6. End-to-end flow

```
 0  check_dependencies.py --json        (read-only; ask before installing anything missing)
     |
 1  dv-overview -> dv-connect            (show current user/env; reuse or re-authenticate+select)
     |
 2  verify: dataverse auth who / dataverse org who / pac org who / auth.py --check
     |
 3  ask: repository process context? (optional)  -> context-rules.json (if confirmed)
     |
 4  ask: workbook destination (default: Desktop)
     |
 5  parse request: table list + per-table count (small=25 / medium=250 / large=2500, or explicit N)
     |
 6  discover_testdata_workbook.py          (live dv-metadata: required cols, option sets, alt keys, FK graph)
     |-- dependency-sorts tables (parents first)
     |-- recursively adds required related tables and records why
     |-- derives a meaningful-data quality profile independently for every table
     |-- generates ALL records in memory (no writes), applying:
     |     - real option-set values (never placeholder ints)
     |     - seed-email +alias for every email column
     |     - realistic human/org names from references/seed-names
     |     - confirmed repository-derived defaults/rules
     |     - uniqueness handling for alternate keys
     |-- renders the Excel workbook (one sheet per table + hidden _Metadata)
     |
 7  validate_testdata_workbook.py          (re-run after any user edit to the workbook)
 8  summarize_testdata_workbook.py         (counts, dependency reasons, completeness, relationships, samples)
     |
 9  ---- HARD APPROVAL GATE: agent must stop and wait for explicit "yes" ----
     |
10  apply_testdata_workbook.py --approved YES
     |-- re-verifies connection
     |-- creates one probe record per table to catch undocumented business rules
     |-- commits parent tables first (CreateMultiple/chunked for >=10 rows)
     |-- binds children via @odata.bind using real parent GUIDs
     |-- writes a rollback manifest (table -> created GUIDs)
     |
11  prints manifest path + per-table counts + environment URL
```

Every phase is user-visible with a stable message such as
`[Step 5/8 - Record generation] RUNNING: ... Next: ...`. Updates explain what is happening, why the phase is
needed, and what comes next. The agent reports conversational connection/setup phases; helper scripts report
prerequisite, discovery, generation, validation, approval, and apply phases. Progress is updated on phase
changes, blockers, approval waits, and each table during writes rather than for every internal command.

### Shorthand trigger
Instead of full prose, a user can type:
```
testdata: <size|count> <table1>[,<table2>,...] [seed=<email>] [env=<name>]
```
Examples: `testdata: small account,contact`, `testdata: medium contact seed=jane.doe@contoso.com`,
`testdata: 40 cr123_project`. The shorthand never skips any mandatory check (dependencies, environment
confirmation, repository-context question, workbook path, or final approval).

---

## 7. The Excel workbook (approval artifact)

- **One worksheet per table**, plus a `README` sheet (environment, run ID, counts, dependency order) and a
  very-hidden `_Metadata` sheet (JSON contract: environment/run id, dependency order, logical/entity-set
  names, structural columns, required flags, lookup→navigation-property→target-table mapping).
- Every row carries a stable **`__row_key`** (e.g. `TD-account-0001`) that is unique within its sheet.
- Lookup cells show the parent's `__row_key` as a clickable hyperlink to the exact row on the related sheet
  — so relationships are understandable without GUIDs, which don't exist yet.
- Required columns and lookup columns are each given a distinct highlight color; option-set columns get
  native Excel data-validation dropdowns where the list fits Excel's inline-list limit.
- The hidden contract records why every table/column was included and the minimum meaningful profile each
  row must continue to satisfy.
- Users may edit values, add rows, or delete rows before approving. Any edit must be re-validated with
  `validate_testdata_workbook.py` before the approval question is asked again.

---

## 8. Seed-email aliasing

Given one real seed address (e.g. `jane.doe@contoso.com`), every generated email becomes
`jane.doe+{tt}-{run}-{row}-{c}@contoso.com`, where `tt` is a two-character table code, `run` a
three-character run hash, `row` a base-36 row number, and `c` a one-character column disambiguator (for
tables with multiple email columns). Because most providers (Outlook/Exchange, Gmail, …) deliver
`local+anything@domain` to `local@domain`, all test mail still lands in the user's own inbox — no
unreachable or third-party addresses are ever fabricated. The seed is validated as ASCII, stripped of any
pre-existing `+tag`, and length-limited (64-char local part / 254-char full address). It is asked for once
per session and not persisted across sessions unless the user explicitly asks it to be.

---

## 9. Safety / hard rules enforced throughout

- No create/update/delete happens before an explicit user approval of the previewed dry run — every run,
  including repeats in the same session.
- Dependencies are only installed after a separate, explicit consent response — never inferred from the
  original data request.
- An existing valid user/environment connection is never replaced without asking first; an environment is
  never auto-selected.
- Service-principal/client-secret/certificate auth is rejected outright (`DELEGATED_AUTH_BLOCKERS` checked
  in `discover_testdata_workbook.py` and `apply_testdata_workbook.py`).
- Repository code, if provided for process context, is only read — never built or executed.
- No parent GUID is ever invented; lookups only bind to GUIDs actually created this run (or explicitly
  supplied by the user).
- System/audit columns (`createdon`, `modifiedon`, `ownerid`, `statecode`, `statuscode`) and any unresolved
  lookup are skipped, matching dv-data's sample-data-generation safety rules.
- A plugin-enforced business rule discovered via a create-probe error stops the batch, patches the
  generator, and re-confirms with the user rather than silently retrying past failures.

---

## 10. Usage (illustrative command sequence)

```powershell
# 1. Read-only dependency audit
python scripts\check_dependencies.py --json

# 2-3. Owned by dv-connect: authenticate, list, select, and verify environment (interactive, delegated)

# 4-5. Agent asks about optional repository context and the workbook destination

# 6. Discover live schema + generate the approval workbook
python scripts\discover_testdata_workbook.py `
  --environment-url https://selected.crm.dynamics.com `
  --seed-email user@contoso.com --tables account,contact --size small `
  --context-rules context-rules.json `
  --output "$HOME\Desktop\suggested-test-data.xlsx" --plan-output testdata-plan.json

# 7. Validate (re-run after any manual edits) and print the pre-approval summary
python scripts\validate_testdata_workbook.py --workbook suggested-test-data.xlsx
python scripts\summarize_testdata_workbook.py --workbook suggested-test-data.xlsx

# 8. Only after explicit user approval
python scripts\apply_testdata_workbook.py `
  --environment-url https://selected.crm.dynamics.com `
  --seed-email user@contoso.com --workbook suggested-test-data.xlsx --approved YES
```

`build_testdata_workbook.py` is an internal renderer invoked by `discover_testdata_workbook.py`; it is not a
normal user/agent entry point on its own.

---

## 11. Further reading

Detailed, authoritative behavior specs live in `references/`:
- `microsoft-dataverse-skills-integration.md` — composition with Microsoft's official skills
- `authentication-and-environment-selection.md` — delegated auth + environment selection sequence
- `dependencies-and-output.md` — dependency consent flow and workbook destination
- `schema-and-dependency-discovery.md` — metadata discovery and FK dependency ordering
- `excel-preview-format.md` — workbook structure and editing rules
- `email-aliasing-and-safety.md` — the seed-email transform in full
- `repository-process-context.md` — optional plugin/process repository context extraction
- `approval-and-rollback.md` — the approval gate and the rollback manifest/workflow

`examples/` contains illustrative (non-executable) JSON shapes for the generation plan and repository
context-rules file, for reference only — they are never used as a substitute for live discovery.
