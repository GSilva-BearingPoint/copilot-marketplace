from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import random
import re
from pathlib import Path
from typing import Any

import realistic_data
from seed_name_loader import load_seed_names


INTERNAL_KEY = "__row_key"
SIZE_COUNTS = {"small": 25, "medium": 250, "large": 2500}
EMAIL_LOCAL_PATTERN = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")
EMAIL_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}$"
)
_SEED_NAMES = load_seed_names()
FIRST_NAMES = _SEED_NAMES["first"]
MIDDLE_NAMES = _SEED_NAMES["middle"]
LAST_NAMES = _SEED_NAMES["last"]

def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_seed_email(seed_email: str) -> str:
    value = seed_email.strip()
    local, separator, domain = value.rpartition("@")
    if (
        not separator
        or not local
        or not EMAIL_LOCAL_PATTERN.fullmatch(local)
        or not EMAIL_DOMAIN_PATTERN.fullmatch(domain)
    ):
        raise ValueError("A valid ASCII seed email address is required.")
    return f"{local}@{domain.lower()}"


def alias_email(seed_email: str, unique_id: str) -> str:
    validated = validate_seed_email(seed_email)
    local, _, domain = validated.rpartition("@")
    local = local.split("+", 1)[0]
    safe_id = re.sub(r"[^a-z0-9-]", "", unique_id.casefold()).strip("-")
    if not safe_id:
        raise ValueError("Email alias identifier must contain letters or digits.")
    max_base_length = 64 - len(safe_id) - 1
    if max_base_length < 1:
        raise ValueError("Email alias identifier is too long.")
    local = local[:max_base_length].rstrip(".")
    result = f"{local}+{safe_id}@{domain}"
    if len(result) > 254:
        raise ValueError("Generated email address exceeds the maximum valid length.")
    return result


def _base36(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result or "0"


def short_email_identifier(
    table_name: str,
    current_run_id: str,
    index: int,
    column_name: str,
) -> str:
    table_code = re.sub(r"[^a-z0-9]", "", table_name.casefold())[:2] or "td"
    run_code = hashlib.sha256(current_run_id.encode("utf-8")).hexdigest()[:3]
    row_code = _base36(index + 1).rjust(2, "0")
    trailing_number = re.search(r"(\d+)$", column_name)
    column_code = (
        trailing_number.group(1)[-1]
        if trailing_number
        else hashlib.sha256(column_name.encode("utf-8")).hexdigest()[0]
    )
    return f"{table_code}-{run_code}-{row_code}-{column_code}"


def desktop_directory() -> Path:
    if os.name == "nt":
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "Desktop")
            return Path(os.path.expandvars(value))
        except (FileNotFoundError, OSError):
            pass
    return Path.home() / "Desktop"


def default_workbook_path(table_names: list[str], count: int) -> Path:
    table_part = "-".join(re.sub(r"[^A-Za-z0-9_-]", "", name) for name in table_names)
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M")
    return desktop_directory() / f"testdata-{table_part}-{count}-{timestamp}.xlsx"


def _truncate(value: str, column: dict[str, Any]) -> str:
    max_length = column.get("max_length")
    return value[: int(max_length)] if max_length else value


def human_friendly_string(
    column: dict[str, Any],
    table_name: str,
    index: int,
    current_run_id: str = "",
    context: dict[str, Any] | None = None,
) -> str:
    context = context or {}
    name = column["logical_name"].casefold()
    meaning = " ".join(
        (
            name,
            str(column.get("label") or "").casefold(),
            str(column.get("description") or "").casefold(),
        )
    )
    if name in {"firstname", "first_name", "givenname", "given_name"}:
        return realistic_data.pick(FIRST_NAMES, index, current_run_id, "first")
    if name in {"middlename", "middle_name", "additionalname", "additional_name"}:
        return realistic_data.pick(MIDDLE_NAMES, index, current_run_id, "middle")
    if name in {"lastname", "last_name", "surname", "familyname", "family_name"}:
        return realistic_data.pick(LAST_NAMES, index, current_run_id, "last")
    if name in {"title", "position"}:
        return realistic_data.generate("job_title", column, table_name, index, current_run_id)
    kind = context.get("kind")
    if name in {"name", "productname"} and kind == "product":
        return str(context["product_name"])
    if any(token in meaning for token in ("product number", "product code", "sku", "productnumber", "productcode")):
        return str(context.get("product_number") or "")
    if kind in {"lead", "opportunity"} and any(
        token in name for token in ("company", "account", "organization")
    ):
        return str(context.get("organization_name") or "")
    if (
        name in {"name", "title", "subject", "opportunityproductname"}
        or name.endswith("name")
        or name.endswith("title")
    ) and kind in {"line_item", "price_list_item"}:
        return str(context.get("product_name") or "")
    if re.search(r"\b(name|title|subject|topic)\b", meaning) and kind in {
        "project",
        "requirement",
        "opportunity",
        "lead",
        "activity",
    }:
        base = str(context.get("project_name") or "")
        if kind == "requirement":
            return f"{base} Requirement"
        if kind == "activity":
            return f"Follow up: {base}"
        return base
    if name == "name" and kind == "price_list":
        return f"Standard {realistic_data.pick(('Commercial', 'Services', 'Products'), index, current_run_id, 'price-list')} Price List"
    org = str(context.get("organization_name") or realistic_data.org_name(index, current_run_id))
    if (
        name in {"name", "accountname", "companyname"}
        or name.endswith("name")
        or any(token in name for token in ("company", "account", "organization", "project"))
    ):
        return org
    if "description" in name or "notes" in name or "comment" in name:
        return str(context.get("description") or f"Representative record for {org}.")
    label = column.get("label") or column["logical_name"]
    return f"{org} {label}"

