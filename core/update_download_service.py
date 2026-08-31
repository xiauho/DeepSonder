"""Secure download and archive validation for Novalist release packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .app_paths import update_cache_dir
from .update_service import ReleaseAsset, UpdateInfo
from .version import AppVersion


MANIFEST_ASSET_NAME = "release-manifest.json"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = 500 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MINIMUM_DISK_RESERVE_BYTES = 64 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:")


class UpdateDownloadError(RuntimeError):
    """An expected, user-presentable secure download failure."""


class UpdateDownloadCancelled(UpdateDownloadError):
    """The user cancelled an in-progress update download."""


@dataclass(frozen=True)
class ManifestAsset:
    name: str
    size: int
    sha256: str


@dataclass(frozen=True)
class UpdateManifest:
    schema_version: int
    version: AppVersion
    channel: str
    platform: str
    architecture: str
    asset: ManifestAsset
    minimum_updater_version: AppVersion | None
    published_at: str


@dataclass(frozen=True)
class DownloadProgress:
    downloaded: int
    total: int


@dataclass(frozen=True)
class ArchiveInspection:
    file_count: int
    expanded_size: int


@dataclass(frozen=True)
class VerifiedUpdate:
    release: UpdateInfo
    manifest: UpdateManifest
    archive_path: Path
    inspection: ArchiveInspection


@dataclass(frozen=True)
class UpdateAssets:
    archive: ReleaseAsset
    manifest: ReleaseAsset


def expected_archive_name(version: AppVersion) -> str:
    return f"Novalist-v{version}-windows-x64.zip"


def select_update_assets(release: UpdateInfo) -> UpdateAssets:
    """Require one exact archive and one exact manifest asset."""
    archive_name = expected_archive_name(release.version)
    archives = [asset for asset in release.assets if asset.name == archive_name]
    manifests = [
        asset for asset in release.assets if asset.name == MANIFEST_ASSET_NAME
    ]
    if len(archives) != 1:
        raise UpdateDownloadError(
            f"发布版本必须包含且仅包含一个 {archive_name}。"
        )
    if len(manifests) != 1:
        raise UpdateDownloadError(
            f"发布版本必须包含且仅包含一个 {MANIFEST_ASSET_NAME}。"
        )
    _require_github_digest(archives[0])
    _require_github_digest(manifests[0])
    if archives[0].size > MAX_ARCHIVE_BYTES:
        raise UpdateDownloadError("更新包超过允许的最大大小。")
    if manifests[0].size > MAX_MANIFEST_BYTES:
        raise UpdateDownloadError("更新清单超过允许的最大大小。")
    return UpdateAssets(archive=archives[0], manifest=manifests[0])


def parse_update_manifest(payload: bytes, release: UpdateInfo) -> UpdateManifest:
    """Parse and validate the schema-v2 manifest for a selected release."""
    if len(payload) > MAX_MANIFEST_BYTES:
        raise UpdateDownloadError("更新清单超过允许的最大大小。")
    try:
        data = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateDownloadError("更新清单格式无效。") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 2:
        raise UpdateDownloadError("更新清单版本不受支持。")
    asset_data = data.get("asset")
    if not isinstance(asset_data, dict):
        raise UpdateDownloadError("更新清单缺少资源信息。")
    try:
        version = AppVersion.parse(str(data.get("version") or ""))
        asset_size = int(asset_data.get("size"))
        minimum_value = str(data.get("minimum_updater_version") or "").strip()
        minimum_version = AppVersion.parse(minimum_value) if minimum_value else None
    except (TypeError, ValueError) as exc:
        raise UpdateDownloadError("更新清单包含无效的版本或大小。") from exc
    sha256 = str(asset_data.get("sha256") or "").strip().casefold()
    manifest = UpdateManifest(
        schema_version=2,
        version=version,
        channel=str(data.get("channel") or "").strip(),
        platform=str(data.get("platform") or "").strip(),
        architecture=str(data.get("architecture") or "").strip(),
        asset=ManifestAsset(
            name=str(asset_data.get("name") or "").strip(),
            size=asset_size,
            sha256=sha256,
        ),
        minimum_updater_version=minimum_version,
        published_at=str(data.get("published_at") or "").strip(),
    )
    if manifest.version != release.version:
        raise UpdateDownloadError("更新清单版本与 GitHub Release 不一致。")
    expected_channel = "beta" if release.version.is_prerelease else "stable"
    if manifest.channel != expected_channel:
        raise UpdateDownloadError("更新清单中的通道与目标版本不一致。")
    if manifest.platform != "windows" or manifest.architecture != "x64":
        raise UpdateDownloadError("更新包不适用于 Windows x64。")
    if manifest.asset.name != expected_archive_name(release.version):
        raise UpdateDownloadError("更新清单中的文件名与目标版本不一致。")
    if manifest.asset.size <= 0 or manifest.asset.size > MAX_ARCHIVE_BYTES:
        raise UpdateDownloadError("更新清单中的文件大小无效。")
    if not _SHA256_PATTERN.fullmatch(manifest.asset.sha256):
        raise UpdateDownloadError("更新清单中的 SHA-256 无效。")
    if (
        manifest.minimum_updater_version is not None
        and manifest.minimum_updater_version > manifest.version
    ):
        raise UpdateDownloadError("更新清单中的最低更新器版本无效。")
    return manifest


def fetch_update_manifest(
    release: UpdateInfo,
    *,
    timeout: float = 20.0,
    opener: Callable | None = None,
) -> tuple[UpdateManifest, UpdateAssets]:
    """Download a small manifest and verify it against GitHub metadata."""
    assets = select_update_assets(release)
    payload = _download_small_asset(
        assets.manifest,
        release.version,
        timeout=timeout,
        opener=opener,
    )
    manifest = parse_update_manifest(payload, release)
    archive_digest = _require_github_digest(assets.archive)
    if manifest.asset.size != assets.archive.size:
        raise UpdateDownloadError("更新清单与 GitHub 记录的文件大小不一致。")
    if manifest.asset.sha256 != archive_digest:
        raise UpdateDownloadError("更新清单与 GitHub 记录的 SHA-256 不一致。")
    return manifest, assets


def download_and_verify_update(
    release: UpdateInfo,
    *,
    current_version: AppVersion | None = None,
    cache_root: Path | None = None,
    timeout: float = 30.0,
    opener: Callable | None = None,
    progress: Callable[[DownloadProgress], None] | None = None,
    cancel_event: Event | None = None,
) -> VerifiedUpdate:
    """Download, hash and inspect one update without modifying the installation."""
    _raise_if_cancelled(cancel_event)
    if current_version is not None and release.version <= current_version:
        raise UpdateDownloadError("安全下载仅接受高于当前版本的更新。")
    manifest, assets = fetch_update_manifest(release, timeout=timeout, opener=opener)
    if (
        current_version is not None
        and manifest.minimum_updater_version is not None
        and current_version < manifest.minimum_updater_version
    ):
        raise UpdateDownloadError("当前版本过旧，无法安全安装此更新。")

    target_root = Path(cache_root) if cache_root is not None else update_cache_dir()
    version_root = target_root / f"v{release.version}"
    version_root.mkdir(parents=True, exist_ok=True)
    required_space = manifest.asset.size * 3 + MINIMUM_DISK_RESERVE_BYTES
    if shutil.disk_usage(version_root).free < required_space:
        raise UpdateDownloadError("磁盘空间不足，无法安全下载并暂存更新。")

    archive_name = Path(manifest.asset.name)
    final_path = version_root / f"{archive_name.stem}.verified{archive_name.suffix}"
    partial_path = version_root / f"{manifest.asset.name}.part"
    if final_path.is_file() and _file_matches(
        final_path, manifest.asset.size, manifest.asset.sha256
    ):
        try:
            inspection = inspect_update_archive(final_path, manifest)
        except Exception:
            final_path.unlink(missing_ok=True)
            raise
        _write_verified_state(version_root, release, manifest, final_path, inspection)
        return VerifiedUpdate(release, manifest, final_path, inspection)

    final_path.unlink(missing_ok=True)
    partial_path.unlink(missing_ok=True)
    request = _asset_request(assets.archive, release.version)
    open_request = opener or _DOWNLOAD_OPENER.open
    digest = hashlib.sha256()
    downloaded = 0
    try:
        with open_request(request, timeout=timeout) as response, partial_path.open(
            "xb"
        ) as stream:
            _validate_response_url(response)
            while True:
                _raise_if_cancelled(cancel_event)
                chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > manifest.asset.size:
                    raise UpdateDownloadError("下载内容超过更新清单声明的大小。")
                stream.write(chunk)
                digest.update(chunk)
                if progress is not None:
                    progress(DownloadProgress(downloaded, manifest.asset.size))
            stream.flush()
            os.fsync(stream.fileno())
    except UpdateDownloadCancelled:
        partial_path.unlink(missing_ok=True)
        raise
    except HTTPError as exc:
        partial_path.unlink(missing_ok=True)
        raise UpdateDownloadError(f"更新下载失败（HTTP {exc.code}）。") from exc
    except (URLError, socket.timeout, TimeoutError, OSError) as exc:
        partial_path.unlink(missing_ok=True)
        raise UpdateDownloadError("更新下载失败，请检查网络和磁盘空间。") from exc
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise

    if downloaded != manifest.asset.size:
        partial_path.unlink(missing_ok=True)
        raise UpdateDownloadError("更新下载不完整。")
    if digest.hexdigest() != manifest.asset.sha256:
        partial_path.unlink(missing_ok=True)
        raise UpdateDownloadError("更新包 SHA-256 校验失败，文件可能已损坏。")
    os.replace(partial_path, final_path)
    try:
        inspection = inspect_update_archive(final_path, manifest)
    except Exception:
        final_path.unlink(missing_ok=True)
        raise
    _write_verified_state(version_root, release, manifest, final_path, inspection)
    return VerifiedUpdate(release, manifest, final_path, inspection)


def inspect_update_archive(
    archive_path: Path,
    manifest: UpdateManifest,
) -> ArchiveInspection:
    """Reject unsafe or structurally invalid ZIP packages before extraction."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_FILES:
                raise UpdateDownloadError("更新包包含过多文件。")
            names: set[str] = set()
            entries_by_name: dict[str, zipfile.ZipInfo] = {}
            expanded_size = 0
            file_count = 0
            for item in entries:
                normalized = _validate_archive_name(item.filename)
                key = normalized.casefold()
                if key in names:
                    raise UpdateDownloadError("更新包包含重复路径。")
                names.add(key)
                entries_by_name[key] = item
                mode = item.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise UpdateDownloadError("更新包包含不允许的符号链接。")
                if item.external_attr & 0x400:
                    raise UpdateDownloadError("更新包包含不允许的重解析点。")
                if item.flag_bits & 0x1:
                    raise UpdateDownloadError("更新包包含加密文件。")
                if item.is_dir():
                    continue
                file_count += 1
                expanded_size += item.file_size
                if expanded_size > MAX_EXPANDED_BYTES:
                    raise UpdateDownloadError("更新包解压后超过允许的最大大小。")
            expansion_limit = manifest.asset.size * 20 + MINIMUM_DISK_RESERVE_BYTES
            if expanded_size > expansion_limit:
                raise UpdateDownloadError("更新包的压缩率异常，已停止处理。")
            required = {"novalist.exe", "_internal/version", "licenses/readme.md"}
            if not required.issubset(names):
                raise UpdateDownloadError("更新包缺少必需的程序或版本文件。")
            version_entry = entries_by_name["_internal/version"]
            with archive.open(version_entry) as version_stream:
                version_bytes = version_stream.read(129)
            if len(version_bytes) > 128:
                raise UpdateDownloadError("更新包内的版本信息过大。")
            bundled_version = version_bytes.decode("utf-8-sig").strip()
            # Metadata and package hashes cannot prove that every compressed
            # member can be decoded.  Read all members once so zipfile also
            # verifies their CRC values before the archive is marked trusted.
            if archive.testzip() is not None:
                raise UpdateDownloadError("更新包包含无法完整解压的损坏文件。")
    except (zipfile.BadZipFile, UnicodeDecodeError, KeyError, OSError) as exc:
        raise UpdateDownloadError("更新包不是有效的 Novalist ZIP 文件。") from exc
    try:
        parsed_version = AppVersion.parse(bundled_version)
    except ValueError as exc:
        raise UpdateDownloadError("更新包内的版本信息无效。") from exc
    if parsed_version != manifest.version:
        raise UpdateDownloadError("更新包内版本与更新清单不一致。")
    return ArchiveInspection(file_count=file_count, expanded_size=expanded_size)


