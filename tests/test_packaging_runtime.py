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

    def test_frozen_resources_resolve_from_meipass(self) -> None:
        with TemporaryDirectory() as tmp, patch.object(
            sys, "frozen", True, create=True
        ), patch.object(sys, "_MEIPASS", tmp, create=True):
            self.assertEqual(resource_root(), Path(tmp).resolve())
            self.assertEqual(
                resource_path("assets/app_icon.ico"),
                Path(tmp).resolve() / "assets/app_icon.ico",
            )

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
