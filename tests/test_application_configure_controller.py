from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from canvas_ed_mcp.application.configure_controller import ConfigureController
from canvas_ed_mcp.application.configure_workflow import ConfigOnlyConfigureSaver
from canvas_ed_mcp.application.configure_safety import SourceRelocationEligibility
from canvas_ed_mcp.application.contracts import OperationStatus
from canvas_ed_mcp.application.setup_controller import SetupStep
from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    ContentSelection,
    CourseConfig,
    CourseSource,
    EdPlatformConfig,
    PlatformsConfig,
    load_config,
    save_config,
)
from canvas_ed_mcp.course_mapping import SourceIdentity
from canvas_ed_mcp.providers.base import CourseCandidate
from canvas_ed_mcp.initial import InitialSyncMode


def fixture(root: Path, *, allowed: bool) -> tuple[AppConfig, dict[str, tuple[CourseCandidate, ...]], tuple[SourceRelocationEligibility, ...]]:
    canvas = CourseCandidate(
        "canvas", "canvas.example.edu", "101", "COMP101", "Computing", "2026-S2"
    )
    ed = CourseCandidate(
        "ed", "edstem.org", "202", "COMP101", "Computing", "2026-S2"
    )
    sources = (
        CourseSource(
            provider="canvas", instance=canvas.instance, course_id=canvas.course_id,
            code=canvas.code, name=canvas.name, term=canvas.term,
        ),
        CourseSource(
            provider="ed", instance=ed.instance, course_id=ed.course_id,
            code=ed.code, name=ed.name, term=ed.term,
        ),
    )
    current = AppConfig(
        profile="default",
        output_root=root,
        platforms=PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:CANVAS_TOKEN",
            ),
            ed=EdPlatformConfig(enabled=True, credential="env:ED_TOKEN"),
        ),
        courses=(
            CourseConfig(
                key="COMP101-2026-S2",
                folder="COMP101",
                confirmed_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
                sources=sources,
                content=ContentSelection(resources=False),
            ),
        ),
    )
    reason = "No sync history or local course files were found." if allowed else "This source has sync history, so changing its course mapping requires file migration."
    eligibility = tuple(
        SourceRelocationEligibility(SourceIdentity.from_source(source), allowed, reason)
        for source in sources
    )
    return current, {"canvas": (canvas,), "ed": (ed,)}, eligibility


