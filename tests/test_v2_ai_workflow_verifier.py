import tempfile
import unittest
from pathlib import Path

from application.document_v2_service import DocumentV2Service
from scripts.verify_v2_ai_workflows import (
    _create_synthetic_project,
    _fingerprint,
    _parse_args,
)


class V2AIWorkflowVerifierTests(unittest.TestCase):
    def test_remote_acknowledgement_is_mandatory(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_args([])
        args = _parse_args(["--acknowledge-synthetic-remote", "--target-chars", "450"])
        self.assertTrue(args.acknowledge_synthetic_remote)
        self.assertEqual(args.target_chars, 450)

    def test_synthetic_fixture_has_two_v2_chapters_and_reviewed_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, reconstruction = _create_synthetic_project(Path(tmp))
            manuscript = DocumentV2Service().snapshot(project)
            knowledge = reconstruction.knowledge_snapshot(project)
            self.assertEqual(manuscript.item_count, 2)
            self.assertGreaterEqual(len(knowledge.entities), 2)
            self.assertGreaterEqual(len(knowledge.relations), 1)
            self.assertGreaterEqual(len(knowledge.worlds), 1)
            self.assertEqual(len(_fingerprint(project.root)), 64)
            self.assertFalse((project.root / "canon").exists())
            self.assertFalse((project.root / "outline").exists())


if __name__ == "__main__":
    unittest.main()
