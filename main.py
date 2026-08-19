import sys

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QStyleFactory

from core.config import load_config
from core.resources import resource_path
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


def main() -> int:
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("Novalist")
    app.setOrganizationName("Novalist")
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setWindowIcon(QIcon(str(resource_path("assets/app_icon.ico"))))

    config = load_config()
    apply_theme(app, config)

    window = MainWindow()
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
