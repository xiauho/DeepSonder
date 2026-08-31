"""Transactional installer used by the standalone Windows updater helper."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .version import AppVersion


PACKAGE_MANIFEST_NAME = "package-files.json"
REQUEST_SCHEMA_VERSION = 1
PACKAGE_MANIFEST_SCHEMA_VERSION = 1
MAX_PACKAGE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_FILES = 10_000
COPY_CHUNK_BYTES = 1024 * 1024
PROCESS_EXIT_TIMEOUT_SECONDS = 120
HEALTH_CHECK_TIMEOUT_SECONDS = 60
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_REQUIRED_PACKAGE_FILES = {
    "novalist.exe",
    "novalistupdater.exe",
    "_internal/version",
    "licenses/readme.md",
}


class UpdateInstallError(RuntimeError):
    """An expected failure that can be shown to the user after rollback."""


@dataclass(frozen=True)
class PackageFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class PackageManifest:
    schema_version: int
    version: AppVersion
    files: tuple[PackageFile, ...]

    @property
    def by_path(self) -> dict[str, PackageFile]:
        return {item.path.casefold(): item for item in self.files}


@dataclass(frozen=True)
class InstallRequest:
    current_pid: int
    current_version: AppVersion
    target_version: AppVersion
    install_dir: Path
    archive_path: Path
    verified_state_path: Path
    transaction_root: Path


@dataclass(frozen=True)
class InstallOutcome:
    success: bool
    rolled_back: bool
    message: str
    result_path: Path


def parse_package_manifest(
    payload: bytes,
    *,
    expected_version: AppVersion | None = None,
) -> PackageManifest:
    """Parse a package-owned allowlist of files managed by Novalist."""
    if len(payload) > MAX_PACKAGE_MANIFEST_BYTES:
        raise UpdateInstallError("软件包文件清单过大。")
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateInstallError("软件包文件清单格式无效。") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise UpdateInstallError("软件包文件清单版本不受支持。")
    try:
        version = AppVersion.parse(str(data.get("version") or ""))
    except ValueError as exc:
        raise UpdateInstallError("软件包文件清单中的版本无效。") from exc
    if expected_version is not None and version != expected_version:
        raise UpdateInstallError("软件包文件清单与目标版本不一致。")
    raw_files = data.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise UpdateInstallError("软件包文件清单缺少文件列表。")
    if len(raw_files) > MAX_PACKAGE_FILES:
        raise UpdateInstallError("软件包文件清单包含过多文件。")

    files: list[PackageFile] = []
    names: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise UpdateInstallError("软件包文件清单包含无效条目。")
        path = validate_package_path(str(raw.get("path") or ""))
        key = path.casefold()
        if key == PACKAGE_MANIFEST_NAME.casefold() or key in names:
            raise UpdateInstallError("软件包文件清单包含重复或保留路径。")
        names.add(key)
        try:
            size = int(raw.get("size"))
        except (TypeError, ValueError) as exc:
            raise UpdateInstallError("软件包文件清单包含无效大小。") from exc
        sha256 = str(raw.get("sha256") or "").strip().casefold()
        if size < 0 or not _SHA256_PATTERN.fullmatch(sha256):
            raise UpdateInstallError("软件包文件清单包含无效大小或 SHA-256。")
        files.append(PackageFile(path=path, size=size, sha256=sha256))

    if not _REQUIRED_PACKAGE_FILES.issubset(names):
        raise UpdateInstallError("软件包文件清单缺少主程序、更新器或许可证。")
    return PackageManifest(
        schema_version=PACKAGE_MANIFEST_SCHEMA_VERSION,
        version=version,
        files=tuple(files),
    )


def validate_update_archive(
    archive_path: Path,
    expected_version: AppVersion,
) -> PackageManifest:
    """Verify the inner package allowlist and every member before extraction."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries: dict[str, zipfile.ZipInfo] = {}
            names: set[str] = set()
            for item in archive.infolist():
                path = validate_package_path(item.filename)
                key = path.casefold()
                if key in names:
                    raise UpdateInstallError("更新包包含重复路径。")
                names.add(key)
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise UpdateInstallError("更新包包含不允许的符号链接。")
                if item.external_attr & 0x400:
                    raise UpdateInstallError("更新包包含不允许的重解析点。")
                if item.flag_bits & 0x1:
                    raise UpdateInstallError("更新包包含加密文件。")
                if not item.is_dir():
                    entries[key] = item
            manifest_entry = entries.get(PACKAGE_MANIFEST_NAME.casefold())
            if manifest_entry is None:
                raise UpdateInstallError("更新包缺少软件包文件清单。")
            if manifest_entry.file_size > MAX_PACKAGE_MANIFEST_BYTES:
                raise UpdateInstallError("软件包文件清单过大。")
            with archive.open(manifest_entry) as stream:
                manifest = parse_package_manifest(
                    stream.read(MAX_PACKAGE_MANIFEST_BYTES + 1),
                    expected_version=expected_version,
                )
            expected = set(manifest.by_path)
            actual = set(entries) - {PACKAGE_MANIFEST_NAME.casefold()}
            if actual != expected:
                raise UpdateInstallError("更新包内容与软件包文件清单不一致。")
            for key, package_file in manifest.by_path.items():
                item = entries[key]
                if item.file_size != package_file.size:
                    raise UpdateInstallError("更新包文件大小与清单不一致。")
                digest = hashlib.sha256()
                with archive.open(item) as stream:
                    while chunk := stream.read(COPY_CHUNK_BYTES):
                        digest.update(chunk)
                if digest.hexdigest() != package_file.sha256:
                    raise UpdateInstallError("更新包文件 SHA-256 与清单不一致。")
            if archive.testzip() is not None:
                raise UpdateInstallError("更新包包含无法完整解压的损坏文件。")
            return manifest
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise UpdateInstallError("更新包无法读取或格式无效。") from exc


