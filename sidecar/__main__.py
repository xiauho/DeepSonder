"""Run the Novalist sidecar over stdin/stdout."""

from __future__ import annotations

import logging
import sys

from sidecar.application import SidecarApplication
from sidecar.server import SidecarServer


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return SidecarServer(
        SidecarApplication(),
        sys.stdin.buffer,
        sys.stdout.buffer,
    ).serve()


if __name__ == "__main__":
    raise SystemExit(main())
