from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.providers.gradescope import GradescopeError, GradescopeProvider
from canvas_ed_mcp.due.model import DueCoverageStatus


class FakeAccount:
    def __init__(self) -> None:
        self.assignment_calls: list[str] = []

    def get_courses(self):
        return {
            "student": {
                "901": SimpleNamespace(
                    full_name="Synthetic Course",
                    name="SAFE1000",
                    semester="Semester 2",
                    year=2099,
                    num_assignments="1 assignment",
                    unexpected_private_field="must-not-escape",
                )
            },
            "instructor": {},
        }

    def get_assignments(self, course_id: str):
        self.assignment_calls.append(course_id)
        return [
            SimpleNamespace(
                id=501,
                name="Synthetic assignment",
                due_date=datetime(2099, 10, 1, 10, tzinfo=timezone.utc),
                late_due_date=None,
                submissions_status="Submitted",
                grade=8,
                max_grade=10,
                unexpected_private_field="must-not-escape",
            )
        ]


class FakeConnection:
    def __init__(self) -> None:
        self.account = FakeAccount()
        self.logins: list[tuple[str, str]] = []
        self.close_calls = 0

    def login(self, email: str, password: str) -> None:
        self.logins.append((email, password))

    def close(self) -> None:
        self.close_calls += 1


class GradescopeProviderTests(unittest.TestCase):
    def test_explicit_credentials_and_allowlisted_projection(self) -> None:
        connection = FakeConnection()
        provider = GradescopeProvider(
            "synthetic@example.invalid",
            "synthetic-password",
            connection_factory=lambda: connection,
        )

        courses = provider.list_courses()
        assignments = provider.list_assignments("901")

        self.assertEqual(
            [("synthetic@example.invalid", "synthetic-password")], connection.logins
        )
        self.assertEqual("901", courses[0].course_id)
        self.assertEqual("Synthetic Course", courses[0].name)
        self.assertFalse(hasattr(courses[0], "unexpected_private_field"))
        self.assertEqual("501", assignments[0].assignment_id)
        self.assertEqual("8", assignments[0].grade)
        self.assertFalse(hasattr(assignments[0], "unexpected_private_field"))

        provider.close()
        provider.close()
        self.assertEqual(1, connection.close_calls)

    def test_due_projection_uses_original_due_and_exact_course_mapping(self) -> None:
        connection = FakeConnection()
        provider = GradescopeProvider(
            "synthetic@example.invalid",
            "synthetic-password",
            connection_factory=lambda: connection,
        )
        provider.list_courses()
        batch = provider.list_due_items(
            "901", logical_course_key="logical-1", course_label="SAFE1000"
        )
        self.assertEqual(DueCoverageStatus.COMPLETE, batch.coverage)
        self.assertEqual("Synthetic assignment", batch.items[0].title)
        self.assertEqual("10", str(batch.items[0].points))
        self.assertEqual("2099-10-01T10:00:00+00:00", batch.items[0].due_at.isoformat())
        provider.close()

    def test_assignments_require_exact_course_from_current_catalog(self) -> None:
        connection = FakeConnection()
        provider = GradescopeProvider(
            "synthetic@example.invalid",
            "synthetic-password",
            connection_factory=lambda: connection,
        )

        with self.assertRaisesRegex(GradescopeError, "course_not_discovered"):
            provider.list_assignments("901")
        provider.list_courses()
        with self.assertRaisesRegex(GradescopeError, "course_not_discovered"):
            provider.list_assignments("0901")
        self.assertEqual([], connection.account.assignment_calls)

    def test_login_and_schema_failures_are_stable_and_sanitized(self) -> None:
        class BadLogin(FakeConnection):
            def login(self, email: str, password: str) -> None:
                raise ValueError(f"secret={password}")

        bad_connection = BadLogin()
        with self.assertRaisesRegex(GradescopeError, r"^authentication_failed$") as caught:
            GradescopeProvider(
                "synthetic@example.invalid",
                "LEAK_CANARY",
                connection_factory=lambda: bad_connection,
            )
        self.assertNotIn("LEAK_CANARY", str(caught.exception))
        self.assertEqual(1, bad_connection.close_calls)

        connection = FakeConnection()
        connection.account.get_courses = lambda: {"student": [], "instructor": {}}
        provider = GradescopeProvider(
            "synthetic@example.invalid",
            "synthetic-password",
            connection_factory=lambda: connection,
        )
        with self.assertRaisesRegex(GradescopeError, r"^schema_drift$"):
            provider.list_courses()


if __name__ == "__main__":
    unittest.main()
