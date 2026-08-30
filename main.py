import os
import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QStyleFactory

from core.config import load_config, normalize_config
from core.resources import resource_path
from core.version import load_current_version
from ui.icons import load_stitch_fonts, material_symbols_available
from ui.main_window import MainWindow
from ui.theme import apply_theme


def _set_windows_app_id() -> None:
    """Give Windows a stable identity for taskbar grouping and icon display."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Novalist.Writer.Desktop"
        )
    except (AttributeError, OSError):
        pass


REQUIRED_RUNTIME_RESOURCES = (
    "VERSION",
    "assets/app_icon.ico",
    "assets/checkmark.svg",
    "assets/chevron-down-dark.svg",
    "assets/chevron-down-light.svg",
    "assets/chevron-up-dark.svg",
    "assets/chevron-up-light.svg",
    "assets/fonts/font-58.woff2",
)


def _configure_application(app: QApplication) -> None:
    _set_windows_app_id()
    app.setApplicationName("Novalist")
    app.setOrganizationName("Novalist")
    app.setStyle(QStyleFactory.create("Fusion"))
    load_stitch_fonts()
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setWindowIcon(QIcon(str(resource_path("assets/app_icon.ico"))))


def validate_runtime_resources() -> None:
    """Fail fast when a source tree or frozen bundle is incomplete."""
    missing = [
        item for item in REQUIRED_RUNTIME_RESOURCES if not resource_path(item).is_file()
    ]
    if missing:
        raise RuntimeError("缺少运行资源：" + "、".join(missing))
    load_current_version()


def run_self_test() -> int:
    """Exercise the frozen UI without reading or writing the user's settings."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([sys.argv[0], "--self-test"])
    _configure_application(app)
    validate_runtime_resources()
    if app.windowIcon().isNull():
        raise RuntimeError("应用图标未能加载。")
    if not material_symbols_available():
        raise RuntimeError("Material Symbols 字体未能加载。")
    window = MainWindow(config=normalize_config({}))
    if "帮助" not in [action.text() for action in window.menuBar().actions()]:
        raise RuntimeError("主界面菜单未完整加载。")
    window.close()
    return 0


def main() -> int:
    if "--self-test" in sys.argv[1:]:
        return run_self_test()

    app = QApplication(sys.argv)
    _configure_application(app)
    validate_runtime_resources()

    config = load_config()
    apply_theme(app, config)

    window = MainWindow(config=config)
    screen = app.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        window.resize(int(area.width() * 0.75), int(area.height() * 0.82))
    else:
        window.resize(1440, 900)
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
