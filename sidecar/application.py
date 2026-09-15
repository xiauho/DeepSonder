"""Allowlisted RPC adapter over the UI-independent application services."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from application.document_service import (
    DocumentNotFoundError,
    DocumentPathError,
    DocumentRevisionConflict,
    DocumentService,
    DocumentServiceError,
    DocumentSnapshot,
)
from application.project_service import OpenedProject, ProjectService
from application.project_content_service import (
    ProjectContentService,
    ProjectContentSnapshot,
    ProjectDocumentItem,
    TrashItem,
    TrashSnapshot,
)
from application.ai_task_service import AITaskService
from application.relationship_graph_service import (
    RelationshipGraphService,
    RelationshipGraphSnapshot,
)
from application.preferences_service import PreferencesService, PreferencesSnapshot
from application.document_v2_service import (
    DocumentV2Service,
    ManuscriptExportResult,
    ManuscriptSnapshot,
    ManuscriptTrashSnapshot,
)
from application.manuscript_import_service import (
    ManuscriptImportError,
    ManuscriptImportPlan,
    ManuscriptImportService,
)
from application.project_v2_service import ProjectV2Service
from application.reconstruction_service import (
    KnowledgeSnapshot,
    ReconstructionError,
    ReconstructionService,
    ReconstructionSnapshot,
)
from application.reconstruction_task_service import ReconstructionTaskService
from core.config import CONFIG_SCHEMA_VERSION
from core.project_data import (
    CanonEntryConflictError,
    CharacterIdConflictError,
    ChapterIdConflictError,
)
from core.project_migrations import ProjectMigrationError
from core.project_schema import PROJECT_SCHEMA_VERSION
from core.project_v2_schema import (
    PROJECT_V2_SCHEMA_VERSION,
    ProjectV2Descriptor,
    ProjectV2ValidationError,
)
from core.version import load_current_version
from sidecar.protocol import (
    RPC_PROTOCOL_VERSION,
    EventMessage,
    ProtocolFault,
    RequestEnvelope,
)


@dataclass(frozen=True)
class ApplicationResult:
    result: dict[str, Any]
    events: tuple[EventMessage, ...] = ()


class SidecarApplication:
    """Stateful, renderer-safe adapter for one active DeepSonder project."""

    SUPPORTED_METHODS = (
        "system.handshake",
        "system.ping",
        "system.shutdown",
        "request.cancel",
        "project.open",
        "project.create",
        "project.restoreLast",
        "project.close",
        "project.current",
        "project.openV2",
        "project.createV2",
        "project.snapshot",
        "manuscript.scanImport",
        "manuscript.snapshot",
        "manuscript.open",
        "manuscript.save",
        "manuscript.create",
        "manuscript.rename",
        "manuscript.reorder",
        "manuscript.delete",
        "manuscript.trashList",
        "manuscript.trashRestore",
        "manuscript.trashDeleteForever",
        "manuscript.appendImport",
        "manuscript.export",
        "reconstruction.snapshot",
        "reconstruction.generate",
        "reconstruction.batch",
        "reconstruction.review",
        "reconstruction.reviewMany",
        "reconstruction.taskStatus",
        "reconstruction.start",
        "reconstruction.cancel",
        "knowledge.snapshot",
        "knowledge.renameEntity",
        "knowledge.setEntityAliases",
        "knowledge.mergeEntities",
        "knowledge.unmergeEntity",
        "knowledge.updateRelation",
        "knowledge.deleteRelation",
        "knowledge.updateCharacterField",
        "knowledge.hideCharacterField",
        "knowledge.restoreCharacterField",
        "knowledge.updateWorld",
        "knowledge.hideWorld",
        "knowledge.restoreWorld",
        "knowledge.updateEvent",
        "knowledge.updateEventLinks",
        "knowledge.reorderEvents",
        "knowledge.hideEvent",
        "knowledge.restoreEvent",
        "knowledge.openCard",
        "knowledge.saveAuthorCard",
        "knowledge.reopenProposal",
        "graph.snapshot",
        "preferences.get",
        "preferences.update",
        "project.setSystemImportance",
        "document.open",
        "document.save",
        "document.createChapter",
        "document.createCanonEntry",
        "document.createTimeline",
        "document.importMarkdown",
        "document.delete",
        "trash.list",
        "trash.restore",
        "trash.deleteForever",
        "ai.status",
        "ai.start",
        "ai.cancel",
        "ai.result",
        "ai.discardResult",
        "ai.applyWritingResult",
        "ai.commitMemoryResult",
    )

    def __init__(
        self,
        *,
        project_service: ProjectService | None = None,
        document_service: DocumentService | None = None,
        content_service: ProjectContentService | None = None,
        ai_task_service: AITaskService | None = None,
        graph_service: RelationshipGraphService | None = None,
        preferences_service: PreferencesService | None = None,
        manuscript_import_service: ManuscriptImportService | None = None,
        project_v2_service: ProjectV2Service | None = None,
        document_v2_service: DocumentV2Service | None = None,
        reconstruction_service: ReconstructionService | None = None,
        reconstruction_task_service: ReconstructionTaskService | None = None,
    ) -> None:
        self.project_service = project_service or ProjectService()
        self.document_service = document_service or DocumentService()
        self.content_service = content_service or ProjectContentService()
        self.graph_service = graph_service or RelationshipGraphService()
        self.preferences_service = preferences_service or PreferencesService(
            project_service=self.project_service
        )
        self.manuscript_import_service = manuscript_import_service or ManuscriptImportService()
        self.project_v2_service = project_v2_service or ProjectV2Service()
        self.document_v2_service = document_v2_service or DocumentV2Service()
        self.reconstruction_service = reconstruction_service or ReconstructionService()
        self.ai_task_service = ai_task_service or AITaskService(
            document_v2_service=self.document_v2_service,
            reconstruction_service=self.reconstruction_service,
        )
        self.reconstruction_task_service = reconstruction_task_service or ReconstructionTaskService(
            reconstruction_service=self.reconstruction_service
        )
        self._opened: OpenedProject | None = None
        self._opened_v2: ProjectV2Descriptor | None = None
        self._import_plans: dict[str, ManuscriptImportPlan] = {}
        self._handlers: dict[
            str, Callable[[dict[str, Any]], ApplicationResult]
        ] = {
            "system.handshake": self._handshake,
            "system.ping": self._ping,
            "project.open": self._open_project,
            "project.create": self._create_project,
            "project.restoreLast": self._restore_last_project,
            "project.close": self._close_project,
            "project.current": self._current_project,
            "project.openV2": self._open_project_v2,
            "project.createV2": self._create_project_v2,
            "project.snapshot": self._project_snapshot,
            "manuscript.scanImport": self._scan_manuscript_import,
            "manuscript.snapshot": self._manuscript_snapshot,
            "manuscript.open": self._open_manuscript,
            "manuscript.save": self._save_manuscript,
            "manuscript.create": self._create_manuscript,
            "manuscript.rename": self._rename_manuscript,
            "manuscript.reorder": self._reorder_manuscript,
            "manuscript.delete": self._delete_manuscript,
            "manuscript.trashList": self._manuscript_trash_list,
            "manuscript.trashRestore": self._restore_manuscript_trash,
            "manuscript.trashDeleteForever": self._delete_manuscript_trash_forever,
            "manuscript.appendImport": self._append_manuscript_import,
            "manuscript.export": self._export_manuscript,
            "reconstruction.snapshot": self._reconstruction_snapshot,
            "reconstruction.generate": self._reconstruction_generate,
            "reconstruction.batch": self._reconstruction_batch,
            "reconstruction.review": self._reconstruction_review,
            "reconstruction.reviewMany": self._reconstruction_review_many,
            "reconstruction.taskStatus": self._reconstruction_task_status,
            "reconstruction.start": self._reconstruction_start,
            "reconstruction.cancel": self._reconstruction_cancel,
            "knowledge.snapshot": self._knowledge_snapshot,
            "knowledge.renameEntity": self._knowledge_rename_entity,
            "knowledge.setEntityAliases": self._knowledge_set_entity_aliases,
            "knowledge.mergeEntities": self._knowledge_merge_entities,
            "knowledge.unmergeEntity": self._knowledge_unmerge_entity,
            "knowledge.updateRelation": self._knowledge_update_relation,
            "knowledge.deleteRelation": self._knowledge_delete_relation,
            "knowledge.updateCharacterField": self._knowledge_update_character_field,
            "knowledge.hideCharacterField": self._knowledge_hide_character_field,
            "knowledge.restoreCharacterField": self._knowledge_restore_character_field,
            "knowledge.updateWorld": self._knowledge_update_world,
            "knowledge.hideWorld": self._knowledge_hide_world,
            "knowledge.restoreWorld": self._knowledge_restore_world,
            "knowledge.updateEvent": self._knowledge_update_event,
            "knowledge.updateEventLinks": self._knowledge_update_event_links,
            "knowledge.reorderEvents": self._knowledge_reorder_events,
            "knowledge.hideEvent": self._knowledge_hide_event,
            "knowledge.restoreEvent": self._knowledge_restore_event,
            "knowledge.openCard": self._knowledge_open_card,
            "knowledge.saveAuthorCard": self._knowledge_save_author_card,
            "knowledge.reopenProposal": self._knowledge_reopen_proposal,
            "graph.snapshot": self._graph_snapshot,
            "preferences.get": self._preferences_get,
            "preferences.update": self._preferences_update,
            "project.setSystemImportance": self._set_system_importance,
            "document.open": self._open_document,
            "document.save": self._save_document,
            "document.createChapter": self._create_chapter,
            "document.createCanonEntry": self._create_canon_entry,
            "document.createTimeline": self._create_timeline,
            "document.importMarkdown": self._import_markdown,
            "document.delete": self._delete_document,
            "trash.list": self._trash_list,
            "trash.restore": self._trash_restore,
            "trash.deleteForever": self._trash_delete_forever,
            "ai.status": self._ai_status,
            "ai.start": self._ai_start,
            "ai.cancel": self._ai_cancel,
            "ai.result": self._ai_result,
            "ai.discardResult": self._ai_discard_result,
            "ai.applyWritingResult": self._ai_apply_writing_result,
            "ai.commitMemoryResult": self._ai_commit_memory_result,
        }

    @property
    def supported_methods(self) -> tuple[str, ...]:
        return self.SUPPORTED_METHODS

    def dispatch(self, request: RequestEnvelope) -> ApplicationResult:
        handler = self._handlers.get(request.method)
        if handler is None:
            raise ProtocolFault(
                "METHOD_NOT_FOUND",
                "请求的方法未开放。",
                {"method": request.method},
            )
        try:
            return handler(request.params)
        except ProtocolFault:
            raise
        except Exception as exc:
            raise fault_from_exception(exc) from exc

    def _handshake(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"clientName", "clientVersion"})
        client_name = _optional_string(params, "clientName", max_length=100)
        client_version = _optional_string(params, "clientVersion", max_length=100)
        version = str(load_current_version())
        return ApplicationResult(
            {
                "applicationVersion": version,
                "protocolVersion": RPC_PROTOCOL_VERSION,
                "sidecarBuildVersion": version,
                "supportedMethods": list(self.supported_methods),
                "projectSchema": {
                    "minimum": PROJECT_SCHEMA_VERSION,
                    "maximum": PROJECT_V2_SCHEMA_VERSION,
                },
                "configSchema": {
                    "minimum": CONFIG_SCHEMA_VERSION,
                    "maximum": CONFIG_SCHEMA_VERSION,
                },
                "client": {
                    "name": client_name,
                    "version": client_version,
                },
            }
        )

    @staticmethod
    def _ping(params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        return ApplicationResult({"alive": True})

    def _open_project(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"path", "remember"})
        self._require_ai_idle()
        remember = _optional_bool(params, "remember", False)
        opened = self.project_service.open_project(_required_string(params, "path"))
        self._opened = opened
        self._opened_v2 = None
        if remember:
            self.preferences_service.remember_project(opened.project.root)
        project = opened_project_dto(opened)
        return ApplicationResult(
            {"opened": project},
            (EventMessage("project.opened", {"opened": project}),),
        )

    def _create_project(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"parentDirectory", "name", "author", "remember"})
        self._require_ai_idle()
        remember = _optional_bool(params, "remember", False)
        opened = self.project_service.create_project(
            _required_string(params, "parentDirectory"),
            _required_string(params, "name", allow_blank=False),
            author=_optional_string(params, "author", max_length=500),
        )
        self._opened = opened
        self._opened_v2 = None
        if remember:
            self.preferences_service.remember_project(opened.project.root)
        project = opened_project_dto(opened)
        return ApplicationResult(
            {"opened": project},
            (EventMessage("project.opened", {"opened": project}),),
        )

    def _restore_last_project(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        self._require_ai_idle()
        path = self.preferences_service.last_project()
        if path is None:
            return ApplicationResult({"opened": None})
        opened = self.project_service.open_project(path)
        self._opened = opened
        self._opened_v2 = None
        project = opened_project_dto(opened)
        return ApplicationResult(
            {"opened": project},
            (EventMessage("project.opened", {"opened": project}),),
        )

    def _close_project(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        self._require_ai_idle()
        closed_root = (
            str(self._opened.project.root)
            if self._opened is not None
            else str(self._opened_v2.root) if self._opened_v2 is not None else None
        )
        self._opened = None
        self._opened_v2 = None
        return ApplicationResult(
            {"closed": closed_root is not None},
            (EventMessage("project.closed", {"root": closed_root}),)
            if closed_root is not None
            else (),
        )

    def _current_project(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        return ApplicationResult(
            {
                "opened": opened_project_dto(self._opened)
                if self._opened is not None
                else None
            }
        )

    def _scan_manuscript_import(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"sourcePath"})
        plan = self.manuscript_import_service.scan(
            _required_string(params, "sourcePath", max_length=32_767)
        )
        self._import_plans[plan.digest] = plan
        while len(self._import_plans) > 4:
            self._import_plans.pop(next(iter(self._import_plans)))
        return ApplicationResult({"plan": manuscript_import_plan_dto(plan)})

    def _create_project_v2(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"parentDirectory", "name", "author", "planDigest"})
        self._require_ai_idle()
        plan_digest = _optional_string(params, "planDigest", max_length=100)
        plan = None
        if plan_digest:
            cached = self._import_plans.get(plan_digest)
            if cached is None:
                raise ProtocolFault("IMPORT_PLAN_EXPIRED", "正文导入计划已过期，请重新扫描。")
            rescanned = self.manuscript_import_service.scan(cached.source_path)
            if rescanned.digest != cached.digest:
                self._import_plans.pop(plan_digest, None)
                raise ProtocolFault("IMPORT_SOURCE_CHANGED", "正文来源已发生变化，请重新预览。")
            plan = rescanned
        opened = self.project_v2_service.create_project(
            _required_string(params, "parentDirectory", max_length=32_767),
            _required_string(params, "name", max_length=200),
            author=_optional_string(params, "author", max_length=500),
            import_plan=plan,
        )
        self._opened = None
        self._opened_v2 = opened
        if plan_digest:
            self._import_plans.pop(plan_digest, None)
        dto = opened_project_v2_dto(opened)
        return ApplicationResult(
            {"opened": dto},
            (EventMessage("projectV2.opened", {"opened": dto}),),
        )

    def _open_project_v2(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"path"})
        self._require_ai_idle()
        opened = self.project_v2_service.open_project(
            _required_string(params, "path", max_length=32_767)
        )
        self._opened = None
        self._opened_v2 = opened
        dto = opened_project_v2_dto(opened)
        return ApplicationResult(
            {"opened": dto},
            (EventMessage("projectV2.opened", {"opened": dto}),),
        )

    def _manuscript_snapshot(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        snapshot = self.document_v2_service.snapshot(self._require_project_v2())
        return ApplicationResult({"snapshot": manuscript_snapshot_dto(snapshot)})

    def _open_manuscript(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"chapterId"})
        document = self.document_v2_service.open_document(
            self._require_project_v2(),
            _required_string(params, "chapterId", max_length=100),
        )
        return ApplicationResult({"document": document_snapshot_dto(document)})

    def _save_manuscript(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"chapterId", "content", "expectedRevision", "force"})
        self._require_reconstruction_idle()
        if "expectedRevision" not in params:
            raise ProtocolFault("INVALID_PARAMS", "manuscript.save 缺少 expectedRevision。")
        expected_revision = params.get("expectedRevision")
        if expected_revision is not None and not isinstance(expected_revision, str):
            raise ProtocolFault("INVALID_PARAMS", "expectedRevision 必须是字符串或 null。")
        force = _optional_bool(params, "force", False)
        document = self.document_v2_service.save_document(
            self._require_project_v2(),
            _required_string(params, "chapterId", max_length=100),
            _required_string(params, "content", allow_blank=True),
            expected_revision=expected_revision,
            force=force,
        )
        invalidated = self.reconstruction_service.invalidate_chapter(
            self._require_project_v2(),
            _required_string(params, "chapterId", max_length=100),
        )
        return ApplicationResult(
            {"document": document_snapshot_dto(document)},
            (EventMessage("manuscript.changed", {
                "chapterId": _required_string(params, "chapterId", max_length=100),
                "relativePath": document.relative_path,
                "revision": document.revision,
                "reconstructionInvalidated": invalidated,
            }),),
        )

    def _create_manuscript(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"title", "afterChapterId"})
        self._require_reconstruction_idle()
        project = self._require_project_v2()
        after_chapter_id = _optional_string(params, "afterChapterId", max_length=100)
        snapshot, document = self.document_v2_service.create_chapter(
            project,
            _required_string(params, "title", max_length=200),
            after_chapter_id=after_chapter_id or None,
        )
        invalidated = self.reconstruction_service.invalidate_manuscript(project)
        return ApplicationResult(
            {
                "snapshot": manuscript_snapshot_dto(snapshot),
                "document": document_snapshot_dto(document),
                "reconstructionInvalidated": invalidated,
            },
            (self._manuscript_structure_event("created", Path(document.relative_path).stem, invalidated),),
        )

    def _rename_manuscript(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"chapterId", "title", "expectedRevision"})
        self._require_reconstruction_idle()
        expected_revision = params.get("expectedRevision")
        if expected_revision is not None and not isinstance(expected_revision, str):
            raise ProtocolFault("INVALID_PARAMS", "expectedRevision 必须是字符串或 null。")
        project = self._require_project_v2()
        chapter_id = _required_string(params, "chapterId", max_length=100)
        snapshot, document = self.document_v2_service.rename_chapter(
            project,
            chapter_id,
            _required_string(params, "title", max_length=200),
            expected_revision=expected_revision,
        )
        invalidated = self.reconstruction_service.invalidate_chapter(project, chapter_id)
        return ApplicationResult(
            {
                "snapshot": manuscript_snapshot_dto(snapshot),
                "document": document_snapshot_dto(document),
                "reconstructionInvalidated": invalidated,
            },
            (self._manuscript_structure_event("renamed", chapter_id, invalidated),),
        )

    def _reorder_manuscript(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"chapterIds"})
        self._require_reconstruction_idle()
        chapter_ids = params.get("chapterIds")
        if (
            not isinstance(chapter_ids, list)
            or len(chapter_ids) > 10_000
            or not all(isinstance(value, str) and 0 < len(value) <= 100 for value in chapter_ids)
        ):
            raise ProtocolFault("INVALID_PARAMS", "章节排序参数无效。")
        project = self._require_project_v2()
        snapshot = self.document_v2_service.reorder_chapters(project, chapter_ids)
        invalidated = self.reconstruction_service.invalidate_manuscript(project)
        return ApplicationResult(
            {"snapshot": manuscript_snapshot_dto(snapshot), "reconstructionInvalidated": invalidated},
            (self._manuscript_structure_event("reordered", "", invalidated),),
        )

    def _delete_manuscript(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"chapterId"})
        self._require_reconstruction_idle()
        project = self._require_project_v2()
        chapter_id = _required_string(params, "chapterId", max_length=100)
        snapshot, trash, deleted = self.document_v2_service.delete_chapter(project, chapter_id)
        invalidated = self.reconstruction_service.invalidate_chapter(project, chapter_id)
        return ApplicationResult(
            {
                "snapshot": manuscript_snapshot_dto(snapshot),
                "trash": manuscript_trash_snapshot_dto(trash),
                "deletedTrashId": deleted.trash_id,
                "reconstructionInvalidated": invalidated,
            },
            (self._manuscript_structure_event("deleted", chapter_id, invalidated),),
        )

    def _manuscript_trash_list(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        trash = self.document_v2_service.trash_snapshot(self._require_project_v2())
        return ApplicationResult({"trash": manuscript_trash_snapshot_dto(trash)})

    def _restore_manuscript_trash(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"trashId"})
        self._require_reconstruction_idle()
        project = self._require_project_v2()
        snapshot, trash, document = self.document_v2_service.restore_chapter(
            project, _required_string(params, "trashId", max_length=100)
        )
        invalidated = self.reconstruction_service.invalidate_manuscript(project)
        return ApplicationResult(
            {
                "snapshot": manuscript_snapshot_dto(snapshot),
                "trash": manuscript_trash_snapshot_dto(trash),
                "document": document_snapshot_dto(document),
                "reconstructionInvalidated": invalidated,
            },
            (self._manuscript_structure_event("restored", Path(document.relative_path).stem, invalidated),),
        )

    def _delete_manuscript_trash_forever(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"trashId"})
        trash = self.document_v2_service.delete_trash_forever(
            self._require_project_v2(), _required_string(params, "trashId", max_length=100)
        )
        return ApplicationResult({"trash": manuscript_trash_snapshot_dto(trash)})

    def _append_manuscript_import(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"planDigest", "afterChapterId"})
        self._require_reconstruction_idle()
        plan_digest = _required_string(params, "planDigest", max_length=100)
        cached = self._import_plans.get(plan_digest)
        if cached is None:
            raise ProtocolFault("IMPORT_PLAN_EXPIRED", "正文导入计划已过期，请重新扫描。")
        rescanned = self.manuscript_import_service.scan(cached.source_path)
        if rescanned.digest != cached.digest:
            self._import_plans.pop(plan_digest, None)
            raise ProtocolFault("IMPORT_SOURCE_CHANGED", "正文来源已发生变化，请重新预览。")
        project = self._require_project_v2()
        after_chapter_id = _optional_string(params, "afterChapterId", max_length=100)
        snapshot, document = self.document_v2_service.append_import(
            project, rescanned, after_chapter_id=after_chapter_id or None,
        )
        self._import_plans.pop(plan_digest, None)
        invalidated = self.reconstruction_service.invalidate_manuscript(project)
        return ApplicationResult(
            {
                "snapshot": manuscript_snapshot_dto(snapshot),
                "document": document_snapshot_dto(document),
                "reconstructionInvalidated": invalidated,
            },
            (self._manuscript_structure_event("imported", Path(document.relative_path).stem, invalidated),),
        )

    def _export_manuscript(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"destination", "format"})
        exported = self.document_v2_service.export_manuscript(
            self._require_project_v2(),
            _required_string(params, "destination", max_length=32_767),
            format_name=_required_string(params, "format", max_length=10),
        )
        return ApplicationResult({"exported": manuscript_export_dto(exported)})

    @staticmethod
    def _manuscript_structure_event(action: str, chapter_id: str, invalidated: bool) -> EventMessage:
        return EventMessage("manuscript.structureChanged", {
            "action": action,
            "chapterId": chapter_id,
            "reconstructionInvalidated": invalidated,
        })

    def _reconstruction_snapshot(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        snapshot = self.reconstruction_service.snapshot(self._require_project_v2())
        return ApplicationResult({"reconstruction": reconstruction_snapshot_dto(snapshot)})

    def _reconstruction_generate(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        self._require_reconstruction_idle()
        batch = self.reconstruction_service.generate(self._require_project_v2())
        return ApplicationResult(
            {"batch": reconstruction_batch_dto(batch)},
            (EventMessage("reconstruction.updated", {"batchId": batch["batch_id"]}),),
        )

    def _reconstruction_batch(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"batchId"})
        batch = self.reconstruction_service.get_batch(
            self._require_project_v2(),
            _required_string(params, "batchId", max_length=100),
        )
        return ApplicationResult({"batch": reconstruction_batch_dto(batch)})

    def _reconstruction_review(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"batchId", "proposalId", "decision"})
        self._require_reconstruction_idle()
        batch = self.reconstruction_service.review(
            self._require_project_v2(),
            _required_string(params, "batchId", max_length=100),
            _required_string(params, "proposalId", max_length=100),
            _required_string(params, "decision", max_length=20),
        )
        return ApplicationResult(
            {"batch": reconstruction_batch_dto(batch)},
            (EventMessage("reconstruction.updated", {"batchId": batch["batch_id"]}),),
        )

    def _reconstruction_review_many(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"batchId", "decisions"})
        self._require_reconstruction_idle()
        decisions = params.get("decisions")
        if not isinstance(decisions, list) or not 1 <= len(decisions) <= 200:
            raise ProtocolFault("INVALID_PARAMS", "批量审核必须包含 1 至 200 项。")
        normalized: list[dict[str, str]] = []
        for item in decisions:
            if not isinstance(item, dict) or set(item) != {"proposalId", "decision"}:
                raise ProtocolFault("INVALID_PARAMS", "批量审核项格式无效。")
            proposal_id = item.get("proposalId")
            decision = item.get("decision")
            if not isinstance(proposal_id, str) or not proposal_id.strip() or len(proposal_id) > 100 or decision not in {"accepted", "rejected"}:
                raise ProtocolFault("INVALID_PARAMS", "批量审核项参数无效。")
            normalized.append({"proposal_id": proposal_id, "decision": decision})
        batch = self.reconstruction_service.review_many(
            self._require_project_v2(), _required_string(params, "batchId", max_length=100), normalized,
        )
        return ApplicationResult(
            {"batch": reconstruction_batch_dto(batch)},
            (EventMessage("reconstruction.updated", {"batchId": batch["batch_id"]}),),
        )

    def _reconstruction_task_status(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        return ApplicationResult({"reconstructionTask": self.reconstruction_task_service.status()})

    def _reconstruction_start(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"mode", "remoteConsent"})
        self._require_ai_idle()
        mode = params.get("mode", "local")
        consent = params.get("remoteConsent", False)
        if mode not in {"local", "dsh"} or not isinstance(consent, bool):
            raise ProtocolFault("INVALID_PARAMS", "正文识别模式或远程授权无效。")
        if mode == "dsh" and not consent:
            raise ProtocolFault("INVALID_PARAMS", "DSH 增强识别需要明确的本次发送授权。")
        task = self.reconstruction_task_service.start(
            self._require_project_v2(), extraction_mode=mode, remote_consent=consent,
        )
        return ApplicationResult({"task": task})

    def _reconstruction_cancel(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"taskId"})
        task = self.reconstruction_task_service.cancel(
            _required_string(params, "taskId", max_length=100)
        )
        return ApplicationResult({"task": task})

    def _knowledge_snapshot(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        snapshot = self.reconstruction_service.knowledge_snapshot(self._require_project_v2())
        return ApplicationResult({"knowledge": knowledge_snapshot_dto(snapshot)})

    def _knowledge_rename_entity(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"entityId", "displayName"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.rename_entity(
            self._require_project_v2(), _required_string(params, "entityId", max_length=100),
            _required_string(params, "displayName", max_length=80),
        )
        return self._knowledge_changed(snapshot, "entity.rename")

    def _knowledge_set_entity_aliases(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"entityId", "aliases"})
        self._require_reconstruction_idle()
        aliases = params.get("aliases")
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            raise ProtocolFault("INVALID_PARAMS", "aliases 必须是字符串数组。")
        snapshot = self.reconstruction_service.set_entity_aliases(
            self._require_project_v2(), _required_string(params, "entityId", max_length=100), aliases,
        )
        return self._knowledge_changed(snapshot, "entity.aliases")

    def _knowledge_merge_entities(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"sourceEntityId", "targetEntityId"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.merge_entities(
            self._require_project_v2(),
            _required_string(params, "sourceEntityId", max_length=100),
            _required_string(params, "targetEntityId", max_length=100),
        )
        return self._knowledge_changed(snapshot, "entity.merge")

    def _knowledge_unmerge_entity(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"sourceEntityId"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.unmerge_entity(
            self._require_project_v2(), _required_string(params, "sourceEntityId", max_length=100)
        )
        return self._knowledge_changed(snapshot, "entity.unmerge")

    def _knowledge_update_relation(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"relationId", "sourceEntityId", "targetEntityId", "label"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.update_relation(
            self._require_project_v2(),
            _required_string(params, "relationId", max_length=100),
            _required_string(params, "sourceEntityId", max_length=100),
            _required_string(params, "targetEntityId", max_length=100),
            _required_string(params, "label", max_length=80),
        )
        return self._knowledge_changed(snapshot, "relation.update")

    def _knowledge_delete_relation(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"relationId"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.delete_relation(
            self._require_project_v2(), _required_string(params, "relationId", max_length=100)
        )
        return self._knowledge_changed(snapshot, "relation.delete")

    def _knowledge_update_character_field(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"entityId", "field", "value"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.update_character_field(
            self._require_project_v2(), _required_string(params, "entityId", max_length=100),
            _required_string(params, "field", max_length=20),
            _required_string(params, "value", max_length=200),
        )
        return self._knowledge_changed(snapshot, "character_field.update")

    def _knowledge_hide_character_field(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"entityId", "field"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.hide_character_field(
            self._require_project_v2(), _required_string(params, "entityId", max_length=100),
            _required_string(params, "field", max_length=20),
        )
        return self._knowledge_changed(snapshot, "character_field.hide")

    def _knowledge_restore_character_field(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"entityId", "field"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.restore_character_field(
            self._require_project_v2(), _required_string(params, "entityId", max_length=100),
            _required_string(params, "field", max_length=20),
        )
        return self._knowledge_changed(snapshot, "character_field.restore")

    def _knowledge_update_world(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"worldId", "name", "category", "description"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.update_world(
            self._require_project_v2(), _required_string(params, "worldId", max_length=100),
            _required_string(params, "name", max_length=80),
            _required_string(params, "category", max_length=80),
            _required_string(params, "description", max_length=200),
        )
        return self._knowledge_changed(snapshot, "world.update")

    def _knowledge_hide_world(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"worldId"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.hide_world(
            self._require_project_v2(), _required_string(params, "worldId", max_length=100)
        )
        return self._knowledge_changed(snapshot, "world.hide")

    def _knowledge_restore_world(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"worldId"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.restore_world(
            self._require_project_v2(), _required_string(params, "worldId", max_length=100)
        )
        return self._knowledge_changed(snapshot, "world.restore")

    def _knowledge_update_event(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"eventId", "timeLabel", "title", "description"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.update_event(
            self._require_project_v2(), _required_string(params, "eventId", max_length=100),
            _required_string(params, "timeLabel", max_length=40),
            _required_string(params, "title", max_length=80),
            _required_string(params, "description", max_length=200),
        )
        return self._knowledge_changed(snapshot, "event.update")

    def _knowledge_update_event_links(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"eventId", "participantEntityIds", "worldIds"})
        self._require_reconstruction_idle()
        participants = params.get("participantEntityIds")
        worlds = params.get("worldIds")
        if (
            not isinstance(participants, list) or len(participants) > 200
            or not all(isinstance(item, str) for item in participants)
            or not isinstance(worlds, list) or len(worlds) > 100
            or not all(isinstance(item, str) for item in worlds)
        ):
            raise ProtocolFault("INVALID_PARAMS", "事件语义连接必须是有界 ID 数组。")
        snapshot = self.reconstruction_service.update_event_links(
            self._require_project_v2(), _required_string(params, "eventId", max_length=100),
            participants, worlds,
        )
        return self._knowledge_changed(snapshot, "event.links.update")

    def _knowledge_reorder_events(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"eventIds"})
        self._require_reconstruction_idle()
        event_ids = params.get("eventIds")
        if (
            not isinstance(event_ids, list) or len(event_ids) > 1000
            or not all(isinstance(item, str) for item in event_ids)
        ):
            raise ProtocolFault("INVALID_PARAMS", "事件排序必须是有界 ID 数组。")
        snapshot = self.reconstruction_service.reorder_events(
            self._require_project_v2(), event_ids,
        )
        return self._knowledge_changed(snapshot, "event.order.update")

    def _knowledge_hide_event(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"eventId"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.hide_event(
            self._require_project_v2(), _required_string(params, "eventId", max_length=100),
        )
        return self._knowledge_changed(snapshot, "event.hide")

    def _knowledge_restore_event(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"eventId"})
        self._require_reconstruction_idle()
        snapshot = self.reconstruction_service.restore_event(
            self._require_project_v2(), _required_string(params, "eventId", max_length=100),
        )
        return self._knowledge_changed(snapshot, "event.restore")

    def _knowledge_open_card(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"ownerKind", "ownerId", "mode"})
        card = self.reconstruction_service.open_knowledge_card(
            self._require_project_v2(), _required_string(params, "ownerKind", max_length=20),
            _required_string(params, "ownerId", max_length=100),
            _required_string(params, "mode", max_length=20),
        )
        return ApplicationResult({"card": knowledge_card_dto(card)})

    def _knowledge_save_author_card(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"ownerKind", "ownerId", "content", "expectedRevision"})
        self._require_reconstruction_idle()
        content = params.get("content")
        if not isinstance(content, str) or len(content) > 200_000:
            raise ProtocolFault("INVALID_PARAMS", "作者卡内容无效或超过上限。")
        card = self.reconstruction_service.save_author_card(
            self._require_project_v2(), _required_string(params, "ownerKind", max_length=20),
            _required_string(params, "ownerId", max_length=100), content,
            _required_string(params, "expectedRevision", max_length=100),
        )
        return ApplicationResult(
            {"card": knowledge_card_dto(card)},
            (EventMessage("knowledge.updated", {"kind": "author_card.save"}),),
        )

    def _knowledge_reopen_proposal(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"batchId", "proposalId"})
        self._require_reconstruction_idle()
        batch = self.reconstruction_service.reopen_proposal(
            self._require_project_v2(), _required_string(params, "batchId", max_length=100),
            _required_string(params, "proposalId", max_length=100),
        )
        return ApplicationResult(
            {"batch": reconstruction_batch_dto(batch)},
            (EventMessage("knowledge.updated", {"kind": "proposal.reopen"}),),
        )

    @staticmethod
    def _knowledge_changed(snapshot: KnowledgeSnapshot, kind: str) -> ApplicationResult:
        return ApplicationResult(
            {"knowledge": knowledge_snapshot_dto(snapshot)},
            (EventMessage("knowledge.updated", {"kind": kind}),),
        )

    def _graph_snapshot(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        if self._opened_v2 is not None:
            return ApplicationResult(
                {"graph": relationship_graph_snapshot_dto(
                    self.reconstruction_service.graph_snapshot(self._opened_v2)
                )}
            )
        opened = self._require_project()
        return ApplicationResult(
            {"graph": relationship_graph_snapshot_dto(self.graph_service.snapshot(opened.project))}
        )

    def _preferences_get(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        return ApplicationResult(
            {"preferences": preferences_snapshot_dto(self.preferences_service.snapshot())}
        )

    def _preferences_update(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"patch"})
        patch = params.get("patch")
        if not isinstance(patch, dict):
            raise ProtocolFault("INVALID_PARAMS", "patch 必须是对象。")
        return ApplicationResult(
            {"preferences": preferences_snapshot_dto(self.preferences_service.update(patch))}
        )

    def _open_document(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"category", "path"})
        opened = self._require_project()
        snapshot = self.document_service.open_document(
            opened.project,
            _required_string(params, "category", allow_blank=True),
            _required_string(params, "path"),
        )
        return ApplicationResult({"document": document_snapshot_dto(snapshot)})

    def _project_snapshot(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        opened = self._require_project()
        snapshot = self.content_service.snapshot(opened.project, opened.data_store)
        return ApplicationResult({"snapshot": project_snapshot_dto(snapshot)})

    def _set_system_importance(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"path", "importance"})
        opened = self._require_project()
        path = self.content_service.set_system_importance(
            opened.data_store,
            _required_string(params, "path"),
            _required_string(params, "importance"),
        )
        return self._content_changed(
            "canon",
            (path, opened.data_store.system_registry_path),
        )

    def _save_document(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(
            params,
            {"category", "path", "content", "expectedRevision", "force"},
        )
        opened = self._require_project()
        if "expectedRevision" not in params:
            raise ProtocolFault(
                "INVALID_PARAMS", "document.save 缺少 expectedRevision。"
            )
        expected_revision = params.get("expectedRevision")
        if expected_revision is not None and not isinstance(expected_revision, str):
            raise ProtocolFault(
                "INVALID_PARAMS", "expectedRevision 必须是字符串或 null。"
            )
        force = params.get("force", False)
        if not isinstance(force, bool):
            raise ProtocolFault("INVALID_PARAMS", "force 必须是布尔值。")
        snapshot = self.document_service.save_document(
            opened.project,
            _required_string(params, "category", allow_blank=True),
            _required_string(params, "path"),
            _required_string(params, "content", allow_blank=True),
            expected_revision=expected_revision,
            force=force,
        )
        document = document_snapshot_dto(snapshot)
        return ApplicationResult(
            {"document": document},
            (
                EventMessage(
                    "document.changed",
                    {
                        "path": snapshot.path,
                        "relativePath": snapshot.relative_path,
                        "kind": "file",
                        "revision": snapshot.revision,
                    },
                ),
            ),
        )

    def _create_chapter(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"title", "chapterId"})
        opened = self._require_project()
        mutation = self.document_service.create_chapter(
            opened.project,
            opened.data_store,
            _required_string(params, "title"),
            _required_string(params, "chapterId"),
        )
        return self._mutation_result(mutation)

    def _create_canon_entry(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"kind", "title"})
        opened = self._require_project()
        kind = _required_string(params, "kind")
        if kind not in {"character", "world", "power"}:
            raise ProtocolFault("INVALID_PARAMS", "不支持的故事资料类型。")
        mutation = self.document_service.create_canon_entry(
            opened.project,
            opened.data_store,
            kind,
            _required_string(params, "title"),
        )
        return self._mutation_result(mutation)

    def _create_timeline(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        opened = self._require_project()
        return self._mutation_result(
            self.document_service.create_timeline(
                opened.project, opened.data_store
            )
        )

    def _import_markdown(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"sources"})
        sources = params.get("sources")
        if (
            not isinstance(sources, list)
            or not sources
            or len(sources) > 100
            or any(not isinstance(item, str) or not item.strip() for item in sources)
        ):
            raise ProtocolFault(
                "INVALID_PARAMS", "sources 必须包含 1 至 100 个文件路径。"
            )
        opened = self._require_project()
        mutations = self.document_service.import_markdown(
            opened.project, opened.data_store, sources
        )
        changed_paths = tuple(
            path
            for mutation in mutations
            for path in mutation.changed_paths
        )
        snapshot = self.content_service.snapshot(opened.project, opened.data_store)
        return ApplicationResult(
            {
                "imported": [mutation_dto(item) for item in mutations],
                "snapshot": project_snapshot_dto(snapshot),
            },
            (self._content_event("chapter", changed_paths),),
        )

    def _delete_document(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"kind", "itemId", "path"})
        opened = self._require_project()
        kind = _required_string(params, "kind")
        if kind == "chapter":
            mutation = self.document_service.delete_chapter(
                opened.project,
                opened.data_store,
                _required_string(params, "itemId"),
            )
        elif kind == "character":
            mutation = self.document_service.delete_character(
                opened.project,
                opened.data_store,
                _required_string(params, "itemId"),
            )
        elif kind in {"world", "power", "timeline"}:
            mutation = self.document_service.delete_canon_entry(
                opened.project,
                opened.data_store,
                kind,
                _required_string(params, "path"),
            )
        else:
            raise ProtocolFault("INVALID_PARAMS", "该文档类型不能移入回收站。")
        return self._mutation_result(mutation)

    def _trash_list(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        opened = self._require_project()
        trash = self.content_service.trash_snapshot(opened.data_store)
        return ApplicationResult({"trash": trash_snapshot_dto(trash)})

    def _trash_restore(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"kind", "trashId", "conflictPolicy"})
        opened = self._require_project()
        policy = params.get("conflictPolicy", "error")
        if not isinstance(policy, str):
            raise ProtocolFault("INVALID_PARAMS", "conflictPolicy 必须是字符串。")
        restored = self.content_service.restore_trash_item(
            opened.project,
            opened.data_store,
            _required_string(params, "kind"),
            _required_string(params, "trashId"),
            conflict_policy=policy,
        )
        snapshot = self.content_service.snapshot(opened.project, opened.data_store)
        trash = self.content_service.trash_snapshot(opened.data_store)
        return ApplicationResult(
            {
                "restoredPath": str(restored),
                "snapshot": project_snapshot_dto(snapshot),
                "trash": trash_snapshot_dto(trash),
            },
            (self._content_event("restore", (restored,)),),
        )

    def _trash_delete_forever(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"kind", "trashId"})
        opened = self._require_project()
        kind = _required_string(params, "kind")
        trash_id = _required_string(params, "trashId")
        self.content_service.delete_trash_item_forever(
            opened.data_store, kind, trash_id
        )
        trash = self.content_service.trash_snapshot(opened.data_store)
        return ApplicationResult(
            {"deleted": True, "trash": trash_snapshot_dto(trash)},
            (
                EventMessage(
                    "trash.changed", {"kind": kind, "trashId": trash_id}
                ),
            ),
        )

    def _ai_status(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, set())
        return ApplicationResult({
            "ai": self.ai_task_service.status(project_root=self._ai_project_root())
        })

    def _ai_start(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(
            params,
            {"kind", "chapterId", "sourceRevision", "options", "noticeAccepted"},
        )
        kind = _required_string(params, "kind")
        self._require_ai_idle()
        options = params.get("options", {})
        if not isinstance(options, dict):
            raise ProtocolFault("INVALID_PARAMS", "options 必须是对象。")
        _validate_ai_options(options)
        notice = params.get("noticeAccepted", False)
        if not isinstance(notice, bool):
            raise ProtocolFault("INVALID_PARAMS", "noticeAccepted 必须是布尔值。")
        source_revision = params.get("sourceRevision")
        if source_revision is not None and not isinstance(source_revision, str):
            raise ProtocolFault("INVALID_PARAMS", "sourceRevision 必须是字符串或 null。")
        if kind == "connection":
            project = None
        elif self._opened_v2 is not None:
            project = self._opened_v2
        else:
            project = self._require_project().project
        task = self.ai_task_service.start(
            project,
            kind,
            _optional_string(params, "chapterId", max_length=200),
            source_revision=source_revision,
            options=options,
            notice_accepted=notice,
        )
        return ApplicationResult({"task": task})

    def _ai_cancel(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"taskId"})
        return ApplicationResult(
            {"task": self.ai_task_service.cancel(
                _required_string(params, "taskId"),
                project_root=self._ai_project_root(),
            )}
        )

    def _ai_result(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"taskId"})
        return ApplicationResult(
            self.ai_task_service.result(
                _required_string(params, "taskId"),
                project_root=self._ai_project_root(),
            )
        )

    def _ai_discard_result(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"taskId"})
        return ApplicationResult(
            {"task": self.ai_task_service.discard(
                _required_string(params, "taskId"),
                project_root=self._ai_project_root(),
            )}
        )

    def _ai_apply_writing_result(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"taskId"})
        task_id = _required_string(params, "taskId")
        if self._opened_v2 is not None:
            document = self.ai_task_service.apply_writing_result(
                self._opened_v2,
                self.document_v2_service,
                task_id,
            )
            invalidated = self.reconstruction_service.invalidate_chapter(
                self._opened_v2,
                Path(document.relative_path).stem,
            )
            return ApplicationResult(
                {"document": document_snapshot_dto(document)},
                (EventMessage("manuscript.changed", {
                    "chapterId": Path(document.relative_path).stem,
                    "relativePath": document.relative_path,
                    "revision": document.revision,
                    "reconstructionInvalidated": invalidated,
                }),),
            )
        opened = self._require_project()
        document = self.ai_task_service.apply_writing_result(
            opened.project,
            self.document_service,
            task_id,
        )
        dto = document_snapshot_dto(document)
        return ApplicationResult(
            {"document": dto},
            (
                EventMessage(
                    "document.changed",
                    {
                        "path": document.path,
                        "relativePath": document.relative_path,
                        "kind": "file",
                        "revision": document.revision,
                    },
                ),
            ),
        )

    def _ai_commit_memory_result(self, params: dict[str, Any]) -> ApplicationResult:
        _only_keys(params, {"taskId"})
        task_id = _required_string(params, "taskId")
        if self._opened_v2 is not None:
            committed = self.ai_task_service.commit_memory_result(
                self._opened_v2, task_id
            )
            return ApplicationResult(
                {"committed": committed},
                (EventMessage("knowledge.updated", {"kind": "ai_memory.commit"}),),
            )
        opened = self._require_project()
        committed = self.ai_task_service.commit_memory_result(
            opened.project, task_id
        )
        return ApplicationResult(
            {"committed": committed},
            (
                self._content_event(
                    "memory",
                    (
                        opened.project.memory_dir / "story_state.json",
                        opened.project.memory_dir / "chapter_summaries.json",
                        opened.project.memory_dir / "accepted_chapter_memory.json",
                    ),
                ),
            ),
        )

    def _mutation_result(self, mutation) -> ApplicationResult:
        opened = self._require_project()
        snapshot = self.content_service.snapshot(opened.project, opened.data_store)
        return ApplicationResult(
            {
                "mutation": mutation_dto(mutation),
                "snapshot": project_snapshot_dto(snapshot),
            },
            (self._content_event(mutation.kind, mutation.changed_paths),),
        )

    def _content_changed(
        self,
        kind: str,
        changed_paths,
    ) -> ApplicationResult:
        opened = self._require_project()
        snapshot = self.content_service.snapshot(opened.project, opened.data_store)
        return ApplicationResult(
            {"snapshot": project_snapshot_dto(snapshot)},
            (self._content_event(kind, changed_paths),),
        )

    @staticmethod
    def _content_event(kind: str, changed_paths) -> EventMessage:
        return EventMessage(
            "project.contentChanged",
            {
                "kind": str(kind),
                "changedPaths": [str(path) for path in changed_paths],
            },
        )

    def _require_project(self) -> OpenedProject:
        if self._opened is None:
            raise ProtocolFault("PROJECT_NOT_OPEN", "当前没有打开的项目。")
        return self._opened

    def _require_project_v2(self) -> ProjectV2Descriptor:
        if self._opened_v2 is None:
            raise ProtocolFault("PROJECT_V2_NOT_OPEN", "当前没有打开 schema-v2 项目。")
        return self._opened_v2

    def _require_ai_idle(self) -> None:
        if self.ai_task_service.is_running():
            raise ProtocolFault(
                "AI_TASK_RUNNING",
                "AI 任务运行期间不能切换或关闭项目，请先取消任务。",
            )
        self._require_reconstruction_idle()

    def _ai_project_root(self) -> str:
        if self._opened_v2 is not None:
            return str(self._opened_v2.root)
        if self._opened is not None:
            return str(self._opened.project.root)
        return ""

    def _require_reconstruction_idle(self) -> None:
        if self.reconstruction_task_service.is_running():
            raise ProtocolFault(
                "RECONSTRUCTION_TASK_RUNNING",
                "正文识别任务运行期间不能切换或关闭项目，请先取消任务。",
            )

    def set_event_sink(self, sink: Callable[[str, dict[str, Any]], None]) -> None:
        self.ai_task_service.set_event_sink(sink)
        self.reconstruction_task_service.set_event_sink(sink)

    def shutdown(self) -> None:
        self.ai_task_service.shutdown()
        self.reconstruction_task_service.shutdown()


def opened_project_dto(opened: OpenedProject) -> dict[str, Any]:
    project = opened.project
    migration = opened.migration
    return {
        "project": {
            "root": str(project.root),
            "name": project.name,
            "author": str(project.meta.get("author", "")),
        },
        "migration": {
            "fromSchema": migration.from_schema,
            "toSchema": migration.to_schema,
            "changedFiles": list(migration.changed_files),
            "backupPath": str(migration.backup_path)
            if migration.backup_path is not None
            else None,
            "recoveredInterruptedMigration": bool(
                migration.recovered_interrupted_migration
            ),
        },
    }


def opened_project_v2_dto(opened: ProjectV2Descriptor) -> dict[str, Any]:
    return {
        "root": str(opened.root),
        "projectId": opened.project_id,
        "name": opened.name,
        "author": opened.author,
        "schemaVersion": 2,
    }


def manuscript_import_plan_dto(plan: ManuscriptImportPlan) -> dict[str, Any]:
    return {
        "sourceLabel": Path(plan.source_path).name,
        "sourceKind": plan.source_kind,
        "digest": plan.digest,
        "totalSourceBytes": plan.total_source_bytes,
        "chapters": [
            {
                "chapterId": chapter.chapter_id,
                "sequence": chapter.sequence,
                "title": chapter.title,
                "sourceName": chapter.source_name,
                "encoding": chapter.encoding,
                "byteCount": chapter.byte_count,
                "contentSha256": chapter.content_sha256,
                "excerpt": chapter.content[:240],
                "empty": not bool(chapter.content.strip()),
            }
            for chapter in plan.chapters
        ],
        "warnings": [
            {"code": warning.code, "message": warning.message, "source": warning.source}
            for warning in plan.warnings
        ],
    }


def manuscript_snapshot_dto(snapshot: ManuscriptSnapshot) -> dict[str, Any]:
    return {
        "chapters": [
            {
                "chapterId": item.chapter_id,
                "sequence": item.sequence,
                "title": item.title,
                "path": item.path,
                "relativePath": item.relative_path,
            }
            for item in snapshot.chapters
        ],
        "itemCount": snapshot.item_count,
    }


def manuscript_trash_snapshot_dto(snapshot: ManuscriptTrashSnapshot) -> dict[str, Any]:
    return {
        "items": [
            {
                "trashId": item.trash_id,
                "chapterId": item.chapter_id,
                "title": item.title,
                "sequence": item.sequence,
                "deletedAt": item.deleted_at,
            }
            for item in snapshot.items
        ]
    }


def manuscript_export_dto(result: ManuscriptExportResult) -> dict[str, Any]:
    return {
        "path": result.path,
        "format": result.format,
        "chapterCount": result.chapter_count,
        "characterCount": result.character_count,
        "sha256": result.sha256,
    }


def reconstruction_snapshot_dto(snapshot: ReconstructionSnapshot) -> dict[str, Any]:
    return {
        "sourceRevision": snapshot.source_revision,
        "acceptedEntityCount": snapshot.accepted_entity_count,
        "acceptedRelationCount": snapshot.accepted_relation_count,
        "acceptedWorldCount": snapshot.accepted_world_count,
        "acceptedCharacterFieldCount": snapshot.accepted_character_field_count,
        "acceptedEventCount": snapshot.accepted_event_count,
        "pendingCount": snapshot.pending_count,
        "staleBatchCount": snapshot.stale_batch_count,
        "batches": [reconstruction_batch_summary_dto(item) for item in snapshot.batches],
    }


def reconstruction_batch_summary_dto(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "batchId": item.get("batch_id", ""),
        "sourceRevision": item.get("source_revision", ""),
        "createdAt": item.get("created_at", ""),
        "status": item.get("status", ""),
        "proposalCount": int(item.get("proposal_count", 0)),
        "pendingCount": int(item.get("pending_count", 0)),
        "acceptedCount": int(item.get("accepted_count", 0)),
        "requestedMode": item.get("requested_mode", "local"),
        "producer": item.get("producer", "local"),
        "fallbackUsed": bool(item.get("fallback_used", False)),
    }


def reconstruction_evidence_dto(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidenceId": item.get("evidence_id", ""),
        "chapterId": item.get("chapter_id", ""),
        "chapterRevision": item.get("chapter_revision", ""),
        "start": int(item.get("start", 0)),
        "end": int(item.get("end", 0)),
        "anchor": item.get("anchor", ""),
        "text": item.get("text", ""),
    }


def reconstruction_batch_dto(batch: dict[str, Any]) -> dict[str, Any]:
    proposals = []
    for item in batch.get("proposals", []):
        proposal = {
            "proposalId": item.get("proposal_id", ""),
            "kind": item.get("kind", ""),
            "status": item.get("status", ""),
            "confidence": float(item.get("confidence", 0)),
            "producers": list(item.get("producers", ["local"])),
            "reviewMode": item.get("review_mode", ""),
            "reviewedAt": item.get("reviewed_at", ""),
            "evidence": [reconstruction_evidence_dto(value) for value in item.get("evidence", [])],
        }
        if item.get("kind") == "entity":
            proposal.update({"name": item.get("name", ""), "entityType": item.get("entity_type", "character")})
        elif item.get("kind") == "relation":
            proposal.update({
                "sourceName": item.get("source_name", ""),
                "targetName": item.get("target_name", ""),
                "label": item.get("label", ""),
            })
        elif item.get("kind") == "world":
            proposal.update({
                "name": item.get("name", ""),
                "category": item.get("category", ""),
                "description": item.get("description", ""),
            })
        elif item.get("kind") == "character_field":
            proposal.update({
                "characterName": item.get("character_name", ""),
                "field": item.get("field", ""),
                "value": item.get("value", ""),
            })
        elif item.get("kind") == "event":
            proposal.update({
                "timeLabel": item.get("time_label", ""),
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "characterNames": list(item.get("character_names", [])),
                "worldNames": list(item.get("world_names", [])),
            })
        proposals.append(proposal)
    return {
        "batchId": batch.get("batch_id", ""),
        "sourceRevision": batch.get("source_revision", ""),
        "createdAt": batch.get("created_at", ""),
        "status": batch.get("status", ""),
        "chapterRevisions": dict(batch.get("chapter_revisions", {})),
        "segmentCount": len(batch.get("segments", [])),
        "extraction": extraction_summary_dto(batch.get("extraction", {})),
        "proposals": proposals,
    }


def extraction_summary_dto(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "requestedMode": item.get("requested_mode", "local"),
        "producer": item.get("producer", "local"),
        "fallbackUsed": bool(item.get("fallback_used", False)),
        "fallbackReason": item.get("fallback_reason", ""),
        "segmentCount": int(item.get("segment_count", 0)),
        "remoteChunkCount": int(item.get("remote_chunk_count", 0)),
        "estimatedInputTokens": int(item.get("estimated_input_tokens", 0)),
        "privacyScope": item.get("privacy_scope", "local_only"),
        "duplicateCount": int(item.get("duplicate_count", 0)),
        "relationConflictCount": int(item.get("relation_conflict_count", 0)),
    }


def knowledge_snapshot_dto(snapshot: KnowledgeSnapshot) -> dict[str, Any]:
    return {
        "entities": [
            {
                "entityId": item.get("entity_id", ""),
                "entityType": item.get("entity_type", "character"),
                "displayName": item.get("display_name", ""),
                "aliases": list(item.get("aliases", [])),
                "profileFields": dict(item.get("profile_fields", {})),
                "hiddenProfileFields": dict(item.get("hidden_profile_fields", {})),
                "profileEvidenceIds": {key: list(value) for key, value in item.get("profile_evidence_ids", {}).items()},
                "evidenceIds": list(item.get("evidence_ids", [])),
                "reviewedAt": item.get("reviewed_at", ""),
                "cardRelativePath": item.get("card_relative_path", ""),
            }
            for item in snapshot.entities
        ],
        "relations": [
            {
                "relationId": item.get("relation_id", ""),
                "sourceEntityId": item.get("source_entity_id", ""),
                "targetEntityId": item.get("target_entity_id", ""),
                "label": item.get("label", ""),
                "evidenceIds": list(item.get("evidence_ids", [])),
                "reviewedAt": item.get("reviewed_at", ""),
            }
            for item in snapshot.relations
        ],
        "worlds": [
            {
                "worldId": item.get("world_id", ""),
                "name": item.get("name", ""),
                "category": item.get("category", ""),
                "description": item.get("description", ""),
                "evidenceIds": list(item.get("evidence_ids", [])),
                "reviewedAt": item.get("reviewed_at", ""),
                "cardRelativePath": item.get("card_relative_path", ""),
            }
            for item in snapshot.worlds
        ],
        "hiddenWorlds": [
            {
                "worldId": item.get("world_id", ""),
                "name": item.get("name", ""),
                "category": item.get("category", ""),
                "description": item.get("description", ""),
                "evidenceIds": list(item.get("evidence_ids", [])),
                "reviewedAt": item.get("reviewed_at", ""),
                "cardRelativePath": item.get("card_relative_path", ""),
            }
            for item in snapshot.hidden_worlds
        ],
        "events": [knowledge_event_dto(item) for item in snapshot.events],
        "hiddenEvents": [knowledge_event_dto(item) for item in snapshot.hidden_events],
        "evidence": [
            {
                "evidenceId": item.get("evidence_id", ""),
                "chapterId": item.get("chapter_id", ""),
                "chapterRevision": item.get("chapter_revision", ""),
                "start": int(item.get("start", 0)),
                "end": int(item.get("end", 0)),
                "anchor": item.get("anchor", ""),
                "text": item.get("text", ""),
            }
            for item in snapshot.evidence
        ],
        "diagnostics": [
            {
                "diagnosticId": item.get("diagnostic_id", ""),
                "code": item.get("code", ""),
                "severity": item.get("severity", "notice"),
                "message": item.get("message", ""),
                "eventIds": list(item.get("event_ids", [])),
                "evidenceIds": list(item.get("evidence_ids", [])),
            }
            for item in snapshot.diagnostics
        ],
        "merges": [
            {
                "sourceEntityId": item.get("source_entity_id", ""),
                "targetEntityId": item.get("target_entity_id", ""),
                "sourceName": item.get("source_name", ""),
                "targetName": item.get("target_name", ""),
            }
            for item in snapshot.merges
        ],
        "operationCount": snapshot.operation_count,
    }


def knowledge_card_dto(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "ownerKind": card.get("owner_kind", ""),
        "ownerId": card.get("owner_id", ""),
        "mode": card.get("mode", ""),
        "title": card.get("title", ""),
        "relativePath": card.get("relative_path", ""),
        "content": card.get("content", ""),
        "revision": card.get("revision", ""),
        "readOnly": bool(card.get("read_only", False)),
    }


def knowledge_event_dto(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "eventId": item.get("event_id", ""),
        "timeLabel": item.get("time_label", ""),
        "title": item.get("title", ""),
        "description": item.get("description", ""),
        "participantEntityIds": list(item.get("participant_entity_ids", [])),
        "worldIds": list(item.get("world_ids", [])),
        "evidenceIds": list(item.get("evidence_ids", [])),
        "reviewedAt": item.get("reviewed_at", ""),
        "order": int(item.get("order", 0)),
    }


def document_snapshot_dto(snapshot: DocumentSnapshot) -> dict[str, Any]:
    return {
        "path": snapshot.path,
        "relativePath": snapshot.relative_path,
        "category": snapshot.category,
        "title": snapshot.title,
        "content": snapshot.content,
        "revision": snapshot.revision,
    }


def project_document_item_dto(item: ProjectDocumentItem) -> dict[str, Any]:
    return {
        "id": item.item_id,
        "kind": item.kind,
        "category": item.category,
        "title": item.title,
        "path": item.path,
        "relativePath": item.relative_path,
        "protected": item.protected,
        "importance": item.importance,
    }


def project_snapshot_dto(snapshot: ProjectContentSnapshot) -> dict[str, Any]:
    return {
        "chapters": [project_document_item_dto(item) for item in snapshot.chapters],
        "outlines": [project_document_item_dto(item) for item in snapshot.outlines],
        "characters": [
            project_document_item_dto(item) for item in snapshot.characters
        ],
        "world": [project_document_item_dto(item) for item in snapshot.world],
        "power": [project_document_item_dto(item) for item in snapshot.power],
        "timeline": [project_document_item_dto(item) for item in snapshot.timeline],
        "nextChapterId": snapshot.next_chapter_id,
        "itemCount": snapshot.item_count,
    }


def relationship_graph_snapshot_dto(
    snapshot: RelationshipGraphSnapshot,
) -> dict[str, Any]:
    return {
        "projectName": snapshot.project_name,
        "revision": snapshot.revision,
        "nodes": [
            {
                "id": node.node_id,
                "name": node.name,
                "aliases": list(node.aliases),
                "state": node.state,
                "location": node.location,
                "resolved": node.resolved,
                "path": node.path,
                "relativePath": node.relative_path,
                "nodeKind": node.node_kind,
                "order": node.order,
            }
            for node in snapshot.nodes
        ],
        "edges": [
            {
                "id": edge.edge_id,
                "source": edge.source,
                "target": edge.target,
                "label": edge.label,
                "directed": True,
                "sourceKind": edge.source_kind,
                "edgeKind": edge.edge_kind,
                "evidence": [
                    {
                        "chapterId": item.chapter_id,
                        "anchor": item.anchor,
                        "description": item.description,
                        "certainty": item.certainty,
                    }
                    for item in edge.evidence
                ],
            }
            for edge in snapshot.edges
        ],
        "warnings": [
            {
                "code": warning.code,
                "message": warning.message,
                "subject": warning.subject,
                "target": warning.target,
            }
            for warning in snapshot.warnings
        ],
        "relationTypes": list(snapshot.relation_types),
    }


def preferences_snapshot_dto(snapshot: PreferencesSnapshot) -> dict[str, Any]:
    return {
        "theme": snapshot.theme,
        "uiFontSize": snapshot.ui_font_size,
        "editorFontSize": snapshot.editor_font_size,
        "autoSave": snapshot.auto_save,
        "autoSaveInterval": snapshot.auto_save_interval,
        "showLineNumbers": snapshot.show_line_numbers,
        "lastProject": snapshot.last_project,
        "recentProjects": list(snapshot.recent_projects),
    }


def trash_item_dto(item: TrashItem) -> dict[str, Any]:
    return {
        "trashId": item.trash_id,
        "kind": item.kind,
        "title": item.title,
        "originalPath": item.original_path.replace("\\", "/"),
        "deletedAt": item.deleted_at,
        "canRename": item.can_rename,
    }


def trash_snapshot_dto(snapshot: TrashSnapshot) -> dict[str, Any]:
    return {"items": [trash_item_dto(item) for item in snapshot.items]}


def mutation_dto(mutation) -> dict[str, Any]:
    return {
        "resultPath": mutation.result_path,
        "changedPaths": list(mutation.changed_paths),
        "kind": mutation.kind,
    }


def fault_from_exception(exc: Exception) -> ProtocolFault:
    if isinstance(exc, DocumentRevisionConflict):
        return ProtocolFault(
            "REVISION_CONFLICT",
            str(exc),
            {
                "path": str(exc.path),
                "expectedRevision": exc.expected_revision,
                "actualRevision": exc.actual_revision,
            },
        )
    if isinstance(exc, DocumentNotFoundError):
        return ProtocolFault("DOCUMENT_NOT_FOUND", str(exc))
    if isinstance(exc, DocumentPathError):
        return ProtocolFault("DOCUMENT_PATH_INVALID", str(exc))
    if isinstance(exc, ProjectMigrationError):
        return ProtocolFault("PROJECT_MIGRATION_FAILED", str(exc))
    if isinstance(exc, ProjectV2ValidationError):
        return ProtocolFault("PROJECT_V2_INVALID", str(exc))
    if isinstance(exc, ManuscriptImportError):
        return ProtocolFault("IMPORT_INVALID", str(exc))
    if isinstance(exc, ReconstructionError):
        return ProtocolFault("RECONSTRUCTION_INVALID", str(exc))
    if isinstance(exc, ChapterIdConflictError):
        return ProtocolFault(
            "ID_CONFLICT",
            str(exc),
            {
                "kind": "chapter",
                "id": exc.chapter_id,
                "suggestedId": exc.suggested_id,
                "path": str(exc.path),
            },
        )
    if isinstance(exc, CharacterIdConflictError):
        return ProtocolFault(
            "ID_CONFLICT",
            str(exc),
            {
                "kind": "character",
                "id": exc.character_id,
                "suggestedId": exc.suggested_id,
                "path": str(exc.path),
            },
        )
    if isinstance(exc, CanonEntryConflictError):
        return ProtocolFault(
            "ID_CONFLICT",
            str(exc),
            {
                "kind": exc.kind,
                "id": exc.entry_id,
                "suggestedId": exc.suggested_id or None,
                "path": str(exc.path),
            },
        )
    if isinstance(exc, FileExistsError):
        return ProtocolFault("ALREADY_EXISTS", str(exc))
    if isinstance(exc, NotADirectoryError):
        return ProtocolFault("NOT_A_DIRECTORY", str(exc))
    if isinstance(exc, FileNotFoundError):
        return ProtocolFault("NOT_FOUND", str(exc))
    if isinstance(exc, PermissionError):
        return ProtocolFault("PERMISSION_DENIED", "没有权限完成此操作。")
    if isinstance(exc, DocumentServiceError):
        return ProtocolFault("DOCUMENT_ERROR", str(exc))
    if isinstance(exc, ValueError):
        return ProtocolFault("INVALID_ARGUMENT", str(exc))
    if isinstance(exc, OSError):
        return ProtocolFault("IO_ERROR", "本地文件操作失败。")
    return ProtocolFault("INTERNAL_ERROR", "Sidecar 内部错误。")


def _only_keys(params: dict[str, Any], allowed: set[str]) -> None:
    unknown = set(params) - allowed
    if unknown:
        raise ProtocolFault(
            "INVALID_PARAMS",
            "参数包含未知字段。",
            {"fields": sorted(unknown)},
        )


def _required_string(
    params: dict[str, Any],
    key: str,
    *,
    allow_blank: bool = False,
    max_length: int | None = None,
) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise ProtocolFault("INVALID_PARAMS", f"{key} 必须是字符串。")
    if not allow_blank and not value.strip():
        raise ProtocolFault("INVALID_PARAMS", f"{key} 不能为空。")
    if max_length is not None and len(value) > max_length:
        raise ProtocolFault("INVALID_PARAMS", f"{key} 超过长度限制。")
    return value


def _optional_string(
    params: dict[str, Any],
    key: str,
    *,
    max_length: int,
) -> str:
    value = params.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ProtocolFault("INVALID_PARAMS", f"{key} 必须是字符串。")
    if len(value) > max_length:
        raise ProtocolFault("INVALID_PARAMS", f"{key} 超过长度限制。")
    return value.strip()


def _optional_bool(params: dict[str, Any], key: str, default: bool) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise ProtocolFault("INVALID_PARAMS", f"{key} 必须是布尔值。")
    return value


def _validate_ai_options(options: dict[str, Any]) -> None:
    unknown = set(options) - {"selectedPower", "selectedForeshadowing"}
    if unknown:
        raise ProtocolFault(
            "INVALID_PARAMS", "AI options 包含未知字段。", {"fields": sorted(unknown)}
        )
    selected_power = options.get("selectedPower", [])
    if (
        not isinstance(selected_power, list)
        or len(selected_power) > 100
        or any(not isinstance(item, str) or not item.strip() for item in selected_power)
    ):
        raise ProtocolFault("INVALID_PARAMS", "selectedPower 必须是有效路径数组。")
    selected_notes = options.get("selectedForeshadowing", [])
    if (
        not isinstance(selected_notes, list)
        or len(selected_notes) > 200
        or any(not isinstance(item, dict) for item in selected_notes)
    ):
        raise ProtocolFault("INVALID_PARAMS", "selectedForeshadowing 必须是对象数组。")
