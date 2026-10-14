"""Contained filesystem observation and atomic publication for course content."""

from __future__ import annotations

import hashlib
import errno
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .locking import LockScope, SyncLock
from .model import LocalStatus, StoredFile, validate_output_relative_path


_IGNORED_SCAN_DIRS = frozenset(
    {".git", ".venv", "node_modules", ".stversions", ".stfolder", "__pycache__"}
)
_IGNORED_SCAN_FILES = frozenset({".DS_Store", ".stignore"})


def _is_internal_staging_name(name: str) -> bool:
    return name.startswith(".canvas-ed-mcp-") and name.endswith(".staging")


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _is_link(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode):
            return True
        attributes = getattr(metadata, "st_file_attributes", 0)
        reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(attributes & reparse_point)
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_course_path(path: PurePosixPath, course_root: PurePosixPath) -> bool:
    return path != course_root and course_root in path.parents


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return a lowercase SHA-256 while reading a file in bounded chunks."""
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) or chunk_size < 1:
        raise ValueError("chunk_size must be a positive integer")
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_contained_path(
    output_root: Path,
    relative_path: PurePosixPath,
    *,
    must_exist: bool,
) -> Path:
    """Resolve a portable relative path without following links outside output_root."""
    if not isinstance(must_exist, bool):
        raise ValueError("must_exist must be a boolean")
    relative_path = validate_output_relative_path(relative_path)
    root = Path(output_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("output_root must be an existing directory")

    lexical = root.joinpath(*relative_path.parts)
    cursor = root
    for component in relative_path.parts:
        cursor /= component
        if _is_link(cursor):
            raise ValueError("output path contains a symbolic link or junction")

    resolved = lexical.resolve(strict=must_exist)
    if not _is_relative_to(resolved, root):
        raise ValueError("output path escapes the configured output root")
    return resolved


@dataclass(frozen=True, slots=True)
class IndexedFile:
    output_relative_path: PurePosixPath
    size_bytes: int

    def __post_init__(self) -> None:
        validate_output_relative_path(self.output_relative_path)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("size_bytes must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class LocalObservation:
    status: LocalStatus
    output_relative_path: PurePosixPath
    current_sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, LocalStatus):
            raise ValueError("status must be a LocalStatus")
        validate_output_relative_path(self.output_relative_path)
        if self.status in {LocalStatus.UNMODIFIED, LocalStatus.MODIFIED}:
            if self.current_sha256 is None or not re.fullmatch(
                r"[0-9a-f]{64}", self.current_sha256
            ):
                raise ValueError("present observations require a lowercase SHA-256")
        elif self.current_sha256 is not None:
            raise ValueError("missing or unsafe observations cannot carry a hash")


@dataclass(frozen=True, slots=True)
class AtomicPublishResult:
    stored_file: StoredFile
    staging_cleanup_pending: PurePosixPath | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.stored_file, StoredFile):
            raise ValueError("stored_file must be a StoredFile")
        if not self.stored_file.managed_by_sync:
            raise ValueError("atomic publication must produce a managed file")
        if self.staging_cleanup_pending is not None:
            validate_output_relative_path(self.staging_cleanup_pending)
            pending = self.staging_cleanup_pending
            if pending.parent != self.stored_file.output_relative_path.parent:
                raise ValueError("pending staging cleanup must share the destination directory")
            if not (
                pending.name.startswith(".canvas-ed-mcp-")
                and pending.name.endswith(".staging")
            ):
                raise ValueError("pending staging cleanup must identify a staging file")


class AtomicPublishUnsupported(RuntimeError):
    """Raised when the destination filesystem cannot publish without clobbering."""


class CourseFileIndex:
    """Deterministic, read-only snapshot of regular files within one course root."""

    def __init__(
        self,
        *,
        output_root: Path,
        course_root: PurePosixPath,
        files: tuple[IndexedFile, ...],
    ) -> None:
        self.output_root = Path(output_root).resolve(strict=True)
        self.course_root = validate_output_relative_path(course_root)
        if not isinstance(files, tuple):
            raise ValueError("files must be an immutable tuple")
        for item in files:
            if not isinstance(item, IndexedFile):
                raise ValueError("files must contain IndexedFile values")
            if not _is_course_path(item.output_relative_path, self.course_root):
                raise ValueError("indexed files must stay within the course root")
        self.files = files
        self._by_size: dict[int, tuple[IndexedFile, ...]] = {}
        self._by_name: dict[str, tuple[IndexedFile, ...]] = {}
        for item in files:
            self._by_size[item.size_bytes] = self._by_size.get(item.size_bytes, ()) + (item,)
            name = _normalized(item.output_relative_path.name)
            self._by_name[name] = self._by_name.get(name, ()) + (item,)

    @classmethod
    def scan(
        cls,
        *,
        output_root: Path,
        course_root: PurePosixPath,
    ) -> CourseFileIndex:
        course_root = validate_output_relative_path(course_root)
        root = Path(output_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("output_root must be an existing directory")
        course_path = resolve_contained_path(root, course_root, must_exist=False)
        indexed: list[IndexedFile] = []
        if not course_path.is_dir():
            return cls(output_root=root, course_root=course_root, files=())

        for current, directory_names, file_names in os.walk(
            course_path, followlinks=False
        ):
            current_path = Path(current)
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name not in _IGNORED_SCAN_DIRS
                and not _is_link(current_path / name)
            )
            for name in sorted(file_names):
                if name in _IGNORED_SCAN_FILES or _is_internal_staging_name(name):
                    continue
                candidate = current_path / name
                if _is_link(candidate):
                    continue
                try:
                    resolved = candidate.resolve(strict=True)
                    if not _is_relative_to(resolved, course_path):
                        continue
                    stat = resolved.stat()
                    if not resolved.is_file():
                        continue
                    relative = PurePosixPath(resolved.relative_to(root).as_posix())
                    validate_output_relative_path(relative)
                    if not _is_course_path(relative, course_root):
                        continue
                except (OSError, ValueError):
                    continue
                indexed.append(IndexedFile(relative, stat.st_size))

        indexed.sort(key=lambda item: item.output_relative_path.as_posix().casefold())
        return cls(output_root=root, course_root=course_root, files=tuple(indexed))

    def _absolute(self, item: IndexedFile) -> Path:
        if not _is_course_path(item.output_relative_path, self.course_root):
            raise ValueError("indexed path is outside the course root")
        return resolve_contained_path(
            self.output_root, item.output_relative_path, must_exist=True
        )

    def find_identical(self, content_path: Path) -> IndexedFile | None:
        content_path = Path(content_path)
        size = content_path.stat().st_size
        candidates = self._by_size.get(size, ())
        if not candidates:
            return None
        wanted = sha256_file(content_path)
        for item in candidates:
            try:
                if sha256_file(self._absolute(item)) == wanted:
                    return item
            except (OSError, ValueError):
                continue
        return None

    def find_same_name(self, filename: str) -> IndexedFile | None:
        matches = self._by_name.get(_normalized(filename), ())
        return matches[0] if matches else None

    def observe(self, stored: StoredFile) -> LocalObservation:
        if not isinstance(stored, StoredFile) or not stored.managed_by_sync:
            raise ValueError("observation requires sync-managed ownership evidence")
        relative = stored.output_relative_path
        if not _is_course_path(relative, self.course_root):
            return LocalObservation(LocalStatus.UNSAFE, relative)
        try:
            candidate = resolve_contained_path(
                self.output_root, relative, must_exist=False
            )
            try:
                metadata = candidate.stat()
            except FileNotFoundError:
                return LocalObservation(LocalStatus.MISSING, relative)
            candidate = resolve_contained_path(
                self.output_root, relative, must_exist=True
            )
            if not stat.S_ISREG(metadata.st_mode):
                return LocalObservation(LocalStatus.UNSAFE, relative)
            current_hash = sha256_file(candidate)
        except (OSError, ValueError):
            return LocalObservation(LocalStatus.UNSAFE, relative)
        status = (
            LocalStatus.UNMODIFIED
            if current_hash == stored.written_sha256
            else LocalStatus.MODIFIED
        )
        return LocalObservation(status, relative, current_hash)


def _validate_course_destination(
    course_root: PurePosixPath, destination: PurePosixPath
) -> tuple[PurePosixPath, PurePosixPath]:
    course_root = validate_output_relative_path(course_root)
    destination = validate_output_relative_path(destination)
    if not _is_course_path(destination, course_root):
        raise ValueError("destination must be a file within the selected course root")
    return course_root, destination


def _validate_source(source: Path) -> Path:
    source = Path(source)
    if _is_link(source) or not source.is_file():
        raise ValueError("source must be a regular, non-symlink file")
    return source


def _stage_copy(source: Path, destination_directory: Path) -> tuple[Path, str]:
    staged: Path | None = None
    digest = hashlib.sha256()
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=destination_directory,
            prefix=".canvas-ed-mcp-",
            suffix=".staging",
            delete=False,
        ) as target:
            staged = Path(target.name)
            with source.open("rb") as incoming:
                for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                    target.write(chunk)
                    digest.update(chunk)
            target.flush()
            os.fsync(target.fileno())
        return staged, digest.hexdigest()
    except BaseException:
        if staged is not None:
            staged.unlink(missing_ok=True)
        raise


def _fsync_directory_best_effort(directory: Path) -> None:
    if os.name == "nt" or not hasattr(os, "O_DIRECTORY"):
        return
    descriptor: int | None = None
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def install_new_atomic(
    source: Path,
    *,
    output_root: Path,
    course_root: PurePosixPath,
    destination: PurePosixPath,
    course_key: str,
    lock: SyncLock | None = None,
) -> AtomicPublishResult:
    """Publish a new managed file from same-directory staging without consuming source."""
    course_root, destination = _validate_course_destination(course_root, destination)
    canonical_output = Path(output_root).resolve(strict=True)
    if (
        not isinstance(lock, SyncLock)
        or not lock.held
        or lock.identity.scope is not LockScope.COURSE
        or lock.identity.output_root != canonical_output
        or lock.identity.course_key != course_key
        or lock.identity.course_root != course_root
    ):
        raise ValueError("publication requires a held matching course lock")
    source = _validate_source(source)
    output_root = canonical_output
    target = resolve_contained_path(output_root, destination, must_exist=False)
    if target.exists() or _is_link(target):
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target = resolve_contained_path(output_root, destination, must_exist=False)
    staged, written_hash = _stage_copy(source, target.parent)
    try:
        checked_target = resolve_contained_path(
            output_root, destination, must_exist=False
        )
        if checked_target != target:
            raise ValueError("destination changed during install")
        if checked_target.exists() or _is_link(checked_target):
            raise FileExistsError(checked_target)
        # A hard-link publish is an atomic create-if-absent operation on the
        # same filesystem. Unlike replace(), it can never overwrite a file
        # created by the user after our final existence check.
        os.link(staged, checked_target, follow_symlinks=False)
        _fsync_directory_best_effort(checked_target.parent)
    except OSError as exc:
        try:
            staged.unlink(missing_ok=True)
        except OSError:
            pass
        unsupported_errors = {
            errno.EACCES,
            errno.EPERM,
            errno.EXDEV,
            getattr(errno, "ENOTSUP", errno.EPERM),
            getattr(errno, "EOPNOTSUPP", errno.EPERM),
        }
        if not isinstance(exc, FileExistsError) and exc.errno in unsupported_errors:
            raise AtomicPublishUnsupported(
                "destination filesystem does not support atomic no-clobber publication"
            ) from exc
        raise

    cleanup_pending: PurePosixPath | None = None
    try:
        staged.unlink(missing_ok=True)
    except OSError:
        cleanup_pending = destination.parent / staged.name
    return AtomicPublishResult(
        StoredFile(destination, written_hash, True),
        cleanup_pending,
    )
