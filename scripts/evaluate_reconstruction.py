"""Evaluate reconstruction quality on a synthetic, checked-in corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from application.reconstruction_evaluation_service import ReconstructionEvaluationService


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Novalist reconstruction quality.")
    parser.add_argument("--corpus", type=Path, default=PROJECT_ROOT / "tests" / "fixtures" / "reconstruction_quality" / "corpus-v3.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args()

    service = ReconstructionEvaluationService()
    report = service.evaluate(service.load_corpus(args.corpus)).to_dict()
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 2 if args.fail_on_regression and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
