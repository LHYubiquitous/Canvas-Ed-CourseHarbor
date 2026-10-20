from __future__ import annotations

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
    CourseConfig,
    CourseSource,
    EdPlatformConfig,
    PlatformsConfig,
)
from canvas_ed_mcp.configure_flow import build_configure_plan
from canvas_ed_mcp.course_mapping import (
    CourseMappingChoice,
    InitialSyncMode,
    MappingSourceChoice,
    SourceIdentity,
    build_mapping_plan,
)
from canvas_ed_mcp.providers.base import CourseCandidate
from canvas_ed_mcp.setup_flow import SetupRequest, build_setup_plan


NOW = datetime(2099, 1, 1, tzinfo=timezone.utc)


def candidates() -> dict[str, tuple[CourseCandidate, ...]]:
    return {
        "canvas": (
            CourseCandidate(
                "canvas", "canvas.invalid", "canvas-1", "SYNTH1000", "Same name", "2099 T1"
            ),
            CourseCandidate(
                "canvas", "canvas.invalid", "canvas-2", "SYNTH1000", "Same name", "2099 T2"
            ),
        ),
        "ed": (
            CourseCandidate(
                "ed", "ed.invalid", "ed-1", "SYNTH1000", "Same name", "2099 T1"
            ),
        ),
    }


def platforms() -> PlatformsConfig:
    return PlatformsConfig(
        canvas=CanvasPlatformConfig(
            enabled=True,
            base_url="https://canvas.invalid/api/v1",
            credential="env:SYNTHETIC_CANVAS_TOKEN",
        ),
        ed=EdPlatformConfig(enabled=True, credential="env:SYNTHETIC_ED_TOKEN"),
    )


