"""Create a disposable schema-v2 project for packaged Electron smoke tests."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from application.manuscript_import_service import ManuscriptImportService
from application.project_v2_service import ProjectV2Service


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_parent", type=Path)
    args = parser.parse_args()

    parent = args.output_parent.expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    source = parent / "packaged-open-source.md"
    source.write_text(
        "# 第一章 干净配置演练\n\n林砚在雾港打开了新的项目。\n",
        encoding="utf-8",
    )
    plan = ManuscriptImportService().scan(source)
    project = ProjectV2Service().create_project(
        parent,
        "schema-v2-open-rehearsal",
        author="Novalist packaged self-test",
        import_plan=plan,
    )
    print(project.root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
