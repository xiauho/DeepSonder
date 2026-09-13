"""Read-only discovery of manuscript chapters for schema-v2 imports."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


ALLOWED_MANUSCRIPT_SUFFIXES = {".md", ".markdown", ".txt"}


class ManuscriptImportError(ValueError):
    """Raised when a source cannot be imported without ambiguity or loss."""


@dataclass(frozen=True)
class ImportWarning:
    code: str
    message: str
    source: str


@dataclass(frozen=True)
class ImportChapterCandidate:
    chapter_id: str
    sequence: int
    title: str
    source_path: str
    source_name: str
    encoding: str
    byte_count: int
    source_sha256: str
    content_sha256: str
    content: str


@dataclass(frozen=True)
class ManuscriptImportPlan:
    source_path: str
    source_root: str
    source_kind: str
    chapters: tuple[ImportChapterCandidate, ...]
    warnings: tuple[ImportWarning, ...]
    total_source_bytes: int
    digest: str


class ManuscriptImportService:
    """Scan v1 projects or external text without writing to the source."""

    MAX_CHAPTERS = 2_000
    MAX_FILE_BYTES = 16 * 1024 * 1024
    MAX_TOTAL_BYTES = 512 * 1024 * 1024

    def scan(self, source: Path | str) -> ManuscriptImportPlan:
        source_path = Path(source).expanduser().resolve()
        if not source_path.exists():
            raise FileNotFoundError(source_path)

        legacy = self._is_v1_project_source(source_path)
        if legacy:
            source_root = source_path
            source_kind = "novalist_v1_manuscript"
            files = list((source_path / "outline" / "chapters").glob("*.md"))
        elif source_path.is_file():
            source_root = source_path.parent
            source_kind = "external_manuscript"
            files = [source_path]
        elif source_path.is_dir():
            source_root = source_path
            source_kind = "external_manuscript"
            files = [
                path for path in source_path.rglob("*")
                if path.is_file() and path.suffix.casefold() in ALLOWED_MANUSCRIPT_SUFFIXES
            ]
        else:
            raise ManuscriptImportError("导入来源必须是普通文件或目录。")

        files = sorted(files, key=lambda path: self._natural_key(path, source_root))
        if not files:
            raise ManuscriptImportError("导入来源中没有可识别的 Markdown 或文本正文。")
        if len(files) > self.MAX_CHAPTERS:
            raise ManuscriptImportError(f"正文文件超过上限：{self.MAX_CHAPTERS}")

        chapters: list[ImportChapterCandidate] = []
        warnings: list[ImportWarning] = []
        total_bytes = 0
        for source_file in files:
            if source_file.is_symlink():
                warnings.append(ImportWarning(
                    "symlink_skipped", "符号链接不会作为正文导入。", self._locator(source_file, source_root)
                ))
                continue
            payload = self._read_consistent_bytes(source_file)
            if len(payload) > self.MAX_FILE_BYTES:
                raise ManuscriptImportError(f"单个正文文件超过 16 MiB：{source_file.name}")
            total_bytes += len(payload)
            if total_bytes > self.MAX_TOTAL_BYTES:
                raise ManuscriptImportError("导入正文总大小超过 512 MiB。")
            if b"\x00" in payload:
                raise ManuscriptImportError(f"正文文件包含二进制数据：{source_file.name}")
            text, encoding = self._decode(payload, source_file)
            title = self._title(text, source_file)
            content = self._legacy_body(text) if legacy else self._external_body(text)
            content = self._normalize_content(content)
            locator = self._locator(source_file, source_root)
            if not content.strip():
                warnings.append(ImportWarning(
                    "empty_manuscript", "没有识别到正文内容，请在导入前检查。", locator
                ))
            sequence = len(chapters) + 1
            chapters.append(ImportChapterCandidate(
                chapter_id=f"chapter_{sequence:04d}",
                sequence=sequence,
                title=title,
                source_path=str(source_file.resolve()),
                source_name=locator,
                encoding=encoding,
                byte_count=len(payload),
                source_sha256=hashlib.sha256(payload).hexdigest(),
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                content=content,
            ))

        if not chapters:
            raise ManuscriptImportError("导入来源中没有普通正文文件。")
        digest = self.plan_digest(source_kind, tuple(chapters))
        return ManuscriptImportPlan(
            source_path=str(source_path),
            source_root=str(source_root),
            source_kind=source_kind,
            chapters=tuple(chapters),
            warnings=tuple(warnings),
            total_source_bytes=total_bytes,
            digest=digest,
        )

    @staticmethod
    def plan_digest(
        source_kind: str,
        chapters: tuple[ImportChapterCandidate, ...],
    ) -> str:
        digest = hashlib.sha256()
        digest.update(f"manuscript-import-v1\0{source_kind}\0".encode("utf-8"))
        for chapter in chapters:
            digest.update(
                f"{chapter.chapter_id}\0{chapter.title}\0{chapter.source_name}\0"
                f"{chapter.source_sha256}\0{chapter.content_sha256}\0".encode("utf-8")
            )
        return f"import-v1:{digest.hexdigest()}"

    @staticmethod
    def _is_v1_project_source(path: Path) -> bool:
        return (
            path.is_dir()
            and (path / "project.json").is_file()
            and (path / "outline" / "chapters").is_dir()
        )

    @staticmethod
    def _decode(payload: bytes, path: Path) -> tuple[str, str]:
        try:
            return payload.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError:
            try:
                return payload.decode("gb18030"), "gb18030"
            except UnicodeDecodeError as exc:
                raise ManuscriptImportError(f"无法识别正文编码：{path.name}") from exc

    @staticmethod
    def _read_consistent_bytes(path: Path) -> bytes:
        for _attempt in range(2):
            try:
                before = path.stat()
                payload = path.read_bytes()
                after = path.stat()
            except OSError as exc:
                raise ManuscriptImportError(f"无法读取正文文件：{path}") from exc
            before_key = (before.st_mtime_ns, before.st_size)
            after_key = (after.st_mtime_ns, after.st_size)
            if before_key == after_key and len(payload) == after.st_size:
                return payload
        raise ManuscriptImportError(f"扫描期间正文文件持续变化：{path.name}")

    @staticmethod
    def _title(text: str, path: Path) -> str:
        for line in text.replace("\r\n", "\n").split("\n"):
            if line.startswith("# ") and line[2:].strip():
                return line[2:].strip()
        return path.stem.strip() or "未命名章节"

    @staticmethod
    def _legacy_body(text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        match = re.search(r"^##[ \t]+正文[ \t]*$", normalized, flags=re.MULTILINE)
        if match is None:
            return ManuscriptImportService._external_body(normalized)
        start = match.end()
        following = re.search(r"^##[ \t]+.+$", normalized[start:], flags=re.MULTILINE)
        end = start + following.start() if following is not None else len(normalized)
        return normalized[start:end]

    @staticmethod
    def _external_body(text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = normalized.split("\n")
        first_nonempty = next((index for index, line in enumerate(lines) if line.strip()), None)
        if first_nonempty is not None and lines[first_nonempty].startswith("# "):
            del lines[first_nonempty]
        return "\n".join(lines)

    @staticmethod
    def _normalize_content(text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
        return f"{normalized}\n" if normalized else ""

    @staticmethod
    def _locator(path: Path, root: Path) -> str:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return path.name

    @classmethod
    def _natural_key(cls, path: Path, root: Path) -> tuple:
        locator = cls._locator(path, root).casefold()
        return tuple(
            (0, int(part)) if part.isdigit() else (1, part)
            for part in re.split(r"(\d+)", locator)
            if part
        )