def load_install_request(request_path: Path) -> InstallRequest:
    """Load a narrowly scoped request created by the running main process."""
    request_path = Path(request_path).resolve()
    try:
        data = json.loads(request_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateInstallError("自动安装请求无法读取。") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise UpdateInstallError("自动安装请求版本不受支持。")
    try:
        current_pid = int(data.get("current_pid"))
        current_version = AppVersion.parse(str(data.get("current_version") or ""))
        target_version = AppVersion.parse(str(data.get("target_version") or ""))
    except (TypeError, ValueError) as exc:
        raise UpdateInstallError("自动安装请求包含无效进程或版本。") from exc
    if current_pid <= 0 or target_version <= current_version:
        raise UpdateInstallError("自动安装请求中的版本顺序无效。")

    transaction_root = request_path.parent.resolve()
    install_dir = _absolute_path(data.get("install_dir"), "安装目录")
    archive_path = _absolute_path(data.get("archive_path"), "更新包")
    state_path = _absolute_path(data.get("verified_state_path"), "验证状态")
    if transaction_root.parent != archive_path.parent or state_path.parent != archive_path.parent:
        raise UpdateInstallError("自动安装请求引用了更新缓存之外的文件。")
    if install_dir.parent == install_dir or len(install_dir.parts) < 2:
        raise UpdateInstallError("拒绝将磁盘根目录作为安装目录。")
    return InstallRequest(
        current_pid=current_pid,
        current_version=current_version,
        target_version=target_version,
        install_dir=install_dir,
        archive_path=archive_path,
        verified_state_path=state_path,
        transaction_root=transaction_root,
    )


def install_update(
    request_path: Path,
    *,
    wait_for_process: Callable[[int, int], None] | None = None,
    health_check: Callable[[Path], None] | None = None,
    launch_application: Callable[[Path], None] | None = None,
) -> InstallOutcome:
    """Apply one verified package, rolling back all managed files on failure."""
    request = load_install_request(request_path)
    wait = wait_for_process or wait_for_process_exit
    check = health_check or run_health_check
    launch = launch_application or launch_novalist
    result_path = request.transaction_root / "install-result.json"
    journal_path = request.transaction_root / "install-journal.json"
    staging_root = request.transaction_root / "staging"
    backup_root = request.transaction_root / "backup"
    old_manifest: PackageManifest | None = None
    new_manifest: PackageManifest | None = None
    backup_complete = False

    try:
        wait(request.current_pid, PROCESS_EXIT_TIMEOUT_SECONDS)
        _validate_verified_state(request)
        new_manifest = validate_update_archive(
            request.archive_path,
            request.target_version,
        )
        old_manifest_path = request.install_dir / PACKAGE_MANIFEST_NAME
        _assert_no_link_components(request.install_dir, PACKAGE_MANIFEST_NAME)
        try:
            old_manifest = parse_package_manifest(
                old_manifest_path.read_bytes(),
                expected_version=request.current_version,
            )
        except OSError as exc:
            raise UpdateInstallError(
                "当前安装缺少软件包文件清单，请改用手动安装。"
            ) from exc
        _verify_installed_files(request.install_dir, old_manifest)
        _reject_unmanaged_collisions(request.install_dir, old_manifest, new_manifest)
        _extract_verified_archive(request.archive_path, staging_root, new_manifest)
        _backup_managed_files(request.install_dir, backup_root, old_manifest)
        backup_complete = True
        _write_json_atomic(
            journal_path,
            _journal_payload(request, "backed_up"),
        )
        _apply_staged_files(
            request.install_dir,
            staging_root,
            old_manifest,
            new_manifest,
        )
        _write_json_atomic(journal_path, _journal_payload(request, "health_check"))
        check(request.install_dir / "Novalist.exe")
        _write_json_atomic(
            result_path,
            _result_payload(request, "installed", "更新已安装并通过启动自检。"),
        )
        _write_json_atomic(journal_path, _journal_payload(request, "completed"))
        shutil.rmtree(backup_root, ignore_errors=True)
        shutil.rmtree(staging_root, ignore_errors=True)
        launch(request.install_dir / "Novalist.exe")
        return InstallOutcome(True, False, "更新安装成功。", result_path)
    except Exception as exc:  # updater must recover the portable installation
        message = str(exc) or "自动安装失败。"
        rolled_back = False
        if backup_complete and old_manifest is not None and new_manifest is not None:
            try:
                _rollback_managed_files(
                    request.install_dir,
                    backup_root,
                    old_manifest,
                    new_manifest,
                )
                rolled_back = True
                message = f"{message} 已恢复原版本。"
            except Exception as rollback_exc:  # noqa: BLE001
                message = f"{message} 自动回滚也失败：{rollback_exc}"
        try:
            _write_json_atomic(
                result_path,
                _result_payload(
                    request,
                    "rolled_back" if rolled_back else "failed",
                    message,
                ),
            )
            _write_json_atomic(
                journal_path,
                _journal_payload(request, "rolled_back" if rolled_back else "failed"),
            )
        except OSError:
            pass
        current_app = request.install_dir / "Novalist.exe"
        if current_app.is_file():
            try:
                launch(current_app)
            except OSError:
                pass
        return InstallOutcome(False, rolled_back, message, result_path)


def wait_for_process_exit(process_id: int, timeout_seconds: int) -> None:
    """Wait until the main process releases every installed program file."""
    if process_id == os.getpid():
        raise UpdateInstallError("更新器不能等待自身退出。")
    if sys.platform == "win32":
        import ctypes

        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, process_id)
        if not handle:
            return
        try:
            result = ctypes.windll.kernel32.WaitForSingleObject(
                handle,
                int(timeout_seconds * 1000),
            )
            if result == 0x00000102:
                raise UpdateInstallError("等待 Novalist 退出超时。")
            if result != 0:
                raise UpdateInstallError("无法确认 Novalist 已安全退出。")
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(process_id, 0)
        except OSError:
            return
        time.sleep(0.1)
    raise UpdateInstallError("等待 Novalist 退出超时。")


