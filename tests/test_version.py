from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from core.version import AppVersion, load_current_version


class AppVersionTests(TestCase):
    def test_accepts_optional_v_prefix(self) -> None:
        self.assertEqual(AppVersion.parse("v2.0.5-beta"), AppVersion.parse("2.0.5-beta"))

    def test_stable_release_is_newer_than_same_version_beta(self) -> None:
        self.assertGreater(AppVersion.parse("2.0.5"), AppVersion.parse("2.0.5-beta"))

    def test_prerelease_identifiers_follow_semver_precedence(self) -> None:
        ordered = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-rc.1",
            "1.0.0",
        ]
        parsed = [AppVersion.parse(item) for item in ordered]
        self.assertEqual(sorted(reversed(parsed)), parsed)

    def test_numeric_prerelease_identifier_is_lower_than_text(self) -> None:
        self.assertLess(AppVersion.parse("1.0.0-1"), AppVersion.parse("1.0.0-alpha"))

    def test_build_metadata_does_not_change_precedence(self) -> None:
        self.assertEqual(AppVersion.parse("1.2.3+build.7"), AppVersion.parse("1.2.3"))

    def test_invalid_version_is_rejected(self) -> None:
        for value in (
            "",
            "2.0",
            "release-2.0.5",
            "2.0.05",
            "2.0.5-alpha..1",
            "2.0.5-01",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AppVersion.parse(value)

    def test_loads_version_from_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "VERSION"
            path.write_text("2.0.6-beta\n", encoding="utf-8")
            self.assertEqual(str(load_current_version(path)), "2.0.6-beta")

    def test_missing_version_file_has_actionable_error(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "无法读取当前版本"):
                load_current_version(Path(tmp) / "VERSION")
