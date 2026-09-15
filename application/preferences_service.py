"""Application preferences shared by the PySide6 and Electron frontends."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.config import load_config, normalize_config, save_config

from .project_service import ProjectService


@dataclass(frozen=True)
class PreferencesSnapshot:
    theme: str
    ui_font_size: int
    editor_font_size: int
    auto_save: bool
    auto_save_interval: int
    show_line_numbers: bool
    last_project: str
    recent_projects: tuple[str, ...]


class PreferencesService:
    """Expose a deliberately small, non-sensitive configuration surface."""

    EDITABLE_FIELDS = {
        "theme",
        "ui_font_size",
        "editor_font_size",
        "auto_save",
        "auto_save_interval",
        "show_line_numbers",
    }

    def __init__(
        self,
        *,
        config_loader: Callable[[], dict] = load_config,
        config_saver: Callable[[dict], None] = save_config,
        project_service: ProjectService | None = None,
    ) -> None:
        self._config_loader = config_loader
        self._config_saver = config_saver
        self._project_service = project_service or ProjectService()

    def snapshot(self) -> PreferencesSnapshot:
        config = normalize_config(deepcopy(self._config_loader()))
        recent = self._project_service.recent_projects(config)
        if recent.changed:
            self._config_saver(config)
        return self._snapshot(config, tuple(str(path) for path in recent.paths))

    def update(self, patch: dict) -> PreferencesSnapshot:
        unknown = set(patch) - self.EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"包含不可修改的设置项：{sorted(unknown)[0]}")
        self._validate_patch(patch)
        config = normalize_config(deepcopy(self._config_loader()))
        for key, value in patch.items():
            config[key] = value
        config = normalize_config(config)
        self._config_saver(config)
        recent = self._project_service.recent_projects(config)
        return self._snapshot(config, tuple(str(path) for path in recent.paths))

    def remember_project(self, path: Path | str) -> PreferencesSnapshot:
        config = normalize_config(deepcopy(self._config_loader()))
        self._project_service.remember_project(config, path)
        self._config_saver(config)
        recent = self._project_service.recent_projects(config)
        return self._snapshot(config, tuple(str(item) for item in recent.paths))

    def last_project(self) -> Path | None:
        config = normalize_config(deepcopy(self._config_loader()))
        return self._project_service.safe_project_path(config.get("last_project"))

    @staticmethod
    def _validate_patch(patch: dict) -> None:
        if "theme" in patch and (
            not isinstance(patch["theme"], str)
            or patch["theme"] not in {"light", "dark"}
        ):
            raise ValueError("theme 必须是 light 或 dark")
        for key in ("ui_font_size", "editor_font_size", "auto_save_interval"):
            if key in patch and type(patch[key]) is not int:
                raise ValueError(f"{key} 必须是整数")
        for key in ("auto_save", "show_line_numbers"):
            if key in patch and type(patch[key]) is not bool:
                raise ValueError(f"{key} 必须是布尔值")

    @staticmethod
    def _snapshot(config: dict, recent: tuple[str, ...]) -> PreferencesSnapshot:
        return PreferencesSnapshot(
            theme=str(config["theme"]),
            ui_font_size=int(config["ui_font_size"]),
            editor_font_size=int(config["editor_font_size"]),
            auto_save=bool(config["auto_save"]),
            auto_save_interval=int(config["auto_save_interval"]),
            show_line_numbers=bool(config["show_line_numbers"]),
            last_project=str(config.get("last_project") or ""),
            recent_projects=recent,
        )
