# Approval Gate and Rollback Manifest

## Approval gate (non-negotiable)

Step 6 of the main flow is a hard stop. Concretely:

1. Render the full dry-run preview (counts, sample records, dependency order, flagged business rules) as
   the final message of a turn.
2. End that turn **without calling any write tool/SDK method.**
3. Only after the user's next message contains an explicit approval (`yes`, `approve`, `go ahead`,
   `confirmed`, etc. — a clear affirmative, not just silence or an unrelated follow-up) do you proceed to
   commit.
4. If the user asks to change counts, tables, or the seed email instead of approving, regenerate the
   preview and ask again. Do not partially commit "the parts that were fine."
5. Treat every run as independent — a prior approval never carries over to a new `testdata:` request later
   in the same session, even for the same table.
6. One requested solution/run has one combined workbook and one approval. Aggregate lookup decisions and
   regenerate the combined artifact instead of requesting approval for table subsets.

## Manifest for rollback

Before committing, decide a manifest path, e.g. `./testdata-manifests/<table-list>-<timestamp>.json`.
After each successful record, create chunk, lifecycle action, or deferred lookup update, persist the
manifest. Include the workbook SHA-256 so resume cannot continue from an edited approval artifact:

```python
import json, datetime, os

manifest = {
    "environment": env_url,
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "workbook": "<absolute-path>",
    "workbook_sha256": "<sha256>",
    "seed_email": seed_email,          # the base seed, not every alias
    "order": ["account", "contact"],
    "records": {
        "account": [{"row_key": "TD-account-0001", "id": "<guid1>"}],
    },
    "actions": {},
    "deferred_updates": {},
}

os.makedirs("testdata-manifests", exist_ok=True)
manifest_path = f"testdata-manifests/{'-'.join(manifest['records'])}-{manifest['created_at'].replace(':','')}.json"
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
print(f"Manifest written: {manifest_path}", flush=True)
```

Report the manifest path to the user in the final summary so they know how to clean up later.

`--resume-manifest` reuses every completed row, not only completed tables. It must reject a different
workbook path or hash. This prevents duplicate probes and duplicate rows after an interrupted bulk or
sequential fallback.

## Rollback helper

When the user later asks to remove a batch ("undo the test data from earlier", "delete what we just
created"), locate the relevant manifest and delete in **reverse dependency order** (children before
parents) using bulk delete:

```python
import json
with open(manifest_path, encoding="utf-8") as f:
    manifest = json.load(f)

# Reverse of the creation order used in the run (children first, then parents)
for table in reversed(manifest["order"]):
    ids = [item["id"] for item in manifest["records"].get(table, [])]
    if ids:
        client.records.delete(table, ids)  # BulkDelete action, one async job
        print(f"Deleted {len(ids)} {table} record(s)", flush=True)
```

Confirm with the user before running any rollback delete, exactly like the creation approval gate —
deletes are just as irreversible as creates.
