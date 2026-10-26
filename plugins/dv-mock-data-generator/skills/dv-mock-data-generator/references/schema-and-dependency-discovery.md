# Schema and Dependency Discovery

Run once per requested table before generating any data.

## 1. Required columns and attribute types

Reuse dv-data's sample-data-generation query (filtered to avoid duplicated lookup sub-attributes):

```python
attrs = client.tables.list_columns(
    TABLE,
    select=["LogicalName", "AttributeType", "RequiredLevel", "DisplayName"],
    filter="AttributeOf eq null",
)
required = [
    a for a in attrs
    if a["RequiredLevel"]["Value"] in {"ApplicationRequired", "SystemRequired"}
]
```

Also capture **recommended-but-not-required** columns (`RequiredLevel.Value == "Recommended"`) — include
them in generation too, since business rules often treat them as effectively required even when metadata
doesn't mark them required. `RequiredLevel.CanBeChanged` only says whether the requirement setting can be
customized; it must never be used to discard an otherwise writable column.

Also add a light "organic profile" **derived from the live metadata, not a fixed column list**. Each writable
column is classified (`classify()` in `scripts/realistic_data.py`) from its `AttributeType`, `Format` and
logical name into a role: address parts, phone (landline/mobile), URL, job title, department, birthdate,
employee count, revenue. Custom columns are picked up the same way; columns absent from the environment are
simply not generated; numeric values are clamped to the column's `MinValue`/`MaxValue`; strings to
`MaxLength`. To stay light, only one address group, one landline, one mobile and one URL column are kept
(primary columns such as `telephone1`/`websiteurl` win; fax/pager/FTP/image columns are ignored). The chosen
role is stored on each column in the plan. Optional choice columns without readable options are skipped.
Use `--all-writable-columns` for everything.

Always include the writable primary-name column. For unfamiliar schemas, select at most three otherwise
unclassified custom business columns as a representative fallback rather than filling every custom column.
Each selected column records why it was selected (`required`, `recommended`, `primary_name`, `email`,
`semantic_profile`, `representative_custom`, `repository_rule`, or `all_writable`).

Build a quality profile for every table from the selected columns and lookups:

- Required writable fields, the primary-name field, and required lookups are core fields.
- Optional fields are grouped by metadata-derived purpose, such as address, contactability, person,
  organization, product, transaction, description, generic business details, and relationships.
- Every row must contain all core fields, at least one value from every selected purpose group, and the
  derived minimum number of meaningful values.
- Optional address lines that are intentionally sparse are excluded from the minimum.

This quality contract is stored in workbook metadata and re-evaluated after user edits. It scales to the
available schema: a junction table can be meaningful through its lookups, while a rich business table
cannot pass with only its name.

The selection and quality logic is environment-independent. Environment identity and current records must
not affect profile decisions; only metadata, confirmed declarative business rules, and relationships may do
so.

Vocabulary lives in `references/seed-data/` (`company-names.txt` ~6000 generated names, `company-sectors.txt`,
`street-names.txt`, `job-titles.txt`, `departments.txt`, `locations.psv` with city/state/postal/phone masks
and legal suffixes per country). Regenerate company names with `python scripts\generate_seed_data.py`.
Selection is offset by the run id, so every run differs while values stay unique within a run: company names
combine a base name + sector + country legal suffix (e.g. `Nacou Capital LLC`). Address, postal code,
country, phone and legal suffix for a row share one location, and a contact shares it with the account of
the same index.

## 2. Valid Picklist / Status option values

Never use a placeholder integer. For each Picklist/Status column, look up the real options:

```python
options = client.tables.describe(TABLE, include="all")  # or targeted OptionSet query
# Resolve the specific attribute's OptionSet values; pick randomly from valid Value entries only.
```

If an option set has a "Default/Inactive" value that shouldn't be used for new test records (e.g., a
"Closed" status), prefer values flagged as open/active where that distinction exists in the metadata
(`State` transitions). When ambiguous, default to the first non-deprecated option and note the choice in
the preview.

## 3. Alternate keys and natural uniqueness

```python
dataverse data describe --table <table> --include all --json
```

Preserve alternate keys as grouped attribute lists. A single-column key must be unique by itself; a
composite key only requires each complete tuple to be unique. When a generated duplicate tuple contains a
writable string component, append the record index to that component within its maximum length. If no
component can be adjusted safely, stop with an explicit error instead of producing an invalid workbook.

## 4. ManyToOne (lookup) relationships -> dependency graph

```
GET /api/data/v9.2/EntityDefinitions(LogicalName='<table>')/ManyToOneRelationships
    ?$select=ReferencingAttribute,ReferencingEntityNavigationPropertyName,ReferencedEntity
```

For every lookup attribute found in step 1 that is `ApplicationRequired`, `SystemRequired`, or selected by a
confirmed relationship rule:
- If `ReferencedEntity` is one of the other requested tables -> add a dependency edge (child depends on
  parent).
- If a required lookup has exactly one external target, dynamically add that related table to the plan.
- If a required lookup is polymorphic or cannot be resolved, stop workbook generation and ask the user to
  select the intended target — never silently skip it or fabricate a GUID.
- Optional lookups are included when their target table is part of the requested/generated table set.
- A confirmed repository rule may select one valid target for an optional or polymorphic lookup. The target
  must still exist in live relationship metadata.

Every generated table records whether it was explicitly requested or implicitly added. Implicit tables also
record the source table, lookup column, requirement type, and dependency path. This provenance is shown in
the workbook and approval summary so automatic creation is never silent.

Topologically sort the requested tables by these edges. Tables with no incoming edges are Level 0 and can
be generated/committed in parallel; each subsequent level depends on the previous (same leveling pattern as
dv-data's `references/multi-table-fk-import.md`).

## 5. Business rules not visible in metadata

plugins and classic business rules can enforce constraints metadata won't show (e.g., "email domain must
match account", "end date must be after start date", cross-field conditional requirements). Discover these
defensively, not exhaustively:

1. Before approval, only inspect metadata and existing records — do not create a probe.
2. After the user approves the Excel plan, create **one single approved probe record** per table (not the
   full batch) before committing the rest.
3. If the probe fails with an `HttpError` 400/409, read the message — it usually names the offending field
   or rule. Adjust the generator for that field and re-probe.
4. If satisfying that rule changes any values beyond the already-approved row, stop, regenerate the Excel
   preview, and ask for approval again. Otherwise retain the probe as row 1 of the approved batch and create
   the remaining rows so the final count stays exact.
