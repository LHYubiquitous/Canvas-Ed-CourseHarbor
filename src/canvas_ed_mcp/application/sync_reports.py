"""Persistent non-secret material-sync reports for the desktop Courses page."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from canvas_ed_mcp.paths import ProfilePaths, resolve_profile_paths

from .contracts import OperationStatus
from .daily_sync import SyncApplyResult


Clock = Callable[[], datetime]
PathsResolver = Callable[[str], ProfilePaths]
_SCHEMA = 1
_HISTORY_LIMIT = 30
_DETAIL_ACTIONS = frozenset(
    {
        "install_new",
        "replace_managed",
        "install_separately",
        "deduplicate",
        "failed",
        "block",
    }
)


@dataclass(frozen=True, slots=True)
class SyncReportItem:
    display_name: str
    provider: str
    kind: str
    action: str
    destination: Path | None


@dataclass(frozen=True, slots=True)
class SyncCourseReport:
    course_key: str
    display_name: str
    destination: Path
    sources: tuple[str, ...]
    action_counts: tuple[tuple[str, int], ...]
    changed_items: tuple[SyncReportItem, ...]


@dataclass(frozen=True, slots=True)
class SyncRunReport:
    run_id: str
    recorded_at: datetime
    status: OperationStatus
    courses: tuple[SyncCourseReport, ...]


class SyncReportStore:
    """Keep a small local history without provider ids, credentials, or raw errors."""

    def __init__(
        self,
        profile: str,
        *,
        paths_resolver: PathsResolver = resolve_profile_paths,
        clock: Clock = lambda: datetime.now(timezone.utc),
        history_limit: int = _HISTORY_LIMIT,
    ) -> None:
        if not isinstance(profile, str) or not profile:
            raise ValueError("profile must be non-empty")
        if isinstance(history_limit, bool) or not isinstance(history_limit, int) or history_limit <= 0:
            raise ValueError("history_limit must be positive")
        self.profile = profile
        self._paths_resolver = paths_resolver
        self._clock = clock
        self._history_limit = history_limit
        paths = paths_resolver(profile)
        if paths.profile != profile:
            raise ValueError("resolved profile does not match")
        self.path = Path(paths.state_file).parent / "sync-reports-v1.json"

    @staticmethod
    def _serialize(report: SyncRunReport) -> dict[str, object]:
        return {
            "run_id": report.run_id,
            "recorded_at": report.recorded_at.isoformat(),
            "status": report.status.value,
            "courses": [
                {
                    "course_key": course.course_key,
                    "display_name": course.display_name,
                    "destination": str(course.destination),
                    "sources": list(course.sources),
                    "action_counts": [[action, count] for action, count in course.action_counts],
                    "changed_items": [
                        {
                            "display_name": item.display_name,
                            "provider": item.provider,
                            "kind": item.kind,
                            "action": item.action,
                            "destination": str(item.destination) if item.destination else None,
                        }
                        for item in course.changed_items
                    ],
                }
                for course in report.courses
            ],
        }

    @staticmethod
    def _parse(payload: object) -> SyncRunReport:
        if not isinstance(payload, dict):
            raise ValueError("report must be an object")
        courses: list[SyncCourseReport] = []
        raw_courses = payload.get("courses")
        if not isinstance(raw_courses, list):
            raise ValueError("courses must be a list")
        for raw in raw_courses:
            if not isinstance(raw, dict):
                raise ValueError("course report must be an object")
            raw_items = raw.get("changed_items")
            raw_counts = raw.get("action_counts")
            raw_sources = raw.get("sources")
            if not isinstance(raw_items, list) or not isinstance(raw_counts, list) or not isinstance(raw_sources, list):
                raise ValueError("course report collections are invalid")
            items = tuple(
                SyncReportItem(
                    display_name=str(item["display_name"]),
                    provider=str(item["provider"]),
                    kind=str(item["kind"]),
                    action=str(item["action"]),
                    destination=(Path(item["destination"]) if item.get("destination") else None),
                )
                for item in raw_items
                if isinstance(item, dict)
            )
            counts_list: list[tuple[str, int]] = []
            for pair in raw_counts:
                if (
                    not isinstance(pair, list)
                    or len(pair) != 2
                    or not isinstance(pair[0], str)
                    or not pair[0]
                    or isinstance(pair[1], bool)
                    or not isinstance(pair[1], int)
                    or pair[1] < 0
                ):
                    raise ValueError("action count must be a name and non-negative integer")
                counts_list.append((pair[0], pair[1]))
            counts = tuple(counts_list)
            courses.append(
                SyncCourseReport(
                    course_key=str(raw["course_key"]),
                    display_name=str(raw["display_name"]),
                    destination=Path(raw["destination"]),
                    sources=tuple(str(source) for source in raw_sources),
                    action_counts=counts,
                    changed_items=items,
                )
            )
        return SyncRunReport(
            run_id=str(payload["run_id"]),
            recorded_at=datetime.fromisoformat(str(payload["recorded_at"])),
            status=OperationStatus(str(payload["status"])),
            courses=tuple(courses),
        )

    def load(self) -> tuple[SyncRunReport, ...]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema") != _SCHEMA:
                return ()
            reports = payload.get("reports")
            if not isinstance(reports, list):
                return ()
            return tuple(self._parse(report) for report in reports)
        except (FileNotFoundError, OSError, UnicodeError, ValueError, KeyError, TypeError):
            return ()

    def _write(self, reports: tuple[SyncRunReport, ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(
            {
                "schema": _SCHEMA,
                "reports": [self._serialize(report) for report in reports],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=self.path.parent, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def record(self, result: SyncApplyResult, *, output_root: Path) -> SyncRunReport:
        if not isinstance(result, SyncApplyResult):
            raise TypeError("result must be a SyncApplyResult")
        root = Path(output_root).resolve(strict=True)
        recorded_at = self._clock().astimezone(timezone.utc)
        grouped: dict[str, list] = {}
        for source in result.sources:
            grouped.setdefault(source.course_key, []).append(source)
        course_reports: list[SyncCourseReport] = []
        for course_key, sources in grouped.items():
            first = sources[0]
            folder = first.course_folder or course_key
            destination = root.joinpath(folder).resolve(strict=False)
            counts = Counter(action for source in sources for action in source.actions)
            changed = tuple(
                SyncReportItem(
                    display_name=item.display_name,
                    provider=source.source_display_name,
                    kind=item.kind,
                    action=item.action,
                    destination=item.destination,
                )
                for source in sources
                for item in source.items
                if item.action in _DETAIL_ACTIONS
            )
            course_reports.append(
                SyncCourseReport(
                    course_key=course_key,
                    display_name=first.course_display_name,
                    destination=destination,
                    sources=tuple(dict.fromkeys(source.source_display_name for source in sources)),
                    action_counts=tuple(sorted(counts.items())),
                    changed_items=changed,
                )
            )
        report = SyncRunReport(
            run_id=recorded_at.isoformat(timespec="microseconds"),
            recorded_at=recorded_at,
            status=result.status,
            courses=tuple(course_reports),
        )
        history = (report, *self.load())[: self._history_limit]
        self._write(history)
        return report


__all__ = [
    "SyncCourseReport",
    "SyncReportItem",
    "SyncReportStore",
    "SyncRunReport",
]
