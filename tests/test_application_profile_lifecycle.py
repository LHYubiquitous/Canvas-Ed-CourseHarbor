from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from canvas_ed_mcp.application.contracts import OperationStatus
from canvas_ed_mcp.application.profile_lifecycle import ProfileLifecycleService
from canvas_ed_mcp.paths import ProfilePaths


class ProfileLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _paths(self, profile: str) -> ProfilePaths:
        return ProfilePaths(
            profile=profile,
            config_file=self.root / "config" / "profiles" / f"{profile}.toml",
            state_file=self.root / "state" / profile / "state-v4.json",
            state_backup_file=self.root / "state" / profile / "state-v4.backup.json",
            lock_dir=self.root / "state" / "locks",
            log_dir=self.root / "logs" / profile,
            cache_dir=self.root / "cache" / profile,
        )

    def _service(self) -> ProfileLifecycleService:
        return ProfileLifecycleService(
            paths_resolver=self._paths,
            clock=lambda: datetime(2026, 10, 25, 12, 30, tzinfo=timezone.utc),
        )

    def test_remove_archives_only_config_and_retains_profile_data(self) -> None:
        paths = self._paths("test-profile")
        paths.config_file.parent.mkdir(parents=True)
        paths.config_file.write_text("profile = 'test-profile'\n", encoding="utf-8")
        paths.state_file.parent.mkdir(parents=True)
        paths.state_file.write_text("retained-state", encoding="utf-8")
        paths.log_dir.mkdir(parents=True)
        paths.cache_dir.mkdir(parents=True)

        review = self._service().review_removal(
            "test-profile", expected_config_path=paths.config_file
        )
        result = self._service().commit_removal(review.review)

        self.assertEqual(OperationStatus.SUCCEEDED, result.status)
        self.assertFalse(paths.config_file.exists())
        self.assertTrue(result.archive_path.is_file())
        self.assertEqual("retained-state", paths.state_file.read_text(encoding="utf-8"))
        self.assertTrue(paths.log_dir.is_dir())
        self.assertTrue(paths.cache_dir.is_dir())
        self.assertEqual(
            ("course_files", "credentials", "state", "logs", "cache"),
            result.retained,
        )

    def test_changed_config_cannot_be_removed_from_stale_review(self) -> None:
        paths = self._paths("test-profile")
        paths.config_file.parent.mkdir(parents=True)
        paths.config_file.write_text("first", encoding="utf-8")
        service = self._service()
        review = service.review_removal("test-profile")
        paths.config_file.write_text("changed", encoding="utf-8")

        result = service.commit_removal(review.review)

        self.assertEqual(OperationStatus.CONFLICT, result.status)
        self.assertTrue(paths.config_file.is_file())
        self.assertFalse(review.review.archive_path.exists())

    def test_expected_path_mismatch_fails_without_touching_files(self) -> None:
        paths = self._paths("test-profile")
        paths.config_file.parent.mkdir(parents=True)
        paths.config_file.write_text("profile", encoding="utf-8")

        result = self._service().review_removal(
            "test-profile", expected_config_path=self.root / "other.toml"
        )

        self.assertEqual(OperationStatus.FAILED, result.status)
        self.assertIsNone(result.review)
        self.assertTrue(paths.config_file.is_file())


if __name__ == "__main__":
    unittest.main()
