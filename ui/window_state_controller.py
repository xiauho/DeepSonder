"""Manage route presentation and shared window chrome state."""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer


class WindowStateController(QObject):
    """Own page presentation, focus mode, side panels, and output visibility."""

    WRITING_ROUTES = frozenset(("writing", "canon"))

    def __init__(
        self,
        *,
        primary_nav,
        page_stack,
        pages: dict[str, object],
        writing_page,
        action_bar,
        app_header,
        menu_bar,
        status_bar,
        left_panel,
        inspector,
        editor,
        output_container,
        main_splitter,
        outer_splitter,
        focus_action,
        exit_focus_button,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.primary_nav = primary_nav
        self.page_stack = page_stack
        self.pages = pages
        self.writing_page = writing_page
        self.action_bar = action_bar
        self.app_header = app_header
        self.menu_bar = menu_bar
        self.status_bar = status_bar
        self.left_panel = left_panel
        self.inspector = inspector
        self.editor = editor
        self.output_container = output_container
        self.main_splitter = main_splitter
        self.outer_splitter = outer_splitter
        self.focus_action = focus_action
        self.exit_focus_button = exit_focus_button

        self._current_route = "dashboard"
        self._focus_mode = False
        self._left_panel_width = 270
        self._inspector_width = 330

    @property
    def current_route(self) -> str:
        return self._current_route

    @property
    def focus_mode(self) -> bool:
        return self._focus_mode

    def activate_route(self, route: str) -> None:
        """Show a route and apply the corresponding shared shell state."""
        if route in self.WRITING_ROUTES:
            self._focus_mode = False
            self._set_focus_chrome(True)
            self.left_panel.set_scope("canon" if route == "canon" else "all")
            self.page_stack.setCurrentWidget(self.writing_page)
        else:
            self.page_stack.setCurrentWidget(self.pages[route])
            self.action_bar.hide()

        self._current_route = route
        self.primary_nav.set_active(route)
        self._apply_action_bar_visibility()

    def toggle_focus_mode(self) -> None:
        self._focus_mode = not self._focus_mode
        self._set_focus_chrome(not self._focus_mode)
        margins = 80 if self._focus_mode else 18
        top = 30 if self._focus_mode else 14
        bottom = 20 if self._focus_mode else 12
        self.editor.layout().setContentsMargins(margins, top, margins, bottom)
        self.focus_action.setText("退出专注模式" if self._focus_mode else "专注模式")
        if self._focus_mode:
            self.editor.text_edit.setFocus()

    def exit_focus_mode(self) -> None:
        if self._focus_mode:
            self.toggle_focus_mode()

    def set_route_shell_visible(self, visible: bool) -> None:
        """Show or hide the normal application chrome around the editor."""
        self._set_focus_chrome(visible)

    def toggle_navigation_panel(self) -> None:
        self._toggle_side_panel(self.left_panel, 0, "_left_panel_width")

    def toggle_inspector(self) -> None:
        self._toggle_side_panel(self.inspector, 2, "_inspector_width")

    def toggle_output(self) -> None:
        self.output_container.setVisible(not self.output_container.isVisible())
        if self.output_container.isVisible():
            self.outer_splitter.setSizes([700, 180])

    def show_output(self, sizes: list[int] | tuple[int, int] = (690, 190)) -> None:
        """Show the AI output panel using the requested splitter proportions."""
        self.output_container.show()
        self.outer_splitter.setSizes(list(sizes))

    def remember_panel_sizes(self, _position: int, _index: int) -> None:
        sizes = self.main_splitter.sizes()
        if len(sizes) < 3:
            return
        if self.left_panel.isVisible() and sizes[0] >= self.left_panel.minimumWidth():
            self._left_panel_width = sizes[0]
        if self.inspector.isVisible() and sizes[2] >= self.inspector.minimumWidth():
            self._inspector_width = sizes[2]

    def _set_focus_chrome(self, visible: bool) -> None:
        if self._focus_mode:
            visible = False
        self.primary_nav.setVisible(visible)
        self.app_header.setVisible(visible)
        self.menu_bar.setVisible(visible)
        self.status_bar.setVisible(visible)
        self._apply_action_bar_visibility(visible)
        self.left_panel.setVisible(visible)
        self.inspector.setVisible(visible)
        self.exit_focus_button.setVisible(not visible)
        if not visible:
            self.output_container.hide()

    def _apply_action_bar_visibility(self, shell_visible: bool | None = None) -> None:
        if shell_visible is None:
            shell_visible = not self._focus_mode
        self.action_bar.setVisible(
            shell_visible and self._current_route in self.WRITING_ROUTES
        )

    def _toggle_side_panel(self, panel, index: int, width_attribute: str) -> None:
        if panel.isVisible():
            sizes = self.main_splitter.sizes()
            if len(sizes) > index and sizes[index] > 0:
                setattr(self, width_attribute, sizes[index])
            panel.hide()
            return
        panel.show()
        QTimer.singleShot(0, self._restore_side_panel_sizes)

    def _restore_side_panel_sizes(self) -> None:
        if self.main_splitter.width() <= 0:
            return
        left = self._left_panel_width if self.left_panel.isVisible() else 0
        right = self._inspector_width if self.inspector.isVisible() else 0
        center = max(1, self.main_splitter.width() - left - right)
        self.main_splitter.setSizes([left, center, right])
