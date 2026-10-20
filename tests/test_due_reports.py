from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from canvas_ed_mcp.due.model import (
    DueBatch,
    DueCoverageStatus,
    DueIssue,
    DueItem,
    DuePrecision,
    DueSourceKey,
)
from canvas_ed_mcp.due.render import (
    latest_versioned_report,
    publish_versioned_report,
    render_semester_markdown,
    render_week_text,
    select_week_items,
)


SYDNEY = ZoneInfo("Australia/Sydney")


def item(title: str, due_at: datetime, *, remote_id: str, points: str | None = None) -> DueItem:
    return DueItem(
        key=DueSourceKey("canvas", "canvas.example.edu", "42", "assignment", remote_id),
        logical_course_key="course-42",
        course_label="COMP0000",
        title=title,
        due_at=due_at,
        precision=DuePrecision.DATETIME,
        points=Decimal(points) if points is not None else None,
        weight_percent=None,
    )


class DueReportTests(unittest.TestCase):
    def test_batch_rejects_failed_coverage_with_items(self) -> None:
        due = item("Assignment 1", datetime(2026, 10, 21, 23, 59, tzinfo=SYDNEY), remote_id="1")
        with self.assertRaises(ValueError):
            DueBatch(
                provider="canvas",
                instance="canvas.example.edu",
                course_id="42",
                coverage=DueCoverageStatus.FAILED,
                items=(due,),
                issues=(DueIssue("request_failed", retry_required=True),),
            )

    def test_week_report_uses_monday_window_and_english_labels(self) -> None:
        items = (
            item("Assignment 1", datetime(2026, 10, 21, 23, 59, tzinfo=SYDNEY), remote_id="1", points="40"),
            item("Next week", datetime(2026, 10, 26, 0, 0, tzinfo=SYDNEY), remote_id="2"),
        )
        rendered = render_week_text(items, now=datetime(2026, 10, 20, 12, 0, tzinfo=SYDNEY))
        self.assertIn("This week's due", rendered)
        self.assertIn("COMP0000 | Assignment 1", rendered)
        self.assertIn("Points: 40", rendered)
        self.assertNotIn("Next week", rendered)

    def test_week_selection_returns_only_items_written_to_report(self) -> None:
        items = (
            item("This week 1", datetime(2026, 10, 20, 9, 0, tzinfo=SYDNEY), remote_id="1"),
            item("This week 2", datetime(2026, 10, 25, 23, 59, tzinfo=SYDNEY), remote_id="2"),
            item("Next week", datetime(2026, 10, 26, 0, 0, tzinfo=SYDNEY), remote_id="3"),
        )

        selected = select_week_items(
            items, now=datetime(2026, 10, 20, 12, 0, tzinfo=SYDNEY)
        )

        self.assertEqual(["This week 1", "This week 2"], [entry.title for entry in selected])

    def test_semester_report_preserves_completion_from_existing_marker(self) -> None:
        due = item("Assignment 1", datetime(2026, 10, 21, 23, 59, tzinfo=SYDNEY), remote_id="1", points="40")
        marker = due.stable_marker
        existing = f"- [x] **COMP0000｜Assignment 1**\n  <!-- canvas-ed-mcp:due={marker} -->\n"
        rendered = render_semester_markdown(
            (due,),
            semester_label="2026 S2",
            existing_markdown=existing,
        )
        self.assertIn("# 2026 S2 Assessment Due Notes", rendered)
        self.assertIn("## October 2026", rendered)
        self.assertIn("- [x] **COMP0000｜Assignment 1**", rendered)
        self.assertIn(f"<!-- canvas-ed-mcp:due={marker} -->", rendered)

    def test_semester_report_preserves_manual_notes_attached_to_marker(self) -> None:
        due = item("Assignment 1", datetime(2026, 10, 21, 23, 59, tzinfo=SYDNEY), remote_id="1")
        existing = (
            f"- [ ] **COMP0000｜Assignment 1**\n"
            f"    <!-- canvas-ed-mcp:due={due.stable_marker} -->\n"
            "    - Manual note: submit the cover sheet separately.\n"
        )
        rendered = render_semester_markdown(
            (due,), semester_label="2026 S2", existing_markdown=existing
        )
        self.assertIn("Manual note: submit the cover sheet separately.", rendered)

    def test_semester_report_preserves_completion_with_points_and_weight(self) -> None:
        due = item("Assignment 1", datetime(2026, 10, 21, 23, 59, tzinfo=SYDNEY), remote_id="1", points="40")
        due = DueItem(
            key=due.key, logical_course_key=due.logical_course_key,
            course_label=due.course_label, title=due.title, due_at=due.due_at,
            precision=due.precision, points=due.points, weight_percent=Decimal("20"),
        )
        existing = (
            f"- [x] **COMP0000｜Assignment 1**\n"
            "    - Points: **40**\n"
            "    - Weight: **20%**\n"
            f"    <!-- canvas-ed-mcp:due={due.stable_marker} -->\n"
        )
        rendered = render_semester_markdown((due,), semester_label="2026 S2", existing_markdown=existing)
        self.assertIn("- [x] **COMP0000｜Assignment 1**", rendered)

    def test_semester_report_groups_items_by_teaching_week(self) -> None:
        due = item("Assignment 1", datetime(2026, 8, 12, 23, 59, tzinfo=SYDNEY), remote_id="1")
        rendered = render_semester_markdown(
            (due,),
            semester_label="2026 Semester 2",
            teaching_start=datetime(2026, 8, 3, tzinfo=SYDNEY).date(),
        )
        self.assertIn("### Teaching Week 2", rendered)

    def test_explicit_teaching_period_handles_breaks_without_guessing(self) -> None:
        due = item("Assignment 1", datetime(2026, 9, 14, 23, 59, tzinfo=SYDNEY), remote_id="1")
        rendered = render_semester_markdown(
            (due,),
            semester_label="2026 Semester 2",
            teaching_periods=((
                "Teaching Week 7",
                datetime(2026, 9, 14, tzinfo=SYDNEY).date(),
                datetime(2026, 9, 20, tzinfo=SYDNEY).date(),
            ),),
        )
        self.assertIn("### Teaching Week 7", rendered)

    def test_publish_is_create_only_and_versions_changed_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = publish_versioned_report(root, "this-week-due.txt", "first\n")
            reused = publish_versioned_report(root, "this-week-due.txt", "first\n")
            second = publish_versioned_report(root, "this-week-due.txt", "second\n")
            self.assertEqual(first, reused)
            self.assertEqual("this-week-due--r002.txt", second.name)
            self.assertEqual("first\n", first.read_text(encoding="utf-8"))
            self.assertEqual("second\n", second.read_text(encoding="utf-8"))

    def test_latest_report_uses_numeric_revision_not_lexicographic_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "this-semester-due.md").write_text("base", encoding="utf-8")
            (root / "this-semester-due--r002.md").write_text("second", encoding="utf-8")
            self.assertEqual(
                root / "this-semester-due--r002.md",
                latest_versioned_report(root, "this-semester-due.md"),
            )


if __name__ == "__main__":
    unittest.main()
