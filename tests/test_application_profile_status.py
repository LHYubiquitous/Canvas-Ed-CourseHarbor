from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from canvas_ed_mcp.application.contracts import OperationStatus
from canvas_ed_mcp.application.fingerprints import MISSING_REVISION
from canvas_ed_mcp.application.profile_status import inspect_profile_overview
from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseConfig,
    CourseSource,
    PlatformsConfig,
    save_config,
)
from canvas_ed_mcp.paths import ProfilePaths


def _paths(root: Path, profile: str = "default") -> ProfilePaths:
    return ProfilePaths(
        profile=profile,
        config_file=root / f"{profile}.toml",
        state_file=root / "state" / "state.json",
        state_backup_file=root / "state" / "state.backup.json",
        lock_dir=root / "locks",
        log_dir=root / "logs",
        cache_dir=root / "cache",
    )


def _config(root: Path, profile: str = "default") -> AppConfig:
    source = CourseSource(
        provider="canvas",
        instance="canvas.example.edu",
        course_id="42",
        code="COMP1000",
        name="Example course",
        term="2026 S2",
    )
    return AppConfig(
        profile=profile,
        output_root=root / "courses",
        platforms=PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="keyring:canvas-ed-mcp/default/canvas",
            )
        ),
        courses=(
            CourseConfig(
                key="comp1000",
                folder="COMP1000",
                confirmed_at=datetime(2026, 10, 23, tzinfo=timezone.utc),
                sources=(source,),
            ),
        ),
    )


class ApplicationProfileStatusTests(unittest.TestCase):
    def test_valid_profile_reports_local_status_without_exposing_secret_reference(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = _paths(root)
            save_config(_config(root), paths.config_file)

            overview = inspect_profile_overview(
                "default", config_path=paths.config_file, paths=paths
            )

            self.assertEqual(OperationStatus.SUCCEEDED, overview.status)
            self.assertEqual("default", overview.profile)
            self.assertEqual((root / "courses").resolve(), overview.output_root)
            self.assertEqual(1, overview.course_count)
            self.assertEqual(1, overview.source_count)
            self.assertFalse(overview.output_exists)
            self.assertFalse(overview.state_exists)
            self.assertNotEqual(MISSING_REVISION, overview.config_revision)
            self.assertEqual(MISSING_REVISION, overview.state_revision)
            self.assertEqual("canvas", overview.providers[0].provider)
            self.assertTrue(overview.providers[0].enabled)
            self.assertTrue(overview.providers[0].credential_configured)
            self.assertFalse(overview.providers[1].enabled)
            self.assertNotIn("keyring:canvas-ed-mcp/default/canvas", repr(overview))

    def test_corrupt_state_is_a_sanitized_partial_warning(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = _paths(root)
            save_config(_config(root), paths.config_file)
            paths.state_file.parent.mkdir(parents=True)
            paths.state_file.write_text("synthetic-token must not leak", encoding="utf-8")

            overview = inspect_profile_overview(
                "default", config_path=paths.config_file, paths=paths
            )

            self.assertEqual(OperationStatus.PARTIAL, overview.status)
            self.assertTrue(overview.state_exists)
            self.assertEqual("invalid_sync_state", overview.issues[0].code)
            self.assertNotIn("synthetic-token", repr(overview))

    def test_missing_and_mismatched_profiles_fail_without_writes(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = _paths(root)
            missing = inspect_profile_overview(
                "default", config_path=paths.config_file, paths=paths
            )
            self.assertEqual(OperationStatus.FAILED, missing.status)
            self.assertEqual("config_missing", missing.issues[0].code)

            save_config(_config(root, profile="other"), paths.config_file)
            mismatched = inspect_profile_overview(
                "default", config_path=paths.config_file, paths=paths
            )
            self.assertEqual(OperationStatus.CONFLICT, mismatched.status)
            self.assertEqual("profile_mismatch", mismatched.issues[0].code)

    def test_config_parse_and_revision_share_one_byte_snapshot(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = _paths(root)
            save_config(_config(root), paths.config_file)
            original_read_bytes = Path.read_bytes
            reads: list[Path] = []

            def tracked_read_bytes(path: Path) -> bytes:
                reads.append(path)
                return original_read_bytes(path)

            with patch.object(Path, "read_bytes", new=tracked_read_bytes):
                overview = inspect_profile_overview(
                    "default", config_path=paths.config_file, paths=paths
                )

            self.assertEqual(OperationStatus.SUCCEEDED, overview.status)
            self.assertEqual(1, reads.count(paths.config_file.resolve()))

    def test_state_parse_and_revision_share_one_byte_snapshot(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = _paths(root)
            save_config(_config(root), paths.config_file)
            paths.state_file.parent.mkdir(parents=True)
            paths.state_file.write_text("{}", encoding="utf-8")
            original_read_bytes = Path.read_bytes
            reads: list[Path] = []

            def tracked_read_bytes(path: Path) -> bytes:
                reads.append(path)
                return original_read_bytes(path)

            with (
                patch.object(Path, "read_bytes", new=tracked_read_bytes),
                patch("canvas_ed_mcp.application.profile_status.load_state_bytes"),
            ):
                overview = inspect_profile_overview(
                    "default", config_path=paths.config_file, paths=paths
                )

            self.assertEqual(OperationStatus.SUCCEEDED, overview.status)
            self.assertNotEqual(MISSING_REVISION, overview.state_revision)
            self.assertEqual(1, reads.count(paths.config_file.resolve()))
            self.assertEqual(1, reads.count(paths.state_file.resolve()))


if __name__ == "__main__":
    unittest.main()
