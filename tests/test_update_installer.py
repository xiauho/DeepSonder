import hashlib
import json
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from core.update_installer import (
    PACKAGE_MANIFEST_NAME,
    UpdateInstallError,
    _extract_verified_archive,
    _wait_for_windows_process_exit,
    install_update,
    parse_package_manifest,
    validate_package_path,
)


OLD_VERSION = "2.0.8-beta"
NEW_VERSION = "2.0.9-beta"


def package_files(version: str, marker: bytes) -> dict[str, bytes]:
    return {
        "Novalist.exe": marker + b" app",
        "NovalistUpdater.exe": marker + b" updater",
        "_internal/VERSION": version.encode(),
        "_internal/runtime.bin": marker + b" runtime",
        "licenses/README.md": b"licenses",
    }


def manifest_payload(version: str, files: dict[str, bytes]) -> bytes:
    payload = {
        "schema_version": 1,
        "version": version,
        "files": [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in sorted(files.items())
        ],
    }
    return (json.dumps(payload, indent=2) + "\n").encode()


def write_install(root: Path, version: str, marker: bytes) -> dict[str, bytes]:
    files = package_files(version, marker)
    for relative, content in files.items():
        path = root / Path(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (root / PACKAGE_MANIFEST_NAME).write_bytes(manifest_payload(version, files))
    return files


def write_archive(path: Path, version: str, marker: bytes) -> dict[str, bytes]:
    files = package_files(version, marker)
    files["_internal/new-only.bin"] = b"new managed file"
    manifest = manifest_payload(version, files)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, content in files.items():
            archive.writestr(relative, content)
        archive.writestr(PACKAGE_MANIFEST_NAME, manifest)
    return files


def write_request(base: Path, install_root: Path) -> tuple[Path, dict[str, bytes]]:
    version_root = base / "v2.0.9-beta"
    version_root.mkdir(parents=True)
    archive_path = version_root / "Novalist-v2.0.9-beta-windows-x64.verified.zip"
    new_files = write_archive(archive_path, NEW_VERSION, b"new")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    state_path = version_root / "verified-update.json"
    state_path.write_text(
        json.dumps(
            {
                "state": "verified",
                "version": NEW_VERSION,
                "archive": archive_path.name,
                "size": archive_path.stat().st_size,
                "sha256": digest,
            }
        ),
        encoding="utf-8",
    )
    transaction = version_root / "install-test"
    transaction.mkdir()
    request_path = transaction / "install-request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "current_pid": 123,
                "current_version": OLD_VERSION,
                "target_version": NEW_VERSION,
                "install_dir": str(install_root.resolve()),
                "archive_path": str(archive_path.resolve()),
                "verified_state_path": str(state_path.resolve()),
            }
        ),
        encoding="utf-8",
    )
    return request_path, new_files


class PackageManifestTests(TestCase):
    def test_manifest_rejects_duplicate_and_unsafe_paths(self) -> None:
        files = package_files(NEW_VERSION, b"new")
        payload = json.loads(manifest_payload(NEW_VERSION, files))
        payload["files"].append(dict(payload["files"][0]))
        with self.assertRaisesRegex(UpdateInstallError, "重复"):
            parse_package_manifest(json.dumps(payload).encode())
        with self.assertRaisesRegex(UpdateInstallError, "不安全"):
            validate_package_path("../outside.exe")


