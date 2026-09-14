import hashlib
import json
import shutil
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path

from sidecar.application import SidecarApplication
from application.ai_task_service import AITaskService
from application.preferences_service import PreferencesService
from core.config import DEFAULT_CONFIG
from sidecar.protocol import ProtocolFault, RequestEnvelope


FIXTURE_PROJECT = (
    Path(__file__).parent
    / "fixtures"
    / "electron_migration"
    / "golden_project"
)
FIXTURE_EXPECTED = json.loads(
    (FIXTURE_PROJECT.parent / "fixture-manifest.json").read_text(encoding="utf-8")
)["expected"]


def _digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def _request(
    method: str,
    params: dict | None = None,
    request_id: str = "test",
) -> RequestEnvelope:
    return RequestEnvelope(request_id, method, params or {})


class SidecarApplicationTests(unittest.TestCase):
    def test_handshake_reports_independent_compatibility_versions(self) -> None:
        app = SidecarApplication()

        result = app.dispatch(
            _request(
                "system.handshake",
                {"clientName": "Novalist Electron", "clientVersion": "preview"},
            )
        ).result

        self.assertEqual(result["protocolVersion"], 1)
        self.assertEqual(result["projectSchema"], {"minimum": 1, "maximum": 2})
        self.assertEqual(result["configSchema"], {"minimum": 6, "maximum": 6})
        self.assertIn("document.save", result["supportedMethods"])
        self.assertIn("graph.snapshot", result["supportedMethods"])
        self.assertIn("preferences.get", result["supportedMethods"])
        self.assertIn("project.restoreLast", result["supportedMethods"])
        self.assertIn("reconstruction.generate", result["supportedMethods"])
        self.assertIn("reconstruction.start", result["supportedMethods"])
        self.assertIn("reconstruction.reviewMany", result["supportedMethods"])
        self.assertIn("knowledge.snapshot", result["supportedMethods"])
        self.assertIn("knowledge.unmergeEntity", result["supportedMethods"])
        self.assertIn("knowledge.updateCharacterField", result["supportedMethods"])
        self.assertIn("knowledge.updateWorld", result["supportedMethods"])
        self.assertIn("knowledge.openCard", result["supportedMethods"])
        self.assertIn("knowledge.saveAuthorCard", result["supportedMethods"])
        self.assertIn("knowledge.updateEvent", result["supportedMethods"])
        self.assertIn("knowledge.updateEventLinks", result["supportedMethods"])
        self.assertIn("knowledge.reorderEvents", result["supportedMethods"])
        self.assertIn("knowledge.restoreEvent", result["supportedMethods"])
        self.assertEqual(result["client"]["name"], "Novalist Electron")

    def test_frontend_preferences_and_last_project_restore_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "golden_project"
            shutil.copytree(FIXTURE_PROJECT, copied)
            stored = deepcopy(DEFAULT_CONFIG)

            def save_config(config: dict) -> None:
                stored.clear()
                stored.update(deepcopy(config))

            preferences = PreferencesService(
                config_loader=lambda: deepcopy(stored),
                config_saver=save_config,
            )
            app = SidecarApplication(preferences_service=preferences)
            try:
                initial = app.dispatch(_request("preferences.get")).result["preferences"]
                self.assertEqual(initial["theme"], "light")
                self.assertNotIn("dsh_command", initial)

                updated = app.dispatch(
                    _request(
                        "preferences.update",
                        {
                            "patch": {
                                "theme": "dark",
                                "editor_font_size": 21,
                                "auto_save_interval": 12,
                            }
                        },
                    )
                ).result["preferences"]
                self.assertEqual(updated["theme"], "dark")
                self.assertEqual(updated["editorFontSize"], 21)

                opened = app.dispatch(
                    _request(
                        "project.open",
                        {"path": str(copied), "remember": True},
                    )
                ).result["opened"]
                self.assertEqual(Path(stored["last_project"]), copied.resolve())
                app.dispatch(_request("project.close"))
                restored = app.dispatch(_request("project.restoreLast"))
                self.assertEqual(
                    restored.result["opened"]["project"]["root"],
                    opened["project"]["root"],
                )
                self.assertEqual(restored.events[0].name, "project.opened")
            finally:
                app.shutdown()

    def test_golden_project_open_and_document_read_do_not_rewrite_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "golden_project"
            shutil.copytree(FIXTURE_PROJECT, copied)
            before = _digests(copied)
            app = SidecarApplication()

            opened_result = app.dispatch(
                _request("project.open", {"path": str(copied)})
            )
            document_result = app.dispatch(
                _request(
                    "document.open",
                    {
                        "category": "章节",
                        "path": "outline/chapters/chapter_02.md",
                    },
                )
            )

            opened = opened_result.result["opened"]
            self.assertEqual(
                opened["project"]["name"], FIXTURE_EXPECTED["project_name"]
            )
            self.assertEqual(opened["migration"]["fromSchema"], 1)
            self.assertEqual(opened["migration"]["toSchema"], 1)
            self.assertEqual(opened["migration"]["changedFiles"], [])
            document = document_result.result["document"]
            self.assertEqual(
                document["relativePath"], "outline/chapters/chapter_02.md"
            )
            self.assertTrue(document["revision"].startswith("v1:"))
            self.assertEqual(before, _digests(copied))
            self.assertEqual(opened_result.events[0].name, "project.opened")

    def test_document_save_returns_event_and_stale_revision_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "golden_project"
            shutil.copytree(FIXTURE_PROJECT, copied)
            app = SidecarApplication()
            app.dispatch(_request("project.open", {"path": str(copied)}))
            opened = app.dispatch(
                _request(
                    "document.open",
                    {"category": "章节", "path": "outline/chapters/chapter_01.md"},
                )
            ).result["document"]

            saved_result = app.dispatch(
                _request(
                    "document.save",
                    {
                        "category": "章节",
                        "path": opened["relativePath"],
                        "content": "# 第一章\n\n## 正文\n新正文\n",
                        "expectedRevision": opened["revision"],
                    },
                )
            )
            saved = saved_result.result["document"]
            self.assertNotEqual(saved["revision"], opened["revision"])
            self.assertEqual(saved_result.events[0].name, "document.changed")

            target = Path(saved["path"])
            target.write_text("外部修改", encoding="utf-8")
            with self.assertRaises(ProtocolFault) as raised:
                app.dispatch(
                    _request(
                        "document.save",
                        {
                            "category": "章节",
                            "path": saved["relativePath"],
                            "content": "本地旧版本",
                            "expectedRevision": saved["revision"],
                        },
                    )
                )
            self.assertEqual(raised.exception.code, "REVISION_CONFLICT")
            self.assertEqual(target.read_text(encoding="utf-8"), "外部修改")

    def test_allowlist_and_active_project_boundary_are_enforced(self) -> None:
        app = SidecarApplication()

        with self.assertRaises(ProtocolFault) as missing_project:
            app.dispatch(
                _request(
                    "document.open",
                    {"category": "章节", "path": "outline/chapters/chapter_01.md"},
                )
            )
        self.assertEqual(missing_project.exception.code, "PROJECT_NOT_OPEN")

        with self.assertRaises(ProtocolFault) as unknown:
            app.dispatch(_request("filesystem.read", {"path": "anything"}))
        self.assertEqual(unknown.exception.code, "METHOD_NOT_FOUND")

    def test_graph_snapshot_is_typed_directed_and_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "golden_project"
            shutil.copytree(FIXTURE_PROJECT, copied)
            before = _digests(copied)
            app = SidecarApplication()
            try:
                app.dispatch(_request("project.open", {"path": str(copied)}))

                graph = app.dispatch(_request("graph.snapshot")).result["graph"]

                self.assertEqual(graph["projectName"], FIXTURE_EXPECTED["project_name"])
                self.assertEqual(len(graph["nodes"]), 3)
                self.assertEqual(len(graph["edges"]), 3)
                self.assertTrue(all(edge["directed"] for edge in graph["edges"]))
                self.assertEqual(graph["warnings"][0]["code"], "missing_character_card")
                self.assertEqual(before, _digests(copied))
            finally:
                app.shutdown()

    def test_v2_import_create_open_and_revision_safe_save_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "legacy"
            output = Path(tmp) / "output"
            output.mkdir()
            shutil.copytree(FIXTURE_PROJECT, source)
            before = _digests(source)
            app = SidecarApplication()
            try:
                scanned = app.dispatch(
                    _request("manuscript.scanImport", {"sourcePath": str(source)})
                ).result["plan"]
                self.assertEqual(scanned["sourceKind"], "novalist_v1_manuscript")
                self.assertEqual(len(scanned["chapters"]), 2)
                self.assertNotIn("sourcePath", scanned)

                created = app.dispatch(
                    _request(
                        "project.createV2",
                        {
                            "parentDirectory": str(output),
                            "name": "重建项目",
                            "author": "测试",
                            "planDigest": scanned["digest"],
                        },
                    )
                )
                self.assertEqual(created.result["opened"]["schemaVersion"], 2)
                self.assertEqual(created.events[0].name, "projectV2.opened")
                self.assertEqual(before, _digests(source))

                snapshot = app.dispatch(_request("manuscript.snapshot")).result["snapshot"]
                self.assertEqual(snapshot["itemCount"], 2)
                chapter_id = snapshot["chapters"][0]["chapterId"]
                opened = app.dispatch(
                    _request("manuscript.open", {"chapterId": chapter_id})
                ).result["document"]
                saved = app.dispatch(
                    _request(
                        "manuscript.save",
                        {
                            "chapterId": chapter_id,
                            "content": opened["content"] + "\n新段落。\n",
                            "expectedRevision": opened["revision"],
                        },
                    )
                )
                self.assertTrue(saved.result["document"]["revision"].startswith("v2:"))
                self.assertEqual(saved.events[0].name, "manuscript.changed")

                root = created.result["opened"]["root"]
                app.dispatch(_request("project.close"))
                reopened = app.dispatch(_request("project.openV2", {"path": root}))
                self.assertEqual(reopened.result["opened"]["name"], "重建项目")
                self.assertFalse((Path(root) / "canon").exists())
            finally:
                app.shutdown()

    def test_v2_create_rejects_changed_import_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.md"
            source.write_text("# 第一章\n\n原文。\n", encoding="utf-8")
            app = SidecarApplication()
            try:
                plan = app.dispatch(
                    _request("manuscript.scanImport", {"sourcePath": str(source)})
                ).result["plan"]
                source.write_text("# 第一章\n\n已经变化。\n", encoding="utf-8")

                with self.assertRaises(ProtocolFault) as raised:
                    app.dispatch(
                        _request(
                            "project.createV2",
                            {
                                "parentDirectory": tmp,
                                "name": "不应创建",
                                "planDigest": plan["digest"],
                            },
                        )
                    )
                self.assertEqual(raised.exception.code, "IMPORT_SOURCE_CHANGED")
                self.assertFalse((Path(tmp) / "不应创建").exists())
            finally:
                app.shutdown()

    def test_v2_manuscript_structure_and_trash_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.md"
            source.write_text("# 第一章\n\n原文。\n", encoding="utf-8")
            output = Path(tmp) / "output"
            output.mkdir()
            app = SidecarApplication()
            try:
                plan = app.dispatch(
                    _request("manuscript.scanImport", {"sourcePath": str(source)})
                ).result["plan"]
                app.dispatch(_request("project.createV2", {
                    "parentDirectory": str(output), "name": "章节管理",
                    "planDigest": plan["digest"],
                }))

                created = app.dispatch(_request("manuscript.create", {
                    "title": "第二章", "afterChapterId": "chapter_0001",
                }))
                self.assertEqual(created.events[0].name, "manuscript.structureChanged")
                self.assertEqual(created.result["snapshot"]["itemCount"], 2)
                chapter = created.result["document"]

                renamed = app.dispatch(_request("manuscript.rename", {
                    "chapterId": "chapter_0002", "title": "第二章：回声",
                    "expectedRevision": chapter["revision"],
                }))
                self.assertEqual(renamed.result["document"]["title"], "第二章：回声")

                reordered = app.dispatch(_request("manuscript.reorder", {
                    "chapterIds": ["chapter_0002", "chapter_0001"],
                }))
                self.assertEqual(
                    [item["chapterId"] for item in reordered.result["snapshot"]["chapters"]],
                    ["chapter_0002", "chapter_0001"],
                )

                deleted = app.dispatch(_request("manuscript.delete", {
                    "chapterId": "chapter_0001",
                }))
                trash_id = deleted.result["deletedTrashId"]
                listed = app.dispatch(_request("manuscript.trashList")).result["trash"]
                self.assertEqual(listed["items"][0]["trashId"], trash_id)

                restored = app.dispatch(_request("manuscript.trashRestore", {
                    "trashId": trash_id,
                }))
                self.assertEqual(restored.result["trash"]["items"], [])
                self.assertEqual(restored.result["document"]["relativePath"], "manuscript/chapter_0001.md")
            finally:
                app.shutdown()

    def test_v2_append_import_and_export_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.md"
            first.write_text("# 第一章\n\n第一章正文。\n", encoding="utf-8")
            second = root / "second.md"
            second.write_text("# 第二章\n\n第二章正文。\n", encoding="utf-8")
            output = root / "output"
            output.mkdir()
            app = SidecarApplication()
            try:
                initial = app.dispatch(_request("manuscript.scanImport", {
                    "sourcePath": str(first),
                })).result["plan"]
                app.dispatch(_request("project.createV2", {
                    "parentDirectory": str(output), "name": "追加导入",
                    "planDigest": initial["digest"],
                }))
                appended_plan = app.dispatch(_request("manuscript.scanImport", {
                    "sourcePath": str(second),
                })).result["plan"]
                appended = app.dispatch(_request("manuscript.appendImport", {
                    "planDigest": appended_plan["digest"],
                    "afterChapterId": "chapter_0001",
                }))
                self.assertEqual(appended.result["snapshot"]["itemCount"], 2)
                self.assertEqual(appended.events[0].data["action"], "imported")

                destination = root / "export.md"
                exported = app.dispatch(_request("manuscript.export", {
                    "destination": str(destination), "format": "md",
                })).result["exported"]
                self.assertEqual(exported["chapterCount"], 2)
                self.assertEqual(len(exported["sha256"]), 64)
                self.assertIn("第二章正文。", destination.read_text(encoding="utf-8-sig"))
            finally:
                app.shutdown()

    def test_v2_reconstruction_review_graph_and_invalidation_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "legacy"
            output = Path(tmp) / "output"
            output.mkdir()
            shutil.copytree(FIXTURE_PROJECT, source)
            app = SidecarApplication()
            try:
                plan = app.dispatch(
                    _request("manuscript.scanImport", {"sourcePath": str(source)})
                ).result["plan"]
                app.dispatch(_request("project.createV2", {
                    "parentDirectory": str(output), "name": "重建审核",
                    "planDigest": plan["digest"],
                }))
                generated = app.dispatch(_request("reconstruction.generate"))
                batch = generated.result["batch"]
                self.assertEqual(generated.events[0].name, "reconstruction.updated")
                self.assertGreaterEqual(batch["segmentCount"], 2)
                entities = [item for item in batch["proposals"] if item["kind"] == "entity"]
                self.assertGreaterEqual(len(entities), 2)
                bulk = app.dispatch(_request("reconstruction.reviewMany", {
                    "batchId": batch["batchId"],
                    "decisions": [{"proposalId": item["proposalId"], "decision": "accepted"} for item in entities],
                }))
                self.assertEqual(bulk.events[0].name, "reconstruction.updated")
                self.assertTrue(all(item["reviewMode"] == "batch" for item in bulk.result["batch"]["proposals"] if item["status"] == "accepted"))
                self.assertTrue(all(item["reviewedAt"] for item in bulk.result["batch"]["proposals"] if item["status"] == "accepted"))
                status = app.dispatch(_request("reconstruction.snapshot")).result["reconstruction"]
                self.assertGreaterEqual(status["acceptedEntityCount"], 2)
                graph = app.dispatch(_request("graph.snapshot")).result["graph"]
                self.assertGreaterEqual(len(graph["nodes"]), 2)
                self.assertTrue(all(node["resolved"] for node in graph["nodes"]))

                knowledge = app.dispatch(_request("knowledge.snapshot")).result["knowledge"]
                first, second = knowledge["entities"][:2]
                renamed = app.dispatch(_request("knowledge.renameEntity", {
                    "entityId": first["entityId"], "displayName": "人工整理人物",
                }))
                self.assertEqual(renamed.events[0].name, "knowledge.updated")
                aliased = app.dispatch(_request("knowledge.setEntityAliases", {
                    "entityId": first["entityId"], "aliases": ["整理别名"],
                })).result["knowledge"]
                self.assertIn("整理别名", next(item for item in aliased["entities"] if item["entityId"] == first["entityId"])["aliases"])
                merged = app.dispatch(_request("knowledge.mergeEntities", {
                    "sourceEntityId": first["entityId"], "targetEntityId": second["entityId"],
                })).result["knowledge"]
                self.assertEqual(len(merged["merges"]), 1)
                split = app.dispatch(_request("knowledge.unmergeEntity", {
                    "sourceEntityId": first["entityId"],
                })).result["knowledge"]
                self.assertEqual(split["merges"], [])
                self.assertEqual(len(split["entities"]), len(knowledge["entities"]))

                opened = app.dispatch(_request("manuscript.open", {"chapterId": "chapter_0001"})).result["document"]
                saved = app.dispatch(_request("manuscript.save", {
                    "chapterId": "chapter_0001", "content": opened["content"] + "\n改写。\n",
                    "expectedRevision": opened["revision"],
                }))
                self.assertTrue(saved.events[0].data["reconstructionInvalidated"])
                invalidated = app.dispatch(_request("reconstruction.snapshot")).result["reconstruction"]
                self.assertEqual(invalidated["acceptedEntityCount"], 0)
                self.assertEqual(invalidated["staleBatchCount"], 1)
            finally:
                app.shutdown()

    def test_v2_reconstruction_runs_as_background_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.md"
            source.write_text("# 第一章\n\n林砚拆开信封。\n", encoding="utf-8")
            events: list[tuple[str, dict]] = []
            app = SidecarApplication()
            app.set_event_sink(lambda name, data: events.append((name, data)))
            try:
                plan = app.dispatch(_request("manuscript.scanImport", {"sourcePath": str(source)})).result["plan"]
                app.dispatch(_request("project.createV2", {
                    "parentDirectory": tmp, "name": "后台项目", "planDigest": plan["digest"],
                }))
                started = app.dispatch(_request("reconstruction.start")).result["task"]
                self.assertIn(started["status"], {"queued", "running"})
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    status = app.dispatch(_request("reconstruction.taskStatus")).result["reconstructionTask"]
                    if status["recent"] and status["recent"]["status"] in {"succeeded", "failed", "cancelled"}:
                        break
                    time.sleep(0.01)
                self.assertEqual(status["recent"]["status"], "succeeded")
                self.assertTrue(status["recent"]["batchId"].startswith("batch_"))
                self.assertTrue(any(name == "reconstruction.taskUpdated" for name, _ in events))
            finally:
                app.shutdown()

    def test_v2_dsh_reconstruction_requires_explicit_remote_consent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.md"
            source.write_text("# 第一章\n\n林砚拆开信封。\n", encoding="utf-8")
            app = SidecarApplication()
            try:
                plan = app.dispatch(_request("manuscript.scanImport", {"sourcePath": str(source)})).result["plan"]
                app.dispatch(_request("project.createV2", {
                    "parentDirectory": tmp, "name": "授权测试", "planDigest": plan["digest"],
                }))
                with self.assertRaises(ProtocolFault) as raised:
                    app.dispatch(_request("reconstruction.start", {"mode": "dsh", "remoteConsent": False}))
                self.assertEqual(raised.exception.code, "INVALID_PARAMS")
            finally:
                app.shutdown()

    def test_save_requires_an_explicit_revision_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "golden_project"
            shutil.copytree(FIXTURE_PROJECT, copied)
            app = SidecarApplication()
            app.dispatch(_request("project.open", {"path": str(copied)}))

            with self.assertRaises(ProtocolFault) as raised:
                app.dispatch(
                    _request(
                        "document.save",
                        {
                            "category": "章节",
                            "path": "outline/chapters/chapter_01.md",
                            "content": "不会写入",
                        },
                    )
                )

            self.assertEqual(raised.exception.code, "INVALID_PARAMS")

    def test_project_snapshot_and_local_data_mutations_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "golden_project"
            shutil.copytree(FIXTURE_PROJECT, copied)
            app = SidecarApplication()
            app.dispatch(_request("project.open", {"path": str(copied)}))

            initial = app.dispatch(_request("project.snapshot")).result["snapshot"]
            self.assertEqual(initial["nextChapterId"], "chapter_03")
            self.assertEqual(len(initial["chapters"]), 2)
            self.assertEqual(len(initial["characters"]), 2)

            created = app.dispatch(
                _request(
                    "document.createChapter",
                    {"title": "第三章", "chapterId": "chapter_03"},
                )
            )
            self.assertEqual(created.events[0].name, "project.contentChanged")
            self.assertEqual(len(created.result["snapshot"]["chapters"]), 3)

            deleted = app.dispatch(
                _request(
                    "document.delete",
                    {
                        "kind": "chapter",
                        "itemId": "chapter_03",
                        "path": "outline/chapters/chapter_03.md",
                    },
                )
            )
            self.assertEqual(len(deleted.result["snapshot"]["chapters"]), 2)
            trash = app.dispatch(_request("trash.list")).result["trash"]
            self.assertEqual(len(trash["items"]), 1)
            self.assertEqual(trash["items"][0]["kind"], "chapter")

            restored = app.dispatch(
                _request(
                    "trash.restore",
                    {
                        "kind": "chapter",
                        "trashId": trash["items"][0]["trashId"],
                        "conflictPolicy": "error",
                    },
                )
            ).result
            self.assertEqual(len(restored["snapshot"]["chapters"]), 3)
            self.assertEqual(restored["trash"]["items"], [])

    def test_trash_kind_validation_and_rename_conflict_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "golden_project"
            shutil.copytree(FIXTURE_PROJECT, copied)
            app = SidecarApplication()
            app.dispatch(_request("project.open", {"path": str(copied)}))
            app.dispatch(
                _request(
                    "document.delete",
                    {
                        "kind": "world",
                        "itemId": "雾港",
                        "path": "canon/world/雾港.md",
                    },
                )
            )
            trash = app.dispatch(_request("trash.list")).result["trash"]["items"][0]

            with self.assertRaises(ProtocolFault) as wrong_kind:
                app.dispatch(
                    _request(
                        "trash.restore",
                        {"kind": "power", "trashId": trash["trashId"]},
                    )
                )
            self.assertEqual(wrong_kind.exception.code, "INVALID_ARGUMENT")

            (copied / "canon" / "world" / "雾港.md").write_text(
                "# 新雾港\n", encoding="utf-8"
            )
            with self.assertRaises(ProtocolFault) as conflict:
                app.dispatch(
                    _request(
                        "trash.restore",
                        {"kind": "world", "trashId": trash["trashId"]},
                    )
                )
            self.assertEqual(conflict.exception.code, "ID_CONFLICT")

            renamed = app.dispatch(
                _request(
                    "trash.restore",
                    {
                        "kind": "world",
                        "trashId": trash["trashId"],
                        "conflictPolicy": "rename",
                    },
                )
            ).result
            self.assertTrue(Path(renamed["restoredPath"]).is_file())
            self.assertNotEqual(Path(renamed["restoredPath"]).name, "雾港.md")

    def test_ai_task_rpc_is_async_review_first_and_explicitly_applied(self) -> None:
        def execute(kind, project, chapter_id, options, cancel_event, report):
            return ({"type": "writing", "mode": "replace", "text": "RPC 生成正文", "charCount": 8}, None)

        with tempfile.TemporaryDirectory() as tmp:
            copied = Path(tmp) / "golden_project"
            shutil.copytree(FIXTURE_PROJECT, copied)
            events = []
            tasks = AITaskService(execution_factory=execute)
            tasks.set_event_sink(lambda name, data: events.append((name, data)))
            app = SidecarApplication(ai_task_service=tasks)
            try:
                app.dispatch(_request("project.open", {"path": str(copied)}))
                document = app.dispatch(
                    _request(
                        "document.open",
                        {"category": "章节", "path": "outline/chapters/chapter_01.md"},
                    )
                ).result["document"]
                started = app.dispatch(
                    _request(
                        "ai.start",
                        {
                            "kind": "expand",
                            "chapterId": "chapter_01",
                            "sourceRevision": document["revision"],
                            "noticeAccepted": True,
                        },
                    )
                ).result["task"]
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    status = app.dispatch(_request("ai.status")).result["ai"]
                    latest = next(item for item in status["recent"] if item["taskId"] == started["taskId"])
                    if latest["status"] == "succeeded":
                        break
                    time.sleep(0.01)
                else:
                    self.fail("AI task did not finish")

                preview = app.dispatch(
                    _request("ai.result", {"taskId": started["taskId"]})
                ).result
                self.assertEqual(preview["result"]["text"], "RPC 生成正文")
                self.assertNotIn("RPC 生成正文", Path(document["path"]).read_text(encoding="utf-8"))

                applied = app.dispatch(
                    _request("ai.applyWritingResult", {"taskId": started["taskId"]})
                )
                self.assertIn("RPC 生成正文", applied.result["document"]["content"])
                self.assertEqual(applied.events[0].name, "document.changed")
                self.assertTrue(any(name == "ai.taskUpdated" for name, _data in events))
            finally:
                app.shutdown()


if __name__ == "__main__":
    unittest.main()
