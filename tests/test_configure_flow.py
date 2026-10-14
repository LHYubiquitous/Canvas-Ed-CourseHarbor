from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    ContentSelection,
    CourseConfig,
    CourseSource,
    DownloadPolicy,
    PlatformsConfig,
    load_config,
    save_config,
)
from canvas_ed_mcp.configure_flow import (
    ConfigureChangeKind,
    ConfigureConflictError,
    InitialSyncMode,
    build_configure_plan,
    commit_configure,
)


CONFIRMED_AT = datetime(2026, 10, 13, tzinfo=timezone.utc)


def course(
    course_id: str,
    *,
    folder: str | None = None,
    content: ContentSelection | None = None,
    policy: DownloadPolicy | None = None,
    confirmed_at: datetime = CONFIRMED_AT,
) -> CourseConfig:
    return CourseConfig(
        key=f"canvas:canvas.example.edu:{course_id}",
        folder=folder or f"TEST{course_id}",
        confirmed_at=confirmed_at,
        sources=(
            CourseSource(
                provider="canvas",
                instance="canvas.example.edu",
                course_id=course_id,
                code=f"TEST{course_id}",
                name=f"Synthetic Course {course_id}",
                term="2026 S2",
            ),
        ),
        content=content or ContentSelection(),
        policy=policy or DownloadPolicy(),
    )


def config(output_root: Path, courses: tuple[CourseConfig, ...]) -> AppConfig:
    return AppConfig(
        profile="default",
        output_root=output_root,
        platforms=PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_CANVAS_TOKEN",
            )
        ),
        courses=courses,
    )


class ConfigurePlanningTests(unittest.TestCase):
    def test_add_remove_change_diff_is_deterministic_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            unchanged = course("101")
            removed = course("202")
            current = config(output, (unchanged, removed, course("303")))
            changed = course(
                "303",
                folder="RENAMED303",
                policy=DownloadPolicy(max_file_bytes=2048),
                confirmed_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
            )
            added = course("404")
            proposed = config(output, (added, changed, unchanged))

            plan = build_configure_plan(
                current,
                proposed,
                new_course_modes={added.key: InitialSyncMode.BASELINE_FROM_NOW},
            )

        self.assertEqual(
            (
                ConfigureChangeKind.ADD,
                ConfigureChangeKind.REMOVE,
                ConfigureChangeKind.CHANGE,
            ),
            tuple(change.kind for change in plan.changes),
        )
        self.assertEqual((added.key, removed.key, changed.key), plan.changed_keys)
        self.assertEqual(("folder", "policy"), plan.changes[2].fields)
        self.assertEqual(InitialSyncMode.BASELINE_FROM_NOW, plan.initial_sync[0].mode)
        self.assertIn(f"+ {added.key}", plan.preview)
        self.assertIn(f"- {removed.key}", plan.preview)
        self.assertIn("local files and historical state retained", plan.preview)
        self.assertIn("folder, policy", plan.preview)
        self.assertNotIn("SYNTHETIC_CANVAS_TOKEN", plan.preview)

    def test_multiple_added_courses_require_individual_initial_sync_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            current = config(output, ())
            first = course("101")
            second = course("202")
            proposed = config(output, (first, second))

            plan = build_configure_plan(
                current,
                proposed,
                new_course_modes={
                    first.key: InitialSyncMode.BASELINE_FROM_NOW,
                    second.key: InitialSyncMode.IMPORT_ALL,
                },
            )
            self.assertEqual(
                (
                    (first.key, InitialSyncMode.BASELINE_FROM_NOW),
                    (second.key, InitialSyncMode.IMPORT_ALL),
                ),
                tuple((item.course_key, item.mode) for item in plan.initial_sync),
            )

            for modes in (
                {},
                {first.key: InitialSyncMode.BASELINE_FROM_NOW},
                {
                    first.key: InitialSyncMode.BASELINE_FROM_NOW,
                    second.key: InitialSyncMode.IMPORT_ALL,
                    "unknown": InitialSyncMode.BASELINE_FROM_NOW,
                },
            ):
                with self.subTest(modes=modes), self.assertRaises(ValueError):
                    build_configure_plan(
                        current,
                        proposed,
                        new_course_modes=modes,
                    )

    def test_initial_sync_mode_must_be_typed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            added = course("101")
            with self.assertRaises(ValueError):
                build_configure_plan(
                    config(output, ()),
                    config(output, (added,)),
                    new_course_modes={added.key: "baseline_from_now"},  # type: ignore[dict-item]
                )

    def test_noop_preserves_existing_course_and_confirmation_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            existing = course("101")
            reconfirmed = course(
                "101",
                confirmed_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
            )
            current = config(output, (existing,))
            proposed = config(output, (reconfirmed,))
            plan = build_configure_plan(current, proposed, new_course_modes={})

        self.assertFalse(plan.has_changes)
        self.assertEqual((), plan.changes)
        self.assertEqual(existing, plan.config.courses[0])
        self.assertIn("No operational changes", plan.preview)

    def test_platform_change_is_reported_without_exposing_credential_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            current = config(output, ())
            proposed = current.model_copy(
                update={
                    "platforms": PlatformsConfig(
                        canvas=CanvasPlatformConfig(
                            enabled=True,
                            base_url="https://canvas.other.example/api/v1",
                            credential="keyring:canvas-ed-mcp/default/canvas-private",
                        )
                    )
                }
            )
            plan = build_configure_plan(current, proposed, new_course_modes={})

        self.assertTrue(plan.platforms_changed)
        self.assertTrue(plan.has_changes)
        self.assertIn("Platform settings: changed", plan.preview)
        self.assertNotIn("canvas-private", plan.preview)

    def test_profile_or_output_root_change_requires_separate_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            current = config(base / "Courses", ())
            cases = (
                current.model_copy(update={"profile": "other"}),
                current.model_copy(update={"output_root": base / "Other"}),
            )
            for proposed in cases:
                with self.subTest(proposed=proposed), self.assertRaises(ValueError):
                    build_configure_plan(current, proposed, new_course_modes={})

    def test_change_records_are_immutable_and_well_formed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            added = course("101")
            plan = build_configure_plan(
                config(output, ()),
                config(output, (added,)),
                new_course_modes={added.key: InitialSyncMode.BASELINE_FROM_NOW},
            )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            plan.changes[0].fields = ("folder",)  # type: ignore[misc]


