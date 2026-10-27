"""Durable local-file writes used by project and application persistence."""

from __future__ import annotations

import errno
import hashlib
import os
import tempfile
import time
from pathlib import Path


_REPLACE_RETRY_DELAYS = (0.02, 0.05, 0.1, 0.2)
_WINDOWS_REPLACE_LOCK_ERRORS = {5, 32, 33}


def _file_revision(path: Path):
    try:
        info = path.stat()
        digest = hashlib.sha256(path.read_bytes()).digest()
    except FileNotFoundError:
        return None
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, digest


def _replace_with_retry(temporary: Path, destination: Path) -> None:
    # Windows scanners and file watchers can briefly deny replacement. Keep the
    # atomic operation and stop if another writer changes the destination.
    revision = _file_revision(destination)
    for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
        if attempt and _file_revision(destination) != revision:
            raise OSError(errno.EBUSY, "等待保存期间文件已发生变化，本地草稿已保留，请重新载入或另存草稿。", str(destination))
        try:
            os.replace(temporary, destination)
            return
        except OSError as error:
            if (getattr(error, "winerror", None) not in _WINDOWS_REPLACE_LOCK_ERRORS
                    or attempt == len(_REPLACE_RETRY_DELAYS)):
                raise
            time.sleep(_REPLACE_RETRY_DELAYS[attempt])


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write text through a sibling temporary file and atomically replace it."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding=encoding, newline="") as handle:
            handle.write(str(text))
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
