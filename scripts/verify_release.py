"""Audit a PySide6 release ZIP and its manifest without extracting it."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile

REQUIRED = {"DeepSonder-PySide6.exe", "LICENSE", "PRIVACY.md", "THIRD_PARTY_NOTICES.md",
            "RELEASE_NOTES.md", "_internal/VERSION", "licenses/README.md"}
FORBIDDEN_PARTS = {"projects", ".git", ".venv", "node_modules", "sidecar", "electron"}
FORBIDDEN_FILES = {"config.json", "ui-state.json", "story_state.json", "chapter_summaries.json",
                   "accepted_chapter_memory.json", ".env"}


def verify_release(directory: Path) -> dict:
    manifest = json.loads((directory / "release-manifest.json").read_text("utf-8-sig"))
    assert manifest["product"] == "DeepSonder-PySide6", "Wrong product"
    assert manifest["platform"] == "windows" and manifest["architecture"] == "x64", "Wrong platform"
    assert manifest["update_channel"] is None, "Unexpected update channel"
    assert manifest.get("source_dirty") is False, "Uncommitted source"
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["source_revision"]), "Missing source revision"
    version = manifest["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", version), "Invalid version"
    asset = manifest["asset"]
    assert asset["name"] == f"DeepSonder-PySide6-v{version}-windows-x64.zip", "Wrong asset name"
    archive = directory / asset["name"]
    hasher = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    digest = hasher.hexdigest()
    assert digest == asset["sha256"], "ZIP checksum mismatch"
    assert archive.stat().st_size == asset["size"], "ZIP size mismatch"
    assert (directory / "SHA256SUMS.txt").read_text("utf-8-sig").strip() == f"{digest}  {asset['name']}", "Checksum file mismatch"
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None, "Corrupt ZIP member"
        names = {info.filename.replace("\\", "/").rstrip("/") for info in bundle.infolist()}
        assert REQUIRED <= names, "Missing release resources: " + str(sorted(REQUIRED - names))
        for name in names:
            parts = PurePosixPath(name).parts
            assert not name.startswith("/") and ":" not in name and ".." not in parts, "Unsafe ZIP path"
            assert not (set(p.casefold() for p in parts) & FORBIDDEN_PARTS), "Unexpected runtime or user data"
            assert PurePosixPath(name).name.casefold() not in FORBIDDEN_FILES, "Local state included"
        assert bundle.read("_internal/VERSION").decode("utf-8-sig").strip() == version, "Bundled version mismatch"
    return {"passed": True, "version": version, "source_revision": manifest["source_revision"],
            "asset": asset["name"], "sha256": digest, "file_count": len(names)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(verify_release(parser.parse_args().directory), indent=2))
