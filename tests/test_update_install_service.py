import hashlib
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from core.update_download_service import (
    ArchiveInspection,
    ManifestAsset,
    UpdateManifest,
    VerifiedUpdate,
)
from core.update_install_service import (
    automatic_install_unavailable_reason,
    launch_verified_update_install,
)
from core.update_service import UpdateInfo
from core.version import AppVersion


CURRENT = AppVersion.parse("2.0.8-beta")
TARGET = AppVersion.parse("2.0.9-beta")


def build_verified_update(cache: Path) -> VerifiedUpdate:
    version_root = cache / "v2.0.9-beta"
    version_root.mkdir(parents=True)
    archive_path = version_root / "Novalist-v2.0.9-beta-windows-x64.verified.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Novalist.exe", b"app")
        archive.writestr("NovalistUpdater.exe", b"updater")
        archive.writestr("package-files.json", b"{}")
        archive.writestr("_internal/VERSION", b"2.0.9-beta")
        archive.writestr("licenses/README.md", b"licenses")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest = UpdateManifest(
        schema_version=2,
        version=TARGET,
        channel="beta",
        platform="windows",
        architecture="x64",
        asset=ManifestAsset(
            "Novalist-v2.0.9-beta-windows-x64.zip",
            archive_path.stat().st_size,
            digest,
        ),
        minimum_updater_version=CURRENT,
        published_at="2026-09-01T00:00:00Z",
    )
    state = {
        "state": "verified",
        "version": str(TARGET),
        "archive": archive_path.name,
        "size": archive_path.stat().st_size,
        "sha256": digest,
    }
    (version_root / "verified-update.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    release = UpdateInfo(
        version=TARGET,
        tag_name="v2.0.9-beta",
        title="v2.0.9-beta",
        notes="",
        published_at="2026-09-01T00:00:00Z",
        release_url="https://github.com/xiauho/novalist/releases/tag/v2.0.9-beta",
        prerelease=True,
    )
    return VerifiedUpdate(
        release=release,
        manifest=manifest,
        archive_path=archive_path,
        inspection=ArchiveInspection(file_count=5, expanded_size=20),
    )


def write_current_manifest(root: Path) -> None:
    required = {
        "Novalist.exe": b"app",
        "NovalistUpdater.exe": b"current updater",
        "_internal/VERSION": str(CURRENT).encode(),
        "licenses/README.md": b"licenses",
    }
    for relative, payload in required.items():
        path = root / Path(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    manifest = {
        "schema_version": 1,
        "version": str(CURRENT),
        "files": [
            {
                "path": path,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            for path, payload in required.items()
        ],
    }
    (root / "package-files.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


class FakeProcess:
    pid = 456


class InstallLaunchTests(TestCase):
    def test_source_workspace_reports_manual_install_fallback(self) -> None:
        reason = automatic_install_unavailable_reason(
            platform="win32",
            frozen=False,
        )
        self.assertIn("源码工作区", reason)

    def test_verified_update_is_handed_to_copied_installed_helper(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            install_root = base / "portable"
            install_root.mkdir()
            write_current_manifest(install_root)
            cache = base / "cache"
            verified = build_verified_update(cache)
            calls = []

            def process_factory(command, **kwargs):
                calls.append((command, kwargs))
                return FakeProcess()

            launch = launch_verified_update_install(
                verified,
                CURRENT,
                install_dir=install_root,
                cache_root=cache,
                platform="win32",
                frozen=True,
                current_pid=123,
                process_factory=process_factory,
            )

            self.assertEqual(launch.process_id, 456)
            self.assertTrue(launch.helper_path.is_file())
            request = json.loads(launch.request_path.read_text(encoding="utf-8"))
            self.assertEqual(request["current_pid"], 123)
            self.assertEqual(request["target_version"], str(TARGET))
            self.assertEqual(Path(request["install_dir"]), install_root.resolve())
            self.assertEqual(calls[0][0][0], str(launch.helper_path))

