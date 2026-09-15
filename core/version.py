"""Application version parsing and precedence rules."""

from __future__ import annotations

import re
from functools import total_ordering
from pathlib import Path

from .resources import resource_path


_VERSION_PATTERN = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


@total_ordering
class AppVersion:
    """Small SemVer-compatible value object used by the updater."""

    def __init__(
        self,
        major: int,
        minor: int,
        patch: int,
        prerelease: tuple[int | str, ...] = (),
    ) -> None:
        self.major = int(major)
        self.minor = int(minor)
        self.patch = int(patch)
        self.prerelease = tuple(prerelease)

    @classmethod
    def parse(cls, value: str) -> "AppVersion":
        candidate = str(value or "").strip()
        match = _VERSION_PATTERN.fullmatch(candidate)
        if match is None:
            raise ValueError(f"无法识别版本号：{candidate or '空值'}")
        prerelease_text = match.group("prerelease") or ""
        prerelease: list[int | str] = []
        for item in prerelease_text.split(".") if prerelease_text else ():
            if not item:
                raise ValueError(f"无法识别版本号：{candidate}")
            if item.isdigit():
                if len(item) > 1 and item.startswith("0"):
                    raise ValueError(f"无法识别版本号：{candidate}")
                prerelease.append(int(item))
            else:
                prerelease.append(item.casefold())
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            tuple(prerelease),
        )

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def prerelease_stage(self) -> str:
        for item in self.prerelease:
            if isinstance(item, str):
                match = re.match(r"[a-z]+", item)
                return match.group(0) if match else item
        return ""

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if not self.prerelease:
            return base
        return base + "-" + ".".join(str(item) for item in self.prerelease)

    def __repr__(self) -> str:
        return f"AppVersion('{self}')"

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AppVersion):
            return NotImplemented
        return self._compare(other) == 0

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, AppVersion):
            return NotImplemented
        return self._compare(other) < 0

    def _compare(self, other: "AppVersion") -> int:
        own_base = (self.major, self.minor, self.patch)
        other_base = (other.major, other.minor, other.patch)
        if own_base != other_base:
            return -1 if own_base < other_base else 1
        if not self.prerelease and not other.prerelease:
            return 0
        if not self.prerelease:
            return 1
        if not other.prerelease:
            return -1
        for own_item, other_item in zip(self.prerelease, other.prerelease):
            if own_item == other_item:
                continue
            if isinstance(own_item, int) and isinstance(other_item, str):
                return -1
            if isinstance(own_item, str) and isinstance(other_item, int):
                return 1
            return -1 if own_item < other_item else 1
        if len(self.prerelease) == len(other.prerelease):
            return 0
        return -1 if len(self.prerelease) < len(other.prerelease) else 1


def load_current_version(path: Path | None = None) -> AppVersion:
    """Read the bundled VERSION file."""
    version_path = Path(path) if path is not None else resource_path("VERSION")
    try:
        value = version_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("无法读取当前版本信息。") from exc
    return AppVersion.parse(value)
