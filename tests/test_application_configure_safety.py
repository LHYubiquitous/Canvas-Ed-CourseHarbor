from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from canvas_ed_mcp.application.configure_safety import (
    evaluate_relocation_eligibility,
    load_relocation_eligibility,
)
from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseConfig,
    CourseSource,
    PlatformsConfig,
)
from canvas_ed_mcp.sync.state import InitializedSource, SyncState


def configured(root: Path) -> AppConfig:
    return AppConfig(
        profile="default",
        output_root=root,
        platforms=PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:CANVAS_TOKEN",
            )
        ),
        courses=(
            CourseConfig(
                key="COMP101",
                folder="COMP101",
                confirmed_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
                sources=(
                    CourseSource(
                        provider="canvas",
                        instance="canvas.example.edu",
                        course_id="101",
                    ),
                ),
            ),
        ),
    )


class ConfigureSafetyTests(unittest.TestCase):
    def test_absent_or_empty_folder_without_state_history_is_relocatable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = (Path(temporary) / "Courses").resolve()
            current = configured(root)
            absent = evaluate_relocation_eligibility(current, None)[0]
            (root / "COMP101").mkdir(parents=True)
            empty = evaluate_relocation_eligibility(current, None)[0]

        self.assertTrue(absent.allowed)
        self.assertTrue(empty.allowed)

    def test_nonempty_folder_warns_but_allows_non_destructive_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = (Path(temporary) / "Courses").resolve()
            current = configured(root)
            folder = root / "COMP101"
            folder.mkdir(parents=True)
            (folder / "notes.txt").write_text("existing", encoding="utf-8")

            decision = evaluate_relocation_eligibility(current, None)[0]

        self.assertTrue(decision.allowed)
        self.assertIn("preserved", decision.reason)

    def test_initialized_source_history_warns_but_allows_non_destructive_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = (Path(temporary) / "Courses").resolve()
            current = configured(root)
            state = SyncState(
                profile="default",
                output_root=root,
                sources=(),
                initialized_sources=(
                    InitializedSource(
                        "COMP101", "canvas", "canvas.example.edu", "101"
                    ),
                ),
            )

            decision = evaluate_relocation_eligibility(current, state)[0]

        self.assertTrue(decision.allowed)
        self.assertIn("sync history", decision.reason)

    def test_unreadable_state_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = (Path(temporary) / "Courses").resolve()
            current = configured(root)
            path = Path(temporary) / "state.json"
            path.write_text("not-json", encoding="utf-8")

            decision = load_relocation_eligibility(current, path)[0]

        self.assertFalse(decision.allowed)
        self.assertIn("could not be verified", decision.reason)


if __name__ == "__main__":
    unittest.main()
