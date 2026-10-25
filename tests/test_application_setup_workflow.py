from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from canvas_ed_mcp.application.contracts import OperationStatus
from canvas_ed_mcp.application.setup_controller import SetupController
from canvas_ed_mcp.application.setup_session import SetupReview
from canvas_ed_mcp.application.setup_workflow import ConfigOnlySetupSaver
from canvas_ed_mcp.config import CanvasPlatformConfig, EdPlatformConfig, load_config
from canvas_ed_mcp.providers.base import CourseCandidate


class ConfigOnlySetupSaverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.output_root = root / "Courses"
        self.output_root.mkdir()
        self.config_path = root / "profiles" / "default.toml"
        self.canvas = CourseCandidate(
            "canvas",
            "canvas.example.edu",
            "c-1",
            "COMP2017",
            "Systems Programming",
            "2026-S1",
        )
        self.ed = CourseCandidate(
            "ed",
            "edstem.org",
            "e-1",
            "COMP2017",
            "Systems Programming",
            "2026-S1",
        )
        self.controller = SetupController(
            profile="default",
            output_root=self.output_root,
            catalogs={"canvas": (self.canvas,), "ed": (self.ed,)},
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_CANVAS_TOKEN",
            ),
            ed=EdPlatformConfig(
                enabled=True,
                credential="env:SYNTHETIC_ED_TOKEN",
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _review(self) -> SetupReview:
        self.controller.select_course(self.canvas.key)
        self.controller.select_course(self.ed.key)
        self.controller.open_mapping()
        match = self.controller.pending_matches[0]
        self.controller.link_match(match.match_id, logical_key="COMP2017-2026-S1")
        self.controller.open_folders()
        return self.controller.build_review()

    def test_save_commits_the_displayed_plan_and_prepares_course_folder(self) -> None:
        review = self._review()
        saver = ConfigOnlySetupSaver(self.controller, self.config_path)

        result = saver.save(review)

        self.assertEqual(OperationStatus.SUCCEEDED, result.status)
        self.assertEqual(review.plan.config, load_config(self.config_path))
        self.assertTrue((self.output_root / "COMP2017").is_dir())
        repeated = saver.save(review)
        self.assertEqual(OperationStatus.CONFLICT, repeated.status)
        self.assertEqual("save_already_completed", repeated.issues[0].code)

    def test_back_or_a_different_review_object_cannot_be_saved(self) -> None:
        review = self._review()
        saver = ConfigOnlySetupSaver(self.controller, self.config_path)
        lookalike = SetupReview(
            revision=review.revision,
            course_count=review.course_count,
            source_count=review.source_count,
            plan=review.plan,
        )

        rejected = saver.save(lookalike)
        self.assertEqual(OperationStatus.CONFLICT, rejected.status)
        self.assertEqual("review_required", rejected.issues[0].code)
        self.controller.back()
        rejected = saver.save(review)
        self.assertEqual(OperationStatus.CONFLICT, rejected.status)
        self.assertEqual("review_required", rejected.issues[0].code)
        self.assertFalse(self.config_path.exists())

    def test_existing_config_is_never_overwritten(self) -> None:
        review = self._review()
        self.config_path.parent.mkdir(parents=True)
        original = b"existing = true\n"
        self.config_path.write_bytes(original)
        saver = ConfigOnlySetupSaver(self.controller, self.config_path)

        result = saver.save(review)

        self.assertEqual(OperationStatus.CONFLICT, result.status)
        self.assertEqual("config_exists", result.issues[0].code)
        self.assertEqual(original, self.config_path.read_bytes())
        self.assertEqual([], list(self.output_root.iterdir()))

    def test_existing_course_folder_is_reused(self) -> None:
        review = self._review()
        existing = self.output_root / "COMP2017"
        existing.mkdir()
        marker = existing / "keep.txt"
        marker.write_text("keep", encoding="utf-8")

        result = ConfigOnlySetupSaver(self.controller, self.config_path).save(review)

        self.assertEqual(OperationStatus.SUCCEEDED, result.status)
        self.assertEqual("keep", marker.read_text(encoding="utf-8"))

    def test_course_folder_file_collision_stops_before_config_write(self) -> None:
        review = self._review()
        collision = self.output_root / "COMP2017"
        collision.write_text("not a directory", encoding="utf-8")

        result = ConfigOnlySetupSaver(self.controller, self.config_path).save(review)

        self.assertEqual(OperationStatus.CONFLICT, result.status)
        self.assertEqual("course_folder_conflict", result.issues[0].code)
        self.assertFalse(self.config_path.exists())

    def test_link_like_library_root_fails_before_folder_or_config_write(self) -> None:
        review = self._review()
        saver = ConfigOnlySetupSaver(self.controller, self.config_path)

        with patch(
            "canvas_ed_mcp.application.setup_workflow._is_link_like",
            side_effect=lambda path: path == review.plan.config.output_root,
        ):
            result = saver.save(review)

        self.assertEqual(OperationStatus.FAILED, result.status)
        self.assertEqual("invalid_output_root", result.issues[0].code)
        self.assertFalse(self.config_path.exists())
        self.assertEqual([], list(self.output_root.iterdir()))

    def test_failed_config_write_rolls_back_only_new_empty_course_folders(self) -> None:
        review = self._review()
        saver = ConfigOnlySetupSaver(self.controller, self.config_path)
        failed = type(
            "Result",
            (),
            {"status": OperationStatus.FAILED, "config_path": self.config_path, "issues": ()},
        )()

        with patch(
            "canvas_ed_mcp.application.setup_workflow.commit_setup_review",
            return_value=failed,
        ):
            result = saver.save(review)

        self.assertEqual(OperationStatus.FAILED, result.status)
        self.assertFalse((self.output_root / "COMP2017").exists())

    def test_unexpected_config_write_failure_also_rolls_back_new_folder(self) -> None:
        review = self._review()
        saver = ConfigOnlySetupSaver(self.controller, self.config_path)

        with patch(
            "canvas_ed_mcp.application.setup_workflow.commit_setup_review",
            side_effect=OSError("synthetic write failure"),
        ):
            result = saver.save(review)

        self.assertEqual(OperationStatus.FAILED, result.status)
        self.assertEqual("config_write_failed", result.issues[0].code)
        self.assertFalse((self.output_root / "COMP2017").exists())

    def test_relative_target_is_bound_before_confirmation(self) -> None:
        saver = ConfigOnlySetupSaver(self.controller, Path("relative-profile.toml"))

        self.assertTrue(saver.config_path.is_absolute())


if __name__ == "__main__":
    unittest.main()
