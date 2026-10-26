from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from build_testdata_workbook import build_workbook
from progress import emit_progress
from quality_profile import build_quality_profile
from realistic_data import classify, select_organic
from testdata_common import SIZE_COUNTS, default_workbook_path, dependency_order, validate_seed_email


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
            "This workflow only accepts the user-delegated authentication configured by "
            f"the Microsoft dv-connect skill. Remove: {', '.join(configured)}."
        )


SYSTEM_LOOKUPS = {
    "createdby",
    "createdonbehalfby",
    "modifiedby",
    "modifiedonbehalfby",
    "ownerid",
    "owningbusinessunit",
    "owningteam",
    "owninguser",
    "transactioncurrencyid",
}
SKIP_TYPES = {
    "EntityName",
    "ManagedProperty",
    "Owner",
    "State",
    "Status",
    "Uniqueidentifier",
    "Virtual",
}
SUPPORTED_TYPES = {
    "BigInt",
    "Boolean",
    "DateTime",
    "Decimal",
    "Double",
    "Integer",
    "Memo",
    "Money",
    "Picklist",
    "String",
}


def import_get_client():
    candidates = [
        Path(os.environ["DV_TESTDATA_AUTH_DIR"]) if os.environ.get("DV_TESTDATA_AUTH_DIR") else None,
        Path.cwd() / "scripts",
        Path.home()
        / ".copilot"
        / "installed-plugins"
        / "awesome-copilot"
        / "dataverse"
        / "scripts",
    ]
    for candidate in candidates:
        if candidate and (candidate / "auth.py").exists():
            sys.path.insert(0, str(candidate))
            return importlib.import_module("auth").get_client
    raise RuntimeError(
        "auth.py was not found. Set DV_TESTDATA_AUTH_DIR to the Dataverse plugin scripts folder."
    )


def localized_label(value: Any, fallback: str) -> str:
    if not isinstance(value, dict):
        return fallback
    user = value.get("UserLocalizedLabel")
    if isinstance(user, dict) and user.get("Label"):
        return str(user["Label"])
    labels = value.get("LocalizedLabels") or []
    if labels and isinstance(labels[0], dict) and labels[0].get("Label"):
        return str(labels[0]["Label"])
    return fallback


def required_level(column: dict[str, Any]) -> str:
    value = column.get("RequiredLevel")
    if isinstance(value, dict):
        return str(value.get("Value") or "None")
    return str(value or "None")


def option_values(column: dict[str, Any]) -> list[dict[str, Any]]:
    option_set = column.get("OptionSet") or column.get("GlobalOptionSet") or {}
    result = []
    for option in option_set.get("Options") or []:
        if option.get("Value") is None:
            continue
        result.append(
            {
                "value": option["Value"],
                "label": localized_label(option.get("Label"), str(option["Value"])),
            }
        )
    return result


def alternate_keys(client, table_name: str) -> list[list[str]]:
    result: list[list[str]] = []
    try:
        keys = client.tables.get_alternate_keys(table_name)
    except (AttributeError, NotImplementedError):
        return result
    for key in keys or []:
        attributes = key.get("KeyAttributes") or key.get("key_attributes") or []
        if attributes:
            result.append([str(attribute) for attribute in attributes])
    return result


def relationship_candidates(client, table_name: str) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for relationship in client.tables.list_table_relationships(table_name) or []:
        referencing_entity = relationship.get("ReferencingEntity")
        attribute = relationship.get("ReferencingAttribute")
        target = relationship.get("ReferencedEntity")
        navigation = relationship.get("ReferencingEntityNavigationPropertyName")
        if (
            referencing_entity == table_name
            and attribute
            and target
            and navigation
            and attribute not in SYSTEM_LOOKUPS
        ):
            grouped[attribute].append(
                {
                    "column": attribute,
                    "navigation_property": navigation,
                    "target_table": target,
                }
            )
    return grouped


def fetch_attribute_flags(client, table_name: str) -> dict[str, dict[str, Any]]:
    """Writability flags the typed column listing omits (display-name shadow columns, read-only fields)."""
    odata = client._get_odata()
    url = (
        f"{odata.api}/EntityDefinitions(LogicalName='{table_name}')/Attributes"
        "?$select=LogicalName,IsValidForCreate,IsValidODataAttribute,AttributeOf,IsCustomAttribute"
    )
    items = odata._request("get", url).json().get("value", [])
    return {
        item["LogicalName"]: {
            key: item[key]
            for key in ("IsValidForCreate", "IsValidODataAttribute", "AttributeOf", "IsCustomAttribute")
            if key in item
        }
        for item in items
    }


