"""Load machine-specific settings from an untracked local JSON file."""

import json
from pathlib import Path
from pathlib import PurePosixPath


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.local.json"


def is_excluded(relative_path: str, excluded_folders: list[str]) -> bool:
    """Return whether a relative path is an excluded folder or its descendant.

    A bare folder name such as ``WBEM`` matches that folder name anywhere in
    the tree, case-insensitively. A path such as ``System/Cache`` matches that
    path and everything below it, also case-insensitively.
    """
    path_parts = tuple(
        part.casefold()
        for part in PurePosixPath(str(relative_path).replace("\\", "/")).parts
    )
    for excluded in excluded_folders:
        excluded_parts = tuple(
            part.casefold()
            for part in PurePosixPath(str(excluded).replace("\\", "/")).parts
        )
        if not excluded_parts:
            continue
        if len(excluded_parts) == 1 and excluded_parts[0] in path_parts:
            return True
        if path_parts[: len(excluded_parts)] == excluded_parts:
            return True
    return False


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Read local settings without printing connection details or folder names.

    ``source_type`` is ``local`` for a filesystem path or ``nas`` for an
    accessible NAS share. The older NAS-only shape remains valid for local
    configuration files by defaulting to ``nas``.
    """
    try:
        with Path(path).open(encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except FileNotFoundError:
        raise FileNotFoundError(
            "Create config.local.json from config.example.json and fill in your settings."
        ) from None

    if not isinstance(settings, dict):
        raise ValueError("Configuration must be a JSON object.")
    source_type = settings.get("source_type", "nas")
    if source_type not in {"local", "nas"}:
        raise ValueError("Configuration source_type must be 'local' or 'nas'.")
    settings["source_type"] = source_type

    if source_type == "local":
        source_path = settings.get("source_path")
        if not isinstance(source_path, str) or not source_path.strip():
            raise ValueError(
                "Local configuration requires a non-empty source_path string."
            )
    else:
        for key in ("nas_host", "nas_username", "nas_share"):
            if not isinstance(settings.get(key), str) or not settings[key].strip():
                raise ValueError(f"Configuration requires a non-empty {key} string.")
    for key in ("folders", "excluded_folders"):
        value = settings.get(key)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(f"Configuration requires a {key} list of non-empty strings.")
    return settings
