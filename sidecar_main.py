"""Packaging-friendly entry point for the Novalist local sidecar."""

from sidecar.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