def run_health_check(application_path: Path) -> None:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [str(application_path), "--self-test"],
            check=False,
            timeout=HEALTH_CHECK_TIMEOUT_SECONDS,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateInstallError("新版程序启动自检失败。") from exc
    if result.returncode != 0:
        raise UpdateInstallError(f"新版程序启动自检失败（{result.returncode}）。")


def launch_novalist(application_path: Path) -> None:
    creation_flags = (
        getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )
    subprocess.Popen(
        [str(application_path)],
        cwd=str(application_path.parent),
        close_fds=True,
        creationflags=creation_flags,
    )


def validate_package_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/")
    if not raw or raw.startswith("/") or _DRIVE_PATH_PATTERN.match(raw):
        raise UpdateInstallError("软件包包含不安全的文件路径。")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise UpdateInstallError("软件包包含不安全的文件路径。")
    normalized = "/".join(path.parts)
    if len(normalized) > 240:
        raise UpdateInstallError("软件包文件路径过长。")
    for part in path.parts:
        if any(ord(char) < 32 for char in part) or ":" in part:
            raise UpdateInstallError("软件包包含不安全的文件路径。")
        if part.endswith((" ", ".")):
            raise UpdateInstallError("软件包包含 Windows 不允许的文件名。")
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise UpdateInstallError("软件包包含 Windows 保留文件名。")
    return normalized


