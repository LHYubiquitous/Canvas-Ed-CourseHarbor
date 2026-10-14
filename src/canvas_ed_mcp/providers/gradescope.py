"""Narrow read-only adapter around the optional ``gradescopeapi`` scraper."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


class GradescopeError(RuntimeError):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


def _safe_text(value: object, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise GradescopeError("schema_drift")
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise GradescopeError("schema_drift")
    text = str(value)
    if (required and not text) or text != text.strip() or any(
        ord(character) < 32 or ord(character) == 127 for character in text
    ):
        raise GradescopeError("schema_drift")
    return text


def _iso_datetime(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise GradescopeError("schema_drift")
    return value.isoformat()


@dataclass(frozen=True, slots=True)
class GradescopeCourse:
    role: str
    course_id: str
    name: str
    semester: str | None
    year: str | None
    num_assignments: str | None


@dataclass(frozen=True, slots=True)
class GradescopeAssignment:
    course_id: str
    assignment_id: str | None
    name: str
    due_at: str | None
    late_due_at: str | None
    submissions_status: str | None
    grade: str | None
    max_grade: str | None


class GradescopeClient(Protocol):
    def list_courses(self) -> tuple[GradescopeCourse, ...]: ...

    def list_assignments(self, course_id: str) -> tuple[GradescopeAssignment, ...]: ...

    def close(self) -> None: ...


class GradescopeProvider:
    """Own one isolated logged-in scraper connection with explicit credentials."""

    def __init__(
        self,
        email: str,
        password: str,
        *,
        connection_factory: Any | None = None,
    ) -> None:
        if not email or not password:
            raise GradescopeError("missing_credential")
        if connection_factory is None:
            try:
                from gradescopeapi.classes.connection import GSConnection
            except ImportError:
                raise GradescopeError("missing_dependency") from None
            connection_factory = GSConnection
        connection = None
        try:
            connection = connection_factory()
            connection.login(email, password)
        except Exception as exc:
            if connection is not None:
                close = getattr(connection, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            if isinstance(exc, GradescopeError):
                raise
            if isinstance(exc, ValueError):
                raise GradescopeError("authentication_failed") from None
            raise GradescopeError("provider_unavailable") from None
        self._connection = connection
        self._discovered_course_ids: set[str] = set()
        self._closed = False

    def list_courses(self) -> tuple[GradescopeCourse, ...]:
        try:
            payload = self._connection.account.get_courses()
        except Exception:
            raise GradescopeError("provider_unavailable") from None
        if not isinstance(payload, dict):
            raise GradescopeError("schema_drift")
        output: list[GradescopeCourse] = []
        discovered: set[str] = set()
        for role in ("student", "instructor"):
            role_courses = payload.get(role, {})
            if not isinstance(role_courses, dict):
                raise GradescopeError("schema_drift")
            for raw_id, course in role_courses.items():
                course_id = _safe_text(raw_id, "course_id", required=True)
                full_name = _safe_text(getattr(course, "full_name", None), "full_name")
                short_name = _safe_text(getattr(course, "name", None), "name")
                name = full_name or short_name or course_id
                output.append(
                    GradescopeCourse(
                        role=role,
                        course_id=course_id,
                        name=name,
                        semester=_safe_text(getattr(course, "semester", None), "semester"),
                        year=_safe_text(getattr(course, "year", None), "year"),
                        num_assignments=_safe_text(
                            getattr(course, "num_assignments", None), "num_assignments"
                        ),
                    )
                )
                discovered.add(course_id)
        self._discovered_course_ids = discovered
        return tuple(output)

    def list_assignments(self, course_id: str) -> tuple[GradescopeAssignment, ...]:
        if course_id not in self._discovered_course_ids:
            raise GradescopeError("course_not_discovered")
        try:
            payload = self._connection.account.get_assignments(course_id)
        except Exception:
            raise GradescopeError("provider_unavailable") from None
        if not isinstance(payload, (list, tuple)):
            raise GradescopeError("schema_drift")
        output: list[GradescopeAssignment] = []
        for assignment in payload:
            name = _safe_text(getattr(assignment, "name", None), "name", required=True)
            assignment_id = _safe_text(
                getattr(assignment, "assignment_id", getattr(assignment, "id", None)),
                "assignment_id",
            )
            output.append(
                GradescopeAssignment(
                    course_id=course_id,
                    assignment_id=assignment_id,
                    name=name,
                    due_at=_iso_datetime(getattr(assignment, "due_date", None)),
                    late_due_at=_iso_datetime(getattr(assignment, "late_due_date", None)),
                    submissions_status=_safe_text(
                        getattr(assignment, "submissions_status", None), "submissions_status"
                    ),
                    grade=_safe_text(getattr(assignment, "grade", None), "grade"),
                    max_grade=_safe_text(getattr(assignment, "max_grade", None), "max_grade"),
                )
            )
        return tuple(output)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._discovered_course_ids.clear()
        close = getattr(self._connection, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


__all__ = [
    "GradescopeAssignment",
    "GradescopeClient",
    "GradescopeCourse",
    "GradescopeError",
    "GradescopeProvider",
]
