"""Portable OS-held locks for course output and profile state namespaces."""

from __future__ import annotations

import errno
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import IO, Any

from canvas_ed_mcp.paths import validate_profile_name

from .model import validate_output_relative_path


_LOCK_OFFSET = 8192


class LockScope(str, Enum):
    COURSE = "course"
    STATE = "state"


class LockUnavailable(RuntimeError):
    """A live OS lock already owns the requested namespace."""

    def __init__(
        self,
        *,
        scope: LockScope,
        path: Path,
        owner: dict[str, object],
    ) -> None:
        super().__init__(f"{scope.value} sync lock is already held")
        self.scope = scope
        self.path = path
        self.owner = owner


def _canonical_output_root(value: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("output_root must be absolute")
    return path.resolve(strict=False)


def _validate_course_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("course_key must be a non-empty trimmed string")
    return value


@dataclass(frozen=True, slots=True)
class LockIdentity:
    scope: LockScope
    profile: str
    output_root: Path
    course_key: str | None = None
    course_root: PurePosixPath | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, LockScope):
            raise ValueError("scope must be a LockScope")
        validate_profile_name(self.profile)
        if not isinstance(self.output_root, Path) or self.output_root != _canonical_output_root(
            self.output_root
        ):
            raise ValueError("output_root must be a canonical absolute Path")
        if self.scope is LockScope.COURSE:
            _validate_course_key(self.course_key)  # type: ignore[arg-type]
            validate_output_relative_path(self.course_root)  # type: ignore[arg-type]
        elif self.course_key is not None or self.course_root is not None:
            raise ValueError("state locks cannot carry course identity")


def _identity_digest(identity: LockIdentity) -> str:
    output = os.path.normcase(str(identity.output_root))
    if identity.scope is LockScope.COURSE:
        # Output protection crosses profile boundaries when two profiles target
        # the same physical course namespace.
        values = (identity.scope.value, output, identity.course_root.as_posix())  # type: ignore[union-attr]
    else:
        values = (identity.scope.value, identity.profile, output)
    payload = json.dumps(values, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _try_os_lock(handle: IO[bytes]) -> bool:
    handle.seek(_LOCK_OFFSET)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True
    if os.name == "posix":
        import fcntl

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    raise RuntimeError(f"unsupported lock platform: {os.name}")


def _unlock_os(handle: IO[bytes]) -> None:
    handle.seek(_LOCK_OFFSET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    if os.name == "posix":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    raise RuntimeError(f"unsupported lock platform: {os.name}")


def _read_owner(path: Path) -> dict[str, object]:
    try:
        with path.open("rb", buffering=0) as handle:
            payload = handle.read(_LOCK_OFFSET).split(b"\0", 1)[0]
        if payload.startswith(b"L"):
            payload = payload[1:]
        value = json.loads(payload.decode("utf-8"))
        if isinstance(value, dict) and all(isinstance(key, str) for key in value):
            return value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    return {}


class SyncLock:
    """A non-reentrant advisory lock whose file may safely outlive its holder."""

    def __init__(self, *, lock_dir: Path, identity: LockIdentity) -> None:
        if not isinstance(identity, LockIdentity):
            raise ValueError("identity must be a LockIdentity")
        self.identity = identity
        self.path = Path(lock_dir) / (
            f"{identity.scope.value}-{_identity_digest(identity)}.lock"
        )
        self._handle: IO[bytes] | None = None
        self._acquired_at: str | None = None

    @classmethod
    def for_course(
        cls,
        *,
        lock_dir: Path,
        profile: str,
        output_root: Path,
        course_key: str,
        course_root: PurePosixPath,
    ) -> "SyncLock":
        return cls(
            lock_dir=lock_dir,
            identity=LockIdentity(
                scope=LockScope.COURSE,
                profile=validate_profile_name(profile),
                output_root=_canonical_output_root(output_root),
                course_key=_validate_course_key(course_key),
                course_root=validate_output_relative_path(course_root),
            ),
        )

    @classmethod
    def for_state(
        cls,
        *,
        lock_dir: Path,
        profile: str,
        output_root: Path,
    ) -> "SyncLock":
        return cls(
            lock_dir=lock_dir,
            identity=LockIdentity(
                scope=LockScope.STATE,
                profile=validate_profile_name(profile),
                output_root=_canonical_output_root(output_root),
            ),
        )

    @property
    def held(self) -> bool:
        return self._handle is not None

    def _metadata(self, status: str) -> dict[str, object]:
        return {
            "schema_version": 1,
            "scope": self.identity.scope.value,
            "profile": self.identity.profile,
            "output_root": str(self.identity.output_root),
            "course_key": self.identity.course_key,
            "course_root": (
                self.identity.course_root.as_posix()
                if self.identity.course_root is not None
                else None
            ),
            "pid": os.getpid(),
            "status": status,
            "acquired_at": self._acquired_at,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    @staticmethod
    def _write_metadata(handle: IO[bytes], metadata: dict[str, object]) -> None:
        payload = b"L" + json.dumps(
            metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        handle.seek(0)
        handle.write(payload)
        handle.truncate(max(len(payload), _LOCK_OFFSET + 1))
        handle.flush()
        os.fsync(handle.fileno())

    def acquire(
        self, *, timeout: float = 0.0, poll_interval: float = 0.05
    ) -> "SyncLock":
        if self.held:
            raise RuntimeError("sync locks are not reentrant")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("timeout must be a non-negative number")
        if (
            isinstance(poll_interval, bool)
            or not isinstance(poll_interval, (int, float))
            or not math.isfinite(poll_interval)
            or poll_interval <= 0
        ):
            raise ValueError("poll_interval must be a positive number")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if os.fstat(handle.fileno()).st_size <= _LOCK_OFFSET:
                handle.seek(_LOCK_OFFSET)
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            deadline = time.monotonic() + float(timeout)
            while not _try_os_lock(handle):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockUnavailable(
                        scope=self.identity.scope,
                        path=self.path,
                        owner=_read_owner(self.path),
                    )
                time.sleep(min(float(poll_interval), remaining))
            self._handle = handle
            self._acquired_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
            self._write_metadata(handle, self._metadata("held"))
            return self
        except Exception:
            if self._handle is handle:
                try:
                    _unlock_os(handle)
                finally:
                    self._handle = None
            handle.close()
            self._acquired_at = None
            raise

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        metadata_error: OSError | None = None
        try:
            try:
                self._write_metadata(handle, self._metadata("released"))
            except OSError as exc:
                metadata_error = exc
            _unlock_os(handle)
        finally:
            self._handle = None
            self._acquired_at = None
            handle.close()
        if metadata_error is not None:
            raise metadata_error

    def __enter__(self) -> "SyncLock":
        return self.acquire()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        self.release()
