import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PySide6SourceLauncherTests(unittest.TestCase):
    def test_root_launcher_uses_only_pyside6(self) -> None:
        launcher = (PROJECT_ROOT / "run.bat").read_text(encoding="utf-8")
        self.assertIn('"%PYTHON_EXE%" main.py', launcher)
        self.assertIn("DeepSonder-PySide6", launcher)
        self.assertNotIn("npm", launcher)
        self.assertNotIn("electron", launcher.casefold())
        self.assertNotIn("sidecar", launcher.casefold())

    def test_launcher_targets_python_312(self) -> None:
        launcher = (PROJECT_ROOT / "run.bat").read_text(encoding="utf-8")
        self.assertIn("py -3.12 -m venv", launcher)


if __name__ == "__main__":
    unittest.main()
