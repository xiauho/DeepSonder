import os
import tempfile
import sys
import threading
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

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

    def locked_error(self, code=32):
        error = PermissionError("temporary Windows file lock")
        error.winerror = code
        return error

    def test_transient_windows_lock_retries_atomic_replace(self):
        for code in (5, 32, 33):
            with self.subTest(winerror=code), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "card.md"
                target.write_bytes(b"original")
                replace = os.replace
                attempts = []

                def temporarily_locked(source, destination):
                    attempts.append(source)
                    self.assertEqual(target.read_bytes(), b"original")
                    if len(attempts) < 3:
                        raise self.locked_error(code)
                    replace(source, destination)

                with patch("core.storage.os.replace", side_effect=temporarily_locked), patch("core.storage.time.sleep") as sleep:
                    atomic_write_text(target, "saved")
                self.assertEqual(target.read_bytes(), b"saved")
                self.assertEqual(len(attempts), 3)
                self.assertEqual(sleep.call_count, 2)
                self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_persistent_lock_preserves_original_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "card.md"
            target.write_bytes(b"original")
            with patch("core.storage.os.replace", side_effect=self.locked_error()) as replace, patch("core.storage.time.sleep") as sleep:
                with self.assertRaises(PermissionError):
                    atomic_write_text(target, "unsaved")
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(replace.call_count, 5)
            self.assertEqual(sleep.call_count, 4)
            self.assertEqual(list(Path(tmp).iterdir()), [target])

    def test_non_windows_permission_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "card.md"
            target.write_bytes(b"original")
            with patch("core.storage.os.replace", side_effect=PermissionError("read only")) as replace, patch("core.storage.time.sleep") as sleep:
                with self.assertRaises(PermissionError):
                    atomic_write_text(target, "unsaved")
            replace.assert_called_once()
            sleep.assert_not_called()
            self.assertEqual(target.read_bytes(), b"original")
            self.assertEqual(list(Path(tmp).iterdir()), [target])

    def test_external_change_during_retry_is_never_overwritten(self):
        for original in (None, b"original"):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "card.md"
                if original is not None:
                    target.write_bytes(original)
                with patch("core.storage.os.replace", side_effect=self.locked_error()) as replace, patch("core.storage.time.sleep", side_effect=lambda _delay: target.write_bytes(b"external revision")):
                    with self.assertRaisesRegex(OSError, "文件已发生变化"):
                        atomic_write_text(target, "local draft")
                replace.assert_called_once()
                self.assertEqual(target.read_bytes(), b"external revision")
                self.assertEqual(list(Path(tmp).iterdir()), [target])

    def test_new_file_can_be_saved_after_transient_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "card.md"
            replace = os.replace
            calls = []

            def temporarily_locked(source, destination):
                calls.append(source)
                if len(calls) == 1:
                    raise self.locked_error()
                replace(source, destination)

            with patch("core.storage.os.replace", side_effect=temporarily_locked), patch("core.storage.time.sleep"):
                atomic_write_text(target, "new file")
            self.assertEqual(target.read_bytes(), b"new file")
            self.assertEqual(list(Path(tmp).iterdir()), [target])

    def test_same_size_external_change_with_restored_mtime_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "card.md"
            target.write_bytes(b"original")
            original_stat = target.stat()

            def external_edit(_delay):
                target.write_bytes(b"external")
                os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

            with patch("core.storage.os.replace", side_effect=self.locked_error()), patch("core.storage.time.sleep", side_effect=external_edit):
                with self.assertRaisesRegex(OSError, "文件已发生变化"):
                    atomic_write_text(target, "local draft")
            self.assertEqual(target.read_bytes(), b"external")
            self.assertEqual(list(Path(tmp).iterdir()), [target])

    @skipUnless(sys.platform == "win32", "Requires Windows file sharing semantics")
    def test_real_windows_sharing_lock_released_during_retry(self):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        close.restype = wintypes.BOOL
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "card.md"
            target.write_bytes(b"original")
            # Share reads/writes, but withhold FILE_SHARE_DELETE to deny rename.
            handle = create(str(target), 0x80000000, 0x1 | 0x2, None, 3, 0, None)
            self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
            released = threading.Event()

            def release():
                close(handle)
                released.set()

            timer = threading.Timer(0.05, release)
            timer.start()
            try:
                atomic_write_text(target, "saved after lock")
                self.assertEqual(target.read_bytes(), b"saved after lock")
            finally:
                timer.join()
            self.assertTrue(released.is_set())
            self.assertEqual(list(Path(tmp).iterdir()), [target])
