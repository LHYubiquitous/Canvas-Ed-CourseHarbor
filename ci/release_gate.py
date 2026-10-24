"""Build and audit a deterministic clean-history public source candidate."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 only
    import tomli as tomllib  # type: ignore[no-redef]


class ReleaseAuditError(RuntimeError):
    """Raised without reflecting the private content that caused a failure."""


_MANIFEST_PATH = "release-manifest.txt"
_VERSION_RE = re.compile(
    r"[0-9]+(?:\.[0-9]+)*(?:(?:a|b|rc)[0-9]+)?(?:\.post[0-9]+)?(?:\.dev[0-9]+)?\Z"
)
_REQUIRED_PUBLIC_FILES = frozenset(
    {
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
        ".github/workflows/ci.yml",
        ".gitignore",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "docs/RELEASE_AUDIT.md",
        "LICENSE",
        "MANIFEST.in",
        "README.md",
        "README.zh-CN.md",
        _MANIFEST_PATH,
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "pylock.toml",
        "pyproject.toml",
        "sbom.cdx.json",
        "uv.lock",
    }
)
_PUBLIC_PREFIXES = ("ci/", "docs/", "src/", "tests/")
_PUBLIC_SUFFIXES = frozenset({".json", ".md", ".py", ".toml", ".yml"})
_EXCLUDED_PUBLIC_FILES = frozenset(
    {
        "docs/CANVAS_SYNC_MANUAL_TEST.md",
        "docs/ED_SYNC_MANUAL_TEST.md",
        "tests/test_mcp_config.py",
        "tests/test_sync_output.py",
        "tests/test_ui_spike_fixture.py",
        "tests/test_ui_spike_presenter.py",
        "tests/test_ui_spike_pyside6_adapter.py",
        "tests/test_ui_spike_runner.py",
        "tests/test_ui_spike_tkinter_adapter.py",
        "tests/test_ui_spike_wx_adapter.py",
    }
)
_PRIVATE_KEY_RE = re.compile(
    br"-----BEGIN (?:[A-Z0-9]+[ -])*PRIVATE KEY(?: BLOCK)?-----",
    re.IGNORECASE,
)
_HOME_PATH_RE = re.compile(
    br"(?:[A-Za-z]:[\\/]Users[\\/](?!<)[^\\/\r\n]+[\\/]"
    br"|/(?:Users|home)/(?!<)[^/\s]+/)",
    re.IGNORECASE,
)
_SECRET_NAME = br"(?:api[_-]?(?:key|token)|access[_-]?token|client[_-]?secret|password|secret|token)"
_LITERAL_SECRET_RES = (
    re.compile(
        br"^\s*(?:export\s+)?[A-Z0-9_-]*"
        + _SECRET_NAME.upper()
        + br"[A-Z0-9_-]*\s*=\s*(?:[\"']([^\"']{16,})[\"']|([A-Za-z0-9_./+=:-]{16,}))",
        re.MULTILINE,
    ),
    re.compile(
        br"[\"'][A-Z0-9_.-]*"
        + _SECRET_NAME
        + br"[A-Z0-9_.-]*[\"']\s*:\s*[\"']([^\"']{16,})[\"']",
        re.IGNORECASE,
    ),
    re.compile(
        br"\[\s*[\"']" + _SECRET_NAME + br"[\"']\s*\]\s*=\s*[\"']([^\"']{16,})[\"']",
        re.IGNORECASE,
    ),
)
_KNOWN_SECRET_RES = (
    re.compile(br"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(br"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_VOLATILE_QUERY_NAMES = frozenset(
    {
        "access_token",
        "api_key",
        "code",
        "code_verifier",
        "credential",
        "expires",
        "key",
        "policy",
        "se",
        "sig",
        "signature",
        "token",
        "x-amz-credential",
        "x-amz-expires",
        "x-amz-security-token",
        "x-amz-signature",
        "x-goog-credential",
        "x-goog-expires",
        "x-goog-signature",
    }
)
_SAFE_LITERAL_VALUES = frozenset(
    {
    b"[redacted]",
    b"example",
    b"placeholder",
    b"synthetic",
    b"synthetic-canary",
    }
)


@dataclass(frozen=True, slots=True)
class SourceArchiveResult:
    archive: Path
    sha256: str
    files: tuple[str, ...]


def read_project_version(root: str | Path) -> str:
    """Read the safe project version and require an exact runtime-version match."""
    repository = Path(root)
    try:
        project = tomllib.loads((repository / "pyproject.toml").read_text("utf-8"))
        version = project["project"]["version"]
        package_init = (repository / "src/canvas_ed_mcp/__init__.py").read_text(
            "utf-8"
        )
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseAuditError(
            "public source audit failed: unreadable_project_version"
        ) from exc
    if not isinstance(version, str) or not _VERSION_RE.fullmatch(version):
        raise ReleaseAuditError("public source audit failed: invalid_version")
    match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$',
        package_init,
        re.MULTILINE,
    )
    if match is None or match.group(1) != version:
        raise ReleaseAuditError("public source audit failed: version_mismatch")
    return version


def _validate_relative_path(relative: str) -> PurePosixPath:
    path = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or path.is_absolute()
        or path.as_posix() != relative
        or any(part in {"", ".", ".."} for part in path.parts)
        or (path.parts and ":" in path.parts[0])
    ):
        raise ReleaseAuditError("public source audit failed: invalid_manifest_path")
    return path


def _is_public_path(relative: str) -> bool:
    if relative in _EXCLUDED_PUBLIC_FILES:
        return False
    if relative in _REQUIRED_PUBLIC_FILES:
        return True
    if not relative.startswith(_PUBLIC_PREFIXES):
        return False
    path = PurePosixPath(relative)
    if any(
        part.startswith(".") or part == "__pycache__"
        for part in path.parts
    ):
        return False
    return path.suffix.casefold() in _PUBLIC_SUFFIXES


def select_public_files(root: str | Path, *, require_complete: bool = True) -> tuple[str, ...]:
    """Load the exact reviewed manifest and reject unreviewed eligible source files."""
    repository = Path(root)
    manifest_path = repository / _MANIFEST_PATH
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ReleaseAuditError("public source audit failed: missing_release_manifest")
    selected = tuple(
        line.strip()
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if tuple(sorted(set(selected))) != selected:
        raise ReleaseAuditError("public source audit failed: manifest_order_or_duplicate")
    for relative in selected:
        _validate_relative_path(relative)
        if not _is_public_path(relative):
            raise ReleaseAuditError(
                f"public source audit failed: disallowed_manifest_path:{relative}"
            )
    if _MANIFEST_PATH not in selected:
        raise ReleaseAuditError("public source audit failed: manifest_not_self_included")
    if require_complete:
        missing = sorted(_REQUIRED_PUBLIC_FILES - set(selected))
        if missing:
            raise ReleaseAuditError(
                "public source audit failed: missing_required:" + ",".join(missing)
            )

    discovered: set[str] = set()
    for prefix in _PUBLIC_PREFIXES:
        directory = repository / prefix.rstrip("/")
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_file() or path.is_symlink():
                    relative = path.relative_to(repository).as_posix()
                    if _is_public_path(relative):
                        discovered.add(relative)
    unreviewed = sorted(discovered - set(selected))
    if unreviewed:
        raise ReleaseAuditError(
            "public source audit failed: unreviewed_file:" + ",".join(unreviewed)
        )

    for relative in selected:
        path = repository.joinpath(*PurePosixPath(relative).parts)
        if path.is_symlink():
            raise ReleaseAuditError(f"public source audit failed: symlink:{relative}")
        if not path.is_file():
            raise ReleaseAuditError(f"public source audit failed: missing_required:{relative}")
    return selected


def _safe_literal(value: bytes) -> bool:
    return value.strip().lower() in _SAFE_LITERAL_VALUES


def _audit_signed_urls(relative: str, payload: bytes) -> None:
    decoded = html.unescape(payload.decode("utf-8", errors="ignore"))
    for raw in _URL_RE.findall(decoded):
        try:
            parsed = urlsplit(raw)
        except ValueError:
            continue
        host = (parsed.hostname or "").casefold()
        reserved_host = host.endswith((".test", ".invalid", ".example")) or host in {
            "example.com",
            "example.net",
            "example.org",
        }
        if reserved_host:
            continue
        for name, value in parse_qsl(parsed.query, keep_blank_values=True):
            if name.casefold() in _VOLATILE_QUERY_NAMES and not _safe_literal(
                value.encode("utf-8")
            ):
                raise ReleaseAuditError(
                    f"public source audit failed: signed_url:{relative}"
                )


def audit_public_files(files: Mapping[str, bytes]) -> None:
    """Scan public candidate bytes without reflecting matched private values."""
    for relative, payload in sorted(files.items()):
        if _PRIVATE_KEY_RE.search(payload):
            raise ReleaseAuditError(f"public source audit failed: private_key:{relative}")
        if _HOME_PATH_RE.search(payload):
            raise ReleaseAuditError(f"public source audit failed: absolute_home:{relative}")
        for pattern in _LITERAL_SECRET_RES:
            for match in pattern.finditer(payload):
                value = next(group for group in match.groups() if group is not None)
                if not _safe_literal(value):
                    raise ReleaseAuditError(
                        f"public source audit failed: literal_secret:{relative}"
                    )
        for pattern in _KNOWN_SECRET_RES:
            for match in pattern.finditer(payload):
                if not _safe_literal(match.group(0)):
                    raise ReleaseAuditError(
                        f"public source audit failed: known_secret:{relative}"
                    )
        _audit_signed_urls(relative, payload)


def _source_bytes(root: Path, files: Sequence[str]) -> dict[str, bytes]:
    output: dict[str, bytes] = {}
    resolved_root = root.resolve()
    for relative in files:
        _validate_relative_path(relative)
        path = root.joinpath(*PurePosixPath(relative).parts)
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise ReleaseAuditError(
                f"public source audit failed: outside_root:{relative}"
            ) from exc
        if path.is_symlink() or not path.is_file():
            raise ReleaseAuditError(f"public source audit failed: missing_or_link:{relative}")
        output[relative] = path.read_bytes()
    return output


def build_source_archive(
    root: str | Path,
    destination: str | Path,
    *,
    version: str,
    files: Sequence[str] | None = None,
    source_date_epoch: int,
    require_complete: bool = True,
) -> SourceArchiveResult:
    """Build a deterministic gzip-compressed tar archive from audited files."""
    repository = Path(root)
    if not _VERSION_RE.fullmatch(version):
        raise ReleaseAuditError("public source audit failed: invalid_version")
    expected = select_public_files(repository, require_complete=require_complete)
    selected = tuple(files) if files is not None else expected
    if selected != expected:
        raise ReleaseAuditError("public source audit failed: explicit_manifest_mismatch")
    payloads = _source_bytes(repository, selected)
    audit_public_files(payloads)

    archive_path = Path(destination)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"canvas-ed-mcp-{version}"
    with archive_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=source_date_epoch) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for relative in selected:
                    payload = payloads[relative]
                    member_name = f"{prefix}/{relative}"
                    member = PurePosixPath(member_name)
                    if member.parts[0] != prefix or ".." in member.parts:
                        raise ReleaseAuditError("public source audit failed: unsafe_tar_member")
                    item = tarfile.TarInfo(member_name)
                    item.size = len(payload)
                    item.mtime = source_date_epoch
                    item.mode = 0o644
                    item.uid = 0
                    item.gid = 0
                    item.uname = ""
                    item.gname = ""
                    archive.addfile(item, fileobj=_BytesReader(payload))
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    return SourceArchiveResult(archive_path, digest, selected)


class _BytesReader:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._position = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._payload) - self._position
        start = self._position
        self._position = min(self._position + size, len(self._payload))
        return self._payload[start : self._position]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--version")
    parser.add_argument("--source-date-epoch", type=int, required=True)
    args = parser.parse_args(argv)

    project_version = read_project_version(args.root)
    if args.version is not None and args.version != project_version:
        raise ReleaseAuditError("public source audit failed: version_mismatch")
    name = f"canvas-ed-mcp-{project_version}-source.tar.gz"
    result = build_source_archive(
        args.root,
        args.output_dir / name,
        version=project_version,
        source_date_epoch=args.source_date_epoch,
    )
    manifest = {
        "archive": result.archive.name,
        "files": list(result.files),
        "sha256": result.sha256,
        "source_date_epoch": args.source_date_epoch,
        "version": project_version,
    }
    manifest_path = args.output_dir / f"{name}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / f"{name}.sha256").write_text(
        f"{result.sha256}  {name}\n",
        encoding="ascii",
    )
    print(f"source release audit passed: {name} ({len(result.files)} files)")
    print(f"sha256: {result.sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
