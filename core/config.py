"""Application configuration loader.

Reads the config.json file placed in the project root.
"""

from __future__ import annotations

import json
from pathlib import Path

DEFAULT_CONFIG = {
    "dsh_command": "dsh",
    "dsh_launcher_args": [],
    "dsh_profile": "headless",
    "dsh_timeout": 180,
    "dsh_extra_args": [],
    # UI settings
    "theme": "dark",
    "ui_font_size": 14,
    "editor_font_size": 16,
    "background_color": "#111419",
    "panel_color": "#181C22",
    "field_color": "#14181E",
    "border_color": "#2A3039",
    "text_color": "#E8E5DE",
    "muted_text_color": "#929AA7",
    "accent_color": "#D79A52",
    "selection_color": "#3A3026",
    "hover_color": "#232933",
    # Writing preferences
    "auto_save": True,
    "auto_save_interval": 30,
    "show_line_numbers": False,
    "ai_notice_acknowledged": False,
    "recent_projects": [],
    "last_project": "",
}


def get_config_path() -> Path:
    """Return the user-editable config.json path."""
    return Path(__file__).resolve().parent.parent / "config.json"


def load_config() -> dict:
    """Load config.json and merge it over the defaults."""
    config = dict(DEFAULT_CONFIG)
    config_path = get_config_path()
    if config_path.exists():
        _merge_config(config, config_path)
    return config


def _merge_config(config: dict, path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            config.update(data)
    except (json.JSONDecodeError, OSError):
        pass


def save_config(config: dict) -> None:
    """Write config.json to the project root."""
    path = get_config_path()
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
