"""Framework-neutral review/apply controller contracts for daily material sync."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Protocol, runtime_checkable

from .contracts import (
    ApplicationIssue,
    CancellationToken,
    IssueSeverity,
    NeverCancelled,
    OperationStatus,
    ProgressEvent,
    ReviewProof,
)


_CODE = re.compile(r"[a-z][a-z0-9_]*\Z")
ProgressCallback = Callable[[ProgressEvent], None]


class DailySyncState(str, Enum):
    IDLE = "idle"
    PREVIEWING = "previewing"
    REVIEW_READY = "review_ready"
    APPLYING = "applying"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CONFLICT = "conflict"
    CANCELLED = "cancelled"


def _safe_text(value: str, field: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field} must be safe non-empty text")


def _codes(values: tuple[str, ...], field: str) -> None:
    if not isinstance(values, tuple) or any(
        not isinstance(value, str) or not _CODE.fullmatch(value) for value in values
    ):
        raise ValueError(f"{field} must contain stable lowercase codes")


@dataclass(frozen=True, slots=True)
class SyncPreviewRequest:
    profile: str
    config_path: Path | None = None
    course_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _safe_text(self.profile, "profile")
        if self.config_path is not None and not isinstance(self.config_path, Path):
            raise ValueError("config_path must be a Path or None")
        if not isinstance(self.course_keys, tuple):
            raise ValueError("course_keys must be an immutable tuple")
        for course_key in self.course_keys:
            _safe_text(course_key, "course_key")
        if len(self.course_keys) != len(set(self.course_keys)):
            raise ValueError("course_keys must be unique")


@dataclass(frozen=True, slots=True)
class SyncItemSummary:
    """Non-secret per-item outcome suitable for sync reports."""

    display_name: str
    kind: str
    action: str
    reason: str
    destination: Path | None = None

    def __post_init__(self) -> None:
        for field_name in ("display_name", "kind", "action", "reason"):
            _safe_text(getattr(self, field_name), field_name)
        if self.destination is not None:
            if not isinstance(self.destination, Path) or not self.destination.is_absolute():
                raise ValueError("destination must be an absolute Path or None")


@dataclass(frozen=True, slots=True)
class SyncSourceSummary:
    course_key: str
    provider: str
    instance: str
    course_id: str
    actions: tuple[str, ...]
    issue_codes: tuple[str, ...] = ()
    course_folder: str | None = None
    course_code: str | None = None
    course_name: str | None = None
    course_term: str | None = None
    items: tuple[SyncItemSummary, ...] = ()

    def __post_init__(self) -> None:
        _safe_text(self.course_key, "course_key")
        if self.provider not in {"canvas", "ed"}:
            raise ValueError("provider must be canvas or ed")
        _safe_text(self.instance, "instance")
        _safe_text(self.course_id, "course_id")
        _codes(self.actions, "actions")
        _codes(self.issue_codes, "issue_codes")
        for field_name in (
            "course_folder",
            "course_code",
            "course_name",
            "course_term",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _safe_text(value, field_name)
        if not isinstance(self.items, tuple) or not all(
            isinstance(item, SyncItemSummary) for item in self.items
        ):
            raise ValueError("items must contain SyncItemSummary values")

    @property
    def course_display_name(self) -> str:
        """Return a human label without exposing the stable internal course key."""

        primary = self.course_code or self.course_name or self.course_folder
        if primary is None:
            primary = "Configured course"
        label = primary
        if (
            self.course_name is not None
            and self.course_name.casefold() != primary.casefold()
        ):
            label = f"{label} — {self.course_name}"
        if (
            self.course_term is not None
            and self.course_term.casefold() != label.casefold()
        ):
            label = f"{label} · {self.course_term}"
        return label

    @property
    def source_display_name(self) -> str:
        """Return the provider product name, not its internal source identity."""

        return {"canvas": "Canvas", "ed": "Ed"}[self.provider]


@dataclass(frozen=True, slots=True)
class SyncReview:
    proof: ReviewProof
    profile: str
    config_path: Path
    state_path: Path
    output_root: Path
    sources: tuple[SyncSourceSummary, ...]

    def __post_init__(self) -> None:
        if self.proof.operation != "sync":
            raise ValueError("sync review requires a sync proof")
        _safe_text(self.profile, "profile")
        for field_name in ("config_path", "state_path", "output_root"):
            if not isinstance(getattr(self, field_name), Path):
                raise ValueError(f"{field_name} must be a Path")
        if not isinstance(self.sources, tuple) or not self.sources:
            raise ValueError("sync review requires at least one source")
        if not all(isinstance(source, SyncSourceSummary) for source in self.sources):
            raise ValueError("sources must contain SyncSourceSummary values")
        identities = tuple(
            (source.course_key, source.provider, source.instance, source.course_id)
            for source in self.sources
        )
        if len(identities) != len(set(identities)):
            raise ValueError("sync review source identities must be unique")


@dataclass(frozen=True, slots=True)
class SyncPreviewResult:
    status: OperationStatus
    review: SyncReview | None = None
    issues: tuple[ApplicationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, OperationStatus):
            raise ValueError("status must be an OperationStatus")
        if self.status in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}:
            if not isinstance(self.review, SyncReview):
                raise ValueError("actionable preview results require a review")
        elif self.review is not None:
            raise ValueError("non-actionable preview results cannot include a review")
        if not isinstance(self.issues, tuple) or not all(
            isinstance(issue, ApplicationIssue) for issue in self.issues
        ):
            raise ValueError("issues must contain ApplicationIssue values")


@dataclass(frozen=True, slots=True)
class SyncApplyResult:
    status: OperationStatus
    sources: tuple[SyncSourceSummary, ...] = ()
    issues: tuple[ApplicationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, OperationStatus):
            raise ValueError("status must be an OperationStatus")
        if not isinstance(self.sources, tuple) or not all(
            isinstance(source, SyncSourceSummary) for source in self.sources
        ):
            raise ValueError("sources must contain SyncSourceSummary values")
        if not isinstance(self.issues, tuple) or not all(
            isinstance(issue, ApplicationIssue) for issue in self.issues
        ):
            raise ValueError("issues must contain ApplicationIssue values")


@runtime_checkable
class SyncOperationPort(Protocol):
    def preview(
        self,
        request: SyncPreviewRequest,
        *,
        progress: ProgressCallback | None,
        cancellation: CancellationToken,
    ) -> SyncPreviewResult: ...

    def apply(
        self,
        review: SyncReview,
        *,
        progress: ProgressCallback | None,
        cancellation: CancellationToken,
    ) -> SyncApplyResult: ...

    def discard(self, review: SyncReview) -> None: ...

    def close(self) -> None: ...


def _issue(code: str) -> ApplicationIssue:
    return ApplicationIssue(code=code, severity=IssueSeverity.ERROR)


def _terminal_state(status: OperationStatus) -> DailySyncState:
    return {
        OperationStatus.SUCCEEDED: DailySyncState.SUCCEEDED,
        OperationStatus.PARTIAL: DailySyncState.PARTIAL,
        OperationStatus.FAILED: DailySyncState.FAILED,
        OperationStatus.CONFLICT: DailySyncState.CONFLICT,
        OperationStatus.CANCELLED: DailySyncState.CANCELLED,
    }[status]


def _canonical_path(path: Path) -> Path | None:
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None


def _review_matches_request(
    review: SyncReview, request: SyncPreviewRequest
) -> bool:
    if review.profile != request.profile:
        return False
    if request.config_path is not None:
        requested = _canonical_path(request.config_path)
        reviewed = _canonical_path(review.config_path)
        if requested is None or reviewed is None or requested != reviewed:
            return False
    if request.course_keys:
        reviewed_courses = {source.course_key for source in review.sources}
        if reviewed_courses != set(request.course_keys):
            return False
    return True


class DailySyncController:
    """Own review identity and state transitions while an adapter owns threading."""

    def __init__(self, port: SyncOperationPort) -> None:
        if not isinstance(port, SyncOperationPort):
            raise TypeError("port must implement SyncOperationPort")
        self._port = port
        self._lock = Lock()
        self._state = DailySyncState.IDLE
        self._review: SyncReview | None = None

    @property
    def state(self) -> DailySyncState:
        with self._lock:
            return self._state

    @property
    def review(self) -> SyncReview | None:
        with self._lock:
            return self._review

    def preview(
        self,
        request: SyncPreviewRequest,
        *,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken = NeverCancelled(),
    ) -> SyncPreviewResult:
        stale_review: SyncReview | None = None
        with self._lock:
            if self._state in {DailySyncState.PREVIEWING, DailySyncState.APPLYING}:
                return SyncPreviewResult(
                    OperationStatus.CONFLICT,
                    issues=(_issue("operation_in_progress"),),
                )
            stale_review = self._review
            self._review = None
            self._state = DailySyncState.PREVIEWING
        if stale_review is not None:
            try:
                self._port.discard(stale_review)
            except Exception:
                pass
        if cancellation.cancellation_requested:
            with self._lock:
                self._state = DailySyncState.CANCELLED
            return SyncPreviewResult(OperationStatus.CANCELLED)
        try:
            result = self._port.preview(
                request, progress=progress, cancellation=cancellation
            )
            if not isinstance(result, SyncPreviewResult):
                raise TypeError("preview port returned an invalid result")
        except Exception:
            result = SyncPreviewResult(
                OperationStatus.FAILED, issues=(_issue("sync_preview_failed"),)
            )
        if (
            result.status in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}
            and result.review is not None
            and not _review_matches_request(result.review, request)
        ):
            try:
                self._port.discard(result.review)
            except Exception:
                pass
            result = SyncPreviewResult(
                OperationStatus.CONFLICT,
                issues=(_issue("review_scope_mismatch"),),
            )
        with self._lock:
            if result.status in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}:
                self._review = result.review
                self._state = DailySyncState.REVIEW_READY
            else:
                self._state = _terminal_state(result.status)
        return result

    def apply(
        self,
        review: SyncReview,
        *,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken = NeverCancelled(),
    ) -> SyncApplyResult:
        with self._lock:
            if (
                self._state is not DailySyncState.REVIEW_READY
                or review is not self._review
            ):
                return SyncApplyResult(
                    OperationStatus.CONFLICT,
                    issues=(_issue("review_required"),),
                )
            self._state = DailySyncState.APPLYING
            self._review = None
        if cancellation.cancellation_requested:
            try:
                self._port.discard(review)
            except Exception:
                pass
            with self._lock:
                self._state = DailySyncState.CANCELLED
            return SyncApplyResult(OperationStatus.CANCELLED)
        try:
            result = self._port.apply(
                review, progress=progress, cancellation=cancellation
            )
            if not isinstance(result, SyncApplyResult):
                raise TypeError("apply port returned an invalid result")
        except Exception:
            result = SyncApplyResult(
                OperationStatus.FAILED, issues=(_issue("sync_apply_failed"),)
            )
        finally:
            try:
                self._port.discard(review)
            except Exception:
                pass
        with self._lock:
            self._state = _terminal_state(result.status)
        return result

    def close(self) -> bool:
        """Release an idle review lease; active work must finish cooperatively."""

        with self._lock:
            if self._state in {DailySyncState.PREVIEWING, DailySyncState.APPLYING}:
                return False
            review = self._review
            self._review = None
            self._state = DailySyncState.IDLE
        if review is not None:
            try:
                self._port.discard(review)
            except Exception:
                pass
        try:
            self._port.close()
        except Exception:
            pass
        return True


__all__ = [
    "DailySyncController",
    "DailySyncState",
    "ProgressCallback",
    "SyncApplyResult",
    "SyncItemSummary",
    "SyncOperationPort",
    "SyncPreviewRequest",
    "SyncPreviewResult",
    "SyncReview",
    "SyncSourceSummary",
]
