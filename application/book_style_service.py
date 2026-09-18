"""One confirmation boundary for the book's existing style files."""
from pathlib import Path
from core.style_library import LIBRARY_PATH, load_library, revision, save_library, validate
from core.writing_style import EXCEPTIONS_PATH, load_exceptions, _validate_quotes
from core.storage import atomic_write_text
import json

PATHS = (LIBRARY_PATH, EXCEPTIONS_PATH)

def snapshot(root):
    return {p: (Path(root) / p).read_bytes() if (Path(root) / p).exists() else None for p in PATHS}

def load_book_style(root):
    before = snapshot(root)
    value = load_library(root)
    quotes = load_exceptions(root)
    if snapshot(root) != before:
        raise ValueError('文风资料读取期间已变化，请重新打开。')
    return value, quotes, before

def save_book_style(root, value, quotes, *, expected):
    root = Path(root)
    validate(value)
    _validate_quotes(quotes)
    if snapshot(root) != expected:
        raise ValueError("文风资料已被其他窗口修改，请重新打开后再保存；本次未覆盖。")
    old = load_library(root)
    old_quotes = load_exceptions(root)
    library_revision = revision(root)
    if snapshot(root) != expected:
        raise ValueError('文风资料已被其他窗口修改，请重新打开后再保存；本次未覆盖。')
    changed = []
    try:
        if value != old:
            save_library(root, value, expected_revision=library_revision)
            changed.append(LIBRARY_PATH)
        if tuple(quotes) != old_quotes:
            atomic_write_text(root / EXCEPTIONS_PATH, json.dumps({'version': 1, 'quotes': list(dict.fromkeys(quotes))}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            changed.append(EXCEPTIONS_PATH)
    except OSError as exc:
        failures = []
        for path in reversed(changed):
            try:
                if expected[path] is None:
                    (root / path).unlink(missing_ok=True)
                else:
                    atomic_write_text(root / path, expected[path].decode('utf-8'), encoding='utf-8')
            except OSError:
                failures.append(str(path))
        if failures:
            raise OSError('保存失败，以下文件未能恢复，请检查：' + '、'.join(failures)) from exc
        raise
    return [root / p for p in changed]
