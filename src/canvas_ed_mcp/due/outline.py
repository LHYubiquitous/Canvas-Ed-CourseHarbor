"""Parse explicitly configured local Unit Outline / reviewed due Markdown."""

from __future__ import annotations

import calendar
import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping

from .model import (
    DueBatch,
    DueCoverageStatus,
    DueIssue,
    DueItem,
    DuePrecision,
    DueSourceKey,
)
from .render import SYDNEY


_CHINESE_DATE = re.compile(
    r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日.*?(?P<hour>\d{1,2}):(?P<minute>\d{2})"
)
_ENGLISH_DATE = re.compile(
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(?P<day>\d{1,2}).*?(?P<hour>\d{1,2}):(?P<minute>\d{2})",
    re.IGNORECASE,
)
_ITEM = re.compile(r"^\s*-\s*\[[ xX]\]\s+\*\*(?P<label>.+?)\*\*\s*$")
_WEIGHT = re.compile(r"(?:Weight|占分)\s*:\s*\*\*(?P<value>\d+(?:\.\d+)?)%\*\*", re.IGNORECASE)
_POINTS = re.compile(r"Points\s*:\s*\*\*(?P<value>\d+(?:\.\d+)?)\*\*", re.IGNORECASE)
_YEAR = re.compile(r"\b(?P<year>20\d{2})\b")


def _heading_datetime(line: str, year: int) -> datetime | None:
    year_match = _YEAR.search(line)
    if year_match:
        year = int(year_match.group("year"))
    match = _CHINESE_DATE.search(line)
    if match:
        month = int(match.group("month"))
    else:
        match = _ENGLISH_DATE.search(line)
        if not match:
            return None
        month = list(calendar.month_name).index(match.group("month").title())
    return datetime(
        year,
        month,
        int(match.group("day")),
        int(match.group("hour")),
        int(match.group("minute")),
        tzinfo=SYDNEY,
    )


def parse_unit_outline_markdown(
    markdown: str,
    *,
    default_year: int,
    course_keys: Mapping[str, tuple[str, str]],
    source_id: str,
) -> DueBatch:
    """Parse only explicit dated checklist entries; unknown labels stay unresolved."""
    current_due: datetime | None = None
    items: list[DueItem] = []
    issues: list[DueIssue] = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("#"):
            current_due = None
            try:
                parsed = _heading_datetime(line, default_year)
            except ValueError:
                parsed = None
                issues.append(DueIssue("invalid_outline_date", item_index=index))
            if parsed is not None:
                current_due = parsed
        match = _ITEM.match(line)
        if match:
            label = match.group("label")
            separator = "｜" if "｜" in label else "|" if "|" in label else None
            if current_due is None or separator is None:
                issues.append(DueIssue("item_schema_drift", item_index=index))
                index += 1
                continue
            course_text, title = (part.strip() for part in label.split(separator, 1))
            mapped = course_keys.get(course_text.casefold())
            if mapped is None:
                issues.append(DueIssue("unknown_course_label", item_index=index))
                index += 1
                continue
            points: Decimal | None = None
            weight: Decimal | None = None
            scan = index + 1
            try:
                while scan < len(lines) and not lines[scan].startswith("#") and not _ITEM.match(lines[scan]):
                    weight_match = _WEIGHT.search(lines[scan])
                    points_match = _POINTS.search(lines[scan])
                    if weight_match:
                        weight = Decimal(weight_match.group("value"))
                    if points_match:
                        points = Decimal(points_match.group("value"))
                    scan += 1
            except InvalidOperation:
                issues.append(DueIssue("item_schema_drift", item_index=index))
                index = scan
                continue
            logical_key, canonical_label = mapped
            remote_id = hashlib.sha256(
                f"{source_id}|{course_text}|{title}|{current_due.isoformat()}".encode("utf-8")
            ).hexdigest()[:24]
            items.append(
                DueItem(
                    key=DueSourceKey(
                        "unit-outline", "local", source_id, "assessment", remote_id
                    ),
                    logical_course_key=logical_key,
                    course_label=canonical_label,
                    title=title,
                    due_at=current_due,
                    precision=DuePrecision.DATETIME,
                    points=points,
                    weight_percent=weight,
                )
            )
            index = scan
            continue
        index += 1
    return DueBatch(
        "unit-outline",
        "local",
        source_id,
        DueCoverageStatus.PARTIAL if issues else DueCoverageStatus.COMPLETE,
        tuple(items),
        tuple(issues),
    )
