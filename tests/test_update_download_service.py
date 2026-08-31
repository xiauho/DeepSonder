import hashlib
import json
import stat
import warnings
import zipfile
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest import TestCase
from unittest.mock import patch

from core.update_download_service import (
    UpdateDownloadCancelled,
    UpdateDownloadError,
    download_and_verify_update,
    inspect_update_archive,
    is_allowed_download_url,
    parse_update_manifest,
    select_update_assets,
)
from core.update_service import ReleaseAsset, UpdateInfo
from core.version import AppVersion


VERSION = AppVersion.parse("2.0.7-beta")
ARCHIVE_NAME = "Novalist-v2.0.7-beta-windows-x64.zip"


class FakeResponse:
    def __init__(self, payload: bytes, url: str):
        self._stream = BytesIO(payload)
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def geturl(self) -> str:
        return self._url


class QueueOpener:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


def make_archive(
    *,
    version: str = "2.0.7-beta",
    unsafe_name: str | None = None,
    symlink: bool = False,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression) as archive:
        archive.writestr("Novalist.exe", b"test executable")
        archive.writestr("NovalistUpdater.exe", b"test updater")
        archive.writestr("package-files.json", b"{}")
        archive.writestr("_internal/VERSION", version.encode())
        archive.writestr("licenses/README.md", b"licenses")
        if unsafe_name:
            archive.writestr(unsafe_name, b"unsafe")
        if symlink:
            link = zipfile.ZipInfo("_internal/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "target")
    return stream.getvalue()


def corrupt_executable_member() -> bytes:
    archive = make_archive(compression=zipfile.ZIP_STORED)
    original = b"test executable"
    replacement = b"FAIL executable"
    if len(original) != len(replacement) or archive.count(original) != 1:
        raise AssertionError("test archive layout changed")
    return archive.replace(original, replacement, 1)


def mark_first_member_encrypted(archive: bytes) -> bytes:
    payload = bytearray(archive)
    for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = payload.find(signature)
        if position < 0:
            raise AssertionError("ZIP header not found")
        offset = position + flag_offset
        flags = int.from_bytes(payload[offset : offset + 2], "little") | 0x1
        payload[offset : offset + 2] = flags.to_bytes(2, "little")
    return bytes(payload)


def make_duplicate_path_archive() -> bytes:
    stream = BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Novalist.exe", b"test executable")
            archive.writestr("_internal/VERSION", b"2.0.7-beta")
            archive.writestr("licenses/README.md", b"licenses")
            archive.writestr("_internal/data.bin", b"first")
            archive.writestr("_internal/data.bin", b"second")
    return stream.getvalue()


def make_manifest(archive: bytes, **overrides) -> bytes:
    data = {
        "schema_version": 2,
        "version": "2.0.7-beta",
        "channel": "beta",
        "platform": "windows",
        "architecture": "x64",
        "asset": {
            "name": ARCHIVE_NAME,
            "size": len(archive),
            "sha256": hashlib.sha256(archive).hexdigest(),
        },
        "minimum_updater_version": "2.0.6-beta",
        "published_at": "2026-08-31T00:00:00Z",
    }
    data.update(overrides)
    return json.dumps(data).encode()


def make_release(archive: bytes, manifest: bytes) -> UpdateInfo:
    base = "https://github.com/xiauho/novalist/releases/download/v2.0.7-beta"
    return UpdateInfo(
        version=VERSION,
        tag_name="v2.0.7-beta",
        title="Novalist v2.0.7-beta",
        notes="",
        published_at="2026-08-31T00:00:00Z",
        release_url="https://github.com/xiauho/novalist/releases/tag/v2.0.7-beta",
        prerelease=True,
        assets=(
            ReleaseAsset(
                name=ARCHIVE_NAME,
                size=len(archive),
                digest="sha256:" + hashlib.sha256(archive).hexdigest(),
                download_url=f"{base}/{ARCHIVE_NAME}",
                content_type="application/zip",
            ),
            ReleaseAsset(
                name="release-manifest.json",
                size=len(manifest),
                digest="sha256:" + hashlib.sha256(manifest).hexdigest(),
                download_url=f"{base}/release-manifest.json",
                content_type="application/json",
            ),
        ),
    )


class ManifestAndAssetTests(TestCase):
    def test_exact_archive_and_manifest_are_required(self) -> None:
        archive = make_archive()
        manifest = make_manifest(archive)
        selected = select_update_assets(make_release(archive, manifest))
        self.assertEqual(selected.archive.name, ARCHIVE_NAME)
        self.assertEqual(selected.manifest.name, "release-manifest.json")

    def test_duplicate_archive_is_rejected(self) -> None:
        archive = make_archive()
        manifest = make_manifest(archive)
        release = make_release(archive, manifest)
        release = UpdateInfo(
            **{**release.__dict__, "assets": release.assets + (release.assets[0],)}
        )
        with self.assertRaisesRegex(UpdateDownloadError, "仅包含一个"):
            select_update_assets(release)

    def test_schema_v2_and_target_platform_are_enforced(self) -> None:
        archive = make_archive()
        release = make_release(archive, make_manifest(archive))
        invalid = make_manifest(archive, schema_version=1)
        with self.assertRaisesRegex(UpdateDownloadError, "不受支持"):
            parse_update_manifest(invalid, release)

        invalid = make_manifest(archive, architecture="arm64")
        with self.assertRaisesRegex(UpdateDownloadError, "Windows x64"):
            parse_update_manifest(invalid, release)

    def test_download_url_rejects_insecure_and_deceptive_hosts(self) -> None:
        self.assertTrue(
            is_allowed_download_url(
                "https://release-assets.githubusercontent.com/github-production/a"
            )
        )
        self.assertFalse(is_allowed_download_url("http://github.com/a"))
        self.assertFalse(
            is_allowed_download_url("https://githubusercontent.com.evil.example/a")
        )


class SecureDownloadTests(TestCase):
    def test_download_is_verified_inspected_and_recorded(self) -> None:
        archive = make_archive()
        manifest = make_manifest(archive)
        release = make_release(archive, manifest)
        progress = []
        opener = QueueOpener(
            [
                FakeResponse(manifest, release.assets[1].download_url),
                FakeResponse(archive, release.assets[0].download_url),
            ]
        )
        with TemporaryDirectory() as tmp:
            result = download_and_verify_update(
                release,
                current_version=AppVersion.parse("2.0.6-beta"),
                cache_root=Path(tmp),
                opener=opener,
                progress=progress.append,
            )
            self.assertTrue(result.archive_path.is_file())
            self.assertTrue(result.archive_path.name.endswith(".verified.zip"))
            self.assertEqual(result.inspection.file_count, 5)
            state = json.loads(
                (result.archive_path.parent / "verified-update.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(state["state"], "verified")
            self.assertEqual(state["sha256"], hashlib.sha256(archive).hexdigest())
            self.assertEqual(progress[-1].downloaded, len(archive))
            self.assertFalse(any(Path(tmp).rglob("*.part")))

    def test_manifest_and_github_archive_digest_must_match(self) -> None:
        archive = make_archive()
        manifest = make_manifest(archive)
        release = make_release(archive, manifest)
        wrong_archive_asset = ReleaseAsset(
            **{**release.assets[0].__dict__, "digest": "sha256:" + "0" * 64}
        )
        release = UpdateInfo(
            **{**release.__dict__, "assets": (wrong_archive_asset, release.assets[1])}
        )
        opener = QueueOpener([FakeResponse(manifest, release.assets[1].download_url)])
        with self.assertRaisesRegex(UpdateDownloadError, "GitHub 记录的 SHA-256"):
            download_and_verify_update(release, opener=opener)

    def test_cancelled_download_removes_partial_file(self) -> None:
        archive = make_archive()
        manifest = make_manifest(archive)
        release = make_release(archive, manifest)
        cancelled = Event()

        def cancel_after_first(progress):
            cancelled.set()

        opener = QueueOpener(
            [
                FakeResponse(manifest, release.assets[1].download_url),
                FakeResponse(archive, release.assets[0].download_url),
            ]
        )
        with TemporaryDirectory() as tmp, self.assertRaises(UpdateDownloadCancelled):
            download_and_verify_update(
                release,
                cache_root=Path(tmp),
                opener=opener,
                progress=cancel_after_first,
                cancel_event=cancelled,
            )
        self.assertFalse(any(Path(tmp).rglob("*.part")))


class ArchiveInspectionTests(TestCase):
    def inspect_bytes(self, archive: bytes) -> None:
        manifest_payload = make_manifest(archive)
        release = make_release(archive, manifest_payload)
        manifest = parse_update_manifest(manifest_payload, release)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "update.zip"
            path.write_bytes(archive)
            inspect_update_archive(path, manifest)

    def test_path_traversal_is_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateDownloadError, "不安全的文件路径"):
            self.inspect_bytes(make_archive(unsafe_name="../outside.exe"))

    def test_symlink_is_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateDownloadError, "符号链接"):
            self.inspect_bytes(make_archive(symlink=True))

    def test_corrupt_non_version_member_is_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateDownloadError, "损坏文件"):
            self.inspect_bytes(corrupt_executable_member())

    def test_duplicate_archive_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateDownloadError, "重复路径"):
            self.inspect_bytes(make_duplicate_path_archive())

    def test_encrypted_member_is_rejected(self) -> None:
        with self.assertRaisesRegex(UpdateDownloadError, "加密文件"):
            self.inspect_bytes(mark_first_member_encrypted(make_archive()))

    def test_missing_required_file_is_rejected(self) -> None:
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("NovalistUpdater.exe", b"test updater")
            archive.writestr("package-files.json", b"{}")
            archive.writestr("_internal/VERSION", b"2.0.7-beta")
            archive.writestr("licenses/README.md", b"licenses")
        with self.assertRaisesRegex(UpdateDownloadError, "缺少必需"):
            self.inspect_bytes(stream.getvalue())

    def test_expanded_size_limit_is_enforced(self) -> None:
        with patch("core.update_download_service.MAX_EXPANDED_BYTES", 1):
            with self.assertRaisesRegex(UpdateDownloadError, "解压后超过"):
                self.inspect_bytes(make_archive())

    def test_bundled_version_must_match_manifest(self) -> None:
        with self.assertRaisesRegex(UpdateDownloadError, "版本与更新清单不一致"):
            self.inspect_bytes(make_archive(version="9.9.9"))
