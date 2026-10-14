"""Immutable, transport-free domain values for sync planning."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
from canvas_ed_mcp.validation import validate_portable_component, validate_provider_instance


class RemoteKind(str, Enum):
    FILE = "file"
    PAGE = "page"
    LESSON = "lesson"
    RESOURCE = "resource"


class SyncMode(str, Enum):
    BASELINE = "baseline"
    SYNC = "sync"


class LocalStatus(str, Enum):
    NONE = "none"
    MISSING = "missing"
    UNMODIFIED = "unmodified"
    MODIFIED = "modified"
    UNSAFE = "unsafe"


class SyncAction(str, Enum):
    RECORD_ONLY = "record_only"
    SKIP = "skip"
    FETCH = "fetch"
    INSTALL_NEW = "install_new"
    REPLACE_MANAGED = "replace_managed"
    INSTALL_SEPARATELY = "install_separately"
    DEDUPLICATE = "deduplicate"
    BLOCK = "block"


class DecisionReason(str, Enum):
    BASELINE = "baseline"
    NEW_SOURCE = "new_source"
    REMOTE_UNCHANGED = "remote_unchanged"
    REMOTE_CHANGED = "remote_changed"
    RETRY_REQUIRED = "retry_required"
    NO_TRACKED_FILE = "no_tracked_file"
    TRACKED_FILE_MISSING = "tracked_file_missing"
    TRACKED_FILE_UNMODIFIED = "tracked_file_unmodified"
    TRACKED_FILE_MODIFIED = "tracked_file_modified"
    SAFE_REPLACE_UNAVAILABLE = "safe_replace_unavailable"
    IDENTICAL_CONTENT_EXISTS = "identical_content_exists"
    UNSAFE_TRACKED_PATH = "unsafe_tracked_path"


def validate_output_relative_path(path: PurePosixPath) -> PurePosixPath:
    """Validate and return a portable path relative to the configured output root."""
    if not isinstance(path, PurePosixPath):
        raise ValueError("output_relative_path must be a PurePosixPath")
    path_text = str(path)
    if (
        path_text in {"", "."}
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in path_text
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in path_text
        )
    ):
        raise ValueError("output_relative_path must be portable and traversal-free")
    for component in path.parts:
        validate_portable_component(component, field="output_relative_path")
    return path


def _validate_text(value: str, field: str, *, canonical_lower: bool = False) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{field} cannot have surrounding whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} cannot contain control characters")
    if canonical_lower and value != value.casefold():
        raise ValueError(f"{field} must already be canonical lowercase")


@dataclass(frozen=True, slots=True)
class SourceKey:
    """Stable remote identity; display metadata is deliberately excluded."""

    provider: str
    instance: str
    course_id: str
    kind: RemoteKind
    remote_id: str

    def __post_init__(self) -> None:
        _validate_text(self.provider, "provider", canonical_lower=True)
        validate_provider_instance(self.instance)
        _validate_text(self.course_id, "course_id")
        _validate_text(self.remote_id, "remote_id")
        if not isinstance(self.kind, RemoteKind):
            raise ValueError("kind must be a RemoteKind")

    @property
    def identity_tuple(self) -> tuple[str, str, str, RemoteKind, str]:
        """Return an in-memory identity without defining a persistence codec."""
        return (
            self.provider,
            self.instance,
            self.course_id,
            self.kind,
            self.remote_id,
        )


@dataclass(frozen=True, slots=True)
class RemoteItem:
    """Non-secret metadata and a provider-computed semantic revision."""

    key: SourceKey
    signature: str
    display_name: str
    group: str | None = None
    size_bytes: int | None = None
    media_type: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.key, SourceKey):
            raise ValueError("key must be a SourceKey")
        _validate_text(self.signature, "signature")
        _validate_text(self.display_name, "display_name")
        if self.group is not None:
            _validate_text(self.group, "group")
        if self.media_type is not None:
            _validate_text(self.media_type, "media_type")
        if self.size_bytes is not None and (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("size_bytes must be a non-negative integer or None")


@dataclass(frozen=True, slots=True)
class StoredFile:
    """Portable ownership evidence relative to the configured output root."""

    output_relative_path: PurePosixPath
    written_sha256: str | None
    managed_by_sync: bool

    def __post_init__(self) -> None:
        validate_output_relative_path(self.output_relative_path)
        if not isinstance(self.managed_by_sync, bool):
            raise ValueError("managed_by_sync must be a boolean")
        if self.managed_by_sync:
            if self.written_sha256 is None or not _SHA256_RE.fullmatch(
                self.written_sha256
            ):
                raise ValueError("managed files require a lowercase SHA-256")
        elif self.written_sha256 is not None:
            raise ValueError("unmanaged files cannot carry sync ownership hashes")


@dataclass(frozen=True, slots=True)
class PreviousSource:
    """Minimal prior state required for a pure source decision."""

    key: SourceKey
    signature: str
    stored_file: StoredFile | None = None
    retry_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.key, SourceKey):
            raise ValueError("key must be a SourceKey")
        _validate_text(self.signature, "signature")
        if self.stored_file is not None:
            if not isinstance(self.stored_file, StoredFile):
                raise ValueError("stored_file must be a StoredFile")
            if not self.stored_file.managed_by_sync:
                raise ValueError("persisted source files must be sync-managed")
        if not isinstance(self.retry_required, bool):
            raise ValueError("retry_required must be a boolean")


@dataclass(frozen=True, slots=True)
class SyncDecision:
    """A pure authorization for the next orchestration step."""

    key: SourceKey
    action: SyncAction
    reason: DecisionReason
    remote_changed: bool | None
    retry_triggered: bool
    retry_required: bool
    previous_file: StoredFile | None

    def __post_init__(self) -> None:
        if not isinstance(self.key, SourceKey):
            raise ValueError("key must be a SourceKey")
        if not isinstance(self.action, SyncAction):
            raise ValueError("action must be a SyncAction")
        if not isinstance(self.reason, DecisionReason):
            raise ValueError("reason must be a DecisionReason")
        if self.remote_changed is not None and not isinstance(
            self.remote_changed, bool
        ):
            raise ValueError("remote_changed must be a boolean or None")
        if not isinstance(self.retry_triggered, bool):
            raise ValueError("retry_triggered must be a boolean")
        if not isinstance(self.retry_required, bool):
            raise ValueError("retry_required must be a boolean")
        if self.previous_file is not None:
            if not isinstance(self.previous_file, StoredFile):
                raise ValueError("previous_file must be a StoredFile or None")
            if not self.previous_file.managed_by_sync:
                raise ValueError("previous_file must be sync-managed")

        valid_reasons = {
            SyncAction.RECORD_ONLY: {DecisionReason.BASELINE},
            SyncAction.SKIP: {DecisionReason.REMOTE_UNCHANGED},
            SyncAction.FETCH: {
                DecisionReason.NEW_SOURCE,
                DecisionReason.REMOTE_CHANGED,
                DecisionReason.RETRY_REQUIRED,
            },
            SyncAction.INSTALL_NEW: {
                DecisionReason.NO_TRACKED_FILE,
                DecisionReason.TRACKED_FILE_MISSING,
            },
            SyncAction.REPLACE_MANAGED: {
                DecisionReason.TRACKED_FILE_UNMODIFIED
            },
            SyncAction.INSTALL_SEPARATELY: {
                DecisionReason.TRACKED_FILE_MODIFIED,
                DecisionReason.SAFE_REPLACE_UNAVAILABLE,
            },
            SyncAction.DEDUPLICATE: {
                DecisionReason.IDENTICAL_CONTENT_EXISTS
            },
            SyncAction.BLOCK: {DecisionReason.UNSAFE_TRACKED_PATH},
        }
        if self.reason not in valid_reasons[self.action]:
            raise ValueError("action and reason are inconsistent")

        if self.reason in {DecisionReason.NEW_SOURCE, DecisionReason.NO_TRACKED_FILE}:
            if self.previous_file is not None:
                raise ValueError("new/untracked decisions cannot own a previous file")
        if self.reason in {
            DecisionReason.TRACKED_FILE_MISSING,
            DecisionReason.TRACKED_FILE_UNMODIFIED,
            DecisionReason.TRACKED_FILE_MODIFIED,
            DecisionReason.SAFE_REPLACE_UNAVAILABLE,
            DecisionReason.UNSAFE_TRACKED_PATH,
        } and self.previous_file is None:
            raise ValueError("tracked-file decisions require ownership evidence")

        if self.reason is DecisionReason.NEW_SOURCE and self.remote_changed is not None:
            raise ValueError("a new source has no previous revision to compare")
        if self.reason is DecisionReason.BASELINE and (
            self.remote_changed is not None
            or self.retry_triggered
            or self.retry_required
        ):
            raise ValueError("a baseline decision cannot fetch or retain retry state")
        if self.reason is DecisionReason.NEW_SOURCE and (
            self.retry_triggered or self.retry_required
        ):
            raise ValueError("a new source cannot be retry-triggered")
        if self.reason is DecisionReason.REMOTE_UNCHANGED and (
            self.remote_changed is not False
            or self.retry_triggered
            or self.retry_required
        ):
            raise ValueError("an unchanged skip cannot be a retry")
        if self.reason is DecisionReason.REMOTE_CHANGED and (
            self.remote_changed is not True
            or self.retry_required is not self.retry_triggered
        ):
            raise ValueError("a remote-changed fetch has inconsistent retry state")
        if self.reason is DecisionReason.RETRY_REQUIRED and (
            self.remote_changed is not False
            or not self.retry_triggered
            or not self.retry_required
        ):
            raise ValueError("a retry decision must be triggered without a remote change")
