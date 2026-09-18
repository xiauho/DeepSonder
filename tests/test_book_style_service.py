import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from application.book_style_service import load_book_style, save_book_style, snapshot
from core.writing_style import STYLE_PATH, EXCEPTIONS_PATH, load_style, load_exceptions
from core.style_library import LIBRARY_PATH, load_library
from core.storage import atomic_write_text

class BookStyleServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'writing').mkdir()
        (self.root / STYLE_PATH).write_bytes('# 原有要求\r\n保留叙事声音。\r\n'.encode('utf-8'))

    def test_no_change_preserves_exact_bytes_and_creates_no_files(self):
        value, quotes, before = load_book_style(self.root)
        changed = save_book_style(self.root, value, list(quotes), expected=before)
        self.assertEqual(changed, [])
        self.assertEqual(snapshot(self.root), before)

    def test_confirmed_save_and_disabled_samples_keep_requirements_effective(self):
        value, quotes, before = load_book_style(self.root)
        old_guide = (self.root / STYLE_PATH).read_bytes()
        value['profile'] = {'节奏': '短句'}
        save_book_style(self.root, value, ['保留原句'], expected=before)
        self.assertIn('短句', load_style(self.root))
        self.assertNotIn('保留叙事声音', load_style(self.root))
        self.assertEqual(load_library(self.root)['profile']['节奏'], '短句')
        self.assertEqual(load_exceptions(self.root), ('保留原句',))
        self.assertEqual((self.root / STYLE_PATH).read_bytes(), old_guide)

    def test_external_changes_to_each_file_reject_entire_save(self):
        for path in (LIBRARY_PATH, EXCEPTIONS_PATH):
            with self.subTest(path=path):
                value, quotes, before = load_book_style(self.root)
                original = before[path]
                (self.root / path).write_text('external edit', encoding='utf-8')
                external = snapshot(self.root)
                with self.assertRaises(ValueError):
                    save_book_style(self.root, value, [], expected=before)
                self.assertEqual(snapshot(self.root), external)
                if original is None: (self.root / path).unlink()
                else: (self.root / path).write_bytes(original)

    def test_failed_exception_write_rolls_back_library(self):
        value, quotes, before = load_book_style(self.root)
        value['enabled'] = True
        def write(path, text, **kwargs):
            if Path(path).name == EXCEPTIONS_PATH.name:
                raise OSError('disk full')
            return atomic_write_text(path, text, **kwargs)
        with patch('application.book_style_service.atomic_write_text', side_effect=write):
            with self.assertRaises(OSError):
                save_book_style(self.root, value, ['原句'], expected=before)
        self.assertEqual(snapshot(self.root), before)

    def test_invalid_exception_cannot_partially_save(self):
        value, quotes, before = load_book_style(self.root)
        value['enabled'] = True
        with self.assertRaises(ValueError):
            save_book_style(self.root, value, [''], expected=before)
        self.assertEqual(snapshot(self.root), before)
