"""Strict, non-secret due-date domain models."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class DuePrecision(str, Enum):
    DATETIME = "datetime"
    DATE = "date"
    INFERRED = "inferred"


class DueCoverageStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DueIssue:
    code: str
    retry_required: bool = False
    item_index: int | None = None

    def __post_init__(self) -> None:
        if not self.code or self.code != self.code.strip():
            raise ValueError("due issue code must be non-empty")
        if self.item_index is not None and self.item_index < 0:
            raise ValueError("due issue item index must be non-negative")


@dataclass(frozen=True, slots=True)
class DueSourceKey:
    provider: str
    instance: str
    course_id: str
    category: str
    remote_id: str

    def __post_init__(self) -> None:
        for value in (self.provider, self.instance, self.course_id, self.category, self.remote_id):
            if not value or value != value.strip() or any(ord(c) < 32 for c in value):
                raise ValueError("due source identity must contain safe non-empty text")

    @property
    def canonical(self) -> str:
        return "|".join((self.provider, self.instance.casefold(), self.course_id, self.category, self.remote_id))


@dataclass(frozen=True, slots=True)
class DueItem:
    key: DueSourceKey
    logical_course_key: str
    course_label: str
    title: str
    due_at: datetime | date
    precision: DuePrecision
    points: Decimal | None = None
    weight_percent: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.logical_course_key.strip() or not self.course_label.strip() or not self.title.strip():
            raise ValueError("due item labels must be non-empty")
        if isinstance(self.due_at, datetime) and (
            self.due_at.tzinfo is None or self.due_at.utcoffset() is None
        ):
            raise ValueError("due datetime must be timezone-aware")
        for value in (self.points, self.weight_percent):
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError("points and weight must be non-negative decimals")

    @property
    def stable_marker(self) -> str:
        return hashlib.sha256(self.key.canonical.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class DueBatch:
    provider: str
    instance: str
    course_id: str
    coverage: DueCoverageStatus
    items: tuple[DueItem, ...] = ()
    issues: tuple[DueIssue, ...] = ()

    def __post_init__(self) -> None:
        if not self.provider or not self.instance or not self.course_id:
            raise ValueError("due batch identity must be non-empty")
        if self.coverage is DueCoverageStatus.FAILED and self.items:
            raise ValueError("failed due coverage cannot contain accepted items")
        if self.coverage is DueCoverageStatus.COMPLETE and self.issues:
            raise ValueError("complete due coverage cannot contain issues")
