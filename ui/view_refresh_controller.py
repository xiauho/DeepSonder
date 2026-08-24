"""Coordinate refreshes for views that depend on the active project."""

from __future__ import annotations

from PySide6.QtCore import QObject

from core.project import NovelProject
from ui.editor import Editor
from ui.inspector import Inspector
from ui.left_panel import LeftPanel
from ui.memory_page import StoryMemoryPage
from ui.pages import DashboardPage, ExportPage, ReportsPage
from ui.project_session import ProjectSession


class ViewRefreshController(QObject):
    """Keep project-bound pages in sync with one project session.

    The main window still decides which route is visible and owns shell-only
    state such as the title and status bar. This controller owns the repeated
    "push the current project into every view" details so new project data
    flows do not need to be added in several unrelated handlers.
    """

    def __init__(
        self,
        project_session: ProjectSession,
        editor: Editor,
        left_panel: LeftPanel,
        inspector: Inspector,
        dashboard_page: DashboardPage,
        memory_page: StoryMemoryPage,
        reports_page: ReportsPage,
        export_page: ExportPage,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_session = project_session
        self.editor = editor
        self.left_panel = left_panel
        self.inspector = inspector
        self.dashboard_page = dashboard_page
        self.memory_page = memory_page
        self.reports_page = reports_page
        self.export_page = export_page

        project_session.project_changed.connect(self.refresh_project)
        project_session.data_changed.connect(self.refresh_project_data)

    def refresh_project(self, project: NovelProject | None) -> None:
        """Refresh all views after switching to another project."""
        self.editor.clear_document("选择一份故事资料")
        self.left_panel.set_project(project)
        self.memory_page.show_project(project)
        self.reports_page.show_project(project)
        self.export_page.show_project(project)
        self.dashboard_page.refresh(project)
        self.inspector.show_project(project)

    def refresh_project_data(self, project: NovelProject) -> None:
        """Refresh views after files in the active project have changed."""
        self.left_panel.set_project(project)
        self.memory_page.show_project(project)
        self.reports_page.show_project(project)
        self.export_page.show_project(project)
        self.dashboard_page.refresh(project)
        self.refresh_inspector(project)

    def refresh_current_context(self, saved_path: str | None = None) -> None:
        """Refresh the current context without rebuilding unrelated pages.

        A path supplied by the document controller means one file was saved.
        Only the story radar depends on that immediate edit; project totals and
        export previews are intentionally refreshed by broader project events.
        """
        project = self.project_session.project
        if project is None:
            return
        self.refresh_inspector(project)
        if saved_path is not None:
            return
        self.dashboard_page.refresh(project)
        self.export_page.render_preview()

    def refresh_inspector(self, project: NovelProject | None = None) -> None:
        """Show the active chapter in the inspector, or project overview."""
        project = project if project is not None else self.project_session.project
        if project is None:
            self.inspector.show_project(None)
            return
        chapter_id = self.editor.current_chapter_id()
        self.inspector.show_project(project, chapter_id) if chapter_id else self.inspector.show_project(project)

    def refresh_route(self, route: str) -> None:
        """Refresh the page-specific data when a route becomes visible."""
        project = self.project_session.project
        if route == "memory":
            self.memory_page.show_project(project)
        elif route == "reports":
            self.reports_page.show_project(project)
        elif route == "export":
            self.export_page.show_project(project)
        elif route == "dashboard":
            self.dashboard_page.refresh(project)
