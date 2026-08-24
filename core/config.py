"""Application configuration and backwards-compatible migration helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copy import deepcopy

from .theme_tokens import DARK_COLORS, LIGHT_COLORS
from .storage import atomic_write_text

DEFAULT_CONFIG = {
    "dsh_command": "dsh",
    "dsh_launcher_args": [],
    "dsh_profile": "headless",
    "dsh_timeout": 600,
    "dsh_extra_args": [],
    # UI settings
    "theme": "light",
    "ui_language": "zh-CN",
    "ui_font_size": 14,
    "editor_font_size": 16,
    **LIGHT_COLORS,
    # Writing preferences
    "auto_save": True,
    "auto_save_interval": 30,
    "expand_target_chars": 2000,
    "ai_context_history_chapters": 5,
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
    config = deepcopy(DEFAULT_CONFIG)
    config_path = get_config_path()
    if config_path.exists():
        _merge_config(config, config_path)
    _normalize_config(config)
    return config


def normalize_config(config: dict) -> dict:
    """Return a validated copy suitable for runtime use and persistence."""
    normalized = deepcopy(config) if isinstance(config, dict) else {}
    _normalize_config(normalized)
    return normalized


def _merge_config(config: dict, path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            config.update(data)
            if "expand_target_chars" not in data and "continue_target_chars" in data:
                config["expand_target_chars"] = data["continue_target_chars"]
    except (json.JSONDecodeError, OSError):
        pass


def _normalize_config(config: dict[str, Any]) -> None:
    """Keep persisted settings inside the small set of supported values."""
    theme = config.get("theme")
    config["theme"] = theme if theme in {"light", "dark"} else "light"

    # Older releases used a beige light palette. Migrate that preset while
    # leaving genuinely customised colours untouched.
    old_light = {
        "background_color": "#F3F1EC",
        "panel_color": "#FAF9F6",
        "field_color": "#FFFDF9",
        "border_color": "#DCD7CE",
        "text_color": "#24272D",
        "muted_text_color": "#747B84",
        "accent_color": "#A8642A",
        "selection_color": "#E9DDCF",
        "hover_color": "#ECE8E1",
    }
    if config["theme"] == "light" and all(
        config.get(key) == value for key, value in old_light.items()
    ):
        config.update(LIGHT_COLORS)

    palette = LIGHT_COLORS if config["theme"] == "light" else DARK_COLORS
    for key, value in palette.items():
        candidate = config.get(key)
        if not isinstance(candidate, str) or not _is_hex_color(candidate):
            config[key] = value

    config["dsh_command"] = str(config.get("dsh_command") or "dsh").strip()
    config["dsh_profile"] = "headless"
    config["dsh_launcher_args"] = _string_list(config.get("dsh_launcher_args"))
    config["dsh_extra_args"] = _string_list(config.get("dsh_extra_args"))
    try:
        config["dsh_timeout"] = max(30, min(1800, int(config.get("dsh_timeout", 600))))
    except (TypeError, ValueError):
        config["dsh_timeout"] = 600
    try:
        config["ui_font_size"] = max(10, min(22, int(config.get("ui_font_size", 14))))
    except (TypeError, ValueError):
        config["ui_font_size"] = 14
    try:
        config["editor_font_size"] = max(12, min(36, int(config.get("editor_font_size", 16))))
    except (TypeError, ValueError):
        config["editor_font_size"] = 16
    try:
        config["auto_save_interval"] = max(5, min(600, int(config.get("auto_save_interval", 30))))
    except (TypeError, ValueError):
        config["auto_save_interval"] = 30
    # Migrate the old setting name without requiring users to edit config.json.
    if "expand_target_chars" not in config and "continue_target_chars" in config:
        config["expand_target_chars"] = config["continue_target_chars"]
    try:
        config["expand_target_chars"] = max(
            300, min(10000, int(config.get("expand_target_chars", 2000)))
        )
    except (TypeError, ValueError):
        config["expand_target_chars"] = 2000
    try:
        config["ai_context_history_chapters"] = max(
            0, min(10, int(config.get("ai_context_history_chapters", 5)))
        )
    except (TypeError, ValueError):
        config["ai_context_history_chapters"] = 5
    config.pop("continue_target_chars", None)
    config["auto_save"] = bool(config.get("auto_save", True))
    config["ai_notice_acknowledged"] = bool(config.get("ai_notice_acknowledged", False))
    config["recent_projects"] = _string_list(config.get("recent_projects"))
    config["last_project"] = str(config.get("last_project") or "")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _is_hex_color(value: str) -> bool:
    if len(value) not in {4, 7} or not value.startswith("#"):
        return False
    digits = value[1:]
    return all(char in "0123456789abcdefABCDEF" for char in digits)


def save_config(config: dict) -> None:
    """Write config.json to the project root."""
    path = get_config_path()
    atomic_write_text(
        path,
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
