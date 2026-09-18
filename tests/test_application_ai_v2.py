import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from application.ai_task_service import AITaskService
from application.ai_v2_context import (
    MEMORY_PATH,
    build_v2_prompt,
    parse_v2_memory,
    prepare_v2_context,
)
from application.document_v2_service import DocumentV2Service
from application.manuscript_import_service import ManuscriptImportService
from application.project_v2_service import ProjectV2Service
from application.reconstruction_service import ReconstructionService


def _wait_for(service: AITaskService, task_id: str, status: str, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = next(item for item in service.status()["recent"] if item["taskId"] == task_id)
        if task["status"] == status:
            return task
        time.sleep(0.01)
    raise AssertionError(f"task {task_id} did not reach {status}")


class AIV2TaskTests(unittest.TestCase):
    def _project(self, root: Path):
        source = root / "source.md"
        source.write_text("# 第一章\n\n原始正文。\n", encoding="utf-8")
        output = root / "output"
        output.mkdir()
        return ProjectV2Service().create_project(
            output,
            "AI v2 项目",
            import_plan=ManuscriptImportService().scan(source),
        )

    def _service(self, execute):
        documents = DocumentV2Service()
        reconstruction = ReconstructionService()
        return AITaskService(
            execution_factory=execute,
            document_v2_service=documents,
            reconstruction_service=reconstruction,
        ), documents, reconstruction

    def test_v2_style_is_shared_but_never_injected_into_fact_tasks(self):
        from application.ai_v2_context import AIV2ContextSnapshot
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            documents = DocumentV2Service()
            reconstruction = ReconstructionService()
            folder = project.root / "writing"
            folder.mkdir(exist_ok=True)
            style = folder / "style_guide.md"
            style.write_text("# 本书风格\n冷峻克制，保留对白节奏。", encoding="utf-8")
            context = prepare_v2_context(project, "chapter_0001", documents=documents, reconstruction=reconstruction)
            snapshot = AIV2ContextSnapshot.capture(project, "chapter_0001", documents=documents, reconstruction=reconstruction)
            for kind in ("expand", "continuation", "check", "memory"):
                _system, user, report = build_v2_prompt(context, kind, target_chars=1000, prompt_budget=9000)
                self.assertEqual("冷峻克制" in user, kind in {"expand", "continuation"})
                self.assertLessEqual(report.total_prompt_chars, 9000)
            from dataclasses import replace
            long_context = replace(context, style_guide="表达要求" * 3000, current_content="正文" * 10000)
            _system, _user, report = build_v2_prompt(long_context, "expand", target_chars=1000, prompt_budget=4000)
            self.assertLessEqual(report.total_prompt_chars, 4000)
            self.assertTrue(any(item.key == "style" and item.status == "trimmed" for item in report.sections))
            style.write_text("新的表达要求", encoding="utf-8")
            self.assertFalse(snapshot.matches(project, "chapter_0001", documents=documents, reconstruction=reconstruction))

    def test_v2_writing_result_is_review_first_and_preserves_heading(self) -> None:
        def execute(kind, project, chapter_id, options, cancel_event, report):
            self.assertEqual(kind, "expand")
            self.assertEqual(chapter_id, "chapter_0001")
            return ({
                "type": "writing", "mode": "replace", "text": "扩写后的正文。", "charCount": 7,
            }, None)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service, documents, _reconstruction = self._service(execute)
            opened = documents.open_document(project, "chapter_0001")
            try:
                task = service.start(
                    project, "expand", "chapter_0001",
                    source_revision=opened.revision, notice_accepted=True,
                )
                _wait_for(service, task["taskId"], "succeeded")
                self.assertNotIn("扩写后的正文", Path(opened.path).read_text(encoding="utf-8"))

                saved = service.apply_writing_result(project, documents, task["taskId"])
                self.assertTrue(saved.content.startswith("# 第一章\n"))
                self.assertIn("扩写后的正文。", saved.content)
                self.assertNotIn("原始正文。", saved.content)
            finally:
                service.shutdown()

    def test_v2_context_change_blocks_apply(self) -> None:
        def execute(*_args):
            return ({"type": "writing", "mode": "append", "text": "新增片段。", "charCount": 5}, None)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service, documents, _reconstruction = self._service(execute)
            opened = documents.open_document(project, "chapter_0001")
            try:
                task = service.start(
                    project, "continuation", "chapter_0001",
                    source_revision=opened.revision, notice_accepted=True,
                )
                _wait_for(service, task["taskId"], "succeeded")
                entities_path = project.root / "knowledge" / "entities.json"
                entities = json.loads(entities_path.read_text(encoding="utf-8"))
                entities["entities"].append({"entity_id": "entity_changed", "display_name": "已审核人物"})
                entities_path.write_text(json.dumps(entities, ensure_ascii=False), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "上下文已经变化"):
                    service.apply_writing_result(project, documents, task["taskId"])
            finally:
                service.shutdown()

    def test_completed_result_is_scoped_to_its_project(self) -> None:
        def execute(*_args):
            return ({"type": "writing", "mode": "append", "text": "私有预览。", "charCount": 5}, None)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_root, second_root = root / "first", root / "second"
            first_root.mkdir()
            second_root.mkdir()
            first = self._project(first_root)
            second = self._project(second_root)
            service, documents, _reconstruction = self._service(execute)
            try:
                opened = documents.open_document(first, "chapter_0001")
                task = service.start(
                    first, "continuation", "chapter_0001",
                    source_revision=opened.revision, notice_accepted=True,
                )
                _wait_for(service, task["taskId"], "succeeded")
                self.assertEqual(service.status(project_root=second.root)["recent"], [])
                with self.assertRaisesRegex(ValueError, "不属于当前项目"):
                    service.result(task["taskId"], project_root=second.root)
            finally:
                service.shutdown()

    def test_v2_memory_is_only_written_after_review_and_bound_to_revision(self) -> None:
        proposal = {
            "chapter_id": "chapter_0001",
            "summary": "主人公抵达雾港。",
            "facts": ["主人公已经抵达雾港"],
            "open_threads": ["来信者身份未明"],
        }

        def execute(*_args):
            return ({
                "type": "memory", "summary": proposal["summary"], "preview": "预览",
                "hasBlockers": False, "conflictCount": 0, "patchCount": 2, "cacheHit": False,
            }, proposal)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service, documents, _reconstruction = self._service(execute)
            opened = documents.open_document(project, "chapter_0001")
            memory_path = project.root / MEMORY_PATH
            try:
                task = service.start(
                    project, "memory", "chapter_0001",
                    source_revision=opened.revision, notice_accepted=True,
                )
                _wait_for(service, task["taskId"], "succeeded")
                self.assertFalse(memory_path.exists())
                committed = service.commit_memory_result(project, task["taskId"])
                self.assertEqual(committed["sourceRevision"], opened.revision)
                stored = json.loads(memory_path.read_text(encoding="utf-8"))["memories"]
                self.assertEqual(stored[0]["producer"], "ai-reviewed")
                self.assertEqual(stored[0]["facts"], proposal["facts"])
            finally:
                service.shutdown()

    def test_v2_context_never_reads_legacy_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            sentinel = "LEGACY_SECRET_MUST_NOT_LEAK"
            legacy = project.root / "canon" / "characters"
            legacy.mkdir(parents=True)
            (legacy / "old.md").write_text(sentinel, encoding="utf-8")
            context = prepare_v2_context(
                project,
                "chapter_0001",
                documents=DocumentV2Service(),
                reconstruction=ReconstructionService(),
            )
            self.assertNotIn(sentinel, json.dumps(context.__dict__, ensure_ascii=False))
            self.assertEqual(context.reviewed_knowledge["entities"], [])

            system, user, report = build_v2_prompt(
                context, "check", target_chars=3_000, prompt_budget=12_000
            )
            self.assertIn("schema-v2", system)
            self.assertIn("novalist-v2-reviewed-only", user)
            self.assertNotIn(sentinel, user)
            self.assertNotIn("原始正文", json.dumps(report.to_dict(), ensure_ascii=False))

    def test_v2_memory_protocol_is_strict(self) -> None:
        parsed = parse_v2_memory(
            '{"type":"v2_chapter_memory","chapter_id":"chapter_0001",'
            '"summary":"摘要","facts":["事实"],"open_threads":[]}',
            "chapter_0001",
        )
        self.assertEqual(parsed["facts"], ["事实"])
        with self.assertRaisesRegex(ValueError, "章节不正确"):
            parse_v2_memory(
                '{"type":"v2_chapter_memory","chapter_id":"chapter_9999",'
                '"summary":"摘要","facts":[],"open_threads":[]}',
                "chapter_0001",
            )

    def test_production_v2_executor_covers_all_review_result_types(self) -> None:
        class FakeDSHClient:
            def __init__(self, **kwargs):
                self.report_callback = kwargs.get("report_callback")

            def use_isolated_workspace(self):
                return None

            def prompt_build_budget(self):
                return 12_000

            def generate(self, system, user, **kwargs):
                report = kwargs.get("context_report")
                if report is not None and self.report_callback is not None:
                    self.report_callback(report)
                if '"type":"v2_chapter_memory"' in user:
                    return '{"type":"v2_chapter_memory","chapter_id":"chapter_0001","summary":"摘要","facts":[],"open_threads":[]}'
                if '"type":"consistency_report"' in user:
                    return '{"type":"consistency_report","chapter_id":"chapter_0001","status":"ok","issues":[]}'
                marker = "续写任务已完成" if "新增片段" in user else "扩写任务已完成"
                return f"<NOVEL_TEXT>{'雾。' * 300}</NOVEL_TEXT><NOVALIST_TASK_DONE>{marker}</NOVALIST_TASK_DONE>"

            def cleanup(self):
                return None

        with tempfile.TemporaryDirectory() as tmp, patch(
            "application.ai_task_service.DSHClient", FakeDSHClient
        ):
            project = self._project(Path(tmp))
            documents = DocumentV2Service()
            service = AITaskService(
                config_loader=lambda: {"chapter_target_chars": 600},
                document_v2_service=documents,
                reconstruction_service=ReconstructionService(),
            )
            try:
                for kind, expected_type in (
                    ("expand", "writing"),
                    ("continuation", "writing"),
                    ("check", "consistency"),
                    ("memory", "memory"),
                ):
                    revision = documents.open_document(project, "chapter_0001").revision
                    task = service.start(
                        project, kind, "chapter_0001",
                        source_revision=revision, notice_accepted=True,
                    )
                    _wait_for(service, task["taskId"], "succeeded")
                    result = service.result(task["taskId"])["result"]
                    self.assertEqual(result["type"], expected_type)
            finally:
                service.shutdown()


if __name__ == "__main__":
    unittest.main()
