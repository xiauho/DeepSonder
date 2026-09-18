import hashlib
import json
from pathlib import Path
import unittest


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "electron_migration"
FIXTURE_MANIFEST = FIXTURE_ROOT / "fixture-manifest.json"
GOLDEN_PROJECT = FIXTURE_ROOT / "golden_project"


def _file_digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class LegacyImportFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
        if cls.manifest.get("fixture_schema_version") != 1:
            raise ValueError("Unsupported legacy import fixture schema")

    def test_fixture_bytes_match_locked_manifest(self) -> None:
        self.assertEqual(
            _file_digests(GOLDEN_PROJECT),
            self.manifest["locked_files_sha256"],
        )

    def test_legacy_import_contract_is_minimal_and_self_contained(self) -> None:
        contract = self.manifest["legacy_import_contract"]
        locked = self.manifest["locked_files_sha256"]
        chapter_sources = contract["chapter_sources"]

        self.assertEqual(contract["import_project_fields"], ["name", "author"])
        self.assertEqual(
            chapter_sources,
            [
                "outline/chapters/chapter_01.md",
                "outline/chapters/chapter_02.md",
            ],
        )
        self.assertTrue(all(path in locked for path in chapter_sources))
        self.assertTrue(
            all(
                not source.startswith(tuple(contract["excluded_prefixes"]))
                and source not in contract["excluded_files"]
                for source in chapter_sources
            )
        )


if __name__ == "__main__":
    unittest.main()
