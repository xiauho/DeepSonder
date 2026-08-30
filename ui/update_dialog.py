"""User-facing release summary for the check-only updater."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from core.update_service import UpdateCheckResult


class UpdateDialog(QDialog):
    OPEN_RELEASE = 1
    SKIP_VERSION = 2

    def __init__(self, result: UpdateCheckResult, parent=None) -> None:
        super().__init__(parent)
        if result.latest is None:
            raise ValueError("更新对话框需要有效的版本信息。")
        latest = result.latest
        self.setObjectName("updateDialog")
        self.setWindowTitle("发现 Novalist 新版本")
        self.resize(620, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)

        title = QLabel(f"发现新版本 {latest.tag_name}")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        current = QLabel(
            f"当前版本：v{result.current_version}    "
            f"发布日期：{_display_date(latest.published_at)}"
        )
        current.setObjectName("mutedLabel")
        root.addWidget(current)

        hint = QLabel("更新将在浏览器中打开 GitHub 发布页，本版本不会自动下载或运行文件。")
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        root.addWidget(hint)

        notes = QPlainTextEdit()
        notes.setObjectName("updateNotes")
        notes.setReadOnly(True)
        notes.setPlainText(latest.notes or "此版本没有提供更新说明。")
        root.addWidget(notes, 1)

        actions = QHBoxLayout()
        skip = QPushButton("忽略此版本")
        skip.setObjectName("ghostButton")
        skip.clicked.connect(lambda: self.done(self.SKIP_VERSION))
        close = QPushButton("稍后")
        close.setObjectName("secondaryButton")
        close.clicked.connect(self.reject)
        open_release = QPushButton("打开下载页面")
        open_release.setObjectName("accentButton")
        open_release.setDefault(True)
        open_release.clicked.connect(lambda: self.done(self.OPEN_RELEASE))
        actions.addWidget(skip)
        actions.addStretch(1)
        actions.addWidget(close)
        actions.addWidget(open_release)
        root.addLayout(actions)


def _display_date(value: str) -> str:
    candidate = str(value or "").strip()
    if len(candidate) >= 10 and candidate[4] == "-" and candidate[7] == "-":
        return candidate[:10]
    return "未知"
