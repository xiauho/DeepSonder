"""Transactional, versioned migrations for author-owned project data."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import uuid4

from .foreshadowing import (
    FORESHADOWING_VERSION,
    ForeshadowingStore,
    legacy_notes_from_story_state,
)
from .project import (
    DEFAULT_CORE_POWER_RULES,
    DEFAULT_STORY_STATE,
    NovelProject,
)
from .project_data import ProjectDataStore
from .project_schema import (
    PROJECT_MANIFEST_RELATIVE_PATH,
    PROJECT_MIGRATION_JOURNAL_RELATIVE_PATH,
    PROJECT_SCHEMA_VERSION,
    project_manifest,
)
from .storage import atomic_write_text


class ProjectMigrationError(RuntimeError):
    """Raised when a project cannot be migrated without risking author data."""


_V0_TO_V1_ALLOWED_PATHS = frozenset(
    {
        "writing/style_guide.md",
        "canon/power/_核心规则.md",
        "canon/system_registry.json",
        "memory/story_state.json",
        "memory/chapter_summaries.json",
        "memory/foreshadowing.json",
        PROJECT_MANIFEST_RELATIVE_PATH.as_posix(),
    }
)


@dataclass(frozen=True)
class ProjectMigrationPlan:
    root: Path
    from_schema: int
    to_schema: int
    changed_files: tuple[str, ...]

    @property
    def required(self) -> bool:
        return self.from_schema < self.to_schema


@dataclass(frozen=True)
class ProjectMigrationResult:
    from_schema: int
    to_schema: int
    changed_files: tuple[str, ...] = ()
    backup_path: Path | None = None
    recovered_interrupted_migration: bool = False

    @property
    def migrated(self) -> bool:
        return self.from_schema < self.to_schema


def inspect_project_schema(root: Path) -> int:
    """Return the on-disk schema without modifying the project."""
    project_root = Path(root).expanduser().resolve()
    manifest_path = project_root / PROJECT_MANIFEST_RELATIVE_PATH
    if not manifest_path.exists():
        return 0
    data = _read_json_object(manifest_path, "项目格式清单")
    version = data.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ProjectMigrationError("项目格式清单缺少有效的 schema_version。")
    if version > PROJECT_SCHEMA_VERSION:
        raise ProjectMigrationError(
            f"项目格式版本 {version} 高于当前程序支持的 {PROJECT_SCHEMA_VERSION}，"
            "请使用更新版本的 DeepSonder-PySide6 打开。"
        )
    return version


def plan_project_migration(root: Path) -> ProjectMigrationPlan:
    """Build a read-only migration plan for one project."""
    project_root = _validate_project_root(root)
    from_schema = inspect_project_schema(project_root)
    if from_schema == PROJECT_SCHEMA_VERSION:
        _validate_current_project(project_root)
        return ProjectMigrationPlan(
            project_root,
            from_schema,
            PROJECT_SCHEMA_VERSION,
            (),
        )
    payloads = _migration_payloads_v0_to_v1(project_root)
    return ProjectMigrationPlan(
        project_root,
        from_schema,
        PROJECT_SCHEMA_VERSION,
        tuple(_relative_text(project_root, path) for path in payloads),
    )


def migrate_project(root: Path) -> ProjectMigrationResult:
    """Migrate a project through the supported chain with backup and recovery."""
    project_root = _validate_project_root(root)
    recovered = _recover_interrupted_migration(project_root)
    from_schema = inspect_project_schema(project_root)
    if from_schema == PROJECT_SCHEMA_VERSION:
        _validate_current_project(project_root)
        return ProjectMigrationResult(
            from_schema,
            PROJECT_SCHEMA_VERSION,
            recovered_interrupted_migration=recovered,
        )
    if from_schema != 0:
        raise ProjectMigrationError(
            f"缺少从项目格式 {from_schema} 到 {PROJECT_SCHEMA_VERSION} 的迁移步骤。"
        )

    payloads = _migration_payloads_v0_to_v1(project_root)
    changed_files = tuple(_relative_text(project_root, path) for path in payloads)
    output_bytes = sum(len(text.encode("utf-8")) for text in payloads.values())
    try:
        backup_bytes = sum(path.stat().st_size for path in payloads if path.exists())
    except OSError as exc:
        raise ProjectMigrationError("无法读取待迁移文件的大小。") from exc
    required_space = backup_bytes + output_bytes * 2
    try:
        free_space = shutil.disk_usage(project_root).free
    except OSError as exc:
        raise ProjectMigrationError("无法检查项目所在磁盘的可用空间。") from exc
    if free_space < max(required_space, 1_000_000):
        raise ProjectMigrationError("磁盘空间不足，无法安全创建项目迁移备份。")

    backup_path, backed_up, created = _create_backup(project_root, payloads)
    journal_path = project_root / PROJECT_MIGRATION_JOURNAL_RELATIVE_PATH
    journal = {
        "schema_version": 1,
        "from_schema": from_schema,
        "to_schema": PROJECT_SCHEMA_VERSION,
        "backup_path": _relative_text(project_root, backup_path),
        "backed_up": list(backed_up),
        "created": list(created),
    }
    try:
        atomic_write_text(
            journal_path,
            json.dumps(journal, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise ProjectMigrationError(
            "无法创建项目迁移日志，尚未修改项目数据。"
        ) from exc
    try:
        for path, text in payloads.items():
            atomic_write_text(path, text, encoding="utf-8")
        if inspect_project_schema(project_root) != PROJECT_SCHEMA_VERSION:
            raise ProjectMigrationError("项目迁移完成后格式版本验证失败。")
        _validate_current_project(project_root)
    except Exception as exc:
        try:
            _restore_backup(project_root, backup_path, backed_up, created)
            journal_path.unlink(missing_ok=True)
        except Exception as rollback_exc:
            raise ProjectMigrationError(
                f"项目迁移失败，且自动恢复未完成。备份位于：{backup_path}"
            ) from rollback_exc
        if isinstance(exc, ProjectMigrationError):
            raise
        raise ProjectMigrationError("项目迁移失败，已恢复迁移前数据。") from exc
    journal_path.unlink(missing_ok=True)
    return ProjectMigrationResult(
        from_schema,
        PROJECT_SCHEMA_VERSION,
        changed_files,
        backup_path,
        recovered_interrupted_migration=recovered,
    )


def _migration_payloads_v0_to_v1(root: Path) -> dict[Path, str]:
    project = NovelProject(root)
    store = ProjectDataStore(project)
    payloads: dict[Path, str] = {}

    if not project.core_power_path.exists():
        payloads[project.core_power_path] = DEFAULT_CORE_POWER_RULES

    state_path = project.memory_dir / "story_state.json"
    if state_path.exists():
        state = _read_json_object(state_path, "故事状态")
    else:
        state = deepcopy(DEFAULT_STORY_STATE)
    legacy_notes = legacy_notes_from_story_state(state)
    if "foreshadowing" in state:
        state = dict(state)
        state.pop("foreshadowing", None)
        payloads[state_path] = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    elif not state_path.exists():
        payloads[state_path] = json.dumps(state, ensure_ascii=False, indent=2) + "\n"

    summaries_path = project.memory_dir / "chapter_summaries.json"
    if not summaries_path.exists():
        payloads[summaries_path] = "{}\n"
    else:
        _read_json_object(summaries_path, "章节摘要")

    foreshadowing_path = project.memory_dir / "foreshadowing.json"
    if foreshadowing_path.exists():
        _validate_foreshadowing(project)
    else:
        payloads[foreshadowing_path] = json.dumps(
            {"version": FORESHADOWING_VERSION, "items": legacy_notes},
            ensure_ascii=False,
            indent=2,
        ) + "\n"

    registry_path = project.system_registry_path
    raw_registry: dict = {}
    if registry_path.exists():
        raw_registry = _read_json_object(registry_path, "体系注册表")
        registry_version = raw_registry.get("version", 1)
        if registry_version != 1:
            raise ProjectMigrationError("体系注册表版本不受支持。")
        if not isinstance(raw_registry.get("entries", {}), dict):
            raise ProjectMigrationError("体系注册表的 entries 必须是对象。")
    normalized_registry = store.load_system_registry()
    if not registry_path.exists() or raw_registry != normalized_registry:
        payloads[registry_path] = (
            json.dumps(normalized_registry, ensure_ascii=False, indent=2) + "\n"
        )

    manifest_path = root / PROJECT_MANIFEST_RELATIVE_PATH
    payloads[manifest_path] = json.dumps(
        project_manifest(created_by="legacy", migrated_from=0),
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    return payloads


def _validate_current_project(root: Path) -> None:
    """Validate schema-owned files without repairing or rewriting them."""
    project = NovelProject(root)
    required_files = (
        project.core_power_path,
        project.memory_dir / "story_state.json",
        project.memory_dir / "chapter_summaries.json",
        project.memory_dir / "foreshadowing.json",
        project.system_registry_path,
    )
    missing = [path for path in required_files if not path.is_file()]
    if missing:
        relative = _relative_text(root, missing[0])
        raise ProjectMigrationError(f"当前项目格式缺少必需文件：{relative}")
    _read_json_object(project.memory_dir / "story_state.json", "故事状态")
    _read_json_object(project.memory_dir / "chapter_summaries.json", "章节摘要")
    _validate_foreshadowing(project)
    registry = _read_json_object(project.system_registry_path, "体系注册表")
    if registry.get("version") != 1 or not isinstance(registry.get("entries"), dict):
        raise ProjectMigrationError("体系注册表格式或版本无效。")


def _validate_foreshadowing(project: NovelProject) -> None:
    path = project.memory_dir / "foreshadowing.json"
    data = _read_json_object(path, "伏笔笔记")
    if data.get("version") != FORESHADOWING_VERSION:
        raise ProjectMigrationError("伏笔笔记版本不受支持。")
    try:
        ForeshadowingStore(project).load_notes()
    except ValueError as exc:
        raise ProjectMigrationError(str(exc)) from exc


def _create_backup(
    root: Path,
    payloads: dict[Path, str],
) -> tuple[Path, tuple[str, ...], tuple[str, ...]]:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = (
        root
        / ".novalist"
        / "backups"
        / f"migration-v0-v{PROJECT_SCHEMA_VERSION}-{timestamp}-{uuid4().hex[:8]}"
    )
    backed_up: list[str] = []
    created: list[str] = []
    try:
        backup_path.mkdir(parents=True, exist_ok=False)
        for path in payloads:
            relative = Path(_relative_text(root, path))
            if path.exists():
                destination = backup_path / "files" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                backed_up.append(relative.as_posix())
            else:
                created.append(relative.as_posix())
        atomic_write_text(
            backup_path / "backup-manifest.json",
            json.dumps(
                {"schema_version": 1, "backed_up": backed_up, "created": created},
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        raise ProjectMigrationError("无法创建项目迁移备份。") from exc
    return backup_path, tuple(backed_up), tuple(created)


def _recover_interrupted_migration(root: Path) -> bool:
    journal_path = root / PROJECT_MIGRATION_JOURNAL_RELATIVE_PATH
    if not journal_path.exists():
        return False
    journal = _read_json_object(journal_path, "项目迁移日志")
    if (
        journal.get("schema_version") != 1
        or journal.get("from_schema") != 0
        or journal.get("to_schema") != PROJECT_SCHEMA_VERSION
    ):
        raise ProjectMigrationError("项目迁移日志的版本链无效。")
    backup_path = _resolve_relative(root, journal.get("backup_path"), "迁移备份路径")
    backups_root = (root / ".novalist" / "backups").resolve()
    if backups_root not in backup_path.parents:
        raise ProjectMigrationError("项目迁移日志中的备份路径越界。")
    backed_up = _string_tuple(journal.get("backed_up"), "backed_up")
    created = _string_tuple(journal.get("created"), "created")
    if (
        set(backed_up) & set(created)
        or not set(backed_up).issubset(_V0_TO_V1_ALLOWED_PATHS)
        or not set(created).issubset(_V0_TO_V1_ALLOWED_PATHS)
    ):
        raise ProjectMigrationError("项目迁移日志包含不允许修改的文件。")
    backup_manifest = _read_json_object(
        backup_path / "backup-manifest.json",
        "迁移备份清单",
    )
    if (
        tuple(backup_manifest.get("backed_up", ())) != backed_up
        or tuple(backup_manifest.get("created", ())) != created
    ):
        raise ProjectMigrationError("迁移日志与备份清单不一致。")
    _restore_backup(root, backup_path, backed_up, created)
    journal_path.unlink(missing_ok=True)
    return True


def _restore_backup(
    root: Path,
    backup_path: Path,
    backed_up: tuple[str, ...],
    created: tuple[str, ...],
) -> None:
    for relative in created:
        target = _resolve_relative(root, relative, "新建文件路径")
        if target.is_file():
            target.unlink()
    for relative in backed_up:
        target = _resolve_relative(root, relative, "备份文件路径")
        source = _resolve_relative(backup_path / "files", relative, "备份来源路径")
        if not source.is_file():
            raise ProjectMigrationError(f"迁移备份缺少文件：{relative}")
        atomic_write_text(target, source.read_text(encoding="utf-8"), encoding="utf-8")


def _validate_project_root(root: Path) -> Path:
    project_root = Path(root).expanduser().resolve()
    if not (project_root / "project.json").is_file():
        raise ProjectMigrationError("目录中缺少 project.json，不是有效项目。")
    _read_json_object(project_root / "project.json", "项目元数据")
    return project_root


def _read_json_object(path: Path, label: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectMigrationError(f"{label}无法安全读取：{path}") from exc
    if not isinstance(data, dict):
        raise ProjectMigrationError(f"{label}必须是 JSON 对象：{path}")
    return data


def _relative_text(root: Path, path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError as exc:
        raise ProjectMigrationError(f"迁移目标不在项目目录内：{path}") from exc


def _resolve_relative(root: Path, value: object, label: str) -> Path:
    raw = str(value or "").strip()
    relative = Path(raw)
    if not raw or relative.is_absolute() or ".." in relative.parts:
        raise ProjectMigrationError(f"{label}无效。")
    target = (Path(root).resolve() / relative).resolve()
    if Path(root).resolve() not in target.parents:
        raise ProjectMigrationError(f"{label}越界。")
    return target


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ProjectMigrationError(f"项目迁移日志中的 {label} 无效。")
    return tuple(value)
