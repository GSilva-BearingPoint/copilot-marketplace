from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from build_testdata_workbook import build_workbook
from auth_loader import import_get_client
from progress import emit_progress
from quality_profile import build_quality_profile
from realistic_data import classify, select_organic, table_kind
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
SERVER_MANAGED_TYPES = {
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


def localized_description(value: Any) -> str:
    label = localized_label(value, "")
    return label.strip()


def localized_labels(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    labels = [
        str(item["Label"])
        for item in value.get("LocalizedLabels") or []
        if isinstance(item, dict) and item.get("Label")
    ]
    user = value.get("UserLocalizedLabel")
    if isinstance(user, dict) and user.get("Label"):
        labels.insert(0, str(user["Label"]))
    return list(dict.fromkeys(labels))


def is_transient_error(error: Exception) -> bool:
    transient_tokens = (
        "database is currently unavailable",
        "please retry the connection later",
        "network name is no longer available",
        "transport-level error",
        "connection was aborted",
        "connection reset",
        "temporarily unavailable",
        "timed out",
        "timeout",
    )
    return any(token in str(error).casefold() for token in transient_tokens)


def retry_transient(operation):
    attempts = 4
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as error:
            if attempt == attempts - 1 or not is_transient_error(error):
                raise
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def metadata_json(odata, url: str) -> dict[str, Any]:
    return retry_transient(lambda: odata._request("get", url).json())


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
        entry = {
            "value": option["Value"],
            "label": localized_label(option.get("Label"), str(option["Value"])),
            "labels": localized_labels(option.get("Label")),
        }
        if option.get("State") is not None:
            entry["state"] = option["State"]
        result.append(entry)
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


def fetch_attribute_context(client, table_name: str) -> dict[str, dict[str, Any]]:
    """Attribute details the typed column listing may omit."""
    odata = client._get_odata()
    url = (
        f"{odata.api}/EntityDefinitions(LogicalName='{table_name}')/Attributes"
        "?$select=LogicalName,AttributeType,RequiredLevel,DisplayName,Description,"
        "IsPrimaryId,IsValidForCreate,IsValidODataAttribute,AttributeOf,IsCustomAttribute"
    )
    items = metadata_json(odata, url).get("value", [])
    return {
        item["LogicalName"]: {
            key: item[key]
            for key in (
                "Description",
                "AttributeType",
                "RequiredLevel",
                "DisplayName",
                "IsPrimaryId",
                "IsValidForCreate",
                "IsValidODataAttribute",
                "AttributeOf",
                "IsCustomAttribute",
            )
            if key in item
        }
        for item in items
    }


def fetch_choice_metadata(client, table_name: str) -> dict[str, dict[str, Any]]:
    """Choice metadata, including defaults and local/global option sets."""
    odata = client._get_odata()
    urls = [
        (
        f"{odata.api}/EntityDefinitions(LogicalName='{table_name}')/Attributes"
        "/Microsoft.Dynamics.CRM.PicklistAttributeMetadata"
        "?$select=LogicalName,DefaultFormValue"
        "&$expand=OptionSet($select=Options),GlobalOptionSet($select=Options)"
        ),
        (
            f"{odata.api}/EntityDefinitions(LogicalName='{table_name}')/Attributes"
            "/Microsoft.Dynamics.CRM.StateAttributeMetadata"
            "?$select=LogicalName,DefaultFormValue&$expand=OptionSet($select=Options)"
        ),
        (
            f"{odata.api}/EntityDefinitions(LogicalName='{table_name}')/Attributes"
            "/Microsoft.Dynamics.CRM.StatusAttributeMetadata"
            "?$select=LogicalName,DefaultFormValue&$expand=OptionSet($select=Options)"
        ),
    ]
    result: dict[str, dict[str, Any]] = {}
    for url in urls:
        for item in metadata_json(odata, url).get("value", []):
            result[item["LogicalName"]] = {
                "option_set": (
                    item.get("OptionSet") or item.get("GlobalOptionSet") or {}
                ),
                "default_value": item.get("DefaultFormValue"),
            }
    return result


def fetch_table_context(client, table_name: str) -> dict[str, Any]:
    odata = client._get_odata()
    url = (
        f"{odata.api}/EntityDefinitions(LogicalName='{table_name}')"
        "?$select=Description,DisplayName"
    )
    return metadata_json(odata, url)


def fetch_primary_name_column(
    client,
    table_name: str,
    primary_name: str,
) -> dict[str, Any]:
    odata = client._get_odata()
    url = (
        f"{odata.api}/EntityDefinitions(LogicalName='{table_name}')"
        f"/Attributes(LogicalName='{primary_name}')"
        "/Microsoft.Dynamics.CRM.StringAttributeMetadata"
        "?$select=LogicalName,AttributeType,RequiredLevel,DisplayName,Description,"
        "IsValidForCreate,IsValidODataAttribute,AttributeOf,IsCustomAttribute,"
        "IsPrimaryId,MaxLength,Format"
    )
    return metadata_json(odata, url)


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
    if hasattr(table, "to_dict"):
        table = table.to_dict()
    else:
        table = dict(table)
    table_context = fetch_table_context(client, table_name)
    table.update(
        {
            key: value
            for key, value in table_context.items()
            if key in {"Description", "DisplayName"} and value is not None
        }
    )
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
    attribute_context = fetch_attribute_context(client, table_name)
    known_columns = {raw.get("LogicalName") for raw in raw_columns}
    raw_columns.extend(
        {"LogicalName": logical_name, **details}
        for logical_name, details in attribute_context.items()
        if logical_name not in known_columns
    )
    if primary_name and not any(
        raw.get("LogicalName") == primary_name for raw in raw_columns
    ):
        raw_columns.append(
            fetch_primary_name_column(client, table_name, primary_name)
        )
    for raw in raw_columns:
        raw.update(attribute_context.get(raw.get("LogicalName"), {}))
    choice_metadata = fetch_choice_metadata(client, table_name)
    for raw in raw_columns:
        metadata = choice_metadata.get(raw.get("LogicalName")) or {}
        if raw.get("AttributeType") in {"Picklist", "State", "Status"}:
            if not option_values(raw):
                raw["OptionSet"] = metadata.get("option_set") or {}
            if raw.get("DefaultFormValue") is None:
                raw["DefaultFormValue"] = metadata.get("default_value")

    lifecycle = {
        raw["AttributeType"].casefold(): {
            "column": raw["LogicalName"],
            "options": option_values(raw),
            "default_value": raw.get("DefaultFormValue"),
        }
        for raw in raw_columns
        if raw.get("AttributeType") in {"State", "Status"}
        and raw.get("LogicalName")
    }

    columns = []
    organic: list[dict[str, Any]] = []
    representative_custom: list[dict[str, Any]] = []
    lookup_columns: dict[str, dict[str, Any]] = {}
    unsupported_required: list[dict[str, str]] = []
    for raw in raw_columns:
        logical_name = raw.get("LogicalName")
        attr_type = raw.get("AttributeType")
        if not logical_name or raw.get("AttributeOf") is not None:
            continue
        if raw.get("IsPrimaryId") or raw.get("IsValidForCreate") is False:
            continue
        level = required_level(raw)
        required = level in {"ApplicationRequired", "SystemRequired"}
        recommended = level == "Recommended"
        if (
            raw.get("IsValidODataAttribute") is False
            and logical_name != primary_name
            and not required
        ):
            continue
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
            if required and attr_type not in SERVER_MANAGED_TYPES:
                unsupported_required.append(
                    {
                        "column": logical_name,
                        "type": attr_type,
                        "required_level": level,
                    }
                )
            continue

        fmt = raw.get("Format")
        is_email = attr_type == "String" and fmt == "Email"
        description = localized_description(raw.get("Description"))
        label = localized_label(raw.get("DisplayName"), logical_name)
        role = classify(
            logical_name,
            attr_type,
            fmt,
            description=description,
            label=label,
        )
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
            "label": label,
            "type": attr_type,
            "required": required,
            "recommended": recommended,
            "unique": logical_name in single_key_columns,
            "selection_reasons": selection_reasons,
        }
        if role:
            column["role"] = role
        if description:
            column["description"] = description
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
            if raw.get("DefaultFormValue") is not None:
                column["default_value"] = raw["DefaultFormValue"]
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
    hard_blockers = [
        item
        for item in unsupported_required
        if item["required_level"] == "SystemRequired"
    ]
    if hard_blockers:
        raise ValueError(
            f"{table_name}: required columns use unsupported attribute types: "
            + ", ".join(
                sorted(
                    f"{item['column']} ({item['type']})"
                    for item in hard_blockers
                )
            )
            + ". Add an explicit generator/capability before creating this table."
        )

    return {
        "logical_name": (
            table.get("LogicalName")
            or table.get("table_logical_name")
            or table_name
        ),
        "display_name": localized_label(table.get("DisplayName"), table_name),
        "description": localized_description(table.get("Description")),
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
        "lifecycle": lifecycle,
        "unsupported_columns": unsupported_required,
        "_lookup_columns": lookup_columns,
    }


def _option_text(option: dict[str, Any]) -> str:
    labels = option.get("labels") or [option.get("label")]
    return " ".join(str(label or "") for label in labels).casefold()


def _active_transition(table: dict[str, Any]) -> dict[str, int] | None:
    lifecycle = table.get("lifecycle") or {}
    state = lifecycle.get("state") or {}
    status = lifecycle.get("status") or {}
    active_state = next(
        (
            option
            for option in state.get("options") or []
            if re.search(r"\bactive\b", _option_text(option))
            and not re.search(r"\binactive\b", _option_text(option))
        ),
        None,
    )
    if not active_state:
        return None
    data = {state["column"]: int(active_state["value"])}
    valid_statuses = [
        option
        for option in status.get("options") or []
        if option.get("state") in {None, active_state["value"]}
    ]
    active_status = next(
        (
            option
            for option in valid_statuses
            if re.search(r"\bactive\b", _option_text(option))
        ),
        None,
    )
    if active_status is None and status.get("default_value") is not None:
        active_status = next(
            (
                option
                for option in valid_statuses
                if option.get("value") == status["default_value"]
            ),
            None,
        )
    if active_status is not None:
        data[status["column"]] = int(active_status["value"])
    return data


def apply_relationship_semantics(
    tables: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    by_name = {table["logical_name"]: table for table in tables}
    kinds = {name: table_kind(table) for name, table in by_name.items()}
    activation_targets: set[str] = set()

    for source in tables:
        if kinds[source["logical_name"]] != "line_item":
            continue
        for lookup in source.get("lookups", []):
            target_name = lookup["target_table"]
            target = by_name.get(target_name)
            if not target or kinds.get(target_name) != "product":
                continue
            activation_targets.add(target_name)
            target_primary_name = str(target.get("primary_name_column") or "").casefold()
            conflicting_names = {
                f"{target_name.casefold()}description",
                f"{target_name.casefold()}name",
            }
            if target_primary_name.startswith(target_name.casefold()):
                conflicting_names.add(target_primary_name)
            retained = []
            removed = []
            for column in source["columns"]:
                logical_name = column["logical_name"].casefold()
                description = str(column.get("description") or "").casefold()
                is_conflicting = (
                    logical_name in conflicting_names
                    or (
                        "write-in" in description
                        and target_name.casefold() in description
                    )
                )
                (removed if is_conflicting else retained).append(column)
            source["columns"] = retained
            if removed:
                warnings.append(
                    f"Omitted {source['logical_name']} fields that conflict with populated "
                    f"{lookup['column']}: "
                    + ", ".join(column["logical_name"] for column in removed)
                    + "."
                )

    for target_name in sorted(activation_targets):
        target = by_name[target_name]
        action_data = _active_transition(target)
        if action_data:
            target.setdefault("post_create_actions", []).append(
                {
                    "kind": "set_state",
                    "description": (
                        f"Set generated {target.get('display_name') or target_name} records "
                        "to the live metadata-defined Active state before dependent line items."
                    ),
                    "data": action_data,
                }
            )
        else:
            warnings.append(
                f"{target_name} is referenced by generated line items but no metadata-defined "
                "Active state transition could be resolved."
            )


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
    existing_limit: int = 500,
) -> dict[str, Any]:
    existing_tables = existing_tables or set()
    if existing_limit <= 0:
        raise ValueError("Existing-record limit must be positive.")
    existing_ids: dict[str, list[str]] = {}
    existing_records: dict[str, list[dict[str, str]]] = {}
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
        for unsupported in info.get("unsupported_columns", []):
            warnings.append(
                f"{table_name}.{unsupported['column']} uses unsupported "
                f"{unsupported['type']} metadata at {unsupported['required_level']}; "
                "it is omitted from the workbook and the approved single-record probe "
                "must verify whether the server enforces it."
            )
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
    lookup_errors: list[str] = []
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
                    lookup_errors.append(
                        f"{table_name}.{column_name} is a required lookup but no target table "
                        f"is included. Add one of: {', '.join(possible) or 'unknown target'}."
                    )
                continue
            if len(candidates) > 1:
                lookup_errors.append(
                    f"{table_name}.{column_name} can target multiple included tables: "
                    f"{', '.join(sorted(item['target_table'] for item in candidates))}. "
                    f"Select one with --lookup-targets {table_name}.{column_name}=<target>."
                )
                continue
            chosen = sorted(candidates, key=lambda item: item["target_table"])[0]
            entry = {
                **chosen,
                "required": lookup["required"],
                "recommended": lookup["recommended"],
            }
            target = chosen["target_table"]
            if target in existing_tables:
                if target not in existing_ids:
                    target_metadata = client.tables.get(target)
                    id_column = (
                        target_metadata.get("PrimaryIdAttribute")
                        or target_metadata.get("primary_id_attribute")
                        or f"{target}id"
                    )
                    name_column = (
                        target_metadata.get("PrimaryNameAttribute")
                        or target_metadata.get("primary_name_attribute")
                    )
                    select = [id_column] + ([name_column] if name_column else [])
                    rows = retry_transient(
                        lambda: client.records.list(
                            target,
                            select=select,
                            top=existing_limit,
                        )
                    )
                    values = sorted(
                        (
                            {
                                "id": str(row[id_column]),
                                "label": str(row.get(name_column) or row[id_column]),
                            }
                            for row in rows
                        ),
                        key=lambda item: (item["label"].casefold(), item["id"]),
                    )
                    existing_records[target] = values
                    existing_ids[target] = [item["id"] for item in values]
                    if len(values) == existing_limit:
                        warnings.append(
                            f"Existing-record selection for {target} is limited to the first "
                            f"{existing_limit} deterministically sorted records."
                        )
                if not existing_ids[target]:
                    raise ValueError(f"No existing {target} records found to link {table_name}.{column_name} to.")
                entry["existing_ids"] = existing_ids[target]
                entry["existing_records"] = existing_records[target]
            lookups.append(entry)
        info["lookups"] = lookups
        if not info.get("entity_set_name"):
            raise ValueError(f"{table_name}: EntitySetName was not returned by Dataverse.")
        tables.append(info)

    if lookup_errors:
        raise ValueError(
            "Unresolved lookup decisions:\n- " + "\n- ".join(sorted(set(lookup_errors)))
        )

    # Optional lookups can form cycles (e.g. account.primarycontactid <-> contact.parentcustomerid).
    # Defer only optional edges that actually participate in a cycle.
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
                    if not lookup.get("deferred")
                    and lookup["target_table"] in names
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
                    and not lookup.get("deferred")
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
            dropped["deferred"] = True
            warnings.append(
                f"Deferred optional lookup on {victim['logical_name']} until after creates: "
                + str(dropped.get("column") or dropped.get("column_name"))
            )

    apply_relationship_semantics(tables, warnings)
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


def parse_lookup_targets(value: str | None) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for item in (value or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item or "." not in item.split("=", 1)[0]:
            raise ValueError(
                f"Invalid lookup target {item!r}; expected table.column=target-table."
            )
        source, target = (part.strip() for part in item.split("=", 1))
        table, column = (part.strip() for part in source.split(".", 1))
        if not table or not column or not target:
            raise ValueError(
                f"Invalid lookup target {item!r}; expected table.column=target-table."
            )
        relationships = result.setdefault(table, {})
        if column in relationships:
            raise ValueError(f"Duplicate lookup target for {table}.{column}.")
        relationships[column] = target
    return result


def _odata_values(odata, url: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    next_url: str | None = url
    while next_url:
        payload = metadata_json(odata, next_url)
        result.extend(payload.get("value", []))
        next_url = payload.get("@odata.nextLink")
    return result


def solution_table_names(client, unique_name: str) -> list[str]:
    escaped = unique_name.replace("'", "''")
    odata = client._get_odata()
    solutions = _odata_values(
        odata,
        f"{odata.api}/solutions?$select=solutionid&$filter=uniquename eq '{escaped}'",
    )
    if len(solutions) != 1:
        raise ValueError(
            f"Expected one solution with unique name {unique_name!r}; found {len(solutions)}."
        )
    solution_id = solutions[0]["solutionid"]
    components = _odata_values(
        odata,
        f"{odata.api}/solutioncomponents?$select=objectid"
        f"&$filter=_solutionid_value eq {solution_id} and componenttype eq 1",
    )
    names = []
    for component in components:
        object_id = component.get("objectid")
        if not object_id:
            continue
        entity = metadata_json(
            odata,
            f"{odata.api}/EntityDefinitions({object_id})?$select=LogicalName",
        )
        if entity.get("LogicalName"):
            names.append(str(entity["LogicalName"]))
    names = list(dict.fromkeys(names))
    if not names:
        raise ValueError(f"Solution {unique_name!r} contains no table components.")
    return names


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a live Dataverse environment and generate the Excel approval workbook "
            "from its current table metadata and relationships."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--tables", help="Comma-separated table logical names.")
    source.add_argument("--solution", help="Solution unique name whose table components should be included.")
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
    parser.add_argument(
        "--existing-limit",
        type=int,
        default=500,
        help="Maximum existing records loaded per lookup target table.",
    )
    parser.add_argument(
        "--lookup-targets",
        help="Comma-separated polymorphic selections, e.g. lead.customerid=account.",
    )
    parser.add_argument("--all-writable-columns", action="store_true")
    parser.add_argument("--no-required-related", action="store_true")
    args = parser.parse_args()

    count = args.count if args.count is not None else SIZE_COUNTS[args.size]
    if count <= 0:
        raise ValueError("Record count must be positive.")
    os.environ["DATAVERSE_URL"] = args.environment_url.rstrip("/")
    get_client = import_get_client()
    require_delegated_auth()
    client = get_client("dv-data")
    # A real read proves the selected environment is reachable with the signed-in user's account.
    organizations = retry_transient(
        lambda: client.records.list(
            "organization",
            select=["organizationid"],
            top=1,
        )
    )
    if not organizations:
        raise RuntimeError("Authenticated connection is valid but no organization row was returned.")
    table_names = (
        [value.strip() for value in args.tables.split(",") if value.strip()]
        if args.tables
        else solution_table_names(client, args.solution)
    )
    if not table_names:
        raise ValueError("At least one table logical name is required.")
    emit_progress(
        4,
        "running",
        "Reading writable columns, descriptions, keys, choices, business constraints, and "
        f"relationships for {', '.join(table_names)}. This determines what can be filled safely.",
        "Resolve required dependencies and build a per-table generation profile.",
    )
    context = {}
    if args.context_rules:
        with Path(args.context_rules).open(encoding="utf-8") as handle:
            context = json.load(handle)
    lookup_targets = parse_lookup_targets(args.lookup_targets)
    if lookup_targets:
        context.setdefault("tables", {})
        for table_name, relationships in lookup_targets.items():
            table_rules = context["tables"].setdefault(table_name, {})
            table_rules.setdefault("relationships", {}).update(relationships)
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
            existing_limit=args.existing_limit,
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
        else default_workbook_path(
            [f"solution-{args.solution}"] if args.solution else table_names,
            count,
        )
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
