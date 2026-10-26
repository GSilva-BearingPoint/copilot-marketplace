from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from progress import emit_progress
from testdata_common import INTERNAL_KEY
from validate_testdata_workbook import load_metadata, validate


DELEGATED_AUTH_BLOCKERS = (
    "CLIENT_ID",
    "CLIENT_SECRET",
    "CLIENT_CERTIFICATE_PATH",
    "CLIENT_CERTIFICATE_PASSWORD",
)


def require_delegated_auth() -> None:
    configured = [name for name in DELEGATED_AUTH_BLOCKERS if os.environ.get(name)]
    if configured:
        raise RuntimeError(
            "This workflow only accepts user-delegated authentication configured by "
            f"the Microsoft dv-connect skill. Remove: {', '.join(configured)}."
        )


def import_get_client():
    candidates = [
        Path(os.environ["DV_TESTDATA_AUTH_DIR"]) if os.environ.get("DV_TESTDATA_AUTH_DIR") else None,
        Path.cwd() / "scripts",
        Path.home() / ".copilot" / "installed-plugins" / "awesome-copilot" / "dataverse" / "scripts",
    ]
    for candidate in candidates:
        if candidate and (candidate / "auth.py").exists():
            sys.path.insert(0, str(candidate))
            return importlib.import_module("auth").get_client
    raise RuntimeError(
        "auth.py was not found. Set DV_TESTDATA_AUTH_DIR to the Dataverse plugin scripts folder."
    )


