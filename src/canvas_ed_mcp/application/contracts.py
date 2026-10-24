"""Framework-neutral contracts shared by application-service adapters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


_CODE = re.compile(r"[a-z][a-z0-9_]*\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MISSING_REVISION = "missing"


class OperationStatus(str, Enum):
    """Stable terminal states for one application operation."""

    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"


class IssueSeverity(str, Enum):
    """Presentation-neutral severity for a stable issue code."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ProgressPhase(str, Enum):
    """Coarse phases suitable for CLI, desktop, or other presenters."""

    CONNECTING = "connecting"
    DISCOVERING = "discovering"
    PLANNING = "planning"
    VALIDATING = "validating"
    APPLYING = "applying"
    COMMITTING = "committing"
    CLEANING_UP = "cleaning_up"


def _require_code(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not _CODE.fullmatch(value):
        raise ValueError(f"{field_name} must be a stable lowercase code")


def _require_safe_text(value: str, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field_name} must be safe non-empty text")


def _require_revision(value: str, field_name: str) -> None:
    if value != _MISSING_REVISION and (
        not isinstance(value, str) or not _DIGEST.fullmatch(value)
    ):
        raise ValueError(
            f"{field_name} must be 'missing' or a lowercase SHA-256 digest"
        )


@dataclass(frozen=True, slots=True)
class ApplicationIssue:
    """Sanitized issue metadata; provider exception text is deliberately absent."""

    code: str
    severity: IssueSeverity
    retryable: bool = False
    provider: str | None = None
    course_key: str | None = None

    def __post_init__(self) -> None:
        _require_code(self.code, "code")
        if not isinstance(self.severity, IssueSeverity):
            raise ValueError("severity must be an IssueSeverity")
        if not isinstance(self.retryable, bool):
            raise ValueError("retryable must be a boolean")
        if self.provider is not None:
            _require_code(self.provider, "provider")
        if self.course_key is not None:
            _require_safe_text(self.course_key, "course_key")


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One safe progress update without display text or sensitive values."""

    operation_id: str
    phase: ProgressPhase
    completed: int
    total: int | None = None
    provider: str | None = None
    course_key: str | None = None
    code: str | None = None

    def __post_init__(self) -> None:
        _require_safe_text(self.operation_id, "operation_id")
        if not isinstance(self.phase, ProgressPhase):
            raise ValueError("phase must be a ProgressPhase")
        if (
            isinstance(self.completed, bool)
            or not isinstance(self.completed, int)
            or self.completed < 0
        ):
            raise ValueError("completed must be a non-negative integer")
        if self.total is not None:
            if (
                isinstance(self.total, bool)
                or not isinstance(self.total, int)
                or self.total < 0
            ):
                raise ValueError("total must be a non-negative integer or None")
            if self.completed > self.total:
                raise ValueError("completed cannot exceed total")
        if self.provider is not None:
            _require_code(self.provider, "provider")
        if self.course_key is not None:
            _require_safe_text(self.course_key, "course_key")
        if self.code is not None:
            _require_code(self.code, "code")


@runtime_checkable
class CancellationToken(Protocol):
    """Minimal cooperative-cancellation port implemented by a caller."""

    @property
    def cancellation_requested(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class NeverCancelled:
    """Default token for callers that do not expose cancellation."""

    @property
    def cancellation_requested(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class SecretValue:
    """Short-lived secret input that is excluded from repr, equality, and hashing."""

    value: str = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.value, str)
            or not self.value
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.value
            )
        ):
            raise ValueError("secret value must be non-empty and contain no controls")


@dataclass(frozen=True, slots=True)
class ReviewProof:
    """Opaque proof binding a reviewed plan to observed local revisions."""

    operation: str
    plan_digest: str
    config_revision: str
    state_revision: str | None = None

    def __post_init__(self) -> None:
        _require_code(self.operation, "operation")
        if not isinstance(self.plan_digest, str) or not _DIGEST.fullmatch(
            self.plan_digest
        ):
            raise ValueError("plan_digest must be a lowercase SHA-256 digest")
        _require_revision(self.config_revision, "config_revision")
        if self.state_revision is not None:
            _require_revision(self.state_revision, "state_revision")


__all__ = [
    "ApplicationIssue",
    "CancellationToken",
    "IssueSeverity",
    "NeverCancelled",
    "OperationStatus",
    "ProgressEvent",
    "ProgressPhase",
    "ReviewProof",
    "SecretValue",
]