class _DownloadRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_download_url(newurl):
            raise UpdateDownloadError("更新下载遇到不受信任的网络跳转。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_DOWNLOAD_OPENER = build_opener(_DownloadRedirectHandler())


def is_allowed_download_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    try:
        port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").casefold()
    trusted_host = host == "github.com" or host.endswith(".githubusercontent.com")
    return (
        parsed.scheme == "https"
        and trusted_host
        and port in {None, 443}
        and not parsed.username
        and not parsed.password
    )


def _download_small_asset(
    asset: ReleaseAsset,
    version: AppVersion,
    *,
    timeout: float,
    opener: Callable | None,
) -> bytes:
    request = _asset_request(asset, version)
    open_request = opener or _DOWNLOAD_OPENER.open
    try:
        with open_request(request, timeout=timeout) as response:
            _validate_response_url(response)
            payload = response.read(MAX_MANIFEST_BYTES + 1)
    except HTTPError as exc:
        raise UpdateDownloadError(f"更新清单下载失败（HTTP {exc.code}）。") from exc
    except (URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise UpdateDownloadError("无法下载更新清单，请检查网络后重试。") from exc
    if len(payload) != asset.size:
        raise UpdateDownloadError("更新清单大小与 GitHub 记录不一致。")
    expected_digest = _require_github_digest(asset)
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise UpdateDownloadError("更新清单 SHA-256 校验失败。")
    return payload


def _asset_request(asset: ReleaseAsset, version: AppVersion) -> Request:
    if not is_allowed_download_url(asset.download_url):
        raise UpdateDownloadError("更新资源下载地址不受信任。")
    return Request(
        asset.download_url,
        headers={"User-Agent": f"Novalist/{version}"},
        method="GET",
    )


def _validate_response_url(response: object) -> None:
    geturl = getattr(response, "geturl", None)
    if callable(geturl) and not is_allowed_download_url(geturl()):
        raise UpdateDownloadError("更新下载返回了不受信任的网络地址。")


def _require_github_digest(asset: ReleaseAsset) -> str:
    digest = str(asset.digest or "").strip().casefold()
    if not digest.startswith("sha256:"):
        raise UpdateDownloadError(f"GitHub 未提供 {asset.name} 的 SHA-256 摘要。")
    value = digest.removeprefix("sha256:")
    if not _SHA256_PATTERN.fullmatch(value):
        raise UpdateDownloadError(f"GitHub 提供的 {asset.name} 摘要无效。")
    return value


def _validate_archive_name(value: str) -> str:
    candidate = str(value or "").replace("\\", "/")
    if (
        not candidate
        or "\x00" in candidate
        or candidate.startswith("/")
        or _DRIVE_PATH_PATTERN.match(candidate)
    ):
        raise UpdateDownloadError("更新包包含不安全的文件路径。")
    parts = candidate.rstrip("/").split("/")
    reserved = {"con", "prn", "aux", "nul"}
    reserved.update(f"com{index}" for index in range(1, 10))
    reserved.update(f"lpt{index}" for index in range(1, 10))
    if any(
        part in {"", ".", ".."}
        or ":" in part
        or part.endswith((" ", "."))
        or part.split(".", 1)[0].casefold() in reserved
        for part in parts
    ):
        raise UpdateDownloadError("更新包包含不安全的文件路径。")
    return "/".join(parts)


def _raise_if_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise UpdateDownloadCancelled("更新下载已取消。")


def _file_matches(path: Path, size: int, sha256: str) -> bool:
    try:
        if path.stat().st_size != size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(DOWNLOAD_CHUNK_BYTES):
                digest.update(chunk)
        return digest.hexdigest() == sha256
    except OSError:
        return False


def _write_verified_state(
    version_root: Path,
    release: UpdateInfo,
    manifest: UpdateManifest,
    archive_path: Path,
    inspection: ArchiveInspection,
) -> None:
    state_path = version_root / "verified-update.json"
    temporary = version_root / "verified-update.json.tmp"
    payload = {
        "state": "verified",
        "verified_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "tag_name": release.tag_name,
        "version": str(manifest.version),
        "archive": archive_path.name,
        "size": manifest.asset.size,
        "sha256": manifest.asset.sha256,
        "file_count": inspection.file_count,
        "expanded_size": inspection.expanded_size,
    }
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, state_path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise UpdateDownloadError("无法写入更新缓存状态。") from exc
