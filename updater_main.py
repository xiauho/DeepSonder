"""Entry point for the standalone Novalist Windows update helper."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.update_installer import UpdateInstallError, install_update


def _show_failure(message: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            str(message),
            "Novalist 自动更新",
            0x10,
        )
    except (AttributeError, OSError):
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--request")
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args(argv)
    if arguments.self_test:
        return 0
    if not arguments.request:
        return 2
    try:
        outcome = install_update(Path(arguments.request))
    except (UpdateInstallError, OSError, ValueError) as exc:
        _show_failure(str(exc) or "自动安装请求无效。")
        return 2
    if not outcome.success:
        _show_failure(outcome.message)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
