from __future__ import annotations

import tempfile
import unittest
import json
from datetime import datetime, timezone
from pathlib import Path

from canvas_ed_mcp.config import (
    AppConfig,
    CourseConfig,
    CourseSource,
    DailyUseConfig,
    DueReportConfig,
    GradescopeDueMapping,
    GradescopePlatformConfig,
    PlatformsConfig,
)
from canvas_ed_mcp.due.model import (
    DueBatch,
    DueCoverageStatus,
    DueIssue,
    DueItem,
    DuePrecision,
    DueSourceKey,
)
from canvas_ed_mcp.due.service import DueStateError, collect_configured_due_items


def _item(title: str) -> DueItem:
    return DueItem(
        key=DueSourceKey("canvas", "canvas.example.edu", "42", "assignment", title),
        logical_course_key="course-42",
        course_label="COMP0000",
        title=title,
        due_at=datetime(2026, 10, 21, 12, tzinfo=timezone.utc),
        precision=DuePrecision.DATETIME,
    )


class _Provider:
    def __init__(self, batch: DueBatch) -> None:
        self.batch = batch

    def list_due_items(self, course_id: str, *, logical_course_key: str, course_label: str) -> DueBatch:
        return self.batch


class DueServiceTests(unittest.TestCase):
    @staticmethod
    def _canvas_config(root: Path, *, due: DueReportConfig | None = None) -> AppConfig:
        from canvas_ed_mcp.config import CanvasPlatformConfig

        return AppConfig(
            profile="daily", output_root=root,
            platforms=PlatformsConfig(canvas=CanvasPlatformConfig(
                enabled=True, base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_TOKEN",
            )),
            courses=(CourseConfig(
                key="course-42", folder="COMP0000",
                confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                sources=(CourseSource(provider="canvas", instance="canvas.example.edu", course_id="42", code="COMP0000"),),
            ),),
            daily_use=DailyUseConfig(due=due or DueReportConfig()),
        )

    def test_explicit_outline_exact_title_overrides_remote_date(self) -> None:
        from canvas_ed_mcp.config import CanvasPlatformConfig

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outline = root / "reviewed.md"
            outline.write_text(
                "#### October 25 (Sun) 23:59\n- [ ] **COMP0000｜Original**\n",
                encoding="utf-8",
            )
            config = AppConfig(
                profile="daily",
                output_root=root,
                platforms=PlatformsConfig(
                    canvas=CanvasPlatformConfig(enabled=True, base_url="https://canvas.example.edu/api/v1", credential="env:SYNTHETIC_TOKEN")
                ),
                courses=(CourseConfig(
                    key="course-42", folder="COMP0000",
                    confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    sources=(CourseSource(provider="canvas", instance="canvas.example.edu", course_id="42", code="COMP0000"),),
                ),),
                daily_use=DailyUseConfig(
                    due=DueReportConfig(
                        semester_label="2026 Semester 2",
                        unit_outline_files=(outline,),
                        outline_title_overrides=(("course-42", "Original"),),
                    )
                ),
            )
            remote = DueBatch("canvas", "canvas.example.edu", "42", DueCoverageStatus.COMPLETE, (_item("Original"),), ())
            result = collect_configured_due_items(
                config,
                {("canvas", "canvas.example.edu"): _Provider(remote)},
                root / "due-state.json",
            )
            self.assertEqual(1, len(result.items))
            self.assertEqual("unit-outline", result.items[0].key.provider)
            self.assertEqual(25, result.items[0].due_at.day)

    def test_partial_refresh_uses_last_complete_source_snapshot(self) -> None:
        from canvas_ed_mcp.config import CanvasPlatformConfig

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(
                profile="daily",
                output_root=root,
                platforms=PlatformsConfig(
                    canvas=CanvasPlatformConfig(
                        enabled=True,
                        base_url="https://canvas.example.edu/api/v1",
                        credential="env:SYNTHETIC_TOKEN",
                    )
                ),
                courses=(
                    CourseConfig(
                        key="course-42",
                        folder="COMP0000",
                        confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        sources=(CourseSource(provider="canvas", instance="canvas.example.edu", course_id="42", code="COMP0000"),),
                    ),
                ),
            )
            state = root / "due-state.json"
            complete = DueBatch("canvas", "canvas.example.edu", "42", DueCoverageStatus.COMPLETE, (_item("Original"),), ())
            first = collect_configured_due_items(config, {("canvas", "canvas.example.edu"): _Provider(complete)}, state)
            self.assertEqual(("Original",), tuple(item.title for item in first.items))

            partial = DueBatch(
                "canvas", "canvas.example.edu", "42", DueCoverageStatus.PARTIAL,
                (_item("Incomplete replacement"),),
                (DueIssue("item_schema_drift"),),
            )
            second = collect_configured_due_items(config, {("canvas", "canvas.example.edu"): _Provider(partial)}, state)
            self.assertEqual(("Original",), tuple(item.title for item in second.items))
            self.assertTrue(second.used_last_good)
            self.assertTrue(second.publishable)
            self.assertEqual(state.read_bytes(), first.state_bytes)

    def test_required_remote_source_without_last_good_blocks_publication(self) -> None:
        from canvas_ed_mcp.config import CanvasPlatformConfig

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(
                profile="daily", output_root=root,
                platforms=PlatformsConfig(canvas=CanvasPlatformConfig(
                    enabled=True, base_url="https://canvas.example.edu/api/v1",
                    credential="env:SYNTHETIC_TOKEN",
                )),
                courses=(CourseConfig(
                    key="course-42", folder="COMP0000",
                    confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    sources=(CourseSource(provider="canvas", instance="canvas.example.edu", course_id="42", code="COMP0000"),),
                ),),
            )
            partial = DueBatch(
                "canvas", "canvas.example.edu", "42", DueCoverageStatus.PARTIAL,
                (_item("Incomplete"),), (DueIssue("item_schema_drift"),),
            )
            result = collect_configured_due_items(
                config, {("canvas", "canvas.example.edu"): _Provider(partial)},
                root / "due-state.json",
            )
            self.assertFalse(result.publishable)

    def test_unavailable_provider_uses_last_good_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._canvas_config(root)
            state = root / "due-state.json"
            complete = DueBatch("canvas", "canvas.example.edu", "42", DueCoverageStatus.COMPLETE, (_item("Cached"),), ())
            collect_configured_due_items(config, {("canvas", "canvas.example.edu"): _Provider(complete)}, state)
            result = collect_configured_due_items(config, {}, state)
            self.assertTrue(result.publishable)
            self.assertTrue(result.used_last_good)
            self.assertEqual(("Cached",), tuple(item.title for item in result.items))
            self.assertEqual("source_request_failed", result.issues[0].code)

    def test_optional_gradescope_failure_does_not_block_canvas_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self._canvas_config(root)
            config = base.model_copy(update={
                "platforms": base.platforms.model_copy(update={"gradescope": GradescopePlatformConfig(enabled=True)}),
                "daily_use": DailyUseConfig(due=DueReportConfig(gradescope_courses=(
                    GradescopeDueMapping(logical_course_key="course-42", course_id="901"),
                ))),
            })
            complete = DueBatch("canvas", "canvas.example.edu", "42", DueCoverageStatus.COMPLETE, (_item("Canvas"),), ())
            result = collect_configured_due_items(config, {("canvas", "canvas.example.edu"): _Provider(complete)}, root / "due-state.json")
            self.assertTrue(result.publishable)
            self.assertEqual(("Canvas",), tuple(item.title for item in result.items))

    def test_required_gradescope_failure_without_snapshot_blocks_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = self._canvas_config(root)
            config = base.model_copy(update={
                "platforms": base.platforms.model_copy(update={"gradescope": GradescopePlatformConfig(enabled=True)}),
                "daily_use": DailyUseConfig(due=DueReportConfig(gradescope_courses=(
                    GradescopeDueMapping(logical_course_key="course-42", course_id="901", required=True),
                ))),
            })
            complete = DueBatch("canvas", "canvas.example.edu", "42", DueCoverageStatus.COMPLETE, (_item("Canvas"),), ())
            result = collect_configured_due_items(config, {("canvas", "canvas.example.edu"): _Provider(complete)}, root / "due-state.json")
            self.assertFalse(result.publishable)

    def test_required_outline_failure_without_snapshot_blocks_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            missing = (root / "missing.md").resolve()
            config = self._canvas_config(root, due=DueReportConfig(
                unit_outline_files=(missing,), required_outline_files=(missing,)
            ))
            complete = DueBatch("canvas", "canvas.example.edu", "42", DueCoverageStatus.COMPLETE, (_item("Canvas"),), ())
            result = collect_configured_due_items(config, {("canvas", "canvas.example.edu"): _Provider(complete)}, root / "due-state.json")
            self.assertFalse(result.publishable)

    def test_corrupt_state_field_types_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "due-state.json"
            item = {
                "key": {"provider": "canvas", "instance": "canvas.example.edu", "course_id": "42", "category": "assignment", "remote_id": "1"},
                "logical_course_key": "course-42", "course_label": "COMP0000", "title": "Bad",
                "due_at": "2026-10-21T12:00:00+00:00", "due_kind": "anything",
                "precision": "datetime", "points": 40, "weight_percent": None,
            }
            state.write_text(json.dumps({"schema_version": 1, "profile": "daily", "sources": {"canvas|canvas.example.edu|42": [item]}}), encoding="utf-8")
            with self.assertRaises(DueStateError):
                collect_configured_due_items(self._canvas_config(root), {}, state)

    def test_state_root_and_source_ownership_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "due-state.json"
            valid_item = {
                "key": {"provider": "canvas", "instance": "canvas.example.edu", "course_id": "42", "category": "assignment", "remote_id": "1"},
                "logical_course_key": "course-42", "course_label": "COMP0000", "title": "Cached",
                "due_at": "2026-10-21T12:00:00+00:00", "due_kind": "datetime",
                "precision": "datetime", "points": None, "weight_percent": None,
            }
            cases = (
                {"schema_version": True, "profile": "daily", "sources": {}},
                {"schema_version": 1, "profile": "daily", "sources": {}, "extra": True},
                {"schema_version": 1, "profile": "daily", "sources": {
                    "canvas|canvas.example.edu|42": [{**valid_item, "key": {**valid_item["key"], "provider": "ed", "instance": "edstem.org", "course_id": "999"}}]
                }},
            )
            for payload in cases:
                with self.subTest(payload=payload):
                    state.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaises(DueStateError):
                        collect_configured_due_items(self._canvas_config(root), {}, state)

    def test_cached_item_must_belong_to_configured_logical_course(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self._canvas_config(root)
            state = root / "due-state.json"
            complete = DueBatch("canvas", "canvas.example.edu", "42", DueCoverageStatus.COMPLETE, (_item("Cached"),), ())
            collect_configured_due_items(config, {("canvas", "canvas.example.edu"): _Provider(complete)}, state)
            payload = json.loads(state.read_text(encoding="utf-8"))
            payload["sources"]["canvas|canvas.example.edu|42"][0]["logical_course_key"] = "another-course"
            state.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(DueStateError):
                collect_configured_due_items(config, {}, state)


if __name__ == "__main__":
    unittest.main()
