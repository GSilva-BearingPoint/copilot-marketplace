from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path


def auth_candidates() -> list[Path]:
    candidates = []
    if os.environ.get("DV_TESTDATA_AUTH_DIR"):
        candidates.append(Path(os.environ["DV_TESTDATA_AUTH_DIR"]))
    candidates.append(Path.cwd() / "scripts")

    search_roots = [
        Path.home() / ".copilot" / "installed-plugins",
        Path.home() / ".copilot" / "skills",
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        candidates.extend(
            path.parent
            for path in root.rglob("auth.py")
            if path.parent.name.casefold() == "scripts"
            and "dataverse" in str(path).casefold()
        )
    return list(dict.fromkeys(path.resolve() for path in candidates))


def import_get_client():
    for candidate in auth_candidates():
        if (candidate / "auth.py").is_file():
            sys.path.insert(0, str(candidate))
            return importlib.import_module("auth").get_client
    raise RuntimeError(
        "auth.py was not found. Set DV_TESTDATA_AUTH_DIR to the Microsoft Dataverse "
        "skills scripts folder."
    )
