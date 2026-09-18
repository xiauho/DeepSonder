import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import zipfile
from scripts.verify_release import REQUIRED, verify_release


class ReleaseAuditTests(TestCase):
    def fixture(self, root, extra=None, embedded_version="0.1.0-beta"):
        version = "0.1.0-beta"
        asset = root / f"DeepSonder-PySide6-v{version}-windows-x64.zip"
        with zipfile.ZipFile(asset, "w") as bundle:
            for name in REQUIRED:
                bundle.writestr(name, embedded_version if name == "_internal/VERSION" else "fixture")
            if extra:
                bundle.writestr(extra, "fixture")
        sha = hashlib.sha256(asset.read_bytes()).hexdigest()
        manifest = dict(product="DeepSonder-PySide6", platform="windows", architecture="x64",
                        update_channel=None, source_revision="a" * 40, source_dirty=False,
                        version=version, asset=dict(name=asset.name, sha256=sha, size=asset.stat().st_size))
        (root/"release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (root/"SHA256SUMS.txt").write_text(f"{sha}  {asset.name}\n", encoding="utf-8")
        return asset

    def test_valid_bundle(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); self.fixture(root)
            self.assertTrue(verify_release(root)["passed"])

    def test_rejects_data_runtime_and_path_escape(self):
        for name in ("projects/private/chapter.md", "_internal/config.json", "../secret", "node_modules/runtime.js"):
            with self.subTest(name=name), TemporaryDirectory() as tmp:
                root=Path(tmp); self.fixture(root, name)
                with self.assertRaises(AssertionError): verify_release(root)

    def test_rejects_tampering_and_wrong_bundled_version(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp); asset=self.fixture(root)
            asset.write_bytes(asset.read_bytes()+b"tampered")
            with self.assertRaisesRegex(AssertionError, "checksum"): verify_release(root)
            self.fixture(root, embedded_version="2.1.0")
            with self.assertRaisesRegex(AssertionError, "version"): verify_release(root)
