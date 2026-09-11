"""Load machine-specific settings from an untracked local JSON file."""

import json
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.local.json")


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    """Read local settings without printing connection details or folder names."""
    try:
        with Path(path).open(encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
    except FileNotFoundError:
        raise FileNotFoundError(
            "Create config.local.json from config.example.json and fill in your settings."
        ) from None

    if not isinstance(settings, dict):
        raise ValueError("Configuration must be a JSON object.")
    for key in ("nas_host", "nas_username", "nas_share"):
        if not isinstance(settings.get(key), str) or not settings[key].strip():
            raise ValueError(f"Configuration requires a non-empty {key} string.")
    for key in ("folders", "excluded_folders"):
        value = settings.get(key)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError(f"Configuration requires a {key} list of non-empty strings.")
    if not settings["folders"]:
        raise ValueError("Configuration requires at least one folder.")
    return settings
