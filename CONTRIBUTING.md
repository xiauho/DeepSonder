# Contributing to DeepSonder-PySide6

Contributions are welcome through issues and pull requests. The displayed brand
is **DeepSonder**; executable, package, diagnostic and release identifiers use
**DeepSonder-PySide6**. This repository is maintained independently.

Before submitting a change:

1. Do not include API keys, `.env` files, local `config.json`, private manuscripts
   or generated environments.
2. Keep DeepSeek and DeepSeek Harness references factual and compatibility-only;
   do not imply official authorization or endorsement.
3. Run `python scripts/run_tests.py`.
4. Run `python -m compileall -q main.py application core ui`.
5. Explain user-visible behavior and privacy effects in the pull request.
6. Update [feature status](docs/feature-status.json) when capabilities change and
   record current-series changes under `Unreleased` in [CHANGELOG.md](CHANGELOG.md).
7. Follow [PACKAGING.md](PACKAGING.md) for release builds. Do not run historical
   build or update instructions from the [archive](docs/archive/README.md).

Do not globally replace legacy identifiers in project data, protocols, import
fixtures, license texts or copyright notices. Such changes require their own
compatibility and data-safety review.

By contributing, you agree that your contribution may be distributed under the
project's MIT License and confirm that you have the right to submit it.

The test runner isolates each module in a fresh process because Qt core and GUI
application instances cannot share the same test process. Module failures, crashes,
missing completion reports, empty suites, and timeouts fail the run.
