from __future__ import annotations

from typing import Any


def has_value(value: Any) -> bool:
    return value is not None and value != ""


def semantic_group(column: dict[str, Any]) -> str | None:
    role = str(column.get("role") or "")
    name = str(column.get("logical_name") or "").casefold()
    attr_type = column.get("type")
    fmt = str(column.get("format") or "").casefold()

    if role.startswith("addr_"):
        return "address"
    if role in {"phone", "phone_mobile", "url"} or fmt == "email":
        return "contactability"
    if role.startswith("person_") or role in {"job_title", "department", "birthdate"}:
        return "person_profile"
    if role in {"employees", "revenue"}:
        return "organization_profile"
    if role in {"product_name", "unit_price", "stock"}:
        return "product_profile"
    if role in {"order_name", "order_date", "line_number", "quantity", "money_total"}:
        return "transaction_profile"
    if role == "description" or attr_type == "Memo" or any(
        token in name for token in ("description", "notes", "comment")
    ):
        return "description"
    if attr_type in {
        "BigInt",
        "Boolean",
        "DateTime",
        "Decimal",
        "Double",
        "Integer",
        "Money",
        "Picklist",
        "String",
    }:
        return "business_details"
    return None


def build_quality_profile(table: dict[str, Any]) -> dict[str, Any]:
    primary_name = table.get("primary_name_column")
    core_columns: list[str] = []
    meaningful_columns: list[str] = []
    groups: dict[str, list[str]] = {}

    for column in table.get("columns", []):
        name = column["logical_name"]
        if (
            column.get("role") in {"addr_line2", "addr_line3"}
            and not column.get("required")
            and name != primary_name
        ):
            continue
        meaningful_columns.append(name)
        if column.get("required") or name == primary_name:
            core_columns.append(name)
            continue
        group = semantic_group(column)
        if group:
            groups.setdefault(group, []).append(name)

    for lookup in table.get("lookups", []):
        name = lookup["column"]
        meaningful_columns.append(name)
        if lookup.get("required"):
            core_columns.append(name)
        else:
            groups.setdefault("relationships", []).append(name)

    core_columns = list(dict.fromkeys(core_columns))
    meaningful_columns = list(dict.fromkeys(meaningful_columns))
    normalized_groups = [
        {"name": name, "columns": columns, "minimum": 1}
        for name, columns in sorted(groups.items())
        if columns
    ]
    minimum = len(core_columns) + len(normalized_groups)
    if meaningful_columns:
        minimum = max(1, min(minimum, len(meaningful_columns)))

    return {
        "version": 1,
        "core_columns": core_columns,
        "groups": normalized_groups,
        "meaningful_columns": meaningful_columns,
        "minimum_populated_fields": minimum,
    }


def evaluate_record(table: dict[str, Any], record: dict[str, Any]) -> list[str]:
    profile = table.get("quality_profile") or build_quality_profile(table)
    errors: list[str] = []

    missing_core = [
        name for name in profile.get("core_columns", []) if not has_value(record.get(name))
    ]
    if missing_core:
        errors.append("missing core field(s): " + ", ".join(missing_core))

    for group in profile.get("groups", []):
        populated = sum(has_value(record.get(name)) for name in group.get("columns", []))
        minimum = int(group.get("minimum", 1))
        if populated < minimum:
            errors.append(
                f"needs {minimum} value(s) from {group['name']}: "
                + ", ".join(group.get("columns", []))
            )

    meaningful = profile.get("meaningful_columns", [])
    populated_total = sum(has_value(record.get(name)) for name in meaningful)
    minimum_total = int(profile.get("minimum_populated_fields", 0))
    if populated_total < minimum_total:
        errors.append(
            f"only {populated_total} meaningful field(s) are populated; "
            f"at least {minimum_total} are required"
        )
    return errors


def completeness(table: dict[str, Any], record: dict[str, Any]) -> tuple[int, int]:
    profile = table.get("quality_profile") or build_quality_profile(table)
    meaningful = profile.get("meaningful_columns", [])
    return sum(has_value(record.get(name)) for name in meaningful), len(meaningful)
