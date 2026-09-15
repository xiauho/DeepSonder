import json
import tempfile
import unittest
from pathlib import Path

from application.reconstruction_evaluation_service import (
    ReconstructionEvaluationError,
    ReconstructionEvaluationService,
)


CORPUS = Path(__file__).parent / "fixtures" / "reconstruction_quality" / "corpus-v3.json"
LEGACY_CORPUS = Path(__file__).parent / "fixtures" / "reconstruction_quality" / "corpus-v1.json"


class ReconstructionEvaluationServiceTests(unittest.TestCase):
    def test_synthetic_baseline_reports_metrics_calibration_and_diagnostics(self) -> None:
        service = ReconstructionEvaluationService()
        report = service.evaluate(service.load_corpus(CORPUS))
        value = report.to_dict()

        self.assertEqual(value["case_count"], 14)
        self.assertTrue(value["passed"])
        self.assertGreaterEqual(value["modes"]["combined"]["entities"]["recall"], value["modes"]["local"]["entities"]["recall"])
        self.assertGreaterEqual(value["modes"]["combined"]["relations"]["recall"], value["modes"]["local"]["relations"]["recall"])
        self.assertGreaterEqual(value["modes"]["combined"]["worlds"]["recall"], value["modes"]["local"]["worlds"]["recall"])
        self.assertGreaterEqual(value["modes"]["combined"]["character_fields"]["recall"], value["modes"]["local"]["character_fields"]["recall"])
        self.assertGreaterEqual(value["modes"]["combined"]["events"]["recall"], value["modes"]["local"]["events"]["recall"])
        self.assertEqual(value["modes"]["combined"]["diagnostics"]["duplicates_merged"], 5)
        self.assertEqual(value["modes"]["combined"]["diagnostics"]["relation_conflicts"], 1)
        pronoun = next(item for item in value["modes"]["local"]["cases"] if item["case_id"] == "pronoun-continuity")
        self.assertEqual(pronoun["entity"]["fp"], 0)
        self.assertIn("ece", value["modes"]["combined"]["relations"]["calibration"])
        self.assertTrue(all(item["passed"] for item in value["gates"]))

    def test_corpus_must_be_explicitly_synthetic_and_personal_data_free(self) -> None:
        value = json.loads(CORPUS.read_text(encoding="utf-8"))
        value["privacy"]["synthetic"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unsafe.json"
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ReconstructionEvaluationError):
                ReconstructionEvaluationService().load_corpus(path)

    def test_failed_quality_gate_is_machine_readable(self) -> None:
        service = ReconstructionEvaluationService()
        corpus = service.load_corpus(CORPUS)
        corpus["quality_gates"]["combined"]["relations"]["precision"] = 1.0
        report = service.evaluate(corpus)
        self.assertFalse(report.passed)
        self.assertTrue(any(not item["passed"] for item in report.gates))

    def test_local_expectations_use_the_same_strict_annotation_contract(self) -> None:
        value = json.loads(CORPUS.read_text(encoding="utf-8"))
        target = next(item for item in value["cases"] if "local_expected" in item)
        target["local_expected"]["unsupported"] = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "invalid-local-expectation.json"
            path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ReconstructionEvaluationError):
                ReconstructionEvaluationService().load_corpus(path)

    def test_v1_corpus_remains_readable_for_historical_comparison(self) -> None:
        service = ReconstructionEvaluationService()
        report = service.evaluate(service.load_corpus(LEGACY_CORPUS)).to_dict()
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["case_count"], 6)
        self.assertTrue(report["passed"])


if __name__ == "__main__":
    unittest.main()
