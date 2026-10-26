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

## Manifest for rollback

Before committing, decide a manifest path, e.g. `./testdata-manifests/<table-list>-<timestamp>.json`.
After each successful create/CreateMultiple call, append the returned GUIDs:

```python
import json, datetime, os

manifest = {
    "environment": env_url,
    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "seed_email": seed_email,          # the base seed, not every alias
    "records": {
        # e.g. "account": ["<guid1>", "<guid2>", ...], "contact": [...]
    },
}

os.makedirs("testdata-manifests", exist_ok=True)
manifest_path = f"testdata-manifests/{'-'.join(manifest['records'])}-{manifest['created_at'].replace(':','')}.json"
with open(manifest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)
print(f"Manifest written: {manifest_path}", flush=True)
```

Report the manifest path to the user in the final summary so they know how to clean up later.

## Rollback helper

When the user later asks to remove a batch ("undo the test data from earlier", "delete what we just
created"), locate the relevant manifest and delete in **reverse dependency order** (children before
parents) using bulk delete:

```python
import json
with open(manifest_path, encoding="utf-8") as f:
    manifest = json.load(f)

# Reverse of the creation order used in the run (children first, then parents)
for table in reversed(list(manifest["records"].keys())):
    ids = manifest["records"][table]
    if ids:
        client.records.delete(table, ids)  # BulkDelete action, one async job
        print(f"Deleted {len(ids)} {table} record(s)", flush=True)
```

Confirm with the user before running any rollback delete, exactly like the creation approval gate —
deletes are just as irreversible as creates.
