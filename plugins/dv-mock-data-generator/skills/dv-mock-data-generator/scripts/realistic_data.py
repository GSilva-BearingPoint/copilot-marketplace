"""Metadata-driven "organic" values for profile-style columns.

* Columns are classified into semantic roles from live Dataverse metadata (attribute type, Format,
  logical name), so custom columns are picked up and missing ones are simply ignored.
* Vocabulary comes from references/seed-data/*.txt|psv. Selection is offset by the run id, so each
  run yields different (but internally consistent) data while staying unique within a run.
* Address, postal code, country and phone formats for one row come from one shared location.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import random
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

SEED_DIR = Path(__file__).resolve().parent.parent / "references" / "seed-data"
ADDRESS_PARTS = ("line1", "line2", "city", "stateorprovince", "postalcode", "country")
PHONE_TOKENS = ("phone", "telephone", "mobile", "cellular")
SKIP_URL_TOKENS = ("ftp", "image", "photo", "logo", "avatar", "social", "linkedin", "twitter", "facebook")
SKIP_PHONE_TOKENS = ("fax", "pager", "assistant", "callback", "home", "other", "company")
LANDLINE_ROLE, MOBILE_ROLE = "phone", "phone_mobile"


@lru_cache(maxsize=None)
def _lines(name: str) -> tuple[str, ...]:
    path = SEED_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Seed-data file not found: {path}")
    values = tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    if not values:
        raise ValueError(f"Seed-data file is empty: {path}")
    return values


@lru_cache(maxsize=1)
def _locations() -> tuple[dict[str, Any], ...]:
    rows = _lines("locations.psv")
    header = rows[0].split("|")
    result = []
    for row in rows[1:]:
        item = dict(zip(header, row.split("|")))
        item["legal_suffixes"] = tuple(item["legal_suffixes"].split(","))
        result.append(item)
    return tuple(result)


def _hash(*parts: Any) -> int:
    return int(hashlib.sha256(":".join(map(str, parts)).encode()).hexdigest()[:12], 16)


def pick(values: Sequence[str], index: int, run_id: str, salt: str) -> str:
    """Run-dependent walk through values: distinct for consecutive indexes until the list wraps."""
    size = len(values)
    start = _hash(run_id, salt) % size
    stride = (_hash(salt, "stride") % size) | 1
    while math.gcd(stride, size) != 1:
        stride += 2
    return values[(start + index * stride) % size]


def _rng(run_id: str, table: str, index: int, salt: str) -> random.Random:
    return random.Random(_hash(run_id, table, index, salt))


def _mask(pattern: str, rng: random.Random) -> str:
    out = []
    for char in pattern:
        if char == "#":
            out.append(str(rng.randint(0, 9)))
        elif char == "@":
            out.append(rng.choice("ABDEFGHJLNPQRSTUWXYZ"))
        else:
            out.append(char)
    return "".join(out)


def location(index: int, run_id: str) -> dict[str, Any]:
    rows = _locations()
    start = _hash(run_id, "loc") % len(rows)
    return rows[(start + index) % len(rows)]


def product(index: int, run_id: str) -> tuple[str, str]:
    """Distinct (base, variant) names per index until the name list wraps."""
    bases = _lines("product-names.txt")
    variants = _lines("product-variants.txt")
    combos = [(base, variant) for variant in variants for base in bases]
    return combos[
        (_hash(run_id, "product-start") + index * _coprime_stride(len(combos), run_id)) % len(combos)
    ]


def _coprime_stride(size: int, run_id: str) -> int:
    stride = (_hash(run_id, "product-stride") % size) | 1
    while math.gcd(stride, size) != 1:
        stride += 2
    return stride


def org_name(index: int, run_id: str) -> str:
    base = pick(_lines("company-names.txt"), index, run_id, "company-base")
    sector = pick(_lines("company-sectors.txt"), index // 3, run_id, "company-sector")
    suffix = pick(location(index, run_id)["legal_suffixes"], index, run_id, "company-suffix")
    return f"{base} {sector} {suffix}"


def website(index: int, run_id: str) -> str:
    base = pick(_lines("company-names.txt"), index, run_id, "company-base")
    return f"https://www.{base.casefold()}.example"


def classify(logical_name: str, attr_type: str | None, fmt: str | None = None) -> str | None:
    """Maps a column's metadata to a semantic role, or None when it has no organic generator."""
    name = (logical_name or "").casefold()
    fmt = (fmt or "").casefold()
    if attr_type == "String":
        if re.search(r"(?:^|_)(?:first|given)name$", name):
            return "person_first_name"
        if re.search(r"(?:^|_)(?:last|family|sur)name$", name):
            return "person_last_name"
        if re.search(r"(?:^|_)(?:middle|additional)name$", name):
            return "person_middle_name"
        for part in ADDRESS_PARTS:
            if re.search(rf"(?:^|_)address\d*_{part}$", name) or (
                part in {"city", "postalcode", "country"} and re.search(rf"(?:^|_){part}$", name)
            ):
                return f"addr_{part}"
        if fmt == "phone" or any(token in name for token in PHONE_TOKENS):
            if any(token in name for token in SKIP_PHONE_TOKENS):
                return None
            return MOBILE_ROLE if re.search(r"mobile|cell", name) else LANDLINE_ROLE
        if fmt == "url" or re.search(r"website|(?:^|_)url$", name):
            return "url"
        if re.search(r"jobtitle|job_title|(?:^|_)position$", name):
            return "job_title"
        if re.search(r"(?:^|_)department$", name):
            return "department"
        if re.search(r"description|notes|comment", name):
            return "description"
    if attr_type == "DateTime" and re.search(r"birthdate|dateofbirth|birth_date", name):
        return "birthdate"
    if attr_type == "DateTime" and re.search(r"(?:^|_)orderdate$", name):
        return "order_date"
    if attr_type == "String":
        if re.search(r"(?:^|_)productname$", name):
            return "product_name"
        if re.search(r"(?:^|_)ordername$", name):
            return "order_name"
        if re.search(r"(?:^|_)line(?:item)?(?:number|no)$", name):
            return "line_number"
    if attr_type in {"Money", "Decimal", "Double"}:
        if re.search(r"(?:^|_)(?:unit)?price$", name):
            return "unit_price"
        if re.search(r"(?:^|_)(?:grand)?total(?:cost|amount)?$", name):
            return "money_total"
    if attr_type in {"Integer", "BigInt"}:
        if re.search(r"stockquantity|quantityonhand|(?:^|_)stock$", name):
            return "stock"
        if re.search(r"(?:^|_)quantity$", name):
            return "quantity"
    if attr_type == "Boolean" and re.search(r"(?:^|_)isactive$", name):
        return "active_flag"
    if attr_type in {"Integer", "BigInt", "Decimal", "Double"} and re.search(
        r"numberofemployees|employeecount|headcount", name
    ):
        return "employees"
    if attr_type in {"Money", "Decimal", "Double"} and re.search(r"(?:^|_)(annual)?revenue$", name):
        return "revenue"
    return None


