from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from canvas_ed_mcp.application.contracts import OperationStatus
from canvas_ed_mcp.application.daily_sync import (
    SyncApplyResult,
    SyncItemSummary,
    SyncSourceSummary,
)
from canvas_ed_mcp.application.sync_reports import SyncReportStore
from canvas_ed_mcp.paths import ProfilePaths


def _paths(root: Path) -> ProfilePaths:
    return ProfilePaths(
        profile="test",
        config_file=root / "config.toml",
        state_file=root / "state" / "state-v4.json",
        state_backup_file=root / "state" / "state-v4.backup.json",
        lock_dir=root / "locks",
        log_dir=root / "logs",
        cache_dir=root / "cache",
    )


class SyncReportStoreTests(unittest.TestCase):
    def test_records_changed_files_counts_and_course_destination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = _paths(root)
            library = root / "library"
            library.mkdir()
            course_folder = library / "COMP0001"
            changed = course_folder / "Resources" / "notes.pdf"
            result = SyncApplyResult(
                OperationStatus.SUCCEEDED,
                sources=(
                    SyncSourceSummary(
                        course_key="course-1",
                        provider="ed",
                        instance="ed.example",
                        course_id="1",
                        actions=("install_new", "skip"),
                        course_folder="COMP0001",
                        course_code="COMP0001",
                        course_name="Example course",
                        items=(
                            SyncItemSummary(
                                "notes.pdf",
                                "resource",
                                "install_new",
                                "new_remote_item",
                                changed,
                            ),
                            SyncItemSummary(
                                "unchanged.pdf",
                                "resource",
                                "skip",
                                "unchanged",
                                course_folder / "Resources" / "unchanged.pdf",
                            ),
                        ),
                    ),
                ),
            )
            store = SyncReportStore(
                "test",
                paths_resolver=lambda profile: paths,
                clock=lambda: datetime(2026, 10, 25, 22, 30, tzinfo=timezone.utc),
            )

            recorded = store.record(result, output_root=library)
            loaded = store.load()

            self.assertEqual((recorded,), loaded)
            self.assertEqual(OperationStatus.SUCCEEDED, recorded.status)
            self.assertEqual(1, len(recorded.courses))
            course = recorded.courses[0]
            self.assertEqual(course_folder, course.destination)
            self.assertEqual((("install_new", 1), ("skip", 1)), course.action_counts)
            self.assertEqual(1, len(course.changed_items))
            self.assertEqual("notes.pdf", course.changed_items[0].display_name)
            self.assertEqual(changed, course.changed_items[0].destination)
            self.assertTrue(store.path.is_file())

    def test_malformed_action_counts_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = _paths(root)
            store = SyncReportStore("test", paths_resolver=lambda profile: paths)
            store.path.parent.mkdir(parents=True)
            store.path.write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "reports": [
                            {
                                "run_id": "broken",
                                "recorded_at": "2026-10-25T22:30:00+00:00",
                                "status": "succeeded",
                                "courses": [
                                    {
                                        "course_key": "course-1",
                                        "display_name": "Example course",
                                        "destination": str(root / "library"),
                                        "sources": [],
                                        "action_counts": [[]],
                                        "changed_items": [],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual((), store.load())


if __name__ == "__main__":
    unittest.main()
