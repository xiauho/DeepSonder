import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from core.resources import resource_path, resource_root
from main import REQUIRED_RUNTIME_RESOURCES, validate_runtime_resources


class PackagedResourceTests(TestCase):
    def test_source_resources_resolve_from_workspace_root(self) -> None:
        self.assertEqual(
            resource_path("VERSION"),
            Path(__file__).parents[1] / "VERSION",
        )

    def test_entry_self_test_runs_outside_workspace_without_creating_profile(self) -> None:
        entry = Path(__file__).resolve().parents[1] / "main.py"
        with TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-B", str(entry), "--self-test"],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_frozen_resources_resolve_from_meipass(self) -> None:
        with TemporaryDirectory() as tmp, patch.object(
            sys, "frozen", True, create=True
        ), patch.object(sys, "_MEIPASS", tmp, create=True):
            self.assertEqual(resource_root(), Path(tmp).resolve())
            self.assertEqual(
                resource_path("assets/app_icon.ico"),
                Path(tmp).resolve() / "assets/app_icon.ico",
            )

    def test_ui_self_test_uses_temporary_profiles_and_reports_real_controller_checks(self):
        entry = Path(__file__).resolve().parents[1] / "main.py"
        with TemporaryDirectory() as tmp:
            report = Path(tmp) / "ui smoke.json"
            result = subprocess.run([sys.executable, "-B", str(entry), "--ui-self-test", str(report)],
                                    cwd=tmp, capture_output=True, text=True, timeout=45)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            data = json.loads(report.read_text(encoding="utf-8"))
            self.assertTrue(data["successful"])
            self.assertTrue(data["temporary_data_only"])
            self.assertFalse(data["native_ime_tested"])
            self.assertEqual(set(data["checks"]), {"fresh_profile", "legacy_profile", "prose_position",
                             "settings_apply", "ai_explicit_review", "save_conflict"})
            self.assertEqual(list(Path(tmp).iterdir()), [report])

    def test_required_runtime_resources_are_present_in_source_tree(self) -> None:
        validate_runtime_resources()
        for item in REQUIRED_RUNTIME_RESOURCES:
            self.assertTrue(resource_path(item).is_file(), item)

    def test_third_party_license_texts_are_not_placeholders(self) -> None:
        license_files = [
            item
            for item in REQUIRED_RUNTIME_RESOURCES
            if item.startswith("licenses/third-party/")
        ]
        self.assertEqual(len(license_files), 6)
        for item in license_files:
            with self.subTest(item=item):
                self.assertGreater(resource_path(item).stat().st_size, 4_000)
