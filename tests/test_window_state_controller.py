import unittest

from PySide6.QtCore import QCoreApplication

from ui.window_state_controller import WindowStateController


class _FakeWidget:
    def __init__(self, visible=True):
        self.visible = visible
        self.current = None
        self.scope = None
        self.text = ""
        self.focused = False
        self._sizes = [270, 820, 330]
        self.layout_state = None

    def setVisible(self, visible):
        self.visible = visible

    def isVisible(self):
        return self.visible

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def set_scope(self, scope):
        self.scope = scope

    def set_active(self, route):
        self.active_route = route

    def setCurrentWidget(self, widget):
        self.current = widget

    def setText(self, text):
        self.text = text

    def setFocus(self):
        self.focused = True

    def minimumWidth(self):
        return 1

    def sizes(self):
        return list(self._sizes)

    def width(self):
        return 1420

    def setSizes(self, sizes):
        self._sizes = list(sizes)

    def layout(self):
        return self

    def setContentsMargins(self, *margins):
        self.layout_state = margins


class WindowStateControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def _controller(self):
        nav = _FakeWidget()
        page_stack = _FakeWidget()
        pages = {name: _FakeWidget() for name in ("dashboard", "memory", "reports", "export", "settings")}
        writing = _FakeWidget()
        action_bar = _FakeWidget()
        app_header = _FakeWidget()
        menu_bar = _FakeWidget()
        status_bar = _FakeWidget()
        left = _FakeWidget()
        inspector = _FakeWidget()
        editor = _FakeWidget()
        editor.text_edit = _FakeWidget()
        editor.exit_focus_button = _FakeWidget()
        output = _FakeWidget(False)
        main_splitter = _FakeWidget()
        outer_splitter = _FakeWidget()
        focus_action = _FakeWidget()
        controller = WindowStateController(
            primary_nav=nav,
            page_stack=page_stack,
            pages=pages,
            writing_page=writing,
            action_bar=action_bar,
            app_header=app_header,
            menu_bar=menu_bar,
            status_bar=status_bar,
            left_panel=left,
            inspector=inspector,
            editor=editor,
            output_container=output,
            main_splitter=main_splitter,
            outer_splitter=outer_splitter,
            focus_action=focus_action,
            exit_focus_button=editor.exit_focus_button,
        )
        return controller, locals()

    def test_route_activation_selects_page_and_scope(self):
        controller, views = self._controller()

        controller.activate_route("memory")
        self.assertIs(views["page_stack"].current, views["pages"]["memory"])
        self.assertEqual(views["nav"].text, "")
        self.assertFalse(views["action_bar"].visible)

        controller.activate_route("canon")
        self.assertIs(views["page_stack"].current, views["writing"])
        self.assertEqual(views["left"].scope, "canon")
        self.assertTrue(views["action_bar"].visible)
        self.assertEqual(controller.current_route, "canon")

    def test_focus_mode_hides_chrome_and_restores_editor(self):
        controller, views = self._controller()
        controller.activate_route("writing")

        controller.toggle_focus_mode()
        self.assertTrue(controller.focus_mode)
        self.assertFalse(views["nav"].visible)
        self.assertFalse(views["app_header"].visible)
        self.assertFalse(views["action_bar"].visible)
        self.assertTrue(views["editor"].text_edit.focused)
        self.assertEqual(views["editor"].layout_state, (80, 30, 80, 20))

        controller.toggle_focus_mode()
        self.assertFalse(controller.focus_mode)
        self.assertTrue(views["nav"].visible)
        self.assertTrue(views["action_bar"].visible)
        self.assertEqual(views["editor"].layout_state, (18, 14, 18, 12))


if __name__ == "__main__":
    unittest.main()
