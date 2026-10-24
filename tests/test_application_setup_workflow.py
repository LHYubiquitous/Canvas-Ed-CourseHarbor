from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

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

    def test_save_commits_the_displayed_plan_once_and_only_creates_config(self) -> None:
        review = self._review()
        saver = ConfigOnlySetupSaver(self.controller, self.config_path)

        result = saver.save(review)

        self.assertEqual(OperationStatus.SUCCEEDED, result.status)
        self.assertEqual(review.plan.config, load_config(self.config_path))
        self.assertFalse(self.output_root.exists())
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
        self.assertFalse(self.output_root.exists())

    def test_relative_target_is_bound_before_confirmation(self) -> None:
        saver = ConfigOnlySetupSaver(self.controller, Path("relative-profile.toml"))

        self.assertTrue(saver.config_path.is_absolute())


if __name__ == "__main__":
    unittest.main()