def select_organic(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keeps the organic profile light: one address group, one landline, one mobile, one URL."""
    chosen: list[dict[str, Any]] = []
    address_groups: dict[str, list[dict[str, Any]]] = {}
    singles: dict[str, dict[str, Any]] = {}
    def rank(item: dict[str, Any]) -> tuple[int, str]:
        name = item["logical_name"]
        # Prefer primary columns (telephone1, websiteurl) over prefixed/secondary or custom ones.
        return (("address" in name) + ("2" in name or "3" in name) + (0 if "website" in name or name.endswith("1") else 1), name)

    for column in sorted(candidates, key=rank):
        role = column["role"]
        if role.startswith("addr_"):
            prefix = re.sub(rf"({'|'.join(ADDRESS_PARTS)})$", "", column["logical_name"])
            address_groups.setdefault(prefix, []).append(column)
        elif role in {LANDLINE_ROLE, MOBILE_ROLE, "url"}:
            singles.setdefault(role, column)
        else:
            chosen.append(column)
    if address_groups:
        best = max(sorted(address_groups), key=lambda key: len(address_groups[key]))
        chosen.extend(address_groups[best])
    chosen.extend(singles.values())
    return chosen


def _clamp(value: float, column: dict[str, Any]) -> float:
    low, high = column.get("min_value"), column.get("max_value")
    if low is not None:
        value = max(value, float(low))
    if high is not None:
        value = min(value, float(high))
    return value


def generate(role: str, column: dict[str, Any], table: str, index: int, run_id: str) -> Any | None:
    loc = location(index, run_id)
    rng = _rng(run_id, table, index, role)
    if role == "addr_line1":
        street = pick(_lines("street-names.txt"), index, run_id, "street")
        return f"{rng.randint(1, 240)} {street}"
    if role == "addr_line2":
        if column.get("required") or index % 3 == 0:
            return f"Suite {rng.randint(1, 40) * 10}"
        return None
    if role == "addr_line3":
        return f"Building {rng.randint(1, 12)}" if column.get("required") else None
    if role == "addr_city":
        return loc["city"]
    if role == "addr_stateorprovince":
        return loc["state"]
    if role == "addr_postalcode":
        return _mask(loc["postal"], _rng(run_id, table, index, "postal"))
    if role == "addr_country":
        return loc["country"]
    if role in {LANDLINE_ROLE, MOBILE_ROLE}:
        pattern = loc["mobile" if role == MOBILE_ROLE else "landline"]
        return _mask(pattern, _rng(run_id, table, index, column["logical_name"]))
    if role == "url":
        return website(index, run_id)
    if role == "job_title":
        return pick(_lines("job-titles.txt"), index, run_id, "job")
    if role == "department":
        return pick(_lines("departments.txt"), index, run_id, "dept")
    if role == "birthdate":
        return f"{rng.randint(1960, 2000)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
    if role == "employees":
        return int(_clamp(rng.choice((8, 15, 24, 40, 75, 120, 250, 500, 1200, 3000)), column))
    if role == "revenue":
        value = rng.choice((250_000, 750_000, 1_500_000, 4_000_000, 12_000_000, 40_000_000))
        return round(_clamp(float(value), column), 2)
    if role == "product_name":
        base, variant = product(index, run_id)
        return f"{base} {variant}"
    if role == "unit_price":
        price = rng.choice((9, 15, 25, 40, 55, 80, 120, 175, 250, 400, 650))
        return float(_clamp(price, column))
    if role in {"order_name", "line_number"}:
        prefix = "SO" if role == "order_name" else "LI"
        return f"{prefix}-{100000 + _hash(run_id, role) % 800000 + index}"
    if role == "order_date":
        day = dt.date.today() - dt.timedelta(days=rng.randint(0, 365))
        return day.isoformat()
    if role == "quantity":
        return int(_clamp(rng.choice((1, 1, 2, 2, 3, 4, 5, 6, 8, 10, 12, 20)), column))
    if role == "stock":
        return int(_clamp(rng.choice((0, 5, 12, 25, 40, 75, 120, 250, 400, 500)), column))
    if role == "active_flag":
        return rng.random() < 0.9
    if role == "money_total":
        return float(_clamp(rng.randint(50, 5000), column))
    return None