def _absolute_path(value: object, label: str) -> Path:
    candidate = Path(str(value or ""))
    if not candidate.is_absolute():
        raise UpdateInstallError(f"自动安装请求中的{label}不是绝对路径。")
    return candidate.resolve()


def _validate_verified_state(request: InstallRequest) -> None:
    try:
        state = json.loads(request.verified_state_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateInstallError("更新包验证状态无法读取。") from exc
    if not isinstance(state, dict) or state.get("state") != "verified":
        raise UpdateInstallError("更新包尚未标记为已验证。")
    if str(state.get("version") or "") != str(request.target_version):
        raise UpdateInstallError("更新包验证状态与目标版本不一致。")
    if str(state.get("archive") or "") != request.archive_path.name:
        raise UpdateInstallError("更新包验证状态与缓存文件不一致。")
    try:
        expected_size = int(state.get("size"))
    except (TypeError, ValueError) as exc:
        raise UpdateInstallError("更新包验证状态中的大小无效。") from exc
    expected_hash = str(state.get("sha256") or "").casefold()
    if expected_size != request.archive_path.stat().st_size:
        raise UpdateInstallError("更新包大小已在下载后发生变化。")
    if not _SHA256_PATTERN.fullmatch(expected_hash):
        raise UpdateInstallError("更新包验证状态中的 SHA-256 无效。")
    if _hash_file(request.archive_path) != expected_hash:
        raise UpdateInstallError("更新包 SHA-256 已在下载后发生变化。")


def _verify_installed_files(root: Path, manifest: PackageManifest) -> None:
    version_path = root / "_internal" / "VERSION"
    _assert_no_link_components(root, "_internal/VERSION")
    try:
        installed_version = AppVersion.parse(version_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise UpdateInstallError("当前安装的版本信息无法验证。") from exc
    if installed_version != manifest.version:
        raise UpdateInstallError("当前安装版本与软件包文件清单不一致。")
    for item in manifest.files:
        _assert_no_link_components(root, item.path)
        target = root / Path(*PurePosixPath(item.path).parts)
        if not target.is_file():
            raise UpdateInstallError(f"当前安装缺少受管文件：{item.path}")
        if target.stat().st_size != item.size or _hash_file(target) != item.sha256:
            raise UpdateInstallError(f"当前安装文件已被修改：{item.path}")


def _reject_unmanaged_collisions(
    root: Path,
    old: PackageManifest,
    new: PackageManifest,
) -> None:
    old_paths = set(old.by_path) | {PACKAGE_MANIFEST_NAME.casefold()}
    for key, item in new.by_path.items():
        _assert_no_link_components(root, item.path, allow_missing=True)
        target = root / Path(*PurePosixPath(item.path).parts)
        if key not in old_paths and target.exists():
            raise UpdateInstallError(f"新版文件会覆盖非受管内容：{item.path}")


def _extract_verified_archive(
    archive_path: Path,
    staging_root: Path,
    manifest: PackageManifest,
) -> None:
    if staging_root.exists():
        raise UpdateInstallError("更新暂存目录已存在，拒绝重复安装。")
    staging_root.mkdir(parents=True)
    allowed = set(manifest.by_path) | {PACKAGE_MANIFEST_NAME.casefold()}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for item in archive.infolist():
                path = validate_package_path(item.filename)
                if item.is_dir():
                    continue
                if path.casefold() not in allowed:
                    raise UpdateInstallError("更新包暂存内容超出文件清单。")
                destination = staging_root / Path(*PurePosixPath(path).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(item) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, COPY_CHUNK_BYTES)
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise
    for item in manifest.files:
        staged = staging_root / Path(*PurePosixPath(item.path).parts)
        if staged.stat().st_size != item.size or _hash_file(staged) != item.sha256:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise UpdateInstallError("更新暂存文件未通过 SHA-256 复核。")


def _backup_managed_files(root: Path, backup: Path, manifest: PackageManifest) -> None:
    if backup.exists():
        raise UpdateInstallError("更新备份目录已存在，拒绝覆盖旧备份。")
    backup.mkdir(parents=True)
    paths = [item.path for item in manifest.files] + [PACKAGE_MANIFEST_NAME]
    for relative in paths:
        source = root / Path(*PurePosixPath(relative).parts)
        if not source.is_file():
            raise UpdateInstallError(f"当前安装缺少待备份文件：{relative}")
        destination = backup / Path(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _apply_staged_files(
    root: Path,
    staging: Path,
    old: PackageManifest,
    new: PackageManifest,
) -> None:
    for item in new.files:
        source = staging / Path(*PurePosixPath(item.path).parts)
        destination = root / Path(*PurePosixPath(item.path).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
    os.replace(staging / PACKAGE_MANIFEST_NAME, root / PACKAGE_MANIFEST_NAME)
    obsolete = set(old.by_path) - set(new.by_path)
    for key in obsolete:
        target = root / Path(*PurePosixPath(old.by_path[key].path).parts)
        target.unlink(missing_ok=True)


def _rollback_managed_files(
    root: Path,
    backup: Path,
    old: PackageManifest,
    new: PackageManifest,
) -> None:
    for item in new.files:
        target = root / Path(*PurePosixPath(item.path).parts)
        if target.is_file():
            target.unlink()
    (root / PACKAGE_MANIFEST_NAME).unlink(missing_ok=True)
    for relative in [item.path for item in old.files] + [PACKAGE_MANIFEST_NAME]:
        source = backup / Path(*PurePosixPath(relative).parts)
        if not source.is_file():
            raise UpdateInstallError(f"更新备份缺少文件：{relative}")
        destination = root / Path(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_no_link_components(
    root: Path,
    relative: str,
    *,
    allow_missing: bool = False,
) -> None:
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if not current.exists():
            if allow_missing:
                return
            continue
        is_junction = getattr(current, "is_junction", None)
        if current.is_symlink() or (callable(is_junction) and is_junction()):
            raise UpdateInstallError(f"安装目录包含不允许的链接路径：{relative}")


def _journal_payload(request: InstallRequest, status: str) -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "current_version": str(request.current_version),
        "target_version": str(request.target_version),
        "install_dir": str(request.install_dir),
        "updated_at": _utc_timestamp(),
    }


def _result_payload(request: InstallRequest, status: str, message: str) -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "current_version": str(request.current_version),
        "target_version": str(request.target_version),
        "message": str(message),
        "completed_at": _utc_timestamp(),
    }


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
