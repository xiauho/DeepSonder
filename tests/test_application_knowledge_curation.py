import tempfile
import unittest
from pathlib import Path

from application.manuscript_import_service import ManuscriptImportService
from application.project_v2_service import ProjectV2Service
from application.reconstruction_service import ReconstructionError, ReconstructionService


class KnowledgeCurationTests(unittest.TestCase):
    def _reviewed(self, root: Path):
        source = root / "source.md"
        source.write_text(
            "# 第一章\n\n林砚拆开信封。苏乔低声说：等等。\n\n"
            "[[关系:林砚|调查搭档|苏乔]]\n",
            encoding="utf-8",
        )
        output = root / "output"
        output.mkdir()
        project = ProjectV2Service().create_project(
            output, "整理项目", import_plan=ManuscriptImportService().scan(source)
        )
        service = ReconstructionService()
        batch = service.generate(project)
        for proposal in batch["proposals"]:
            if proposal["kind"] == "entity":
                service.review(project, batch["batch_id"], proposal["proposal_id"], "accepted")
        relation = next(item for item in batch["proposals"] if item["kind"] == "relation")
        service.review(project, batch["batch_id"], relation["proposal_id"], "accepted")
        return project, service, batch

    def test_rename_alias_relation_edit_delete_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, service, _batch = self._reviewed(Path(tmp))
            knowledge = service.knowledge_snapshot(project)
            by_name = {item["display_name"]: item for item in knowledge.entities}
            lin, su = by_name["林砚"], by_name["苏乔"]
            relation = knowledge.relations[0]

            renamed = service.rename_entity(project, lin["entity_id"], "林砚舟")
            self.assertIn("林砚舟", {item["display_name"] for item in renamed.entities})
            aliased = service.set_entity_aliases(project, lin["entity_id"], ["阿砚", "林砚"])
            updated_lin = next(item for item in aliased.entities if item["entity_id"] == lin["entity_id"])
            self.assertEqual(updated_lin["aliases"], ["阿砚", "林砚"])

            updated = service.update_relation(
                project, relation["relation_id"], su["entity_id"], lin["entity_id"], "保护对象"
            )
            self.assertEqual(updated.relations[0]["source_entity_id"], su["entity_id"])
            self.assertEqual(updated.relations[0]["label"], "保护对象")
            deleted = service.delete_relation(project, relation["relation_id"])
            self.assertEqual(deleted.relations, ())
            self.assertEqual(deleted.operation_count, 4)

            reopened = ProjectV2Service.open_project(project.root)
            persisted = service.knowledge_snapshot(reopened)
            self.assertEqual(persisted.operation_count, 4)
            self.assertEqual(persisted.relations, ())
            self.assertTrue((project.root / "provenance" / "curation_log.json").is_file())

    def test_merge_rewrites_identity_and_removes_self_relation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, service, _batch = self._reviewed(Path(tmp))
            entities = {item["display_name"]: item for item in service.knowledge_snapshot(project).entities}
            service.rename_entity(project, entities["林砚"]["entity_id"], "林砚舟")
            service.set_entity_aliases(project, entities["林砚"]["entity_id"], ["阿砚"])
            merged = service.merge_entities(
                project, entities["林砚"]["entity_id"], entities["苏乔"]["entity_id"]
            )
            self.assertEqual(len(merged.entities), 1)
            self.assertIn("林砚舟", merged.entities[0]["aliases"])
            self.assertIn("阿砚", merged.entities[0]["aliases"])
            self.assertEqual(merged.relations, ())
            self.assertEqual(len(merged.merges), 1)

            split = service.unmerge_entity(project, entities["林砚"]["entity_id"])
            self.assertEqual(len(split.entities), 2)
            self.assertEqual(split.merges, ())
            restored = next(item for item in split.entities if item["entity_id"] == entities["林砚"]["entity_id"])
            self.assertEqual(restored["display_name"], "林砚舟")
            self.assertIn("阿砚", restored["aliases"])
            self.assertEqual(len(split.relations), 1)

    def test_identity_collision_and_proposal_reopen_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, service, batch = self._reviewed(Path(tmp))
            entities = {item["display_name"]: item for item in service.knowledge_snapshot(project).entities}
            with self.assertRaises(ReconstructionError):
                service.rename_entity(project, entities["林砚"]["entity_id"], "苏乔")

            proposal = next(item for item in batch["proposals"] if item.get("name") == "林砚")
            reopened = service.reopen_proposal(project, batch["batch_id"], proposal["proposal_id"])
            self.assertEqual(next(item for item in reopened["proposals"] if item["proposal_id"] == proposal["proposal_id"])["status"], "pending")
            self.assertEqual(service.knowledge_snapshot(project).operation_count, 1)
            self.assertEqual(len(service.knowledge_snapshot(project).entities), 1)


if __name__ == "__main__":
    unittest.main()
