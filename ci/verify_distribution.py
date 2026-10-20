"""Fail-closed manifest audit for locally built wheel and source archives."""

from __future__ import annotations

import argparse
import re
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


class AuditError(RuntimeError):
    """Raised when an archive crosses the public distribution boundary."""


_WHEEL_PACKAGE_FILES = frozenset(
    {
        "canvas_ed_mcp/__init__.py",
        "canvas_ed_mcp/__main__.py",
        "canvas_ed_mcp/app_menu.py",
        "canvas_ed_mcp/cli.py",
        "canvas_ed_mcp/config.py",
        "canvas_ed_mcp/configure_flow.py",
        "canvas_ed_mcp/server.py",
        "canvas_ed_mcp/course_mapping.py",
        "canvas_ed_mcp/dry_run.py",
        "canvas_ed_mcp/due/__init__.py",
        "canvas_ed_mcp/due/model.py",
        "canvas_ed_mcp/due/outline.py",
        "canvas_ed_mcp/due/render.py",
        "canvas_ed_mcp/due/service.py",
        "canvas_ed_mcp/initial.py",
        "canvas_ed_mcp/launchers.py",
        "canvas_ed_mcp/paths.py",
        "canvas_ed_mcp/secrets.py",
        "canvas_ed_mcp/setup_flow.py",
        "canvas_ed_mcp/validation.py",
        "canvas_ed_mcp/providers/__init__.py",
        "canvas_ed_mcp/providers/base.py",
        "canvas_ed_mcp/providers/canvas.py",
        "canvas_ed_mcp/providers/ed.py",
        "canvas_ed_mcp/providers/gradescope.py",
        "canvas_ed_mcp/renderers/__init__.py",
        "canvas_ed_mcp/renderers/html.py",
        "canvas_ed_mcp/sync/__init__.py",
        "canvas_ed_mcp/sync/decisions.py",
        "canvas_ed_mcp/sync/engine.py",
        "canvas_ed_mcp/sync/locking.py",
        "canvas_ed_mcp/sync/model.py",
        "canvas_ed_mcp/sync/naming.py",
        "canvas_ed_mcp/sync/policy.py",
        "canvas_ed_mcp/sync/state.py",
        "canvas_ed_mcp/sync/storage.py",
    }
)
_SDIST_REQUIRED = frozenset(
    {
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "MANIFEST.in",
        "README.md",
        "README.zh-CN.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "pyproject.toml",
        "src/canvas_ed_mcp/__init__.py",
        "src/canvas_ed_mcp/server.py",
    }
)
_FORBIDDEN_PARTS = frozenset(
    {
        ".git",
        ".github",
        ".claude",
        ".agents",
        ".venv",
        "__pycache__",
        "downloads",
        "exports",
        "output",
        "artifacts",
    }
)
_FORBIDDEN_NAME_RE = re.compile(
    r"(?:^|[._-])(?:credential|credentials|env|secret|secrets|token|tokens|state|index)(?:[._-]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AuditResult:
    archive: Path
    kind: str
    entries: int


def _normalise_name(raw: str) -> str:
    if not raw or "\\" in raw or any(ord(character) < 32 for character in raw):
        raise AuditError(f"unsafe archive path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise AuditError(f"unsafe archive path: {raw!r}")
    normalised = path.as_posix().rstrip("/")
    if not normalised or normalised.startswith("/"):
        raise AuditError(f"unsafe archive path: {raw!r}")
    return normalised


def _reject_forbidden(name: str, *, wheel: bool) -> None:
    path = PurePosixPath(name)
    lowered_parts = tuple(part.casefold() for part in path.parts)
    lowered_name = path.name.casefold()
    if any(part in _FORBIDDEN_PARTS for part in lowered_parts):
        raise AuditError(f"forbidden distribution entry: {name}")
    if any(part.startswith(".env") for part in lowered_parts):
        raise AuditError(f"forbidden environment entry: {name}")
    for part in lowered_parts[:-1]:
        if _FORBIDDEN_NAME_RE.search(part):
            raise AuditError(f"private-looking distribution entry: {name}")
    if lowered_name.endswith((".pyc", ".pyo", ".command", ".key", ".pem", ".env")):
        raise AuditError(f"forbidden distribution entry: {name}")
    if _FORBIDDEN_NAME_RE.search(lowered_name) and not lowered_name.endswith(".py"):
        raise AuditError(f"private-looking distribution entry: {name}")
    if wheel and not (
        name.startswith("canvas_ed_mcp/")
        or re.match(r"^canvas_ed_mcp-[^/]+\.dist-info/", name)
    ):
        raise AuditError(f"wheel contains a non-package entry: {name}")


def _audit_names(names: Iterable[str], *, wheel: bool) -> tuple[str, ...]:
    normalised: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = _normalise_name(raw)
        if name in seen:
            raise AuditError(f"duplicate archive entry: {name}")
        seen.add(name)
        _reject_forbidden(name, wheel=wheel)
        normalised.append(name)
    return tuple(normalised)


def _audit_wheel(path: Path) -> AuditResult:
    try:
        with zipfile.ZipFile(path) as archive:
            names = _audit_names((item.filename for item in archive.infolist()), wheel=True)
    except (OSError, zipfile.BadZipFile) as exc:
        raise AuditError(f"cannot read wheel {path.name}: {exc}") from exc

    present = set(names)
    package_files = {name for name in names if name.startswith("canvas_ed_mcp/")}
    missing = sorted(_WHEEL_PACKAGE_FILES - package_files)
    if missing:
        raise AuditError(f"wheel is missing required entries: {', '.join(missing)}")
    unexpected = sorted(package_files - _WHEEL_PACKAGE_FILES)
    if unexpected:
        raise AuditError(f"wheel contains unexpected package entries: {', '.join(unexpected)}")
    if not any(name.endswith(".dist-info/METADATA") for name in names):
        raise AuditError("wheel is missing dist-info/METADATA")
    if not any(name.endswith(".dist-info/entry_points.txt") for name in names):
        raise AuditError("wheel is missing console entry points")
    if not any(".dist-info/licenses/" in name and name.endswith("LICENSE") for name in names):
        raise AuditError("wheel is missing its license")
    return AuditResult(path, "wheel", len(names))


def _strip_sdist_root(names: Sequence[str]) -> tuple[str, ...]:
    roots = {PurePosixPath(name).parts[0] for name in names}
    if len(roots) != 1:
        raise AuditError("sdist must contain exactly one top-level directory")
    stripped = []
    for name in names:
        parts = PurePosixPath(name).parts
        if len(parts) > 1:
            stripped.append(PurePosixPath(*parts[1:]).as_posix())
    return tuple(stripped)


def _is_allowed_sdist_entry(name: str) -> bool:
    path = PurePosixPath(name)
    if name in {
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "MANIFEST.in",
        "PKG-INFO",
        "README.md",
        "README.zh-CN.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "pyproject.toml",
        "setup.cfg",
    }:
        return True
    if name in {"src", "src/canvas_ed_mcp", "tests"}:
        return True
    if name in {f"src/{package_name}" for package_name in _WHEEL_PACKAGE_FILES}:
        return True
    if name in {
        "src/canvas_ed_mcp/due",
        "src/canvas_ed_mcp/providers",
        "src/canvas_ed_mcp/renderers",
        "src/canvas_ed_mcp/sync",
        "src/canvas_ed_mcp.egg-info",
    }:
        return True
    if name.startswith("src/canvas_ed_mcp.egg-info/"):
        return path.name in {
            "PKG-INFO",
            "SOURCES.txt",
            "dependency_links.txt",
            "entry_points.txt",
            "requires.txt",
            "top_level.txt",
        }
    if len(path.parts) == 2 and path.parts[0] == "tests":
        return path.name == "__init__.py" or (
            path.name.startswith("test_") and path.suffix == ".py"
        )
    return False


def _audit_sdist(path: Path) -> AuditResult:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                if member.issym() or member.islnk() or member.isdev():
                    raise AuditError(f"sdist contains an unsafe special entry: {member.name}")
            names = _audit_names((member.name for member in members), wheel=False)
    except (OSError, tarfile.TarError) as exc:
        raise AuditError(f"cannot read sdist {path.name}: {exc}") from exc

    stripped = set(_strip_sdist_root(names))
    missing = sorted(_SDIST_REQUIRED - stripped)
    if missing:
        raise AuditError(f"sdist is missing required entries: {', '.join(missing)}")
    for name in stripped:
        _reject_forbidden(name, wheel=False)
        if not _is_allowed_sdist_entry(name):
            raise AuditError(f"sdist contains an unallowlisted entry: {name}")
    return AuditResult(path, "sdist", len(names))


def audit_archive(path: str | Path) -> AuditResult:
    archive = Path(path)
    if archive.name.endswith(".whl"):
        return _audit_wheel(archive)
    if archive.name.endswith(".tar.gz"):
        return _audit_sdist(archive)
    raise AuditError(f"unsupported distribution archive: {archive.name}")


def _find_archives(arguments: Sequence[str]) -> tuple[Path, ...]:
    archives: list[Path] = []
    for argument in arguments:
        path = Path(argument)
        if path.is_dir():
            archives.extend(sorted(path.glob("*.whl")))
            archives.extend(sorted(path.glob("*.tar.gz")))
        else:
            archives.append(path)
    return tuple(archives)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", help="wheel, sdist, or directory to audit")
    args = parser.parse_args(argv)
    archives = _find_archives(args.archives)
    wheel_count = sum(path.name.endswith(".whl") for path in archives)
    sdist_count = sum(path.name.endswith(".tar.gz") for path in archives)
    if wheel_count != 1 or sdist_count != 1:
        print("distribution audit failed: expected exactly one wheel and one sdist", file=sys.stderr)
        return 2
    try:
        results = tuple(audit_archive(path) for path in archives)
    except AuditError as exc:
        print(f"distribution audit failed: {exc}", file=sys.stderr)
        return 2
    for result in results:
        print(f"distribution audit passed: {result.archive.name} ({result.entries} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
