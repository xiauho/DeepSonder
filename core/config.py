"""Application configuration and backwards-compatible migration helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copy import deepcopy

from .app_paths import app_config_dir, legacy_application_root, update_cache_dir
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
    # Update preferences and non-sensitive check metadata
    "update_channel": "beta",
    "auto_check_updates": False,
    "last_update_check_at": "",
    "skipped_update_version": "",
    "recent_projects": [],
    "last_project": "",
}


def get_config_path() -> Path:
    """Return the user-editable config.json path."""
    return app_config_dir() / "config.json"


def get_legacy_config_path() -> Path:
    """Return the pre-migration config path beside the application source."""
    return legacy_application_root() / "config.json"


def get_update_cache_path() -> Path:
    """Return the directory reserved for future update downloads."""
    return update_cache_dir()


def load_config() -> dict:
    """Load user settings, migrating a legacy root config when necessary."""
    config = deepcopy(DEFAULT_CONFIG)
    config_path = get_config_path()
    if config_path.exists():
        _merge_config(config, config_path)
        _normalize_config(config)
        return config

    legacy_path = get_legacy_config_path()
    migrated = legacy_path != config_path and _merge_config(config, legacy_path)
    _normalize_config(config)
    if migrated:
        try:
            _write_config(config_path, config)
        except OSError:
            # A read-only or unavailable user-data directory must not prevent
            # the application from starting with the successfully loaded data.
            pass
    return config


def normalize_config(config: dict) -> dict:
    """Return a validated copy suitable for runtime use and persistence."""
    normalized = deepcopy(config) if isinstance(config, dict) else {}
    _normalize_config(normalized)
    return normalized


def _merge_config(config: dict, path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            config.update(data)
            if "expand_target_chars" not in data and "continue_target_chars" in data:
                config["expand_target_chars"] = data["continue_target_chars"]
            return True
    except (json.JSONDecodeError, OSError):
        pass
    return False


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
    update_channel = str(config.get("update_channel") or "beta").strip().lower()
    config["update_channel"] = (
        update_channel if update_channel in {"stable", "beta"} else "beta"
    )
    config["auto_check_updates"] = bool(config.get("auto_check_updates", False))
    config["last_update_check_at"] = str(
        config.get("last_update_check_at") or ""
    ).strip()
    config["skipped_update_version"] = str(
        config.get("skipped_update_version") or ""
    ).strip()
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
    """Write config.json to the per-user application-data directory."""
    _write_config(get_config_path(), config)


def _write_config(path: Path, config: dict) -> None:
    atomic_write_text(
        path,
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
