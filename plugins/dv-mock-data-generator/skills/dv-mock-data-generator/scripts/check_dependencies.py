from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path

from progress import emit_progress, progress_payload


TOOLS = {
    "pac": "Install PAC CLI using the Microsoft dv-connect skill.",
    "dataverse": "npm install -g @microsoft/dataverse@latest",
    "npm": "Install Node.js, then retry.",
}
MODULES = {
    "openpyxl": "openpyxl",
    "PowerPlatform.Dataverse": "PowerPlatform-Dataverse-Client",
    "azure.identity": "azure-identity",
    "msal": "msal",
    "msal_extensions": "msal-extensions",
    "pandas": "pandas",
}


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError):
        return False


def check() -> dict:
    missing_tools = [
        {"name": name, "install": instruction}
        for name, instruction in TOOLS.items()
        if shutil.which(name) is None
    ]
    missing_modules = [
        {"name": name, "package": package}
        for name, package in MODULES.items()
        if not module_available(name)
    ]
    return {
        "python": sys.executable,
        "missing_tools": missing_tools,
        "missing_python_modules": missing_modules,
        "ready": not missing_tools and not missing_modules,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only dependency check. This script never installs anything."
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = check()
    detail = (
        "All required tools and Python modules are available."
        if result["ready"]
        else "Required tools or Python modules are missing; generation cannot start safely."
    )
    next_action = (
        "Confirm the delegated Dataverse connection."
        if result["ready"]
        else "Review the missing items and choose whether to install them."
    )
    result["progress"] = progress_payload(
        1,
        "complete" if result["ready"] else "blocked",
        detail,
        next_action,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        emit_progress(
            1,
            "complete" if result["ready"] else "blocked",
            detail,
            next_action,
        )
        if not result["ready"]:
            print("Missing dependencies:")
            for item in result["missing_tools"]:
                print(f"- Tool {item['name']}: {item['install']}")
            for item in result["missing_python_modules"]:
                print(f"- Python module {item['name']}: pip package {item['package']}")
            requirements = Path(__file__).with_name("requirements.txt")
            print(f"Python install command: python -m pip install -r \"{requirements}\"")
    raise SystemExit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
