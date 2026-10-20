"""Deterministic English-first weekly and semester due reports."""

from __future__ import annotations

import calendar
import os
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Iterable
from pathlib import Path
from zoneinfo import ZoneInfo

from .model import DueItem, DuePrecision


SYDNEY = ZoneInfo("Australia/Sydney")
_MARKER = re.compile(r"<!-- canvas-ed-mcp:due=([0-9a-f]{20}) -->")


def _decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _as_datetime(item: DueItem) -> datetime:
    if isinstance(item.due_at, datetime):
        return item.due_at.astimezone(SYDNEY)
    return datetime.combine(item.due_at, time.min, tzinfo=SYDNEY)


def _existing_completion(markdown: str) -> set[str]:
    completed: set[str] = set()
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        match = _MARKER.search(line)
        if not match:
            continue
        for prior in reversed(lines[:index]):
            if prior.startswith("#") or _MARKER.search(prior):
                break
            if re.match(r"\s*- \[[ xX]\]", prior):
                if "- [x]" in prior.casefold():
                    completed.add(match.group(1))
                break
    return completed


def _existing_manual_notes(markdown: str) -> dict[str, tuple[str, ...]]:
    notes: dict[str, tuple[str, ...]] = {}
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        match = _MARKER.search(line)
        if not match:
            continue
        preserved: list[str] = []
        for following in lines[index + 1:]:
            if following.startswith("#") or re.match(r"\s*- \[[ xX]\]", following):
                break
            if following.strip() and following.startswith((" ", "\t")):
                preserved.append(following)
            elif following.strip():
                break
        if preserved:
            notes[match.group(1)] = tuple(preserved)
    return notes


def select_week_items(items: Iterable[DueItem], *, now: datetime) -> tuple[DueItem, ...]:
    """Return the deterministically ordered items inside the Sydney week window."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local = now.astimezone(SYDNEY)
    start = datetime.combine((local - timedelta(days=local.weekday())).date(), time.min, tzinfo=SYDNEY)
    end = start + timedelta(days=7)
    return tuple(
        sorted(
            (item for item in items if start <= _as_datetime(item) < end),
            key=lambda item: (
                _as_datetime(item),
                item.course_label.casefold(),
                item.title.casefold(),
            ),
        )
    )


def render_week_text(items: Iterable[DueItem], *, now: datetime) -> str:
    selected = select_week_items(items, now=now)
    local = now.astimezone(SYDNEY)
    start = datetime.combine((local - timedelta(days=local.weekday())).date(), time.min, tzinfo=SYDNEY)
    end = start + timedelta(days=7)
    lines = ["This week's due", f"Window: {start:%Y-%m-%d} to {(end - timedelta(days=1)):%Y-%m-%d}", ""]
    if not selected:
        lines.append("No due items were found for this week.")
    for item in selected:
        due = _as_datetime(item)
        lines.extend((f"{item.course_label} | {item.title}", f"Due: {due:%Y-%m-%d %H:%M} Australia/Sydney"))
        if item.points is not None:
            lines.append(f"Points: {_decimal(item.points)}")
        if item.weight_percent is not None:
            lines.append(f"Weight: {_decimal(item.weight_percent)}%")
        lines.extend((f"Status: {'estimated' if item.precision is DuePrecision.INFERRED else 'exact'}", ""))
    return "\n".join(lines).rstrip() + "\n"


def render_semester_markdown(
    items: Iterable[DueItem],
    *,
    semester_label: str,
    existing_markdown: str = "",
    teaching_start: date | None = None,
    teaching_periods: tuple[tuple[str, date, date], ...] = (),
) -> str:
    completed = _existing_completion(existing_markdown)
    manual_notes = _existing_manual_notes(existing_markdown)
    ordered = sorted(
        items,
        key=lambda item: (_as_datetime(item), item.course_label.casefold(), item.title.casefold()),
    )
    grouped: dict[tuple[int, int], list[DueItem]] = defaultdict(list)
    for item in ordered:
        due = _as_datetime(item)
        grouped[(due.year, due.month)].append(item)
    lines = [f"# {semester_label} Assessment Due Notes", ""]
    last_period: str | None = None
    for (year, month), month_items in grouped.items():
        lines.extend((f"## {calendar.month_name[month]} {year}", ""))
        by_time: dict[datetime, list[DueItem]] = defaultdict(list)
        for item in month_items:
            by_time[_as_datetime(item)].append(item)
        for due, due_items in sorted(by_time.items()):
            explicit_label = next(
                (label for label, start, end in teaching_periods if start <= due.date() <= end),
                None,
            )
            if explicit_label is not None:
                if explicit_label != last_period:
                    lines.extend((f"### {explicit_label}", ""))
                    last_period = explicit_label
            elif teaching_start is not None and not teaching_periods:
                week = ((due.date() - teaching_start).days // 7) + 1
                label = f"Teaching Week {week}" if week > 0 else "Before Teaching Period"
                if label != last_period:
                    lines.extend((f"### {label}", ""))
                    last_period = label
            lines.extend((f"#### {due.strftime('%B')} {due.day} ({due.strftime('%a')}) {due:%H:%M}", ""))
            for item in due_items:
                checked = "x" if item.stable_marker in completed else " "
                inferred = " (estimated)" if item.precision is DuePrecision.INFERRED else ""
                lines.append(f"- [{checked}] **{item.course_label}｜{item.title}{inferred}**")
                if item.points is not None:
                    lines.append(f"    - Points: **{_decimal(item.points)}**")
                if item.weight_percent is not None:
                    lines.append(f"    - Weight: **{_decimal(item.weight_percent)}%**")
                lines.append(f"    <!-- canvas-ed-mcp:due={item.stable_marker} -->")
                lines.extend(manual_notes.get(item.stable_marker, ()))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def publish_versioned_report(directory: Path, filename: str, content: str) -> Path:
    """Publish a report create-only, reusing identical bytes or adding a revision."""
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise ValueError("report filename must be one path component")
    directory.mkdir(parents=True, exist_ok=True)
    payload = content.encode("utf-8")
    requested = Path(filename)
    stem, suffix = requested.stem, requested.suffix
    revision = 1
    while True:
        name = filename if revision == 1 else f"{stem}--r{revision:03d}{suffix}"
        target = directory / name
        if target.exists():
            if target.is_file() and target.read_bytes() == payload:
                return target
            revision += 1
            continue
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target


def latest_versioned_report(directory: Path, filename: str) -> Path | None:
    """Return the numerically newest report from one create-only report series."""
    requested = Path(filename)
    if requested.name != filename or filename in {"", ".", ".."}:
        raise ValueError("report filename must be one path component")
    pattern = re.compile(
        rf"^{re.escape(requested.stem)}(?:--r(?P<revision>\d{{3}}))?{re.escape(requested.suffix)}$"
    )
    candidates: list[tuple[int, Path]] = []
    if directory.exists():
        for candidate in directory.iterdir():
            match = pattern.fullmatch(candidate.name)
            if match and candidate.is_file():
                candidates.append((int(match.group("revision") or "1"), candidate))
    return max(candidates, default=(0, None), key=lambda pair: pair[0])[1]
