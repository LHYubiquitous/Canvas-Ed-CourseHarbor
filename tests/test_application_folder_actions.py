from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from canvas_ed_mcp.application.contracts import OperationStatus
from canvas_ed_mcp.application.folder_actions import (
    OpenOutputFolderRequest,
    OutputFolderService,
)
from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseConfig,
    CourseSource,
    PlatformsConfig,
    save_config,
)
from canvas_ed_mcp.paths import ProfilePaths


def _paths(root: Path) -> ProfilePaths:
    return ProfilePaths(
        profile="test",
        config_file=root / "profile.toml",
        state_file=root / "state.json",
        state_backup_file=root / "state.backup.json",
        lock_dir=root / ".locks",
        log_dir=root / "logs",
        cache_dir=root / ".cache",
    )


def _write_config(paths: ProfilePaths, output_root: Path, *, profile: str = "test") -> None:
    save_config(
        AppConfig(
            profile=profile,
            output_root=output_root,
            platforms=PlatformsConfig(
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url="https://canvas.invalid/api/v1",
                    credential="env:SYNTHETIC_SECRET_NAME",
                )
            ),
            courses=(
                CourseConfig(
                    key="course-1",
                    folder="COURSE1",
                    confirmed_at=datetime.now(timezone.utc),
                    sources=(
                        CourseSource(
                            provider="canvas",
                            instance="canvas.invalid",
                            course_id="1",
                        ),
                    ),
                ),
            ),
        ),
        paths.config_file,
    )


class OutputFolderServiceTests(unittest.TestCase):
    def test_opens_only_the_revalidated_exact_output_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _paths(root)
            output = root / "Courses"
            output.mkdir()
            _write_config(paths, output)
            opened: list[Path] = []
            service = OutputFolderService(
                opener=lambda path: opened.append(path) is None,
                paths_resolver=lambda profile: paths,
            )

            result = service.open(
                OpenOutputFolderRequest(
                    "test",
                    config_path=paths.config_file,
                    expected_output_root=output,
                )
            )

            self.assertEqual(OperationStatus.SUCCEEDED, result.status)
            self.assertEqual(output.resolve(), result.path)
            self.assertEqual([output.resolve()], opened)
            self.assertNotIn("SYNTHETIC_SECRET_NAME", repr(result))

    def test_output_root_drift_fails_before_opener(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _paths(root)
            output = root / "Courses"
            output.mkdir()
            _write_config(paths, output)
            calls: list[Path] = []
            service = OutputFolderService(
                opener=lambda path: calls.append(path) is None,
                paths_resolver=lambda profile: paths,
            )

            result = service.open(
                OpenOutputFolderRequest(
                    "test",
                    config_path=paths.config_file,
                    expected_output_root=root / "Old Courses",
                )
            )

            self.assertEqual(OperationStatus.CONFLICT, result.status)
            self.assertEqual("output_root_changed", result.issues[0].code)
            self.assertEqual([], calls)

    def test_missing_and_non_directory_outputs_fail_closed(self) -> None:
        for kind in ("missing", "file"):
            with self.subTest(kind=kind), TemporaryDirectory() as directory:
                root = Path(directory)
                paths = _paths(root)
                output = root / "Courses"
                if kind == "file":
                    output.write_text("not a directory", encoding="utf-8")
                _write_config(paths, output)
                service = OutputFolderService(
                    opener=lambda path: self.fail("opener must not run"),
                    paths_resolver=lambda profile: paths,
                )

                result = service.open(OpenOutputFolderRequest("test"))

                self.assertEqual(OperationStatus.FAILED, result.status)
                self.assertEqual(
                    "output_root_missing" if kind == "missing" else "output_root_not_directory",
                    result.issues[0].code,
                )

    def test_opener_failure_is_sanitized(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _paths(root)
            output = root / "Courses"
            output.mkdir()
            _write_config(paths, output)

            for opener in (
                lambda path: False,
                lambda path: (_ for _ in ()).throw(RuntimeError("synthetic-secret")),
            ):
                with self.subTest(opener=opener):
                    result = OutputFolderService(
                        opener=opener,
                        paths_resolver=lambda profile: paths,
                    ).open(OpenOutputFolderRequest("test"))
                    self.assertEqual(OperationStatus.FAILED, result.status)
                    self.assertEqual("output_folder_open_failed", result.issues[0].code)
                    self.assertNotIn("synthetic-secret", repr(result))


if __name__ == "__main__":
    unittest.main()
