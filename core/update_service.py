"""GitHub Release lookup and trusted asset discovery."""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .version import AppVersion


RELEASES_API_URL = "https://api.github.com/repos/xiauho/novalist/releases?per_page=30"
MAX_RESPONSE_BYTES = 2_000_000
ALLOWED_RELEASE_HOST = "github.com"


class UpdateCheckError(RuntimeError):
    """An expected, user-presentable update lookup failure."""


@dataclass(frozen=True)
class ReleaseAsset:
    """Download metadata supplied by the GitHub Releases API."""

    name: str
    size: int
    digest: str
    download_url: str
    content_type: str


@dataclass(frozen=True)
class UpdateInfo:
    version: AppVersion
    tag_name: str
    title: str
    notes: str
    published_at: str
    release_url: str
    prerelease: bool
    assets: tuple[ReleaseAsset, ...] = ()


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: AppVersion
    latest: UpdateInfo | None

    @property
    def update_available(self) -> bool:
        return self.latest is not None and self.latest.version > self.current_version


class _RestrictedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname != "api.github.com":
            raise UpdateCheckError("更新检查遇到不受信任的网络跳转。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(_RestrictedRedirectHandler())


def check_for_updates(
    current_version: AppVersion,
    channel: str,
    *,
    timeout: float = 10.0,
    opener: Callable | None = None,
) -> UpdateCheckResult:
    """Fetch public releases and choose the newest eligible version."""
    releases = fetch_releases(
        current_version,
        timeout=timeout,
        opener=opener,
    )
    return UpdateCheckResult(
        current_version=current_version,
        latest=select_latest_release(releases, channel),
    )


def fetch_releases(
    current_version: AppVersion,
    *,
    timeout: float = 10.0,
    opener: Callable | None = None,
) -> list[dict]:
    """Return the GitHub Release list using an unauthenticated HTTPS request."""
    request = Request(
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Novalist/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    open_request = opener or _OPENER.open
    try:
        with open_request(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if exc.code in {403, 429}:
            raise UpdateCheckError("GitHub 暂时限制了更新检查，请稍后再试。") from exc
        raise UpdateCheckError(f"GitHub 更新服务返回错误（HTTP {exc.code}）。") from exc
    except (URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise UpdateCheckError("无法连接 GitHub，请检查网络后重试。") from exc

    if len(payload) > MAX_RESPONSE_BYTES:
        raise UpdateCheckError("GitHub 返回的更新信息过大，已停止处理。")
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateCheckError("GitHub 返回了无法识别的更新信息。") from exc
    if not isinstance(parsed, list):
        raise UpdateCheckError("GitHub 返回的更新信息格式不正确。")
    return [item for item in parsed if isinstance(item, dict)]


def select_latest_release(releases: list[dict], channel: str) -> UpdateInfo | None:
    """Choose the highest valid release allowed by the selected channel."""
    normalized_channel = channel if channel in {"stable", "beta"} else "beta"
    candidates: list[UpdateInfo] = []
    for release in releases:
        if bool(release.get("draft")):
            continue
        info = _parse_release(release)
        if info is None or not _channel_allows(info, normalized_channel):
            continue
        candidates.append(info)
    return max(candidates, key=lambda item: item.version, default=None)


def is_allowed_release_url(value: str) -> bool:
    parsed = urlparse(str(value or "").strip())
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == ALLOWED_RELEASE_HOST
        and port in {None, 443}
        and not parsed.username
        and not parsed.password
        and parsed.path.startswith("/xiauho/novalist/")
    )


def is_allowed_asset_url(value: str) -> bool:
    """Accept only browser downloads belonging to this repository."""
    parsed = urlparse(str(value or "").strip())
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == ALLOWED_RELEASE_HOST
        and port in {None, 443}
        and not parsed.username
        and not parsed.password
        and parsed.path.startswith("/xiauho/novalist/releases/download/")
    )


def _parse_release(release: dict) -> UpdateInfo | None:
    tag_name = str(release.get("tag_name") or "").strip()
    release_url = str(release.get("html_url") or "").strip()
    if not tag_name or not is_allowed_release_url(release_url):
        return None
    try:
        version = AppVersion.parse(tag_name)
    except ValueError:
        return None
    return UpdateInfo(
        version=version,
        tag_name=tag_name,
        title=str(release.get("name") or tag_name).strip(),
        notes=str(release.get("body") or "").strip(),
        published_at=str(release.get("published_at") or "").strip(),
        release_url=release_url,
        prerelease=bool(release.get("prerelease")) or version.is_prerelease,
        assets=_parse_assets(release.get("assets")),
    )


def _parse_assets(value: object) -> tuple[ReleaseAsset, ...]:
    if not isinstance(value, list):
        return ()
    assets: list[ReleaseAsset] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        download_url = str(item.get("browser_download_url") or "").strip()
        digest = str(item.get("digest") or "").strip()
        try:
            size = int(item.get("size"))
        except (TypeError, ValueError):
            continue
        if not name or size <= 0 or not is_allowed_asset_url(download_url):
            continue
        assets.append(
            ReleaseAsset(
                name=name,
                size=size,
                digest=digest,
                download_url=download_url,
                content_type=str(item.get("content_type") or "").strip(),
            )
        )
    return tuple(assets)


def _channel_allows(info: UpdateInfo, channel: str) -> bool:
    if channel == "stable":
        return not info.prerelease and not info.version.is_prerelease
    if not info.version.is_prerelease:
        return True
    return info.version.prerelease_stage in {"beta", "b", "rc"}
