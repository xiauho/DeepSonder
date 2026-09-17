"""Review-first, keyboard-accessible prose editing dialogs."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPlainTextEdit,
    QDialogButtonBox, QListWidget, QListWidgetItem, QPushButton, QSplitter)
from core.prose_review import TASK_LABELS


def _fit(dialog, width=820, height=720):
    screen = dialog.screen().availableGeometry()
    dialog.resize(min(width, int(screen.width() * .9)), min(height, int(screen.height() * .9)))


class ProseRequestDialog(QDialog):
    def __init__(self, kind, count, parent=None):
        super().__init__(parent)
        self.setWindowTitle(TASK_LABELS[kind])
        layout = QVBoxLayout(self)
        hint = QLabel(f'本次处理 {count} 字。生成后先审阅，不会直接修改正文。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        label = QLabel('本次要求（可留空；只对本次操作生效）')
        layout.addWidget(label)
        self.requirements = QPlainTextEdit()
        self.requirements.setAccessibleName('本次写作要求')
        label.setBuddy(self.requirements)
        self.requirements.setPlaceholderText('例如：保留克制语气，展开动作细节；不要增加新事件。')
        layout.addWidget(self.requirements)
        self.error = QLabel('')
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('生成建议')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        _fit(self, 580, 360)

    def _submit(self):
        if len(self.requirements.toPlainText()) > 2000:
            self.error.setText('本次要求最多 2000 字，请精简。')
            self.requirements.setFocus()
            return
        self.accept()


class ProseReviewDialog(QDialog):
    def __init__(self, review, parent=None):
        super().__init__(parent)
        self.review = review
        self.exceptions = set()
        self.setWindowTitle(TASK_LABELS[review.kind] + ' · 审阅建议')
        root = QVBoxLayout(self)
        hint = QLabel('勾选要采用的修改；未勾选的原文保留。文风例外仅对本书后续文风审校和润色生效。')
        hint.setWordWrap(True)
        root.addWidget(hint)
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.items = QListWidget()
        self.items.setAccessibleName('修改建议，空格切换是否采用')
        for index, patch in enumerate(review.patches):
            item = QListWidgetItem(f'{index + 1}. {patch.category} · {patch.expected_original[:45].replace(chr(10), " ")}')
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.items.addItem(item)
        splitter.addWidget(self.items)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName('当前建议的原文、修改后和理由')
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([90, 300])
        root.addWidget(splitter, 1)
        self.keep = QPushButton('将此原句设为文风例外')
        self.keep.setEnabled(bool(review.patches) and review.kind != 'selection_expand')
        self.keep.clicked.connect(self._toggle_exception)
        root.addWidget(self.keep)
        self.findings = QPlainTextEdit()
        self.findings.setReadOnly(True)
        self.findings.setAccessibleName('规则检测提示')
        self.findings.setPlainText('\n'.join(review.findings) or '规则检测未发现明显重复或输出残留。此结果不代表文学质量评分。')
        self.findings.setMaximumHeight(105)
        root.addWidget(self.findings)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText('确认采用勾选项')
        self.apply_button.setEnabled(False)
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('保留原文并关闭')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.items.currentRowChanged.connect(self._show_patch)
        self.items.itemChanged.connect(self._changed)
        if review.patches:
            self.items.setCurrentRow(0)
        else:
            self.preview.setPlainText('本次没有可采用的修改建议，原文保持不变。')
        _fit(self)

    def _show_patch(self, index):
        if not 0 <= index < len(self.review.patches):
            return
        p = self.review.patches[index]
        self.preview.setPlainText(f'理由：{p.reason}\n\n原文：\n{p.expected_original}\n\n建议：\n{p.replacement}')
        self.keep.setText('撤销此原句的文风例外' if index in self.exceptions else '将此原句设为文风例外')

    def _toggle_exception(self):
        index = self.items.currentRow()
        if index < 0:
            return
        if index in self.exceptions:
            self.exceptions.remove(index)
        else:
            self.exceptions.add(index)
            self.items.item(index).setCheckState(Qt.CheckState.Unchecked)
        self._show_patch(index)
        self._changed()

    def _changed(self, item=None):
        if item is not None and item.checkState() == Qt.CheckState.Checked:
            self.exceptions.discard(self.items.row(item))
            self._show_patch(self.items.currentRow())
        self.apply_button.setEnabled(bool(self.selected_patches() or self.exceptions))
        count, exceptions = len(self.selected_patches()), len(self.exceptions)
        self.apply_button.setText(f'应用 {count} 处 · 例外 {exceptions} 条' if count and exceptions else f'确认采用 {count} 处修改' if count else f'确认保存 {exceptions} 条例外')

    def selected_patches(self):
        return tuple(p for i, p in enumerate(self.review.patches) if self.items.item(i).checkState() == Qt.CheckState.Checked and i not in self.exceptions)


class StyleExceptionsDialog(QDialog):
    def __init__(self, quotes, parent=None):
        super().__init__(parent)
        self.setWindowTitle('本书文风例外')
        root = QVBoxLayout(self)
        hint = QLabel('这些原句在文风审校和润色中受保护；不影响选区扩写。选中后可移除，再保存。')
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.items = QListWidget()
        self.items.setAccessibleName('已保存的文风例外')
        self.items.addItems(quotes)
        root.addWidget(self.items, 1)
        remove = QPushButton('移除选中的例外')
        remove.clicked.connect(lambda: self.items.takeItem(self.items.currentRow()) if self.items.currentRow() >= 0 else None)
        root.addWidget(remove)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        _fit(self, 620, 450)

    def quotes(self):
        return [self.items.item(i).text() for i in range(self.items.count())]
