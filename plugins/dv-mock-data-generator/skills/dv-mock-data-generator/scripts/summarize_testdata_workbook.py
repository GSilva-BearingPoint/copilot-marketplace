from __future__ import annotations

import argparse
import json
from pathlib import Path

from openpyxl import load_workbook

from progress import progress_message
from quality_profile import completeness
from validate_testdata_workbook import load_metadata, read_rows


def _inclusion_reason(table: dict) -> str:
    inclusion = table.get("inclusion") or {}
    reasons = inclusion.get("reasons") or []
    if not reasons:
        return "explicitly requested"
    reason_text = "; ".join(
        f"{reason['source_table']}.{reason['lookup_column']} "
        f"required {reason['target_table']} ({reason['requirement']})"
        for reason in reasons
    )
    path = inclusion.get("dependency_path") or []
    return reason_text + (f"; path: {' -> '.join(path)}" if path else "")


def summarize(workbook_path: Path) -> str:
    workbook = load_workbook(workbook_path, data_only=False)
    run, tables = load_metadata(workbook)
    total = 0
    table_summaries = []
    relationships = []
    lines = [
        progress_message(
            7,
            "waiting",
            "The dry-run workbook is ready for review. Writes are paused because explicit approval "
            "is required for this exact preview.",
            "Approve, reject, or edit the workbook; edited workbooks must be validated again.",
        ),
        f"Environment: {run.get('environment')}",
    ]
    for table_name in run["order"]:
        table = tables[table_name]
        rows = read_rows(workbook, table)
        total += len(rows)
        table_summaries.append(f"{len(rows)} {table_name}")
        for lookup in table.get("lookups", []):
            source = "existing records" if lookup.get("existing_ids") else "generated records"
            if lookup.get("deferred"):
                source += ", deferred until after creates"
            relationships.append(
                f"{table_name}.{lookup['column']} -> {lookup['target_table']} ({source})"
            )

    lines.append(f"Create: {', '.join(table_summaries)} ({total} total records)")
    lines.append(
        "Relationships: "
        + (", ".join(relationships) if relationships else "none in this workbook")
    )
    lines.append(f"Repository context: {run.get('context_source') or 'none provided'}")

    for table_name in run["order"]:
        table = tables[table_name]
        rows = read_rows(workbook, table)
        inclusion = table.get("inclusion") or {}
        lines.append(
            f"Table {table_name}: {len(rows)} record(s), "
            f"{inclusion.get('kind', 'requested')}; {_inclusion_reason(table)}"
        )
        scores = [completeness(table, row) for row in rows]
        if scores:
            populated = [score[0] for score in scores]
            available = scores[0][1]
            lines.append(
                f"  Completeness: min {min(populated)}/{available}, "
                f"average {sum(populated) / len(populated):.1f}/{available}, "
                f"max {max(populated)}/{available}"
            )
        for sample_number, row in enumerate(rows[:3], start=1):
            lines.append(
                f"  Sample {sample_number}: "
                + json.dumps(row, default=str, ensure_ascii=True, sort_keys=True)
            )
        for action in table.get("post_create_actions", []):
            lines.append(
                "  Post-create action: "
                + str(action.get("description") or action.get("kind"))
            )

    warnings = run.get("warnings") or []
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warning}" for warning in warnings)
    if run.get("seed_email"):
        lines.append("Email aliases: compact +tags derived from the confirmed seed email")
    lines.append(f"Workbook: {workbook_path.resolve()}")
    lines.append("No Dataverse records have been written yet.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print the short summary shown immediately before approval."
    )
    parser.add_argument("--workbook", required=True)
    args = parser.parse_args()
    print(summarize(Path(args.workbook)))


if __name__ == "__main__":
    main()
