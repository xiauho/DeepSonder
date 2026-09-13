"""Prepare and launch the standalone updater from the desktop application."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from .app_paths import update_cache_dir
from .update_download_service import VerifiedUpdate, inspect_update_archive
from .update_installer import (
    MINIMUM_TRANSACTION_FREE_BYTES,
    PACKAGE_MANIFEST_NAME,
    parse_package_manifest,
    validate_update_archive,
)
from .version import AppVersion


UPDATER_EXECUTABLE_NAME = "NovalistUpdater.exe"


class UpdateInstallLaunchError(RuntimeError):
    """The verified update could not be handed to the external updater."""


@dataclass(frozen=True)
class UpdateInstallLaunch:
    target_version: AppVersion
    request_path: Path
    helper_path: Path
    process_id: int


def automatic_install_unavailable_reason(
    *,
    install_dir: Path | None = None,
    platform: str | None = None,
    frozen: bool | None = None,
) -> str:
    """Return an explanation when this installation cannot replace itself."""
    current_platform = sys.platform if platform is None else platform
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if current_platform != "win32":
        return "自动安装目前仅支持 Windows x64 便携版。"
    if not is_frozen:
        return "源码工作区不会自动替换自身，请使用打包后的便携版验证。"
    root = Path(install_dir) if install_dir is not None else Path(sys.executable).parent
    if not (root / UPDATER_EXECUTABLE_NAME).is_file():
        return "当前安装缺少独立更新器，请手动解压升级。"
    if not (root / PACKAGE_MANIFEST_NAME).is_file():
        return "当前安装缺少受管文件清单，请手动解压升级。"
    if (root / UPDATER_EXECUTABLE_NAME).is_symlink() or (
        root / PACKAGE_MANIFEST_NAME
    ).is_symlink():
        return "当前安装包含不受支持的链接文件，请手动解压升级。"
    if not os.access(root, os.W_OK):
        return "当前安装目录不可写，请移动到可写目录或手动升级。"
    return ""


def launch_verified_update_install(
    verified: VerifiedUpdate,
    current_version: AppVersion,
    *,
    install_dir: Path | None = None,
    cache_root: Path | None = None,
    platform: str | None = None,
    frozen: bool | None = None,
    current_pid: int | None = None,
    process_factory: Callable = subprocess.Popen,
) -> UpdateInstallLaunch:
    """Copy the installed helper to cache, write a request, and detach it."""
    root = (
        Path(install_dir).resolve()
        if install_dir is not None
        else Path(sys.executable).resolve().parent
    )
    reason = automatic_install_unavailable_reason(
        install_dir=root,
        platform=platform,
        frozen=frozen,
    )
    if reason:
        raise UpdateInstallLaunchError(reason)
    if verified.manifest.version <= current_version:
        raise UpdateInstallLaunchError("自动安装仅接受高于当前版本的更新。")
    if verified.release.version != verified.manifest.version:
        raise UpdateInstallLaunchError("下载结果中的版本信息不一致。")
    archive_path = verified.archive_path.resolve()
    allowed_cache = Path(cache_root or update_cache_dir()).resolve()
    if not archive_path.is_relative_to(allowed_cache):
        raise UpdateInstallLaunchError("更新包不在 Novalist 更新缓存中。")
    if root == allowed_cache or root.is_relative_to(allowed_cache):
        raise UpdateInstallLaunchError("安装目录不能位于更新缓存中。")
    state_path = archive_path.parent / "verified-update.json"
    if not state_path.is_file():
        raise UpdateInstallLaunchError("更新包缺少独立验证状态。")

    # Re-run the archive checks immediately before handing control to a new
    # process. The helper independently repeats these checks after the app exits.
    try:
        inspect_update_archive(archive_path, verified.manifest)
        target_manifest = validate_update_archive(
            archive_path,
            verified.manifest.version,
        )
    except RuntimeError as exc:
        raise UpdateInstallLaunchError(
            "更新包在安装前复核失败，请重新下载。"
        ) from exc
    try:
        current_manifest = parse_package_manifest(
            (root / PACKAGE_MANIFEST_NAME).read_bytes(),
            expected_version=current_version,
        )
    except (OSError, RuntimeError) as exc:
        raise UpdateInstallLaunchError("当前安装的受管文件清单无效。") from exc
    if current_manifest.version != current_version:
        raise UpdateInstallLaunchError("当前安装版本与受管文件清单不一致。")

    source_helper = root / UPDATER_EXECUTABLE_NAME
    helper_entry = current_manifest.by_path.get(UPDATER_EXECUTABLE_NAME.casefold())
    try:
        helper_is_valid = (
            helper_entry is not None
            and source_helper.stat().st_size == helper_entry.size
            and _hash_file(source_helper) == helper_entry.sha256
        )
    except OSError as exc:
        raise UpdateInstallLaunchError("无法校验当前安装的独立更新器。") from exc
    if not helper_is_valid:
        raise UpdateInstallLaunchError("当前安装的独立更新器未通过受管文件校验。")

    required_space = (
        sum(item.size for item in current_manifest.files)
        + sum(item.size for item in target_manifest.files)
        + MINIMUM_TRANSACTION_FREE_BYTES
    )
    try:
        available_space = shutil.disk_usage(root).free
    except OSError as exc:
        raise UpdateInstallLaunchError("无法确认安装盘剩余空间。") from exc
    if available_space < required_space:
        raise UpdateInstallLaunchError(
            "安装盘空间不足，无法安全暂存并备份更新。"
        )

    transaction_id = uuid4().hex
    transaction_root = archive_path.parent / f"install-{transaction_id}"
    try:
        transaction_root.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        raise UpdateInstallLaunchError("无法创建自动安装事务目录。") from exc
    helper_copy = transaction_root / UPDATER_EXECUTABLE_NAME
    request_path = transaction_root / "install-request.json"
    try:
        shutil.copy2(source_helper, helper_copy)
        if (
            helper_copy.stat().st_size != helper_entry.size
            or _hash_file(helper_copy) != helper_entry.sha256
        ):
            raise UpdateInstallLaunchError("独立更新器副本未通过受管文件校验。")
        payload = {
            "schema_version": 1,
            "transaction_id": transaction_id,
            "created_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "current_pid": int(current_pid or os.getpid()),
            "current_version": str(current_version),
            "target_version": str(verified.manifest.version),
            "install_dir": str(root),
            "archive_path": str(archive_path),
            "verified_state_path": str(state_path.resolve()),
        }
        request_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        creation_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        process = process_factory(
            [str(helper_copy), "--request", str(request_path)],
            cwd=str(transaction_root),
            close_fds=True,
            creationflags=creation_flags,
        )
    except Exception as exc:
        shutil.rmtree(transaction_root, ignore_errors=True)
        if isinstance(exc, UpdateInstallLaunchError):
            raise
        raise UpdateInstallLaunchError("无法启动独立更新器。") from exc
    return UpdateInstallLaunch(
        target_version=verified.manifest.version,
        request_path=request_path,
        helper_path=helper_copy,
        process_id=int(process.pid),
    )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