def apply_context_rules(
    record: dict[str, Any],
    rules: dict[str, Any],
    index: int,
) -> None:
    for column, value in (rules.get("defaults") or {}).items():
        record[column] = value

    for column, values in (rules.get("allowed_values") or {}).items():
        if not values:
            raise ValueError(f"Context rule {column} has an empty allowed-values list.")
        record[column] = values[index % len(values)]

    for column, template in (rules.get("templates") or {}).items():
        try:
            record[column] = str(template).format_map(record)
        except KeyError as error:
            raise ValueError(
                f"Context template for {column} references missing field {error.args[0]}."
            ) from error

    for calculation in rules.get("calculations") or []:
        target = calculation["target"]
        operation = calculation["operation"]
        sources = calculation.get("sources") or []
        try:
            values = [record[source] for source in sources]
        except KeyError as error:
            raise ValueError(
                f"Context calculation for {target} references missing field {error.args[0]}."
            ) from error
        if operation == "add":
            result = sum(values)
        elif operation == "multiply":
            result = 1
            for value in values:
                result *= value
        elif operation == "subtract":
            if not values:
                raise ValueError(f"Context calculation for {target} has no sources.")
            result = values[0] - sum(values[1:])
        elif operation == "divide":
            if len(values) != 2:
                raise ValueError(f"Divide calculation for {target} requires two sources.")
            result = values[0] / values[1]
        elif operation == "concat":
            result = str(calculation.get("separator", "")).join(str(value) for value in values)
        else:
            raise ValueError(f"Unsupported context calculation operation: {operation}")
        record[target] = result


def safe_sheet_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "_", name).strip()[:31] or "Table"
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        marker = f"_{suffix}"
        candidate = f"{base[:31 - len(marker)]}{marker}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def dependency_order(tables: list[dict[str, Any]]) -> list[str]:
    names = {table["logical_name"] for table in tables}
    dependencies = {
        table["logical_name"]: {
            lookup["target_table"]
            for lookup in table.get("lookups", [])
            if not lookup.get("deferred")
            if lookup["target_table"] in names
            and lookup["target_table"] != table["logical_name"]
        }
        for table in tables
    }
    ordered: list[str] = []
    while dependencies:
        ready = sorted(name for name, deps in dependencies.items() if not deps)
        if not ready:
            cycle = ", ".join(sorted(dependencies))
            raise ValueError(f"Lookup dependency cycle detected: {cycle}")
        ordered.extend(ready)
        for name in ready:
            dependencies.pop(name)
        for deps in dependencies.values():
            deps.difference_update(ready)
    return ordered


def run_id(spec: dict[str, Any]) -> str:
    return spec.get("run_id") or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")


def record_key(table_name: str, index: int) -> str:
    return f"TD-{table_name}-{index + 1:04d}"


def _choice_value(column: dict[str, Any], index: int) -> Any:
    options = column.get("options") or []
    if not options:
        raise ValueError(
            f"Choice column {column['logical_name']} has no valid options in the plan."
        )
    preferred_labels = {
        "productstructure": ("product",),
        "pricingmethodcode": ("currency amount", "amount"),
        "quantitysellingcode": ("no control", "whole"),
        "roundingpolicycode": ("none", "no rounding"),
        "roundingoptioncode": ("none", "ends in"),
        "propertyconfigurationstatus": ("not configured",),
        "producttypecode": ("product",),
    }
    preferred = preferred_labels.get(str(column.get("logical_name") or "").casefold(), ())
    option = None
    for token in preferred:
        option = next(
            (
                candidate
                for candidate in options
                if token in str(
                    candidate.get("label") if isinstance(candidate, dict) else candidate
                ).casefold()
            ),
            None,
        )
        if option is not None:
            break
    if option is None:
        option = options[index % len(options)]
    return option["value"] if isinstance(option, dict) else option


def _numeric_range(column: dict[str, Any], default_maximum: float) -> tuple[float, float]:
    raw_minimum = float(column.get("min_value", 0))
    raw_maximum = float(column.get("max_value", default_maximum))
    minimum = max(raw_minimum, 0.0) if raw_maximum >= 0 else raw_minimum
    maximum = min(raw_maximum, max(minimum, default_maximum))
    if maximum < minimum:
        maximum = minimum
    return minimum, maximum


