"""Versioned application configuration loading and migration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from copy import deepcopy

from .app_paths import app_config_dir
from .theme_tokens import DARK_COLORS, LIGHT_COLORS
from .storage import atomic_write_text
from .token_budget import (
    DEFAULT_CHUNK_OVERLAP_TOKENS,
    DEFAULT_CHUNK_TOKEN_BUDGET,
    DEFAULT_INPUT_TOKEN_BUDGET,
    DEFAULT_RUNTIME_RESERVE_TOKENS,
)
from .context_capacity import (
    DEFAULT_CONTEXT_STRATEGY,
    DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS,
    MAX_TASK_FILE_BYTES,
    MIN_FILE_PROMPT_CHARS,
    derive_context_capacity,
    normalize_context_strategy,
    normalize_model_context_window,
)

AI_CONTEXT_HISTORY_CHAPTERS_MAX = 200
CHAPTER_TARGET_CHARS_DEFAULT = 3000
CHAPTER_TARGET_CHARS_MIN = 300
CHAPTER_TARGET_CHARS_MAX = 10_000
CONFIG_SCHEMA_VERSION = 6
DSH_FILE_PROMPT_BUDGET_MIN = MIN_FILE_PROMPT_CHARS
DSH_FILE_PROMPT_BUDGET_MAX = 300_000
_DEFAULT_CONTEXT_CAPACITY = derive_context_capacity(
    DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS,
    DEFAULT_CONTEXT_STRATEGY,
)
DSH_FILE_PROMPT_BUDGET_DEFAULT = _DEFAULT_CONTEXT_CAPACITY.prompt_char_budget
DSH_TASK_FILE_MAX_BYTES_MIN = 64_000
DSH_TASK_FILE_MAX_BYTES_MAX = MAX_TASK_FILE_BYTES
DSH_TASK_FILE_MAX_BYTES_DEFAULT = _DEFAULT_CONTEXT_CAPACITY.task_file_max_bytes

DEFAULT_CONFIG = {
    "config_schema_version": CONFIG_SCHEMA_VERSION,
    "dsh_command": "dsh",
    "dsh_launcher_args": [],
    "dsh_profile": "headless",
    "dsh_timeout": 600,
    "dsh_extra_args": [],
    "ai_model_context_window_tokens": DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS,
    "ai_context_strategy": DEFAULT_CONTEXT_STRATEGY,
    "dsh_file_prompt_budget": DSH_FILE_PROMPT_BUDGET_DEFAULT,
    "dsh_task_file_max_bytes": DSH_TASK_FILE_MAX_BYTES_DEFAULT,
    "ai_input_token_budget": _DEFAULT_CONTEXT_CAPACITY.input_token_budget,
    "ai_runtime_reserve_tokens": _DEFAULT_CONTEXT_CAPACITY.runtime_reserve_tokens,
    "ai_chunk_token_budget": DEFAULT_CHUNK_TOKEN_BUDGET,
    "ai_chunk_overlap_tokens": DEFAULT_CHUNK_OVERLAP_TOKENS,
    # UI settings
    "theme": "light",
    "ui_language": "zh-CN",
    "ui_font_size": 14,
    "editor_font_size": 16,
    **LIGHT_COLORS,
    # Writing preferences
    "auto_save": True,
    "auto_save_interval": 30,
    "chapter_target_chars": CHAPTER_TARGET_CHARS_DEFAULT,
    "ai_context_history_chapters": 5,
    "ai_history_mode": "auto",
    "ai_history_remote_enabled": True,
    "show_line_numbers": False,
    "ai_notice_acknowledged": False,
    "recent_projects": [],
    "last_project": "",
}


def get_config_path() -> Path:
    """Return the user-editable config.json path."""
    return app_config_dir() / "config.json"


def load_config() -> dict:
    """Load settings from the isolated DeepSonder-PySide6 profile."""
    config = deepcopy(DEFAULT_CONFIG)
    config_path = get_config_path()
    if config_path.exists():
        loaded = _merge_config(config, config_path)
        needs_schema_write = config.get("config_schema_version") != CONFIG_SCHEMA_VERSION
        _normalize_config(config)
        if loaded and needs_schema_write:
            try:
                _write_config(config_path, config)
            except OSError:
                pass
        return config

    _normalize_config(config)
    return config


def normalize_config(config: dict) -> dict:
    """Return a validated copy suitable for runtime use and persistence."""
    normalized = deepcopy(config) if isinstance(config, dict) else {}
    _normalize_config(normalized)
    return normalized


def get_chapter_target_chars(config: dict | None) -> int:
    """Return the validated chapter target from the application settings.

    This is the only runtime accessor for the writing target.  Business
    workflows receive its returned value explicitly instead of carrying their
    own fallback defaults.
    """
    value = (
        config.get("chapter_target_chars", CHAPTER_TARGET_CHARS_DEFAULT)
        if isinstance(config, dict)
        else CHAPTER_TARGET_CHARS_DEFAULT
    )
    try:
        return max(
            CHAPTER_TARGET_CHARS_MIN,
            min(CHAPTER_TARGET_CHARS_MAX, int(value)),
        )
    except (TypeError, ValueError):
        return CHAPTER_TARGET_CHARS_DEFAULT


def _merge_config(config: dict, path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            config.update(data)
            if "ai_history_mode" not in data:
                config["ai_history_mode"] = (
                    "custom" if "ai_context_history_chapters" in data else "auto"
                )
            if "config_schema_version" not in data:
                config["config_schema_version"] = 0
            # Defaults are merged before persisted values. Preserve the raw
            # key-presence rule needed by the v0 -> v1 rename.
            if "chapter_target_chars" not in data:
                if "expand_target_chars" in data:
                    config["chapter_target_chars"] = data["expand_target_chars"]
                elif "continue_target_chars" in data:
                    config["chapter_target_chars"] = data["continue_target_chars"]
            return True
    except (json.JSONDecodeError, OSError):
        pass
    return False


def _normalize_config(config: dict[str, Any]) -> None:
    """Keep persisted settings inside the small set of supported values."""
    raw_schema = config.get("config_schema_version", 0)
    try:
        schema = int(raw_schema)
    except (TypeError, ValueError):
        schema = 0
    if schema > CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"配置格式版本 {schema} 高于当前程序支持的 {CONFIG_SCHEMA_VERSION}。"
        )
    schema = _migrate_config_schema(config, schema)
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
    model_window = normalize_model_context_window(
        config.get("ai_model_context_window_tokens")
    )
    context_strategy = normalize_context_strategy(config.get("ai_context_strategy"))
    capacity = derive_context_capacity(model_window, context_strategy)
    config["ai_model_context_window_tokens"] = capacity.model_context_window_tokens
    config["ai_context_strategy"] = capacity.strategy
    # These remain persisted for diagnostics and compatibility, but are never
    # independent sources of truth after schema v3.
    config["dsh_file_prompt_budget"] = capacity.prompt_char_budget
    config["dsh_task_file_max_bytes"] = capacity.task_file_max_bytes
    config["ai_input_token_budget"] = capacity.input_token_budget
    config["ai_runtime_reserve_tokens"] = capacity.runtime_reserve_tokens
    config["ai_chunk_token_budget"] = _bounded_int(
        config.get("ai_chunk_token_budget"),
        default=DEFAULT_CHUNK_TOKEN_BUDGET,
        minimum=1_000,
        maximum=16_000,
    )
    config["ai_chunk_overlap_tokens"] = _bounded_int(
        config.get("ai_chunk_overlap_tokens"),
        default=DEFAULT_CHUNK_OVERLAP_TOKENS,
        minimum=0,
        maximum=min(2_000, config["ai_chunk_token_budget"] // 3),
    )
    # Legacy transport and AI-pipeline switches have been retired. Business
    # prompts always use a task file; argv carries only the short loader.
    config.pop("dsh_prompt_transport", None)
    config.pop("ai_memory_pipeline", None)
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
    # Migrate both historical names without requiring users to edit config.json.
    if "chapter_target_chars" not in config:
        if "expand_target_chars" in config:
            config["chapter_target_chars"] = config["expand_target_chars"]
        elif "continue_target_chars" in config:
            config["chapter_target_chars"] = config["continue_target_chars"]
    config["chapter_target_chars"] = get_chapter_target_chars(config)
    try:
        config["ai_context_history_chapters"] = max(
            0,
            min(
                AI_CONTEXT_HISTORY_CHAPTERS_MAX,
                int(config.get("ai_context_history_chapters", 5)),
            ),
        )
    except (TypeError, ValueError):
        config["ai_context_history_chapters"] = 5
    if config.get("ai_history_mode") not in {"auto", "custom"}:
        config["ai_history_mode"] = "auto"
    config["ai_history_remote_enabled"] = config.get("ai_history_remote_enabled", True) is not False
    config.pop("ai_context_selection_mode", None)
    config.pop("continue_target_chars", None)
    config.pop("expand_target_chars", None)
    config["auto_save"] = bool(config.get("auto_save", True))
    config["ai_notice_acknowledged"] = bool(config.get("ai_notice_acknowledged", False))
    for retired_key in (
        "update_channel",
        "auto_check_updates",
        "last_update_check_at",
        "skipped_update_version",
    ):
        config.pop(retired_key, None)
    config["recent_projects"] = _string_list(config.get("recent_projects"))
    config["last_project"] = str(config.get("last_project") or "")
    config["config_schema_version"] = CONFIG_SCHEMA_VERSION


def _migrate_config_schema(config: dict[str, Any], schema: int) -> int:
    """Apply explicit, idempotent config migrations one version at a time."""
    while schema < CONFIG_SCHEMA_VERSION:
        if schema == 0:
            if (
                "expand_target_chars" not in config
                and "continue_target_chars" in config
            ):
                config["expand_target_chars"] = config["continue_target_chars"]
            config.pop("ai_memory_pipeline", None)
            config.pop("ai_context_selection_mode", None)
            config.pop("continue_target_chars", None)
            schema = 1
            continue
        if schema == 1:
            config.pop("dsh_prompt_transport", None)
            schema = 2
            continue
        if schema == 2:
            # Old builds exposed independent character/token/file ceilings.
            # Preserve their safe behaviour as "unknown" until the user
            # explicitly declares the capacity configured in DSH.
            config.setdefault(
                "ai_model_context_window_tokens",
                DEFAULT_MODEL_CONTEXT_WINDOW_TOKENS,
            )
            config.setdefault("ai_context_strategy", DEFAULT_CONTEXT_STRATEGY)
            schema = 3
            continue
        if schema == 3:
            config.setdefault(
                "ai_history_mode",
                "custom" if "ai_context_history_chapters" in config else "auto",
            )
            schema = 4
            continue
        if schema == 4:
            config.setdefault("ai_history_remote_enabled", True)
            schema = 5
            continue
        if schema == 5:
            config.setdefault(
                "chapter_target_chars",
                config.get(
                    "expand_target_chars",
                    config.get("continue_target_chars", CHAPTER_TARGET_CHARS_DEFAULT),
                ),
            )
            config.pop("expand_target_chars", None)
            config.pop("continue_target_chars", None)
            schema = 6
            continue
        raise ValueError(
            f"缺少从配置格式 {schema} 到 {schema + 1} 的迁移步骤。"
        )
    config["config_schema_version"] = schema
    return schema


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        return max(minimum, min(maximum, int(value)))
    except (TypeError, ValueError):
        return default


def _is_hex_color(value: str) -> bool:
    if len(value) not in {4, 7} or not value.startswith("#"):
        return False
    digits = value[1:]
    return all(char in "0123456789abcdefABCDEF" for char in digits)


def save_config(config: dict) -> None:
    """Write config.json to the per-user application-data directory."""
    _write_config(get_config_path(), normalize_config(config))


def _write_config(path: Path, config: dict) -> None:
    atomic_write_text(
        path,
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