def serializable(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def chunks(items: list[Any], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def write_manifest(manifest: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply an approved Dataverse test-data Excel workbook."
    )
    parser.add_argument("--workbook", required=True)
    parser.add_argument(
        "--environment-url",
        required=True,
        help="Environment explicitly selected through the Microsoft dv-connect skill.",
    )
    parser.add_argument("--seed-email", required=True)
    parser.add_argument(
        "--approved",
        required=True,
        choices=["YES"],
        help="Explicit approval gate. Must be exactly YES.",
    )
    parser.add_argument("--chunk-size", type=int, default=500)
    parser.add_argument("--manifest-dir", default="testdata-manifests")
    parser.add_argument(
        "--resume-manifest",
        help="Manifest of an interrupted run; tables already in it are reused, not recreated.",
    )
    args = parser.parse_args()

    emit_progress(
        8,
        "running",
        "Revalidating the approved workbook and connection before the first Dataverse write.",
        "Create one retained probe per table, then create the remaining rows in dependency order.",
    )
    os.environ["DATAVERSE_URL"] = args.environment_url.rstrip("/")
    workbook_path = Path(args.workbook)
    rows_by_table, errors = validate(workbook_path)
    if errors:
        emit_progress(
            8,
            "blocked",
            f"The approved workbook has {len(errors)} validation error(s); no records were created.",
            "Correct and revalidate the workbook, then request approval again.",
        )
        for error in errors:
            print(f"ERROR: {error}")
        raise SystemExit("Workbook validation failed; no records were created.")

    workbook = load_workbook(workbook_path, data_only=False)
    run, tables = load_metadata(workbook)
    get_client = import_get_client()
    require_delegated_auth()
    client = get_client("dv-data")
    active_environment = (os.environ.get("DATAVERSE_URL") or "").rstrip("/")
    planned_environment = (run.get("environment") or "").rstrip("/")
    if not active_environment or active_environment.casefold() != planned_environment.casefold():
        emit_progress(
            8,
            "blocked",
            "The active Dataverse environment does not match the approved workbook.",
            "Reconnect to the workbook environment or generate a new preview for the active environment.",
        )
        raise RuntimeError(
            "The active Dataverse environment does not match the workbook. "
            f"Workbook={planned_environment!r}, active={active_environment!r}. No records were created."
        )
    if args.environment_url.rstrip("/").casefold() != planned_environment.casefold():
        emit_progress(
            8,
            "blocked",
            "The explicitly selected environment does not match the approved workbook.",
            "Select the workbook environment or generate and approve a new workbook.",
        )
        raise RuntimeError(
            "The selected environment does not match the workbook environment. "
            "No records were created."
        )
    if args.seed_email.strip().casefold() != str(run.get("seed_email") or "").casefold():
        emit_progress(
            8,
            "blocked",
            "The supplied seed email does not match the approved workbook.",
            "Use the workbook seed email or generate and approve a new preview.",
        )
        raise RuntimeError(
            "The supplied seed email does not match the approved workbook. "
            "No records were created."
        )

    id_by_key: dict[str, dict[str, str]] = {}
    manifest = {
        "environment": run.get("environment"),
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "workbook": str(workbook_path.resolve()),
        "records": {},
    }
    manifest_path = Path(args.manifest_dir) / (
        f"{run.get('name', 'testdata')}-{run['order'][0]}-{run.get('run_id', 'run')}.json"
    )

    resumed: dict[str, list[dict[str, str]]] = {}
    if args.resume_manifest:
        with Path(args.resume_manifest).open(encoding="utf-8") as handle:
            previous = json.load(handle)
        if str(previous.get("workbook")) != str(workbook_path.resolve()):
            raise RuntimeError("Resume manifest belongs to a different workbook. No records were created.")
        resumed = {
            name: items
            for name, items in previous["records"].items()
            if len(items) == len(rows_by_table.get(name, []))
        }

    for table_name in run["order"]:
        if table_name in resumed:
            id_by_key[table_name] = {item["row_key"]: item["id"] for item in resumed[table_name]}
            manifest["records"][table_name] = resumed[table_name]
            emit_progress(
                8,
                "running",
                f"{table_name}: reusing {len(resumed[table_name])} previously created record(s) "
                "from the resume manifest.",
                "Continue with the next dependency-ordered table.",
            )
            continue
        table = tables[table_name]
        payloads: list[dict[str, Any]] = []
        keys: list[str] = []
        lookup_columns = {lookup["column"]: lookup for lookup in table.get("lookups", [])}
        allowed_columns = {column["logical_name"] for column in table.get("columns", [])}

        for row in rows_by_table[table_name]:
            payload = {
                key: serializable(value)
                for key, value in row.items()
                if key in allowed_columns and value is not None and value != ""
            }
            for column_name, lookup in lookup_columns.items():
                target_key = row.get(column_name)
                if not target_key:
                    continue
                if str(target_key) in (lookup.get("existing_ids") or []):
                    target_guid = str(target_key)
                else:
                    target_guid = id_by_key[lookup["target_table"]][str(target_key)]
                if lookup["target_table"] in tables:
                    target_entity_set = tables[lookup["target_table"]]["entity_set_name"]
                else:
                    info = client.tables.get(lookup["target_table"])
                    target_entity_set = info.get("EntitySetName") or info.get("entity_set_name")
                payload[f"{lookup['navigation_property']}@odata.bind"] = (
                    f"/{target_entity_set}({target_guid})"
                )
            payloads.append(payload)
            keys.append(str(row[INTERNAL_KEY]))

        created_ids: list[str] = []
        manifest["records"][table_name] = []
        dependencies = [
            lookup["target_table"]
            for lookup in table.get("lookups", [])
            if not lookup.get("existing_ids")
        ]
        emit_progress(
            8,
            "running",
            f"{table_name}: preparing {len(payloads)} record(s). "
            + (
                "Generated parent records are already available from: "
                + ", ".join(sorted(set(dependencies)))
                + "."
                if dependencies
                else "This table has no generated-parent dependency."
            ),
            "Create the first approved row as a retained probe before sending the remaining chunks.",
        )

        # The first approved row is a retained probe. If a plugin-enforced rule rejects it,
        # the remaining rows for this table are not attempted.
        payload_chunks = []
        if payloads:
            payload_chunks.append(payloads[:1])
            payload_chunks.extend(chunks(payloads[1:], args.chunk_size))

        offset = 0
        for payload_chunk in payload_chunks:
            try:
                result = client.records.create(table_name, payload_chunk)
            except Exception as error:
                emit_progress(
                    8,
                    "blocked",
                    f"{table_name}: Dataverse rejected the current probe or chunk: {error}",
                    "Review the business-rule error; regenerate and reapprove if values must change.",
                )
                raise
            if isinstance(result, str):
                result = [result]
            result_ids = [str(value) for value in result]
            chunk_keys = keys[offset:offset + len(payload_chunk)]
            if len(result_ids) != len(chunk_keys):
                raise RuntimeError(
                    f"{table_name}: Dataverse returned {len(result_ids)} IDs for "
                    f"{len(chunk_keys)} rows in a chunk."
                )
            created_ids.extend(result_ids)
            manifest["records"][table_name].extend(
                {"row_key": key, "id": record_id}
                for key, record_id in zip(chunk_keys, result_ids)
            )
            offset += len(payload_chunk)
            write_manifest(manifest, manifest_path)
            emit_progress(
                8,
                "running",
                f"{table_name}: created {len(created_ids)}/{len(payloads)} record(s).",
                "Continue the current table or advance to the next dependent table.",
            )
        if len(created_ids) != len(keys):
            raise RuntimeError(
                f"{table_name}: Dataverse returned {len(created_ids)} IDs for {len(keys)} rows."
            )
        id_by_key[table_name] = dict(zip(keys, created_ids))

    emit_progress(
        8,
        "complete",
        "All approved records were created and recorded in the rollback manifest.",
        f"Use {manifest_path.resolve()} if this run must be reviewed or rolled back.",
    )
    print(f"Manifest written: {manifest_path.resolve()}")


if __name__ == "__main__":
    main()