def generate_value(
    column: dict[str, Any],
    table_name: str,
    index: int,
    seed_email: str | None,
    current_run_id: str,
    context: dict[str, Any] | None = None,
) -> Any:
    context = context or {}
    name = column["logical_name"]
    lowered = name.casefold()
    attr_type = column.get("type", "String")

    if attr_type == "String" and column.get("format") == "Email":
        if not seed_email:
            raise ValueError(f"Column {table_name}.{name} requires a seed email.")
        identifier = short_email_identifier(
            table_name,
            current_run_id,
            index,
            name,
        )
        return alias_email(seed_email, identifier)
    role = column.get("role") or realistic_data.classify(
        name,
        attr_type,
        column.get("format"),
        description=str(column.get("description") or ""),
        label=str(column.get("label") or ""),
    )
    if role:
        organic = realistic_data.generate(
            role,
            column,
            table_name,
            index,
            current_run_id,
            context,
        )
        if organic is not None:
            return _truncate(organic, column) if isinstance(organic, str) else organic
        if role == "addr_line2":
            return None
    if attr_type in {"Picklist", "Status"}:
        return _choice_value(column, index)
    if attr_type == "Boolean":
        return bool(index % 2)
    if attr_type in {"Integer", "BigInt"}:
        raw_minimum, raw_maximum = _numeric_range(column, 1000)
        minimum, maximum = int(raw_minimum), int(raw_maximum)
        return minimum + (index % (maximum - minimum + 1))
    if attr_type in {"Decimal", "Double", "Money"}:
        minimum, maximum = _numeric_range(column, 10000)
        return round(random.Random(f"{current_run_id}:{table_name}:{name}:{index}").uniform(minimum, maximum), 2)
    if attr_type == "DateTime" and column.get("format") == "DateOnly":
        value = context.get("due_date") or context.get("start_date")
        return value.isoformat() if value else (dt.date.today() + dt.timedelta(days=index)).isoformat()
    if attr_type == "DateTime":
        value = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=index)
        return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if attr_type in {"String", "Memo"}:
        if any(token in lowered for token in ("phone", "telephone", "fax")):
            return _truncate(
                realistic_data.generate("phone", column, table_name, index, current_run_id), column
            )
        value = human_friendly_string(column, table_name, index, current_run_id, context)
        if column.get("unique") and not value.endswith(str(index + 1)):
            value = f"{value} {index + 1}"
        return _truncate(value, column)
    return None


def generate_records(
    table: dict[str, Any],
    seed_email: str | None,
    current_run_id: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    count = int(table["count"])
    for index in range(count):
        record = {INTERNAL_KEY: record_key(table["logical_name"], index)}
        context = realistic_data.row_context(table, index, current_run_id)
        for column in table.get("columns", []):
            if column.get("generate", True) is False:
                continue
            value = generate_value(
                column,
                table["logical_name"],
                index,
                seed_email,
                current_run_id,
                context,
            )
            if value is not None:
                record[column["logical_name"]] = value
        apply_context_rules(record, table.get("context_rules") or {}, index)
        records.append(record)
    alternate_keys = table.get("alternate_keys") or [
        [column["logical_name"]]
        for column in table.get("columns", [])
        if column.get("unique")
    ]
    columns_by_name = {
        column["logical_name"]: column for column in table.get("columns", [])
    }
    for key_columns in alternate_keys:
        seen: set[tuple[str, ...]] = set()
        for index, record in enumerate(records):
            if not all(
                record.get(column_name) is not None and record.get(column_name) != ""
                for column_name in key_columns
            ):
                continue
            key = tuple(str(record[column_name]) for column_name in key_columns)
            if key in seen:
                adjustable = next(
                    (
                        columns_by_name[column_name]
                        for column_name in reversed(key_columns)
                        if columns_by_name.get(column_name, {}).get("type") in {"String", "Memo"}
                    ),
                    None,
                )
                if adjustable is None:
                    raise ValueError(
                        f"{table['logical_name']}: generated duplicate alternate key "
                        f"({', '.join(key_columns)}) and no string component can be adjusted."
                    )
                column_name = adjustable["logical_name"]
                suffix = f"-{index + 1}"
                max_length = adjustable.get("max_length")
                base = str(record[column_name])
                if max_length is not None:
                    available = int(max_length) - len(suffix)
                    if available < 1:
                        raise ValueError(
                            f"{table['logical_name']}.{column_name} is too short to make "
                            "the generated alternate key unique."
                        )
                    base = base[:available]
                record[column_name] = base + suffix
                key = tuple(str(record[name]) for name in key_columns)
                if key in seen:
                    raise ValueError(
                        f"{table['logical_name']}: could not make alternate key "
                        f"({', '.join(key_columns)}) unique."
                    )
            seen.add(key)
    return records
