import json
import tempfile
import unittest
from pathlib import Path

from application.document_v2_service import DocumentV2Service
from application.manuscript_import_service import ManuscriptImportService
from application.project_v2_service import ProjectV2Service
from application.reconstruction_service import ReconstructionError, ReconstructionService


class ReconstructionServiceTests(unittest.TestCase):
    def _project(self, root: Path):
        source = root / "source.md"
        source.write_text(
            "# 第一章\n\n林砚拆开信封。苏乔低声说：先别点灯。\n\n"
            "[[关系:林砚|调查搭档|苏乔]]\n",
            encoding="utf-8",
        )
        output = root / "output"
        output.mkdir()
        plan = ManuscriptImportService().scan(source)
        return ProjectV2Service().create_project(output, "证据项目", import_plan=plan)

    def test_generate_review_and_graph_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service = ReconstructionService()

            batch = service.generate(project)
            self.assertGreater(len(batch["segments"]), 0)
            entities = [item for item in batch["proposals"] if item["kind"] == "entity"]
            relation = next(item for item in batch["proposals"] if item["kind"] == "relation")
            self.assertEqual({item["name"] for item in entities}, {"林砚", "苏乔"})
            self.assertNotIn("低声", {item["name"] for item in entities})
            self.assertNotIn("自己先", {item["name"] for item in entities})
            self.assertEqual(relation["label"], "调查搭档")
            self.assertEqual(service.generate(project)["batch_id"], batch["batch_id"])

            with self.assertRaises(ReconstructionError):
                service.review(project, batch["batch_id"], relation["proposal_id"], "accepted")
            for entity in entities:
                service.review(project, batch["batch_id"], entity["proposal_id"], "accepted")
            reviewed = service.review(project, batch["batch_id"], relation["proposal_id"], "accepted")
            self.assertEqual(reviewed["status"], "reviewed")

            snapshot = service.snapshot(project)
            self.assertEqual(snapshot.accepted_entity_count, 2)
            self.assertEqual(snapshot.accepted_relation_count, 1)
            self.assertEqual(snapshot.pending_count, 0)
            graph = service.graph_snapshot(project)
            self.assertEqual(len(graph.nodes), 2)
            self.assertEqual(len(graph.edges), 1)
            self.assertEqual(graph.edges[0].source_kind, "reviewed_v2")
            self.assertEqual(graph.edges[0].evidence[0].certainty, "reviewed")

            evidence = json.loads((project.root / "provenance" / "evidence.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(len(evidence["evidence"]), 1)
            self.assertTrue(all(item["chapter_revision"].startswith("v2:") for item in evidence["evidence"]))

    def test_manuscript_change_invalidates_batch_and_reviewed_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service = ReconstructionService()
            batch = service.generate(project)
            first = next(item for item in batch["proposals"] if item["kind"] == "entity")
            service.review(project, batch["batch_id"], first["proposal_id"], "accepted")
            self.assertEqual(service.snapshot(project).accepted_entity_count, 1)

            documents = DocumentV2Service()
            opened = documents.open_document(project, "chapter_0001")
            documents.save_document(
                project, "chapter_0001", "# 第一章\n\n完全改写后的正文。\n",
                expected_revision=opened.revision,
            )
            self.assertTrue(service.invalidate_chapter(project, "chapter_0001"))
            snapshot = service.snapshot(project)
            self.assertEqual(snapshot.stale_batch_count, 1)
            self.assertEqual(snapshot.accepted_entity_count, 0)
            self.assertEqual(snapshot.accepted_relation_count, 0)

    def test_reject_is_durable_and_never_enters_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service = ReconstructionService()
            batch = service.generate(project)
            proposal = batch["proposals"][0]
            service.review(project, batch["batch_id"], proposal["proposal_id"], "rejected")
            reopened = ProjectV2Service.open_project(project.root)
            reread = service.get_batch(reopened, batch["batch_id"])
            self.assertEqual(reread["proposals"][0]["status"], "rejected")

    def test_batch_review_is_atomic_and_accepts_relation_dependencies_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service = ReconstructionService()
            batch = service.generate(project)
            decisions = [
                {"proposal_id": item["proposal_id"], "decision": "accepted"}
                for item in batch["proposals"]
            ]
            reviewed = service.review_many(project, batch["batch_id"], decisions)
            self.assertEqual(reviewed["status"], "reviewed")
            self.assertTrue(all(item["review_mode"] == "batch" for item in reviewed["proposals"]))
            self.assertEqual(service.snapshot(project).accepted_entity_count, 2)
            self.assertEqual(service.snapshot(project).accepted_relation_count, 1)

        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            service = ReconstructionService()
            batch = service.generate(project)
            before = (project.root / "proposals" / f"{batch['batch_id']}.json").read_text(encoding="utf-8")
            with self.assertRaises(ReconstructionError):
                service.review_many(project, batch["batch_id"], [
                    {"proposal_id": batch["proposals"][0]["proposal_id"], "decision": "accepted"},
                    {"proposal_id": "proposal_missing", "decision": "rejected"},
                ])
            after = (project.root / "proposals" / f"{batch['batch_id']}.json").read_text(encoding="utf-8")
            self.assertEqual(after, before)
            self.assertTrue(all(item["status"] == "pending" for item in service.get_batch(project, batch["batch_id"])["proposals"]))

    def test_reviewed_world_and_character_fields_generate_read_only_cards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            source.write_text(
                "# 第一章\n\n[[人物:林砚]] [[角色字段:林砚|身份|雾港调查员]] "
                "[[世界:雾港|地点|终年被浓雾笼罩的港城]]\n",
                encoding="utf-8",
            )
            output = root / "output"
            output.mkdir()
            project = ProjectV2Service().create_project(
                output, "结构化知识", import_plan=ManuscriptImportService().scan(source),
            )
            service = ReconstructionService()
            batch = service.generate(project)
            kinds = {item["kind"] for item in batch["proposals"]}
            self.assertEqual(kinds, {"entity", "character_field", "world"})
            reviewed = service.review_many(project, batch["batch_id"], [
                {"proposal_id": item["proposal_id"], "decision": "accepted"}
                for item in batch["proposals"]
            ])
            self.assertEqual(reviewed["status"], "reviewed")
            knowledge = service.knowledge_snapshot(project)
            self.assertEqual(knowledge.entities[0]["profile_fields"], {"身份": "雾港调查员"})
            self.assertEqual(knowledge.worlds[0]["name"], "雾港")
            character_card = project.root / knowledge.entities[0]["card_relative_path"]
            world_card = project.root / knowledge.worlds[0]["card_relative_path"]
            self.assertIn("NOVALIST GENERATED PROJECTION", character_card.read_text(encoding="utf-8"))
            self.assertIn("雾港调查员", character_card.read_text(encoding="utf-8"))
            self.assertIn("终年被浓雾笼罩", world_card.read_text(encoding="utf-8"))

            entity_id = knowledge.entities[0]["entity_id"]
            world_id = knowledge.worlds[0]["world_id"]
            changed = service.update_character_field(project, entity_id, "身份", "雾港首席调查员")
            self.assertEqual(changed.entities[0]["profile_fields"]["身份"], "雾港首席调查员")
            hidden = service.hide_character_field(project, entity_id, "身份")
            self.assertNotIn("身份", hidden.entities[0]["profile_fields"])
            self.assertEqual(hidden.entities[0]["hidden_profile_fields"]["身份"], "雾港首席调查员")
            restored = service.restore_character_field(project, entity_id, "身份")
            self.assertEqual(restored.entities[0]["profile_fields"]["身份"], "雾港首席调查员")

            changed = service.update_world(project, world_id, "雾港城", "核心地点", "一座终年被浓雾笼罩的港城")
            self.assertEqual(changed.worlds[0]["name"], "雾港城")
            hidden = service.hide_world(project, world_id)
            self.assertEqual(hidden.worlds, ())
            self.assertEqual(hidden.hidden_worlds[0]["description"], "一座终年被浓雾笼罩的港城")
            self.assertFalse(world_card.exists())
            restored = service.restore_world(project, world_id)
            self.assertEqual(restored.worlds[0]["category"], "核心地点")
            self.assertTrue(world_card.exists())

            generated = service.open_knowledge_card(project, "character", entity_id, "generated")
            self.assertTrue(generated["read_only"])
            self.assertIn("雾港首席调查员", generated["content"])
            author = service.open_knowledge_card(project, "character", entity_id, "author")
            self.assertFalse(author["read_only"])
            with self.assertRaises(ReconstructionError):
                service.open_knowledge_card(project, "character", "../越界", "author")
            saved = service.save_author_card(
                project, "character", entity_id,
                "# 作者补充\n\n此内容不应被重建覆盖。\n", author["revision"],
            )
            with self.assertRaises(ReconstructionError):
                service.save_author_card(project, "character", entity_id, "过期写入", author["revision"])
            service.reconcile(project)
            self.assertIn("不应被重建覆盖", service.open_knowledge_card(project, "character", entity_id, "author")["content"])
            self.assertNotEqual(saved["revision"], author["revision"])
            self.assertEqual(service.knowledge_snapshot(project).operation_count, 6)

    def test_conflicting_character_fields_cannot_both_be_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            source.write_text(
                "# 第一章\n\n[[人物:林砚]] [[角色字段:林砚|身份|调查员]] "
                "[[角色字段:林砚|身份|记者]]\n",
                encoding="utf-8",
            )
            output = root / "output"
            output.mkdir()
            project = ProjectV2Service().create_project(output, "冲突字段", import_plan=ManuscriptImportService().scan(source))
            service = ReconstructionService()
            batch = service.generate(project)
            with self.assertRaises(ReconstructionError):
                service.review_many(project, batch["batch_id"], [
                    {"proposal_id": item["proposal_id"], "decision": "accepted"}
                    for item in batch["proposals"]
                ])
            self.assertTrue(all(item["status"] == "pending" for item in service.get_batch(project, batch["batch_id"])["proposals"]))

    def test_merge_keeps_target_character_field_visibility_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            source.write_text(
                "# 第一章\n\n[[人物:林砚]] [[人物:苏乔]] "
                "[[角色字段:林砚|身份|调查员]] [[角色字段:苏乔|身份|医生]]\n",
                encoding="utf-8",
            )
            output = root / "output"
            output.mkdir()
            project = ProjectV2Service().create_project(
                output, "合并字段", import_plan=ManuscriptImportService().scan(source),
            )
            service = ReconstructionService()
            batch = service.generate(project)
            service.review_many(project, batch["batch_id"], [
                {"proposal_id": item["proposal_id"], "decision": "accepted"}
                for item in batch["proposals"]
            ])
            entities = {item["display_name"]: item for item in service.knowledge_snapshot(project).entities}
            lin_id, su_id = entities["林砚"]["entity_id"], entities["苏乔"]["entity_id"]
            service.hide_character_field(project, su_id, "身份")
            merged = service.merge_entities(project, lin_id, su_id)
            self.assertEqual(len(merged.entities), 1)
            self.assertNotIn("身份", merged.entities[0]["profile_fields"])
            self.assertEqual(merged.entities[0]["hidden_profile_fields"]["身份"], "医生")
            split = service.unmerge_entity(project, lin_id)
            values = {item["display_name"]: item for item in split.entities}
            self.assertEqual(values["林砚"]["profile_fields"]["身份"], "调查员")
            self.assertEqual(values["苏乔"]["hidden_profile_fields"]["身份"], "医生")

    def test_reviewed_event_projects_timeline_links_and_audited_curation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            source.write_text(
                "# 第一章\n\n[[人物:林砚]] [[人物:苏乔]] "
                "[[世界:雾港|地点|终年被浓雾笼罩的港城]] "
                "[[事件:雨夜|收到密信|林砚与苏乔在雾港收到密信|林砚、苏乔|雾港]]\n",
                encoding="utf-8",
            )
            output = root / "output"
            output.mkdir()
            project = ProjectV2Service().create_project(
                output, "事件图谱", import_plan=ManuscriptImportService().scan(source),
            )
            service = ReconstructionService()
            batch = service.generate(project)
            event = next(item for item in batch["proposals"] if item["kind"] == "event")
            self.assertEqual(event["character_names"], ["林砚", "苏乔"])
            with self.assertRaises(ReconstructionError):
                service.review(project, batch["batch_id"], event["proposal_id"], "accepted")
            reviewed = service.review_many(project, batch["batch_id"], [
                {"proposal_id": item["proposal_id"], "decision": "accepted"}
                for item in batch["proposals"]
            ])
            self.assertEqual(reviewed["status"], "reviewed")
            knowledge = service.knowledge_snapshot(project)
            self.assertEqual(len(knowledge.events), 1)
            self.assertEqual(knowledge.events[0]["order"], 1)
            self.assertEqual(len(knowledge.events[0]["participant_entity_ids"]), 2)
            self.assertEqual(len(knowledge.events[0]["world_ids"]), 1)
            graph = service.graph_snapshot(project)
            self.assertEqual({item.node_kind for item in graph.nodes}, {"character", "world", "event"})
            self.assertEqual({item.edge_kind for item in graph.edges}, {"participation", "setting"})
            self.assertEqual(len(graph.edges), 3)

            event_id = knowledge.events[0]["event_id"]
            changed = service.update_event(project, event_id, "深夜", "密信抵达", "两人在港口收到匿名密信")
            self.assertEqual(changed.events[0]["title"], "密信抵达")
            hidden = service.hide_event(project, event_id)
            self.assertEqual(hidden.events, ())
            self.assertEqual(hidden.hidden_events[0]["time_label"], "深夜")
            self.assertFalse(any(node.node_kind == "event" for node in service.graph_snapshot(project).nodes))
            restored = service.restore_event(project, event_id)
            self.assertEqual(restored.events[0]["description"], "两人在港口收到匿名密信")
            self.assertEqual(restored.operation_count, 3)

    def test_event_links_manual_order_and_diagnostics_are_rebuild_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.md"
            source.write_text(
                "# 第一章\n\n[[人物:林砚]] [[人物:苏乔]] "
                "[[世界:雾港|地点|终年被浓雾笼罩的港城]] "
                "[[事件:雨夜|收到密信|两人在港口收到密信|林砚、苏乔|雾港]] "
                "[[事件:雨夜|前往灯塔|林砚决定前往灯塔|林砚|雾港]]\n",
                encoding="utf-8",
            )
            output = root / "output"
            output.mkdir()
            project = ProjectV2Service().create_project(
                output, "事件整理", import_plan=ManuscriptImportService().scan(source),
            )
            service = ReconstructionService()
            batch = service.generate(project)
            service.review_many(project, batch["batch_id"], [
                {"proposal_id": item["proposal_id"], "decision": "accepted"}
                for item in batch["proposals"]
            ])
            knowledge = service.knowledge_snapshot(project)
            self.assertEqual(len(knowledge.evidence), 1)
            self.assertEqual([item["code"] for item in knowledge.diagnostics], ["temporal_overlap"])
            by_title = {item["title"]: item for item in knowledge.events}
            first, second = by_title["收到密信"], by_title["前往灯塔"]
            lin_id = next(item["entity_id"] for item in knowledge.entities if item["display_name"] == "林砚")

            changed = service.update_event_links(project, first["event_id"], [lin_id], [])
            changed_first = next(item for item in changed.events if item["event_id"] == first["event_id"])
            self.assertEqual(changed_first["participant_entity_ids"], [lin_id])
            self.assertEqual(changed_first["world_ids"], [])
            with self.assertRaises(ReconstructionError):
                service.update_event_links(project, first["event_id"], [lin_id, lin_id], [])

            reordered = service.reorder_events(project, [second["event_id"], first["event_id"]])
            self.assertEqual([item["title"] for item in reordered.events], ["前往灯塔", "收到密信"])
            self.assertEqual([item["order"] for item in reordered.events], [1, 2])
            self.assertEqual(
                next(item for item in reordered.events if item["event_id"] == first["event_id"])["source_order"],
                first["source_order"],
            )
            with self.assertRaises(ReconstructionError):
                service.reorder_events(project, [first["event_id"]])

            fog_id = knowledge.worlds[0]["world_id"]
            hidden = service.hide_world(project, fog_id)
            self.assertIn("inactive_world_link", {item["code"] for item in hidden.diagnostics})
            self.assertTrue(all(item["world_ids"] == [] for item in hidden.events))
            restored = service.restore_world(project, fog_id)
            restored_second = next(item for item in restored.events if item["event_id"] == second["event_id"])
            self.assertEqual(restored_second["world_ids"], [fog_id])
            self.assertEqual({item["code"] for item in restored.diagnostics}, {"temporal_overlap"})
            self.assertEqual([item["title"] for item in restored.events], ["前往灯塔", "收到密信"])


if __name__ == "__main__":
    unittest.main()
