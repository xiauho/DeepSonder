"""Integration checks for the CI runner's failure detection."""
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.py"
PASS = "import unittest\nclass Example(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n"


class TestRunnerTests(unittest.TestCase):
    def invoke(self, sources, timeout=20):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name, source in sources.items():
                (directory / name).write_text(source, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(RUNNER), "--test-dir", temporary,
                 "--timeout", str(timeout)], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )

    def test_success_and_process_state_are_isolated(self):
        source = "import os\nassert 'DEEPSONDER_RUNNER_TEST' not in os.environ\nos.environ['DEEPSONDER_RUNNER_TEST']='1'\n" + PASS
        result = self.invoke({"test_a.py": source, "test_b.py": source})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("tests: 2", result.stdout)

    def test_failures_crashes_and_false_success_do_not_stop_other_modules(self):
        for source in (PASS.replace("assertTrue(True)", "assertTrue(False)"),
                       "import os\nos._exit(7)\n",
                       "import os\nos._exit(0)\n",
                       "# empty suite\n"):
            with self.subTest(source=source):
                result = self.invoke({"test_a.py": source, "test_b.py": PASS})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("[2/2] test_b.py", result.stdout)
                self.assertIn("failed modules: 1", result.stdout)

    def test_timeout_is_a_failure(self):
        result = self.invoke({"test_wait.py": "import time\ntime.sleep(30)\n"}, timeout=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TIMEOUT: test_wait.py", result.stdout)

    def test_no_modules_is_a_failure(self):
        result = self.invoke({})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No test modules found", result.stdout)
