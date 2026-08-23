import tempfile
from pathlib import Path
from unittest import TestCase

from core.storage import atomic_write_text


class StorageTests(TestCase):
    def test_atomic_write_replaces_content_without_leaving_temp_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "memory.json"
            target.write_text("旧内容", encoding="utf-8")

            atomic_write_text(target, "新内容")

            self.assertEqual(target.read_text(encoding="utf-8"), "新内容")
            self.assertEqual(list(root.glob(".memory.json.*.tmp")), [])
