"""Cross-table consistency for generated records (line totals, order totals, copied dates).

Rules are inferred from live column names/types, so they only apply when the pattern exists:
* child amount  = child quantity x parent unit price   (child has a lookup to the priced parent)
* parent total  = sum of the child amounts pointing at it
* child date = parent value when the child repeats that DateTime column (e.g. orderdate)
"""
from __future__ import annotations

import re
from typing import Any

from testdata_common import INTERNAL_KEY


def _column(table: dict[str, Any], types: set[str], pattern: str) -> str | None:
    for column in table.get("columns", []):
        if column.get("type") in types and re.search(pattern, column["logical_name"].casefold()):
            return column["logical_name"]
    return None


def _column_meaning(column: dict[str, Any]) -> str:
    return " ".join(
        (
            str(column.get("logical_name") or ""),
            str(column.get("label") or ""),
            str(column.get("description") or ""),
        )
    ).casefold()


def _choice_text(column: dict[str, Any], value: Any) -> str:
    option = next(
        (
            item
            for item in column.get("options") or []
            if isinstance(item, dict) and item.get("value") == value
        ),
        {},
    )
    labels = option.get("labels") or [option.get("label")]
    return " ".join(str(label or "") for label in labels).casefold()


def derive_values(
    tables: dict[str, dict[str, Any]],
    records: dict[str, list[dict[str, Any]]],
    order: list[str],
) -> None:
    by_key = {name: {r[INTERNAL_KEY]: r for r in rows} for name, rows in records.items()}
    money = {"Money", "Decimal", "Double"}
    child_amounts: dict[str, list[tuple[str, str, str]]] = {}

    for table_name, rows in records.items():
        table = tables[table_name]
        pricing_method = next(
            (
                column
                for column in table.get("columns", [])
                if column.get("type") == "Picklist"
                and (
                    "pricing method" in _column_meaning(column)
                    or "pricingmethod" in _column_meaning(column)
                )
            ),
            None,
        )
        percentage = next(
            (
                column["logical_name"]
                for column in table.get("columns", [])
                if column.get("role") == "percentage"
                and "percentage" in _column_meaning(column)
            ),
            None,
        )
        if pricing_method and percentage:
            method_name = pricing_method["logical_name"]
            for row in rows:
                method_text = _choice_text(pricing_method, row.get(method_name))
                if "amount" in method_text and percentage in row:
                    row[percentage] = 0

    for child in order:
        table = tables[child]
        quantity = _column(table, {"Integer", "BigInt"}, r"(?:^|_)quantity$")
        amount = _column(table, money, r"(?:^|_)(?:line|extended)?(?:total)?amount$|(?:^|_)linetotal$")
        for lookup in table.get("lookups", []):
            parent = lookup["target_table"]
            if lookup.get("existing_ids") or parent not in records:
                continue
            ptable = tables[parent]
            price = _column(ptable, money, r"(?:^|_)(?:unit)?price$")
            for row in records[child]:
                target = by_key[parent].get(row.get(lookup["column"]))
                if target is None:
                    continue
                if quantity and amount and price and quantity in row and price in target:
                    row[amount] = round(row[quantity] * target[price], 2)
                elif amount and price and price in target:
                    row[amount] = round(float(target[price]), 2)
                for column in table.get("columns", []):
                    name = column["logical_name"]
                    if column.get("type") == "DateTime" and name in target:
                        row[name] = target[name]
            if amount:
                child_amounts.setdefault(parent, []).append((child, lookup["column"], amount))

    for parent, sources in child_amounts.items():
        total = _column(tables[parent], money, r"(?:^|_)(?:grand)?total(?:cost|amount)?$")
        if not total:
            continue
        sums: dict[str, float] = {}
        for child, lookup_column, amount in sources:
            for row in records[child]:
                key = row.get(lookup_column)
                if key is not None and amount in row:
                    sums[key] = sums.get(key, 0.0) + float(row[amount])
        for row in records[parent]:
            if row[INTERNAL_KEY] in sums:
                row[total] = round(sums[row[INTERNAL_KEY]], 2)
