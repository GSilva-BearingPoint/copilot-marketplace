# Excel Preview Format

The generated workbook is both the human-readable approval artifact and the apply input. It must be built
from a fresh metadata inspection of the selected Power Apps environment and returned/attached to the user
for confirmation. Static example workbooks are never used for a real request.

## Visible worksheets

- `README` summarizes environment, run ID, approval status, counts, and dependency/creation order.
- The creation-order table marks each table as requested or implicit and explains the lookup that caused
  every implicit table to be generated.
- One worksheet per table contains a stable `__row_key`, generated structural columns, and lookup columns.
- Required columns are highlighted. Lookup columns use a separate highlight.
- Lookup cells contain the parent's `__row_key` and hyperlink directly to that record's row on the related
  worksheet. For example, a contact's `parentcustomerid` cell can display `TD-account-0001` and open the
  exact account row when clicked.

The workbook intentionally uses stable plan keys instead of Dataverse GUIDs because GUIDs do not exist
until the approved apply step. The apply script replaces each lookup plan key with the actual GUID returned
when its parent record is created.

## Hidden metadata

The very-hidden `_Metadata` worksheet stores JSON describing:

- Environment and run ID.
- Dependency order.
- Logical table and entity-set names.
- Structural columns and required flags.
- Lookup column, navigation property, and target table.
- Column-selection reasons, the per-table meaningful-data quality profile, and explicit/implicit table
  provenance.
- Discovery warnings, including optional relationships skipped to break dependency cycles.

Do not expose metadata as editable business data. The validator treats it as the contract for the workbook.

## Editing rules

- Users may edit suggested values, add rows, or remove rows before approval.
- Person/contact sheets use human first and last names; other primary/name columns use readable
  domain-oriented names rather than generic placeholder labels.
- `__row_key` must remain non-empty and unique within its worksheet.
- A lookup value must equal an existing `__row_key` on its target worksheet.
- Required cells cannot be blank.
- Edited rows must continue to satisfy the table's metadata-derived quality profile; a technically valid but
  bare-bones row is rejected with the missing core fields or semantic groups named.
- Choice values, alternate-key uniqueness, string lengths, and numeric bounds are revalidated from workbook
  metadata.
- Always run `validate_testdata_workbook.py` after edits and before asking for approval.
