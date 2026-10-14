"""Small read-only provider contracts for course discovery and content."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from contextlib import AbstractContextManager
from enum import Enum
from typing import Protocol

from canvas_ed_mcp.renderers.html import RenderedHtml
from canvas_ed_mcp.sync.model import RemoteItem, RemoteKind


class ProviderError(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class CoverageStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class IssueScope(str, Enum):
    CATEGORY = "category"
    ITEM = "item"


@dataclass(frozen=True, slots=True)
class CategoryCoverage:
    kind: RemoteKind
    status: CoverageStatus

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RemoteKind):
            raise ValueError("coverage kind must be a RemoteKind")
        if not isinstance(self.status, CoverageStatus):
            raise ValueError("coverage status must be a CoverageStatus")


@dataclass(frozen=True, slots=True)
class ContentIssue:
    provider: str
    instance: str
    course_id: str
    kind: RemoteKind
    scope: IssueScope
    code: str
    category: str
    retry_required: bool
    remote_id: str | None = None
    item_index: int | None = None

    def __post_init__(self) -> None:
        for field_name in ("provider", "instance", "course_id", "code", "category"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
                raise ValueError(f"{field_name} must be a non-empty safe string")
        if not isinstance(self.kind, RemoteKind):
            raise ValueError("issue kind must be a RemoteKind")
        if not isinstance(self.scope, IssueScope):
            raise ValueError("issue scope must be an IssueScope")
        if not isinstance(self.retry_required, bool):
            raise ValueError("retry_required must be a boolean")
        if self.remote_id is not None and (
            not isinstance(self.remote_id, str)
            or not self.remote_id
            or any(ord(char) < 32 for char in self.remote_id)
        ):
            raise ValueError("remote_id must be a safe non-empty string")
        if self.item_index is not None and (
            isinstance(self.item_index, bool)
            or not isinstance(self.item_index, int)
            or self.item_index < 0
        ):
            raise ValueError("item_index must be a non-negative integer")
        if self.scope is IssueScope.CATEGORY and (
            self.remote_id is not None or self.item_index is not None
        ):
            raise ValueError("category issues cannot identify an item")
        if self.scope is IssueScope.ITEM and (
            self.remote_id is None and self.item_index is None
        ):
            raise ValueError("item issues must identify an item or list position")


@dataclass(frozen=True, slots=True)
class ContentBatch:
    provider: str
    instance: str
    course_id: str
    requested_kinds: frozenset[RemoteKind]
    coverage: tuple[CategoryCoverage, ...]
    items: tuple[RemoteItem, ...]
    issues: tuple[ContentIssue, ...]

    def __post_init__(self) -> None:
        for field_name in ("provider", "instance", "course_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
                raise ValueError(f"{field_name} must be a non-empty safe string")
        if not isinstance(self.requested_kinds, frozenset) or not all(
            isinstance(kind, RemoteKind) for kind in self.requested_kinds
        ):
            raise ValueError("requested_kinds must be a frozenset of RemoteKind values")
        if not isinstance(self.coverage, tuple):
            raise ValueError("coverage must be an immutable tuple")
        if not isinstance(self.items, tuple):
            raise ValueError("items must be an immutable tuple")
        if not isinstance(self.issues, tuple):
            raise ValueError("issues must be an immutable tuple")

        coverage_by_kind: dict[RemoteKind, CoverageStatus] = {}
        for entry in self.coverage:
            if not isinstance(entry, CategoryCoverage) or entry.kind in coverage_by_kind:
                raise ValueError("coverage must contain each requested kind exactly once")
            coverage_by_kind[entry.kind] = entry.status
        if frozenset(coverage_by_kind) != self.requested_kinds:
            raise ValueError("coverage must match requested_kinds")

        seen_keys = set()
        item_kinds: set[RemoteKind] = set()
        for item in self.items:
            if not isinstance(item, RemoteItem):
                raise ValueError("items must contain RemoteItem values")
            key = item.key
            if (
                key.provider != self.provider
                or key.instance != self.instance
                or key.course_id != self.course_id
                or key.kind not in self.requested_kinds
            ):
                raise ValueError("item does not belong to this content batch")
            if key in seen_keys:
                raise ValueError("content batch cannot contain duplicate source keys")
            seen_keys.add(key)
            item_kinds.add(key.kind)

        issue_scopes: dict[RemoteKind, set[IssueScope]] = {}
        for issue in self.issues:
            if not isinstance(issue, ContentIssue) or (
                issue.provider != self.provider
                or issue.instance != self.instance
                or issue.course_id != self.course_id
                or issue.kind not in self.requested_kinds
            ):
                raise ValueError("issue does not belong to this content batch")
            issue_scopes.setdefault(issue.kind, set()).add(issue.scope)

        for kind, status in coverage_by_kind.items():
            scopes = issue_scopes.get(kind, set())
            if status is CoverageStatus.FAILED and kind in item_kinds:
                raise ValueError("failed coverage cannot contain items")
            if status is CoverageStatus.COMPLETE and scopes:
                raise ValueError("complete coverage cannot contain issues")
            if status is CoverageStatus.PARTIAL and scopes != {IssueScope.ITEM}:
                raise ValueError("partial coverage requires only item issues")
            if status is CoverageStatus.FAILED and scopes != {IssueScope.CATEGORY}:
                raise ValueError("failed coverage requires only category issues")

    @property
    def complete(self) -> bool:
        return all(entry.status is CoverageStatus.COMPLETE for entry in self.coverage)

    def items_for(self, kind: RemoteKind) -> tuple[RemoteItem, ...]:
        if kind not in self.requested_kinds:
            raise ValueError("kind was not requested")
        return tuple(item for item in self.items if item.key.kind is kind)

    def require_complete(self) -> tuple[RemoteItem, ...]:
        if not self.complete:
            raise ProviderError("incomplete_discovery", "Provider content discovery was incomplete")
        return self.items


@dataclass(frozen=True, slots=True)
class CourseCandidate:
    provider: str
    instance: str
    course_id: str
    code: str
    name: str
    term: str | None

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.instance}:{self.course_id}"


class CourseCatalogProvider(Protocol):
    provider_name: str
    instance: str

    def list_courses(self) -> tuple[CourseCandidate, ...]: ...

    def close(self) -> None: ...


class CourseContentProvider(CourseCatalogProvider, Protocol):
    """Metadata-only discovery plus transport-neutral content retrieval."""

    supported_kinds: frozenset[RemoteKind]

    def list_items(
        self,
        course_id: str,
        *,
        kinds: frozenset[RemoteKind],
    ) -> ContentBatch: ...

    def open_binary(
        self,
        item: RemoteItem,
        *,
        chunk_size: int = 64 * 1024,
    ) -> AbstractContextManager[Iterator[bytes]]: ...

    def render_html(self, item: RemoteItem) -> RenderedHtml: ...