class ConfigureCommitTests(unittest.TestCase):
    def test_cancel_and_commit_never_touch_course_output_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            removed = course("101")
            current = config(output, (removed,))
            proposed = config(output, ())
            config_path = base / "config" / "default.toml"
            state_path = base / "state" / "state-v4.json"
            course_file = output / removed.folder / "notes.txt"
            state_path.parent.mkdir(parents=True)
            course_file.parent.mkdir(parents=True)
            state_path.write_bytes(b"historical-state")
            course_file.write_bytes(b"user-course-material")
            save_config(current, config_path)
            original_config = config_path.read_bytes()
            plan = build_configure_plan(current, proposed, new_course_modes={})

            self.assertFalse(commit_configure(plan, config_path, confirmed=False))
            self.assertEqual(original_config, config_path.read_bytes())
            self.assertEqual(b"historical-state", state_path.read_bytes())
            self.assertEqual(b"user-course-material", course_file.read_bytes())

            self.assertTrue(commit_configure(plan, config_path, confirmed=True))
            committed = load_config(config_path)
            self.assertEqual((), committed.courses)
            self.assertEqual(1, len(committed.retired_sources))
            self.assertEqual(removed.key, committed.retired_sources[0].former_course_key)
            self.assertEqual(removed.sources[0], committed.retired_sources[0].source)
            self.assertEqual(b"historical-state", state_path.read_bytes())
            self.assertEqual(b"user-course-material", course_file.read_bytes())

    def test_commit_rejects_config_changed_after_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            current = config(output, (course("101"),))
            proposed = config(output, ())
            path = base / "config.toml"
            save_config(current, path)
            plan = build_configure_plan(current, proposed, new_course_modes={})
            concurrent = current.model_copy(
                update={
                    "platforms": PlatformsConfig(
                        canvas=CanvasPlatformConfig(
                            enabled=True,
                            base_url="https://canvas.other.example/api/v1",
                            credential="env:SYNTHETIC_OTHER_TOKEN",
                        )
                    )
                }
            )
            save_config(concurrent, path)

            with self.assertRaises(ConfigureConflictError):
                commit_configure(plan, path, confirmed=True)
            self.assertEqual(concurrent, load_config(path))


if __name__ == "__main__":
    unittest.main()