class TransactionalInstallTests(TestCase):
    def test_staged_manifest_must_match_the_previously_validated_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive_path = base / "update.zip"
            files = package_files(NEW_VERSION, b"new")
            expected_payload = manifest_payload(NEW_VERSION, files)
            reordered = json.loads(expected_payload)
            reordered["files"].reverse()
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for relative, content in files.items():
                    archive.writestr(relative, content)
                archive.writestr(
                    PACKAGE_MANIFEST_NAME,
                    json.dumps(reordered).encode(),
                )
            expected = parse_package_manifest(expected_payload)
            staging_root = base / "staging"

            with self.assertRaisesRegex(UpdateInstallError, "暂存清单.*不一致"):
                _extract_verified_archive(archive_path, staging_root, expected)

            self.assertFalse(staging_root.exists())

    def test_windows_process_open_failure_does_not_fail_open(self) -> None:
        class Kernel32:
            @staticmethod
            def OpenProcess(_access, _inherit, _process_id):
                return 0

        with self.assertRaisesRegex(UpdateInstallError, "Windows 错误 5"):
            _wait_for_windows_process_exit(
                123,
                1,
                kernel32=Kernel32(),
                get_last_error=lambda: 5,
            )

    def test_wait_failure_does_not_launch_a_second_application(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            install_root = base / "portable"
            install_root.mkdir()
            write_install(install_root, OLD_VERSION, b"old")
            request_path, _new_files = write_request(base / "cache", install_root)
            launched = []

            outcome = install_update(
                request_path,
                wait_for_process=lambda _pid, _timeout: (_ for _ in ()).throw(
                    UpdateInstallError("simulated wait failure")
                ),
                health_check=lambda _app: None,
                launch_application=launched.append,
            )

            self.assertFalse(outcome.success)
            self.assertFalse(outcome.rolled_back)
            self.assertIn("simulated wait failure", outcome.message)
            self.assertEqual(launched, [])

    def test_success_replaces_only_managed_files_and_launches_new_app(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            install_root = base / "portable"
            install_root.mkdir()
            write_install(install_root, OLD_VERSION, b"old")
            unmanaged = install_root / "my-novel.txt"
            unmanaged.write_text("keep me", encoding="utf-8")
            request_path, new_files = write_request(base / "cache", install_root)
            launched = []

            def health_check(app: Path) -> None:
                self.assertEqual(app.read_bytes(), new_files["Novalist.exe"])

            outcome = install_update(
                request_path,
                wait_for_process=lambda _pid, _timeout: None,
                health_check=health_check,
                launch_application=launched.append,
            )

            self.assertTrue(outcome.success)
            self.assertFalse(outcome.rolled_back)
            self.assertEqual(unmanaged.read_text(encoding="utf-8"), "keep me")
            self.assertEqual(
                (install_root / "Novalist.exe").read_bytes(),
                new_files["Novalist.exe"],
            )
            self.assertTrue((install_root / "_internal" / "new-only.bin").is_file())
            self.assertEqual(
                launched,
                [(install_root / "Novalist.exe").resolve()],
            )
            installed_manifest = parse_package_manifest(
                (install_root / PACKAGE_MANIFEST_NAME).read_bytes()
            )
            self.assertEqual(str(installed_manifest.version), NEW_VERSION)

    def test_health_check_failure_restores_old_version(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            install_root = base / "portable"
            install_root.mkdir()
            old_files = write_install(install_root, OLD_VERSION, b"old")
            request_path, _new_files = write_request(base / "cache", install_root)
            launched = []

            outcome = install_update(
                request_path,
                wait_for_process=lambda _pid, _timeout: None,
                health_check=lambda _app: (_ for _ in ()).throw(
                    UpdateInstallError("simulated smoke failure")
                ),
                launch_application=launched.append,
            )

            self.assertFalse(outcome.success)
            self.assertTrue(outcome.rolled_back)
            for relative, content in old_files.items():
                self.assertEqual(
                    (install_root / Path(*relative.split("/"))).read_bytes(),
                    content,
                )
            self.assertFalse((install_root / "_internal" / "new-only.bin").exists())
            self.assertEqual(
                launched,
                [(install_root / "Novalist.exe").resolve()],
            )

    def test_new_managed_file_cannot_overwrite_unmanaged_content(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp)
            install_root = base / "portable"
            install_root.mkdir()
            write_install(install_root, OLD_VERSION, b"old")
            collision = install_root / "_internal" / "new-only.bin"
            collision.write_bytes(b"user content")
            request_path, _new_files = write_request(base / "cache", install_root)

            outcome = install_update(
                request_path,
                wait_for_process=lambda _pid, _timeout: None,
                health_check=lambda _app: None,
                launch_application=lambda _app: None,
            )

            self.assertFalse(outcome.success)
            self.assertFalse(outcome.rolled_back)
            self.assertIn("非受管内容", outcome.message)
            self.assertEqual(collision.read_bytes(), b"user content")
