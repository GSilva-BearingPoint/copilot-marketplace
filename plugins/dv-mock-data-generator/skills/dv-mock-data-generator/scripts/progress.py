from __future__ import annotations

from typing import Any


TOTAL_STEPS = 8
STEP_TITLES = {
    1: "Prerequisites",
    2: "Connection",
    3: "Run setup",
    4: "Schema discovery",
    5: "Record generation",
    6: "Workbook validation",
    7: "Approval",
    8: "Dataverse apply",
}


def progress_message(
    step: int,
    status: str,
    detail: str,
    next_action: str | None = None,
) -> str:
    if step not in STEP_TITLES:
        raise ValueError(f"Unknown progress step: {step}")
    message = (
        f"[Step {step}/{TOTAL_STEPS} - {STEP_TITLES[step]}] "
        f"{status.upper()}: {detail}"
    )
    if next_action:
        message += f" Next: {next_action}"
    return message


def emit_progress(
    step: int,
    status: str,
    detail: str,
    next_action: str | None = None,
) -> None:
    print(progress_message(step, status, detail, next_action), flush=True)


def progress_payload(
    step: int,
    status: str,
    detail: str,
    next_action: str | None = None,
) -> dict[str, Any]:
    return {
        "step": step,
        "total_steps": TOTAL_STEPS,
        "title": STEP_TITLES[step],
        "status": status,
        "detail": detail,
        "next_action": next_action,
        "message": progress_message(step, status, detail, next_action),
    }