class CourseMappingTests(unittest.TestCase):
    def test_same_name_is_not_auto_mapped_and_explicit_mapping_preserves_both_ids(self) -> None:
        empty = build_mapping_plan(candidates(), (), confirmed_at=NOW)
        self.assertEqual((), empty.courses)

        catalog = candidates()
        choice = CourseMappingChoice(
            course_key="logical-synth-1",
            confirmed=True,
            folder="SYNTH1000",
            sources=(
                MappingSourceChoice(
                    catalog["canvas"][0].key,
                    confirmed=True,
                    initial_mode=InitialSyncMode.BASELINE_FROM_NOW,
                ),
                MappingSourceChoice(
                    catalog["ed"][0].key,
                    confirmed=True,
                    initial_mode=InitialSyncMode.IMPORT_ALL,
                ),
            ),
        )
        plan = build_mapping_plan(catalog, (choice,), confirmed_at=NOW)

        self.assertEqual(1, len(plan.courses))
        course = plan.courses[0]
        self.assertEqual("logical-synth-1", course.key)
        self.assertEqual(
            [("canvas", "canvas-1"), ("ed", "ed-1")],
            [(source.provider, source.course_id) for source in course.sources],
        )
        self.assertEqual(
            [InitialSyncMode.BASELINE_FROM_NOW, InitialSyncMode.IMPORT_ALL],
            [source.initial_mode for source in course.sources],
        )
        self.assertEqual(2, len(plan.initial_sync))
        self.assertIn("canvas@canvas.invalid id=canvas-1", plan.preview)
        self.assertIn("ed@ed.invalid id=ed-1", plan.preview)
        self.assertNotIn("SYNTHETIC_ED_TOKEN", plan.preview)

    def test_setup_plan_persists_explicit_both_provider_mapping(self) -> None:
        catalog = candidates()
        mapping = CourseMappingChoice(
            "logical-synth-1",
            (
                MappingSourceChoice(
                    catalog["canvas"][0].key,
                    True,
                    InitialSyncMode.BASELINE_FROM_NOW,
                ),
                MappingSourceChoice(
                    catalog["ed"][0].key,
                    True,
                    InitialSyncMode.BASELINE_FROM_NOW,
                ),
            ),
            confirmed=True,
            folder="SYNTH1000",
        )
        with tempfile.TemporaryDirectory() as temporary:
            request = SetupRequest(
                profile="default",
                output_root=Path(temporary) / "Courses",
                canvas=platforms().canvas,
                ed=platforms().ed,
                mappings=(mapping,),
            )
            setup = build_setup_plan(request, catalog)

        self.assertEqual("logical-synth-1", setup.config.courses[0].key)
        self.assertEqual(2, len(setup.config.courses[0].sources))
        self.assertEqual(2, len(setup.initial_sync))
        self.assertIn("canvas@canvas.invalid id=canvas-1", setup.preview)
        self.assertIn("ed@ed.invalid id=ed-1", setup.preview)

    def test_every_source_and_mapping_requires_explicit_confirmation_and_initial_mode(self) -> None:
        candidate = candidates()["canvas"][0]
        cases = (
            CourseMappingChoice(
                "logical", (MappingSourceChoice(candidate.key, True, InitialSyncMode.BASELINE_FROM_NOW),)
            ),
            CourseMappingChoice(
                "logical",
                (MappingSourceChoice(candidate.key, False, InitialSyncMode.BASELINE_FROM_NOW),),
                confirmed=True,
            ),
            CourseMappingChoice(
                "logical", (MappingSourceChoice(candidate.key, True, None),), confirmed=True
            ),
        )
        for choice in cases:
            with self.subTest(choice=choice):
                with self.assertRaises(ValueError):
                    build_mapping_plan(candidates(), (choice,), confirmed_at=NOW)

    def test_unknown_duplicate_and_multiple_same_provider_sources_fail_closed(self) -> None:
        catalog = candidates()
        canvas_one, canvas_two = catalog["canvas"]
        with self.assertRaises(ValueError):
            build_mapping_plan(
                catalog,
                (
                    CourseMappingChoice(
                        "unknown",
                        (MappingSourceChoice("canvas:canvas.invalid:missing", True, InitialSyncMode.BASELINE_FROM_NOW),),
                        confirmed=True,
                    ),
                ),
                confirmed_at=NOW,
            )
        with self.assertRaises(ValueError):
            build_mapping_plan(
                catalog,
                (
                    CourseMappingChoice(
                        "two-canvas",
                        (
                            MappingSourceChoice(canvas_one.key, True, InitialSyncMode.BASELINE_FROM_NOW),
                            MappingSourceChoice(canvas_two.key, True, InitialSyncMode.BASELINE_FROM_NOW),
                        ),
                        confirmed=True,
                    ),
                ),
                confirmed_at=NOW,
            )
        duplicate = MappingSourceChoice(canvas_one.key, True, InitialSyncMode.BASELINE_FROM_NOW)
        with self.assertRaises(ValueError):
            build_mapping_plan(
                catalog,
                (
                    CourseMappingChoice("one", (duplicate,), confirmed=True),
                    CourseMappingChoice("two", (duplicate,), confirmed=True),
                ),
                confirmed_at=NOW,
            )

    def test_direct_config_construction_enforces_mapping_uniqueness(self) -> None:
        source = CourseSource(provider="canvas", instance="canvas.invalid", course_id="canvas-1")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            with self.assertRaises(ValueError):
                CourseConfig(
                    key="bad",
                    folder="bad",
                    confirmed_at=NOW,
                    sources=(
                        source,
                        CourseSource(
                            provider="canvas", instance="canvas.invalid", course_id="canvas-2"
                        ),
                    ),
                )
            with self.assertRaises(ValueError):
                AppConfig(
                    output_root=output,
                    platforms=platforms(),
                    courses=(
                        CourseConfig(
                            key="one", folder="one", confirmed_at=NOW, sources=(source,)
                        ),
                        CourseConfig(
                            key="two", folder="two", confirmed_at=NOW, sources=(source,)
                        ),
                    ),
                )

    def test_mapping_change_keeps_logical_key_and_requires_mode_for_added_source(self) -> None:
        canvas = CourseSource(
            provider="canvas", instance="canvas.invalid", course_id="canvas-1"
        )
        ed = CourseSource(provider="ed", instance="ed.invalid", course_id="ed-1")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            current_course = CourseConfig(
                key="logical-synth-1",
                folder="SYNTH1000",
                confirmed_at=NOW,
                sources=(canvas,),
            )
            proposed_course = current_course.model_copy(
                update={"sources": (canvas, ed), "confirmed_at": NOW}
            )
            current = AppConfig(
                output_root=output, platforms=platforms(), courses=(current_course,)
            )
            proposed = AppConfig(
                output_root=output, platforms=platforms(), courses=(proposed_course,)
            )
            with self.assertRaises(ValueError):
                build_configure_plan(current, proposed, new_source_modes={})
            plan = build_configure_plan(
                current,
                proposed,
                new_source_modes={
                    SourceIdentity("ed", "ed.invalid", "ed-1"): InitialSyncMode.BASELINE_FROM_NOW
                },
            )

        self.assertEqual(("logical-synth-1",), plan.changed_keys)
        self.assertEqual("logical-synth-1", plan.config.courses[0].key)
        self.assertEqual(1, len(plan.initial_sync))
        self.assertIn(
            "+ ed@ed.invalid id=ed-1 [initial=baseline_from_now]", plan.preview
        )

    def test_moving_existing_source_between_logical_courses_requires_migration(self) -> None:
        source = CourseSource(provider="canvas", instance="canvas.invalid", course_id="canvas-1")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            current = AppConfig(
                output_root=output,
                platforms=platforms(),
                courses=(CourseConfig(key="one", folder="one", confirmed_at=NOW, sources=(source,)),),
            )
            proposed = AppConfig(
                output_root=output,
                platforms=platforms(),
                courses=(CourseConfig(key="two", folder="two", confirmed_at=NOW, sources=(source,)),),
            )
            with self.assertRaisesRegex(ValueError, "migration"):
                build_configure_plan(
                    current,
                    proposed,
                    new_source_modes={},
                )

    def test_detached_source_cannot_be_reattached_in_a_later_configure(self) -> None:
        source = CourseSource(provider="canvas", instance="canvas.invalid", course_id="canvas-1")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            current = AppConfig(
                output_root=output,
                platforms=platforms(),
                courses=(CourseConfig(key="one", folder="one", confirmed_at=NOW, sources=(source,)),),
            )
            empty = AppConfig(output_root=output, platforms=platforms(), courses=())
            detach = build_configure_plan(current, empty, new_source_modes={})
            self.assertEqual(1, len(detach.config.retired_sources))
            proposed = AppConfig(
                output_root=output,
                platforms=platforms(),
                courses=(CourseConfig(key="two", folder="two", confirmed_at=NOW, sources=(source,)),),
            )
            with self.assertRaisesRegex(ValueError, "migration"):
                build_configure_plan(
                    detach.config,
                    proposed,
                    new_source_modes={
                        SourceIdentity("canvas", "canvas.invalid", "canvas-1"):
                        InitialSyncMode.BASELINE_FROM_NOW
                    },
                )

    def test_instance_aliases_and_preview_control_characters_fail_closed(self) -> None:
        for invalid_instance in (
            "canvas.invalid:443",
            "canvas.invalid/path",
            "canvas.invalid:bad",
            "canvas.invalid:70000",
        ):
            with self.subTest(instance=invalid_instance):
                with self.assertRaises(ValueError):
                    CourseSource(
                        provider="canvas",
                        instance=invalid_instance,
                        course_id="canvas-1",
                    )
                with self.assertRaises(ValueError):
                    SourceIdentity("canvas", invalid_instance, "canvas-1")

        source = CourseSource(provider="canvas", instance="canvas.invalid", course_id="canvas-1")
        for field, value in (("key", "safe\n- forged"), ("folder", "safe\nforged"), ("folder", "CON")):
            values = {
                "key": "safe",
                "folder": "safe",
                "confirmed_at": NOW,
                "sources": (source,),
            }
            values[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    CourseConfig(**values)


if __name__ == "__main__":
    unittest.main()
