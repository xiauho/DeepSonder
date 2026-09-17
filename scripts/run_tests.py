"""Run each unittest module in a fresh process to isolate Qt application state."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]


def run_module(directory, pattern, report):
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(str(directory), pattern=pattern)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report.write_text(json.dumps({
        "tests": result.testsRun,
        "skipped": len(result.skipped),
        "successful": result.wasSuccessful(),
    }), encoding="utf-8")
    return 0 if result.wasSuccessful() and result.testsRun else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test-dir", type=Path, default=ROOT / "tests")
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    directory = args.test_dir.resolve()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.report:
        return run_module(directory, args.pattern, args.report)
    modules = sorted(directory.glob(args.pattern))
    if not modules:
        print("No test modules found", flush=True)
        return 1
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTHONUTF8": "1"}
    failures = []
    tests = skipped = 0
    with TemporaryDirectory(prefix="deepsonder-tests-") as temporary:
        for index, module in enumerate(modules, 1):
            print(f"\n[{index}/{len(modules)}] {module.name}", flush=True)
            report = Path(temporary) / f"{index}.json"
            command = [sys.executable, str(Path(__file__).resolve()),
                       "--test-dir", str(directory), "--pattern", module.name,
                       "--report", str(report)]
            try:
                process = subprocess.run(command, cwd=ROOT, env=env, timeout=args.timeout)
                data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
                tests += data.get("tests", 0)
                skipped += data.get("skipped", 0)
                if process.returncode or not data.get("successful") or not data.get("tests"):
                    failures.append(module.name)
                    print(f"FAILED: {module.name} (exit {process.returncode}; missing or failed results)", flush=True)
            except subprocess.TimeoutExpired:
                failures.append(module.name)
                print(f"TIMEOUT: {module.name} after {args.timeout}s", flush=True)
    print(f"\nModules: {len(modules)}; tests: {tests}; skipped: {skipped}; failed modules: {len(failures)}", flush=True)
    if failures:
        print("Failed modules: " + ", ".join(failures), flush=True)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