class ConfigureControllerTests(unittest.TestCase):
    def test_all_courses_can_be_deactivated_and_later_one_can_be_reactivated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = (Path(temporary) / "Courses").resolve()
            current, catalogs, eligibility = fixture(root, allowed=False)
            controller = ConfigureController(
                current=current, catalogs=catalogs, eligibility=eligibility
            )
            for candidate in (*catalogs["canvas"], *catalogs["ed"]):
                controller.deselect_course(candidate.key)
            controller.open_mapping()
            controller.open_folders()
            deactivated = controller.build_review().plan.config

            reopened = ConfigureController(
                current=deactivated,
                catalogs={"canvas": (), "ed": ()},
                eligibility=(),
            )
            canvas_row = next(row for row in reopened.visible_rows if row.provider == "canvas")
            reopened.select_course(canvas_row.candidate_key)
            reopened.open_mapping()
            reopened.open_folders()
            restored = reopened.build_review().plan.config

        self.assertEqual((), deactivated.courses)
        self.assertEqual(2, len(deactivated.retired_sources))
        self.assertEqual(1, len(restored.courses))
        self.assertEqual(1, restored.courses[0].sources[0].reconcile_generation)
        self.assertEqual(1, len(restored.retired_sources))

    def test_existing_selection_mapping_folder_and_content_are_seeded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current, catalogs, eligibility = fixture(
                (Path(temporary) / "Courses").resolve(), allowed=False
            )
            controller = ConfigureController(
                current=current, catalogs=catalogs, eligibility=eligibility
            )

            self.assertEqual(2, controller.selected_count)
            self.assertEqual("COMP101-2026-S2", controller.logical_groups[0].logical_key)
            choice = controller.mapping_choices[0]
            self.assertEqual("COMP101", choice.folder)
            self.assertFalse(choice.content.resources)
            self.assertEqual(1, len(controller.linked_matches))
            self.assertIsNone(
                controller.match_lock_reason(controller.linked_matches[0].match_id)
            )

    def test_opening_and_reviewing_without_edits_is_a_true_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current, catalogs, eligibility = fixture(
                (Path(temporary) / "Courses").resolve(), allowed=False
            )
            catalogs = {
                provider: tuple(
                    CourseCandidate(
                        item.provider,
                        item.instance,
                        item.course_id,
                        item.code,
                        "Provider renamed this course",
                        item.term,
                    )
                    for item in courses
                )
                for provider, courses in catalogs.items()
            }
            controller = ConfigureController(
                current=current, catalogs=catalogs, eligibility=eligibility
            )

            controller.open_mapping()
            controller.open_folders()
            review = controller.build_review()

        self.assertFalse(review.plan.has_changes)
        self.assertEqual(current, review.plan.config)

    def test_initialized_mapping_can_be_unlinked_for_non_destructive_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current, catalogs, eligibility = fixture(
                (Path(temporary) / "Courses").resolve(), allowed=False
            )
            controller = ConfigureController(
                current=current, catalogs=catalogs, eligibility=eligibility
            )

            controller.undo_link(controller.linked_matches[0].match_id)
            self.assertEqual(1, len(controller.pending_matches))

    def test_one_initialized_source_can_be_removed_without_rekeying_the_remaining_course(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current, catalogs, eligibility = fixture(
                (Path(temporary) / "Courses").resolve(), allowed=False
            )
            controller = ConfigureController(
                current=current, catalogs=catalogs, eligibility=eligibility
            )

            controller.deselect_course(catalogs["ed"][0].key)
            controller.open_mapping()
            controller.open_folders()
            review = controller.build_review()

        self.assertEqual("COMP101-2026-S2", review.plan.config.courses[0].key)
        self.assertEqual(("canvas",), tuple(
            source.provider for source in review.plan.config.courses[0].sources
        ))
        self.assertEqual(("sources", "content"), review.plan.changes[0].fields)

    def test_uninitialized_empty_mapping_can_be_unlinked_and_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current, catalogs, eligibility = fixture(
                (Path(temporary) / "Courses").resolve(), allowed=True
            )
            controller = ConfigureController(
                current=current, catalogs=catalogs, eligibility=eligibility
            )
            match = controller.linked_matches[0]

            controller.open_mapping()
            controller.undo_link(match.match_id)
            controller.keep_separate(match.match_id)
            controller.open_folders()
            review = controller.build_review()

        self.assertEqual(SetupStep.REVIEW, controller.step)
        self.assertTrue(review.plan.has_changes)
        self.assertIn("Removed courses", review.plan.preview)
        self.assertIn("Added courses", review.plan.preview)

    def test_new_source_mode_is_editable_without_changing_existing_source_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current, catalogs, eligibility = fixture(
                (Path(temporary) / "Courses").resolve(), allowed=False
            )
            current = current.model_copy(
                update={
                    "courses": (
                        current.courses[0].model_copy(
                            update={"sources": current.courses[0].sources[:1]}
                        ),
                    )
                }
            )
            controller = ConfigureController(
                current=current,
                catalogs=catalogs,
                eligibility=eligibility[:1],
            )

            controller.select_course(catalogs["ed"][0].key)
            controller.open_mapping()
            match = controller.pending_matches[0]
            controller.link_match(match.match_id, logical_key="COMP101-2026-S2")
            self.assertTrue(controller.initial_mode_editable("COMP101-2026-S2"))
            controller.set_course_initial_mode(
                "COMP101-2026-S2", InitialSyncMode.IMPORT_ALL
            )
            controller.open_folders()
            review = controller.build_review()

        modes = tuple(source.initial_mode for source in review.plan.config.courses[0].sources)
        self.assertEqual(
            (InitialSyncMode.BASELINE_FROM_NOW, InitialSyncMode.IMPORT_ALL),
            modes,
        )

    def test_existing_folder_is_read_only_but_initial_mode_can_be_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            current, catalogs, eligibility = fixture(
                (Path(temporary) / "Courses").resolve(), allowed=True
            )
            controller = ConfigureController(
                current=current, catalogs=catalogs, eligibility=eligibility
            )

            with self.assertRaisesRegex(ValueError, "folder names are fixed"):
                controller.set_folder_overrides({"COMP101-2026-S2": "OTHER"})
            controller.set_course_initial_mode(
                "COMP101-2026-S2", InitialSyncMode.IMPORT_ALL
            )
            controller.open_mapping()
            controller.open_folders()
            review = controller.build_review()

        self.assertEqual(
            (1, 1),
            tuple(source.reconcile_generation for source in review.plan.config.courses[0].sources),
        )

    def test_reviewed_change_saves_only_config_and_only_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            current, catalogs, eligibility = fixture(
                (base / "Courses").resolve(), allowed=True
            )
            path = base / "profile.toml"
            state_path = base / "state-v5.json"
            save_config(current, path)
            controller = ConfigureController(
                current=current, catalogs=catalogs, eligibility=eligibility
            )
            match = controller.linked_matches[0]
            controller.open_mapping()
            controller.undo_link(match.match_id)
            controller.keep_separate(match.match_id)
            controller.open_folders()
            review = controller.build_review()
            saver = ConfigOnlyConfigureSaver(controller, path, state_path)

            result = saver.save(review)
            repeated = saver.save(review)

            self.assertEqual(OperationStatus.SUCCEEDED, result.status)
            self.assertEqual(review.plan.config, load_config(path))
            self.assertFalse(current.output_root.exists())
            self.assertFalse(state_path.exists())
            self.assertEqual(OperationStatus.CONFLICT, repeated.status)

    def test_save_preserves_newly_arrived_file_during_non_destructive_remap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            current, catalogs, eligibility = fixture(
                (base / "Courses").resolve(), allowed=True
            )
            path = base / "profile.toml"
            state_path = base / "state-v5.json"
            save_config(current, path)
            controller = ConfigureController(
                current=current, catalogs=catalogs, eligibility=eligibility
            )
            match = controller.linked_matches[0]
            controller.open_mapping()
            controller.undo_link(match.match_id)
            controller.keep_separate(match.match_id)
            controller.open_folders()
            review = controller.build_review()
            saver = ConfigOnlyConfigureSaver(controller, path, state_path)
            folder = current.output_root / current.courses[0].folder
            folder.mkdir(parents=True)
            (folder / "arrived-after-preview.txt").write_text(
                "existing", encoding="utf-8"
            )

            result = saver.save(review)

            self.assertEqual(OperationStatus.SUCCEEDED, result.status)
            self.assertTrue((folder / "arrived-after-preview.txt").is_file())
            self.assertNotEqual(current, load_config(path))


if __name__ == "__main__":
    unittest.main()
