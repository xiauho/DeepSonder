import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ElectronSourceLauncherTests(unittest.TestCase):
    def test_root_launcher_uses_electron_and_python_sidecar(self) -> None:
        launcher = (PROJECT_ROOT / "run.bat").read_text(encoding="utf-8")
        self.assertIn("call npm start", launcher)
        self.assertIn("call npm run self-test", launcher)
        self.assertIn("NOVALIST_PYTHON", launcher)
        self.assertIn("node_modules\\.bin\\tsc.cmd", launcher)
        self.assertIn("Get-Process electron", launcher)
        self.assertNotIn('"%APP_PYTHON%" main.py', launcher)

    def test_documented_node_floor_matches_electron_package(self) -> None:
        launcher = (PROJECT_ROOT / "run.bat").read_text(encoding="utf-8")
        package = (PROJECT_ROOT / "electron" / "package.json").read_text(
            encoding="utf-8"
        )
        self.assertIn(">=24.0.0", package)
        self.assertIn(">= 24", launcher)


if __name__ == "__main__":
    unittest.main()