def inspect_table(
    client,
    table_name: str,
    count: int,
    all_writable: bool,
    context_rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context_rules = context_rules or {}
    context_columns = set((context_rules.get("defaults") or {}).keys())
    context_columns.update((context_rules.get("allowed_values") or {}).keys())
    context_columns.update((context_rules.get("templates") or {}).keys())
    for calculation in context_rules.get("calculations") or []:
        context_columns.add(calculation["target"])
        context_columns.update(calculation.get("sources") or [])
    table = client.tables.get(table_name)
    if not table:
        raise ValueError(f"Dataverse table not found: {table_name}")
    primary_name = table.get("PrimaryNameAttribute") or table.get("primary_name_attribute")
    table_alternate_keys = alternate_keys(client, table_name)
    single_key_columns = {
        key[0] for key in table_alternate_keys if len(key) == 1
    }
    relationships = relationship_candidates(client, table_name)
    raw_columns = client.tables.list_columns(
        table_name,
        typed=True,
    )
    attribute_flags = fetch_attribute_flags(client, table_name)
    for raw in raw_columns:
        raw.update(attribute_flags.get(raw.get("LogicalName"), {}))

    columns = []
    organic: list[dict[str, Any]] = []
    representative_custom: list[dict[str, Any]] = []
    lookup_columns: dict[str, dict[str, Any]] = {}
    for raw in raw_columns:
        logical_name = raw.get("LogicalName")
        attr_type = raw.get("AttributeType")
        if not logical_name or raw.get("AttributeOf") is not None:
            continue
        if raw.get("IsPrimaryId") or raw.get("IsValidForCreate") is False:
            continue
        if raw.get("IsValidODataAttribute") is False:
            continue
        level = required_level(raw)
        required = level in {"ApplicationRequired", "SystemRequired"}
        recommended = level == "Recommended"
        if attr_type in {"Lookup", "Customer"}:
            if logical_name not in SYSTEM_LOOKUPS:
                relationship_rule = (context_rules.get("relationships") or {}).get(logical_name)
                selected_target = (
                    relationship_rule.get("target_table")
                    if isinstance(relationship_rule, dict)
                    else relationship_rule
                )
                lookup_columns[logical_name] = {
                    "required": required,
                    "recommended": recommended,
                    "candidates": relationships.get(logical_name, []),
                    "selected_target": selected_target,
                }
            continue
        if attr_type in SKIP_TYPES or attr_type not in SUPPORTED_TYPES:
            continue

        fmt = raw.get("Format")
        is_email = attr_type == "String" and fmt == "Email"
        role = classify(logical_name, attr_type, fmt)
        selection_reasons = []
        if all_writable:
            selection_reasons.append("all_writable")
        if required:
            selection_reasons.append("required")
        if recommended:
            selection_reasons.append("recommended")
        if logical_name == primary_name:
            selection_reasons.append("primary_name")
        if is_email:
            selection_reasons.append("email")
        if logical_name in context_columns:
            selection_reasons.append("repository_rule")
        include = bool(selection_reasons)
        if not include and not role:
            if raw.get("IsCustomAttribute") is not True:
                continue
        column = {
            "logical_name": logical_name,
            "label": localized_label(raw.get("DisplayName"), logical_name),
            "type": attr_type,
            "required": required,
            "recommended": recommended,
            "unique": logical_name in single_key_columns,
            "selection_reasons": selection_reasons,
        }
        if role:
            column["role"] = role
        if fmt:
            column["format"] = fmt
        for source, target in (
            ("MinValue", "min_value"),
            ("MaxValue", "max_value"),
            ("MaxLength", "max_length"),
        ):
            if raw.get(source) is not None:
                column[target] = raw[source]
        if attr_type == "Picklist":
            column["options"] = option_values(raw)
            if not column["options"] and not required:
                continue
        if include:
            columns.append(column)
        elif role:
            organic.append(column)
        else:
            representative_custom.append(column)

    selected_organic = select_organic(organic)
    for column in selected_organic:
        column["selection_reasons"].append("semantic_profile")
    columns.extend(selected_organic)
    for column in sorted(
        representative_custom,
        key=lambda item: (item["type"] == "Memo", item["logical_name"]),
    )[:3]:
        column["selection_reasons"].append("representative_custom")
        columns.append(column)
    columns.sort(key=lambda item: item["logical_name"])
    unsupported_context_columns = context_columns.difference(
        column["logical_name"] for column in columns
    )
    if unsupported_context_columns:
        raise ValueError(
            f"{table_name}: repository context references missing or non-writable columns: "
            + ", ".join(sorted(unsupported_context_columns))
        )

    return {
        "logical_name": (
            table.get("LogicalName")
            or table.get("table_logical_name")
            or table_name
        ),
        "display_name": localized_label(table.get("DisplayName"), table_name),
        "entity_set_name": table.get("EntitySetName") or table.get("entity_set_name"),
        "primary_id_column": (
            table.get("PrimaryIdAttribute")
            or table.get("primary_id_attribute")
        ),
        "primary_name_column": primary_name,
        "alternate_keys": table_alternate_keys,
        "count": count,
        "columns": columns,
        "context_rules": context_rules,
        "_lookup_columns": lookup_columns,
    }


def discover_plan(
    client,
    table_names: list[str],
    count: int,
    seed_email: str | None,
    environment: str,
    all_writable: bool = False,
    include_required_related: bool = True,
    context: dict[str, Any] | None = None,
    table_counts: dict[str, int] | None = None,
    existing_tables: set[str] | None = None,
) -> dict[str, Any]:
    existing_tables = existing_tables or set()
    existing_limit = 500
    existing_ids: dict[str, list[str]] = {}
    context = context or {}
    table_counts = table_counts or {}
    context_tables = context.get("tables") or {}
    requested = list(dict.fromkeys(table_names))
    queue = deque(requested)
    inspected: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    inclusion: dict[str, dict[str, Any]] = {
        name: {
            "kind": "requested",
            "dependency_path": [name],
            "reasons": [],
        }
        for name in requested
    }

    while queue:
        table_name = queue.popleft()
        if table_name in inspected:
            continue
        info = inspect_table(
            client,
            table_name,
            table_counts.get(table_name, count),
            all_writable,
            context_tables.get(table_name),
        )
        info["inclusion"] = inclusion[table_name]
        inspected[table_name] = info
        for column_name, lookup in info["_lookup_columns"].items():
            selected_target = lookup.get("selected_target")
            if lookup["required"] and not include_required_related and not selected_target:
                continue
            if not lookup["required"] and not selected_target:
                continue
            targets = {
                candidate["target_table"]
                for candidate in lookup["candidates"]
                if candidate["target_table"] != table_name
            }
            if selected_target:
                if selected_target not in targets:
                    raise ValueError(
                        f"{table_name}.{column_name} repository rule selected {selected_target}, "
                        "but that target is not present in live relationship metadata."
                    )
                targets = {selected_target}
            if len(targets) == 1:
                target = next(iter(targets))
                reason = {
                    "source_table": table_name,
                    "lookup_column": column_name,
                    "requirement": "required" if lookup["required"] else "selected_rule",
                    "target_table": target,
                }
                if target not in inclusion:
                    inclusion[target] = {
                        "kind": "implicit",
                        "dependency_path": inclusion[table_name]["dependency_path"] + [target],
                        "reasons": [reason],
                    }
                elif inclusion[target]["kind"] == "implicit" and reason not in inclusion[target]["reasons"]:
                    inclusion[target]["reasons"].append(reason)
                if target not in inspected and target not in existing_tables:
                    queue.append(target)
            elif len(targets) > 1:
                warnings.append(
                    f"{table_name}.{column_name} is a required polymorphic lookup; "
                    f"choose one target: {', '.join(sorted(targets))}."
                )
            else:
                warnings.append(
                    f"{table_name}.{column_name} is required but its target relationship "
                    "could not be resolved."
                )

    selected_names = set(inspected)
    tables = []
    for table_name, info in inspected.items():
        lookups = []
        for column_name, lookup in info.pop("_lookup_columns").items():
            selected_target = lookup.get("selected_target")
            candidates = [
                candidate
                for candidate in lookup["candidates"]
                if (
                    candidate["target_table"] in selected_names
                    or candidate["target_table"] in existing_tables
                )
                and candidate["target_table"] != table_name
                and (not selected_target or candidate["target_table"] == selected_target)
            ]
            if not candidates:
                if lookup["required"]:
                    possible = sorted(
                        {
                            candidate["target_table"]
                            for candidate in lookup["candidates"]
                            if candidate["target_table"] != table_name
                        }
                    )
                    raise ValueError(
                        f"{table_name}.{column_name} is a required lookup but no target table "
                        f"is included. Add one of: {', '.join(possible) or 'unknown target'}."
                    )
                continue
            if len(candidates) > 1:
                raise ValueError(
                    f"{table_name}.{column_name} can target multiple included tables: "
                    f"{', '.join(sorted(item['target_table'] for item in candidates))}. "
                    "Generate separate plans or include only the intended target table."
                )
            chosen = sorted(candidates, key=lambda item: item["target_table"])[0]
            entry = {
                **chosen,
                "required": lookup["required"],
                "recommended": lookup["recommended"],
            }
            target = chosen["target_table"]
            if target in existing_tables:
                if target not in existing_ids:
                    id_column = client.tables.get(target).get("PrimaryIdAttribute") or f"{target}id"
                    rows = client.records.list(target, select=[id_column], top=existing_limit)
                    existing_ids[target] = [str(row[id_column]) for row in rows]
                if not existing_ids[target]:
                    raise ValueError(f"No existing {target} records found to link {table_name}.{column_name} to.")
                entry["existing_ids"] = existing_ids[target]
            lookups.append(entry)
        info["lookups"] = lookups
        if not info.get("entity_set_name"):
            raise ValueError(f"{table_name}: EntitySetName was not returned by Dataverse.")
        tables.append(info)

    # Optional lookups can form cycles (e.g. account.primarycontactid <-> contact.parentcustomerid).
    # Drop only optional edges that actually participate in a cycle.
    while True:
        try:
            dependency_order(tables)
            break
        except ValueError:
            names = {table["logical_name"] for table in tables}
            graph = {
                table["logical_name"]: {
                    lookup["target_table"]
                    for lookup in table.get("lookups", [])
                    if lookup["target_table"] in names
                    and lookup["target_table"] != table["logical_name"]
                }
                for table in tables
            }

            def reaches(start: str, goal: str) -> bool:
                pending = [start]
                seen: set[str] = set()
                while pending:
                    current = pending.pop()
                    if current == goal:
                        return True
                    if current in seen:
                        continue
                    seen.add(current)
                    pending.extend(graph.get(current, set()).difference(seen))
                return False

            cyclic_edges = sorted(
                [
                    (table, lookup)
                    for table in tables
                    for lookup in table.get("lookups", [])
                    if not lookup["required"]
                    and lookup["target_table"] in names
                    and reaches(lookup["target_table"], table["logical_name"])
                ],
                key=lambda item: (
                    item[0]["logical_name"],
                    str(item[1].get("column") or ""),
                    item[1]["target_table"],
                ),
            )
            if not cyclic_edges:
                raise
            victim, dropped = cyclic_edges[0]
            victim["lookups"].remove(dropped)
            warnings.append(
                f"Skipped optional lookup on {victim['logical_name']} to avoid a dependency cycle: "
                + str(dropped.get("column") or dropped.get("column_name"))
            )
    for table in tables:
        table["quality_profile"] = build_quality_profile(table)

    email_columns = [
        f"{table['logical_name']}.{column['logical_name']}"
        for table in tables
        for column in table["columns"]
        if column.get("type") == "String" and column.get("format") == "Email"
    ]
    if email_columns and not seed_email:
        raise ValueError(
            "A seed email is required because these columns need email values: "
            + ", ".join(email_columns)
        )
    unknown_count_tables = sorted(set(table_counts).difference(selected_names))
    if unknown_count_tables:
        raise ValueError(
            "Per-table counts were supplied for tables not included in the plan: "
            + ", ".join(unknown_count_tables)
        )

    return {
        "name": "dataverse-testdata",
        "environment": environment,
        "seed_email": seed_email,
        "context_source": context.get("source"),
        "context_notes": context.get("notes") or [],
        "warnings": sorted(set(warnings)),
        "tables": tables,
    }


def parse_table_counts(value: str | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError(
                f"Invalid per-table count {item!r}; expected table=positive-integer."
            )
        name, raw_count = (part.strip() for part in item.split("=", 1))
        if not name or name in result:
            raise ValueError(f"Invalid or duplicate per-table count for {name!r}.")
        try:
            count = int(raw_count)
        except ValueError as error:
            raise ValueError(
                f"Invalid per-table count for {name}: {raw_count!r}."
            ) from error
        if count <= 0:
            raise ValueError(f"Per-table count for {name} must be positive.")
        result[name] = count
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a live Dataverse environment and generate the Excel approval workbook "
            "from its current table metadata and relationships."
        )
    )
    parser.add_argument("--tables", required=True, help="Comma-separated table logical names.")
    volume = parser.add_mutually_exclusive_group(required=True)
    volume.add_argument("--size", choices=sorted(SIZE_COUNTS))
    volume.add_argument("--count", type=int)
    parser.add_argument(
        "--environment-url",
        required=True,
        help="Environment explicitly selected through the Microsoft dv-connect skill.",
    )
    parser.add_argument("--seed-email", required=True)
    parser.add_argument(
        "--output",
        help="Excel workbook returned for approval. Defaults to the user's Desktop.",
    )
    parser.add_argument("--plan-output", help="Optional JSON metadata snapshot for audit/debugging.")
    parser.add_argument(
        "--context-rules",
        help="Optional JSON rules extracted from a user-confirmed plugin/custom-process repository.",
    )
    parser.add_argument(
        "--table-counts",
        help="Per-table overrides, e.g. account=50,contact=100.",
    )
    parser.add_argument(
        "--existing-tables",
        help="Comma-separated lookup target tables to link to existing records instead of generating them.",
    )
    parser.add_argument("--all-writable-columns", action="store_true")
    parser.add_argument("--no-required-related", action="store_true")
    args = parser.parse_args()

    count = args.count if args.count is not None else SIZE_COUNTS[args.size]
    if count <= 0:
        raise ValueError("Record count must be positive.")
    table_names = [value.strip() for value in args.tables.split(",") if value.strip()]
    if not table_names:
        raise ValueError("At least one table logical name is required.")
    emit_progress(
        4,
        "running",
        "Reading writable columns, keys, choices, business constraints, and relationships "
        f"for {', '.join(table_names)}. This determines what can be filled safely.",
        "Resolve required dependencies and build a per-table generation profile.",
    )
    os.environ["DATAVERSE_URL"] = args.environment_url.rstrip("/")
    get_client = import_get_client()
    require_delegated_auth()
    client = get_client("dv-data")
    # A real read proves the selected environment is reachable with the signed-in user's account.
    organizations = client.records.list(
        "organization",
        select=["organizationid"],
        top=1,
    )
    if not organizations:
        raise RuntimeError("Authenticated connection is valid but no organization row was returned.")
    context = {}
    if args.context_rules:
        with Path(args.context_rules).open(encoding="utf-8") as handle:
            context = json.load(handle)
    try:
        plan = discover_plan(
            client=client,
            table_names=table_names,
            count=count,
            seed_email=validate_seed_email(args.seed_email),
            environment=args.environment_url.rstrip("/"),
            all_writable=args.all_writable_columns,
            include_required_related=not args.no_required_related,
            context=context,
            existing_tables={
                name.strip() for name in (args.existing_tables or "").split(",") if name.strip()
            },
            table_counts=parse_table_counts(args.table_counts),
        )
    except Exception as error:
        emit_progress(
            4,
            "blocked",
            f"Schema discovery could not produce a safe generation plan: {error}",
            "Resolve the reported metadata, relationship, or run-configuration issue and retry.",
        )
        raise
    implicit_tables = [
        table["logical_name"]
        for table in plan["tables"]
        if (table.get("inclusion") or {}).get("kind") == "implicit"
    ]
    emit_progress(
        4,
        "complete",
        f"Discovered {len(plan['tables'])} table profile(s). "
        + (
            "Automatically included dependencies: " + ", ".join(implicit_tables) + "."
            if implicit_tables
            else "No additional dependency tables were required."
        ),
        "Generate meaningful records and the editable approval workbook.",
    )
    if args.plan_output:
        plan_path = Path(args.plan_output)
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        with plan_path.open("w", encoding="utf-8") as handle:
            json.dump(plan, handle, indent=2)
    output_path = (
        Path(args.output)
        if args.output
        else default_workbook_path(table_names, count)
    )
    emit_progress(
        5,
        "running",
        "Generating values from the discovered metadata and linking related rows in dependency order.",
        "Write the Excel preview, then validate every row against its quality profile.",
    )
    try:
        build_workbook(plan, output_path)
    except Exception as error:
        emit_progress(
            5,
            "blocked",
            f"Record generation or workbook creation failed: {error}",
            "Correct the reported profile or generation rule and regenerate the preview.",
        )
        raise
    emit_progress(
        5,
        "complete",
        f"Generated the approval workbook at {output_path.resolve()}.",
        "Run workbook validation and review the generated samples before approval.",
    )
    print(f"Approval workbook written: {output_path.resolve()}")


if __name__ == "__main__":
    main()
