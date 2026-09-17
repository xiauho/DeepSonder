"""Bounded, profile-local UI preferences. Never stores manuscript content."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from core.storage import atomic_write_text


def fit_geometry(values, screens):
    """Clamp a saved client rectangle to an available monitor (including negative origins)."""
    if not screens:
        return QRect(80, 80, 1100, 720)
    valid = isinstance(values, list) and len(values) == 4 and all(type(v) is int and abs(v) < 10_000_000 for v in values)
    wanted = QRect(*values) if valid else QRect()
    screen = max(screens, key=lambda r: r.intersected(wanted).width() * r.intersected(wanted).height())
    width = min(screen.width(), max(min(1100, screen.width()), wanted.width() or int(screen.width() * .8)))
    height = min(max(1, screen.height() - 40), max(min(720, screen.height() - 40), wanted.height() or int(screen.height() * .82)))
    x = min(max(wanted.x(), screen.left()), screen.right() - width + 1)
    y = min(max(wanted.y(), screen.top() + 24), screen.bottom() - height + 1)
    return QRect(x, y, width, height)


class WorkspaceStateController(QObject):
    def __init__(self, window, path=None):
        super().__init__(window)
        self.window = window
        self.path = Path(path) if path is not None else None
        self.data = {"projects": {}}
        if self.path is not None:
            try:
                if self.path.stat().st_size <= 1_000_000:
                    value = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(value, dict):
                        self.data = value
            except (OSError, ValueError):
                pass
        if not isinstance(self.data.get("projects"), dict):
            self.data["projects"] = {}
        self._binding = None
        self._restoring = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(800)
        self.timer.timeout.connect(self.flush)
        editor = window.editor
        editor.document_about_to_change.connect(self.capture)
        editor.document_loaded.connect(self.restore_document)
        editor.text_edit.cursorPositionChanged.connect(self.schedule)
        editor.text_edit.verticalScrollBar().valueChanged.connect(self.schedule)
        editor.text_edit.horizontalScrollBar().valueChanged.connect(self.schedule)
        window.window_state_controller.panels_changed.connect(self.schedule)
        window.main_splitter.splitterMoved.connect(self.schedule)
        layout = self.data.get("layout", {})
        if isinstance(layout, dict):
            controller = window.window_state_controller
            for name in ("navigation_visible", "inspector_visible"):
                if type(layout.get(name)) is bool:
                    setattr(controller, name, layout[name])
            for key, attr in (("navigation_width", "_left_panel_width"), ("inspector_width", "_inspector_width")):
                value = layout.get(key)
                if type(value) is int:
                    setattr(controller, attr, max(200, min(600, value)))
            controller.set_route_shell_visible(True)
            controller.panels_changed.emit()

    def restore_geometry(self):
        screens = [s.availableGeometry() for s in QApplication.screens()]
        rect = fit_geometry(self.data.get("geometry"), screens)
        self.window.setMinimumSize(min(1100, rect.width()), min(720, rect.height()))
        self.window.setGeometry(rect)
        if self.data.get("maximized") is True:
            self.window.showMaximized()

    def schedule(self, *_args):
        if not self._restoring:
            self.timer.start()

    def capture(self):
        editor = self.window.editor
        if self._binding is None or self._restoring or editor.current_path() != self._binding[2]:
            return
        root, relative, _path = self._binding
        cursor = editor.text_edit.textCursor()
        record = self.data["projects"].get(root)
        if not isinstance(record, dict):
            record = {}
        documents = record.get("documents", {})
        if not isinstance(documents, dict):
            documents = {}
        documents.pop(relative, None)
        documents[relative] = {"cursor": cursor.position(), "anchor": cursor.anchor(),
            "scroll": editor.text_edit.verticalScrollBar().value(),
            "horizontal": editor.text_edit.horizontalScrollBar().value()}
        record.update(last=relative, documents=dict(list(documents.items())[-100:]))
        self.data["projects"].pop(root, None)
        self.data["projects"][root] = record
        self.data["projects"] = dict(list(self.data["projects"].items())[-50:])

    def restore_document(self):
        editor = self.window.editor
        project = self.window.project
        if project is None or not editor.current_path():
            return
        try:
            relative = Path(editor.current_path()).resolve().relative_to(project.root.resolve()).as_posix()
        except (ValueError, OSError):
            return
        root = str(project.root.resolve())
        self._binding = (root, relative, editor.current_path())
        binding = self._binding
        record = self.data["projects"].get(root, {})
        documents = record.get("documents", {}) if isinstance(record, dict) else {}
        state = documents.get(relative, {}) if isinstance(documents, dict) else {}
        if not isinstance(state, dict):
            state = {}
        def number(key):
            value = state.get(key, 0)
            return max(0, min(2_000_000_000, value)) if type(value) is int else 0
        self._restoring = True
        cursor = editor.text_edit.textCursor()
        maximum = editor.text_edit.document().characterCount() - 1
        cursor.setPosition(min(maximum, number("anchor")))
        cursor.setPosition(min(maximum, number("cursor")), QTextCursor.MoveMode.KeepAnchor)
        editor.text_edit.setTextCursor(cursor)
        self._restoring = False
        def scroll():
            if self._binding != binding or editor.current_path() != binding[2]:
                return
            self._restoring = True
            editor.text_edit.verticalScrollBar().setValue(number("scroll"))
            editor.text_edit.horizontalScrollBar().setValue(number("horizontal"))
            self._restoring = False
            self.schedule()
        QTimer.singleShot(0, scroll)

    def last_document(self, project):
        record = self.data["projects"].get(str(project.root.resolve()), {})
        relative = record.get("last") if isinstance(record, dict) else None
        if not isinstance(relative, str):
            return None
        try:
            path = (project.root / relative).resolve()
            path.relative_to(project.root.resolve())
            # Only a path already present in the navigation inventory may be reopened.
            tree = self.window.left_panel.tree
            for index in range(tree.topLevelItemCount()):
                parent = tree.topLevelItem(index)
                for child_index in range(parent.childCount()):
                    child = parent.child(child_index)
                    candidate = child.data(0, Qt.ItemDataRole.UserRole)
                    if isinstance(candidate, (str, Path)) and Path(candidate).resolve() == path and path.is_file():
                        return path
        except (OSError, ValueError):
            pass
        return None

    def flush(self):
        self.timer.stop()
        self.capture()
        window = self.window
        controller = window.window_state_controller
        controller.remember_panel_sizes(0, 0)
        self.data["layout"] = {"navigation_visible": controller.navigation_visible,
            "inspector_visible": controller.inspector_visible,
            "navigation_width": controller._left_panel_width,
            "inspector_width": controller._inspector_width}
        rect = window.normalGeometry() if window.isMaximized() else window.geometry()
        if not window.isMinimized():
            self.data["geometry"] = [rect.x(), rect.y(), rect.width(), rect.height()]
            self.data["maximized"] = window.isMaximized()
        if self.path is not None:
            try:
                atomic_write_text(self.path, json.dumps(self.data, ensure_ascii=False, indent=2))
            except OSError:
                window.status_message.setText("界面偏好保存失败；请检查配置目录权限。正文保存不受影响。")
