from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from canvas_ed_mcp.application.contracts import OperationStatus
from canvas_ed_mcp.application.due_reports import (
    DueRefreshRequest,
    DueReportScope,
    DueReportService,
)
from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseConfig,
    CourseSource,
    DailyUseConfig,
    DueReportConfig,
    GradescopeDueMapping,
    GradescopePlatformConfig,
    PlatformsConfig,
    load_config,
    save_config,
)
from canvas_ed_mcp.due.model import (
    DueBatch,
    DueCoverageStatus,
    DueItem,
    DuePrecision,
    DueSourceKey,
)
from canvas_ed_mcp.paths import ProfilePaths


def _paths(root: Path) -> ProfilePaths:
    return ProfilePaths(
        profile="test",
        config_file=root / "profile.toml",
        state_file=root / "state" / "state-v4.json",
        state_backup_file=root / "state" / "state-v4.backup.json",
        lock_dir=root / "locks",
        log_dir=root / "logs",
        cache_dir=root / "cache",
    )


def _config(root: Path, paths: ProfilePaths) -> None:
    save_config(
        AppConfig(
            profile="test",
            output_root=root / "library",
            platforms=PlatformsConfig(
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url="https://canvas.example.edu/api/v1",
                    credential="env:TEST_CANVAS_TOKEN",
                )
            ),
            courses=(
                CourseConfig(
                    key="course-42",
                    folder="COMP0000",
                    confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    sources=(
                        CourseSource(
                            provider="canvas",
                            instance="canvas.example.edu",
                            course_id="42",
                            code="COMP0000",
                            term="2026 Semester 2",
                        ),
                    ),
                ),
            ),
            daily_use=DailyUseConfig(
                due=DueReportConfig(semester_label="2026 Semester 2")
            ),
        ),
        paths.config_file,
    )
    (root / "library").mkdir()


class _Provider:
    provider_name = "canvas"
    instance = "canvas.example.edu"

    def __init__(self) -> None:
        self.calls = 0
        self.close_calls = 0

    def list_due_items(self, course_id, *, logical_course_key, course_label):
        self.calls += 1
        item = DueItem(
            key=DueSourceKey("canvas", self.instance, course_id, "assignment", "a1"),
            logical_course_key=logical_course_key,
            course_label=course_label,
            title="Assignment 1",
            due_at=datetime(2026, 10, 28, 12, tzinfo=timezone.utc),
            precision=DuePrecision.DATETIME,
        )
        return DueBatch(
            self.provider_name,
            self.instance,
            course_id,
            DueCoverageStatus.COMPLETE,
            (item,),
        )

    def close(self) -> None:
        self.close_calls += 1


class _GradescopeProvider:
    provider_name = "gradescope"
    instance = "gradescope.com"

    def __init__(self) -> None:
        self.catalog_calls = 0
        self.due_calls = 0
        self.close_calls = 0

    def list_courses(self):
        self.catalog_calls += 1
        return ()

    def list_due_items(self, course_id, *, logical_course_key, course_label):
        self.due_calls += 1
        item = DueItem(
            key=DueSourceKey("gradescope", self.instance, course_id, "assignment", "g1"),
            logical_course_key=logical_course_key,
            course_label=course_label,
            title="Gradescope assignment",
            due_at=datetime(2026, 10, 29, 12, tzinfo=timezone.utc),
            precision=DuePrecision.DATETIME,
        )
        return DueBatch(
            self.provider_name,
            self.instance,
            course_id,
            DueCoverageStatus.COMPLETE,
            (item,),
        )

    def close(self) -> None:
        self.close_calls += 1


class _Cancellation:
    cancellation_requested = False


class DueReportServiceTests(unittest.TestCase):
    def _service(self, root: Path, provider: _Provider) -> tuple[DueReportService, ProfilePaths]:
        paths = _paths(root)
        _config(root, paths)
        return (
            DueReportService(
                paths_resolver=lambda profile: paths,
                secret_resolver=lambda reference: "synthetic-token",
                canvas_factory=lambda base_url, token: provider,
                ed_factory=lambda token: (_ for _ in ()).throw(
                    AssertionError("Ed must not be created")
                ),
                clock=lambda: datetime(2026, 10, 27, 12, tzinfo=timezone.utc),
            ),
            paths,
        )

    @staticmethod
    def _enable_gradescope(paths: ProfilePaths) -> None:
        config = load_config(paths.config_file)
        save_config(
            config.model_copy(
                update={
                    "platforms": config.platforms.model_copy(
                        update={
                            "gradescope": GradescopePlatformConfig(
                                enabled=True,
                                email_credential="env:TEST_GRADESCOPE_EMAIL",
                                password_credential="env:TEST_GRADESCOPE_PASSWORD",
                            )
                        }
                    ),
                    "daily_use": config.daily_use.model_copy(
                        update={
                            "due": DueReportConfig(
                                gradescope_courses=(
                                    GradescopeDueMapping(
                                        logical_course_key="course-42",
                                        course_id="99",
                                        required=True,
                                    ),
                                )
                            )
                        }
                    ),
                }
            ),
            paths.config_file,
        )

    def test_refresh_builds_both_previews_without_publishing_reports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = _Provider()
            service, _ = self._service(root, provider)

            result = service.refresh(DueRefreshRequest("test"))

            self.assertEqual(OperationStatus.SUCCEEDED, result.status)
            self.assertIsNotNone(result.snapshot)
            snapshot = result.snapshot
            assert snapshot is not None
            self.assertIn("This week's due", snapshot.weekly_text)
            self.assertIn("Assignment 1", snapshot.weekly_text)
            self.assertIn("2026 Semester 2 Assessment Due Notes", snapshot.semester_markdown)
            self.assertEqual(1, snapshot.week_item_count)
            self.assertEqual(1, snapshot.item_count)
            self.assertEqual(1, provider.calls)
            self.assertEqual(1, provider.close_calls)
            self.assertFalse(snapshot.export_directory.exists())

    def test_weekly_scope_builds_and_exports_only_the_weekly_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, _ = self._service(root, _Provider())
            target = root / "weekly"

            refreshed = service.refresh(
                DueRefreshRequest(
                    "test",
                    export_directory=target,
                    scope=DueReportScope.THIS_WEEK,
                )
            )

            assert refreshed.snapshot is not None
            self.assertEqual(DueReportScope.THIS_WEEK, refreshed.snapshot.scope)
            self.assertIn("This week's due", refreshed.snapshot.weekly_text)
            self.assertEqual("", refreshed.snapshot.semester_markdown)
            exported = service.export(refreshed.snapshot)
            self.assertEqual(OperationStatus.SUCCEEDED, exported.status)
            self.assertTrue(exported.weekly_path and exported.weekly_path.is_file())
            self.assertIsNone(exported.semester_path)

    def test_semester_scope_builds_and_exports_only_the_semester_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, _ = self._service(root, _Provider())
            target = root / "semester"

            refreshed = service.refresh(
                DueRefreshRequest(
                    "test",
                    export_directory=target,
                    scope=DueReportScope.SEMESTER,
                )
            )

            assert refreshed.snapshot is not None
            self.assertEqual(DueReportScope.SEMESTER, refreshed.snapshot.scope)
            self.assertEqual("", refreshed.snapshot.weekly_text)
            self.assertIn(
                "2026 Semester 2 Assessment Due Notes",
                refreshed.snapshot.semester_markdown,
            )
            exported = service.export(refreshed.snapshot)
            self.assertEqual(OperationStatus.SUCCEEDED, exported.status)
            self.assertIsNone(exported.weekly_path)
            self.assertTrue(exported.semester_path and exported.semester_path.is_file())

    def test_retarget_preserves_existing_semester_completion_without_provider_io(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = _Provider()
            service, _ = self._service(root, provider)
            refreshed = service.refresh(DueRefreshRequest("test"))
            snapshot = refreshed.snapshot
            assert snapshot is not None
            target = root / "chosen"
            target.mkdir()
            marker = snapshot.items[0].stable_marker
            (target / "this-semester-due.md").write_text(
                f"- [x] **COMP0000｜Assignment 1**\n    <!-- canvas-ed-mcp:due={marker} -->\n",
                encoding="utf-8",
            )

            retargeted = service.retarget(snapshot, target)

            self.assertEqual(OperationStatus.SUCCEEDED, retargeted.status)
            assert retargeted.snapshot is not None
            self.assertIn("- [x] **COMP0000｜Assignment 1**", retargeted.snapshot.semester_markdown)
            self.assertEqual(1, provider.calls)

    def test_export_writes_both_versioned_reports_and_rejects_config_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider = _Provider()
            service, paths = self._service(root, provider)
            refreshed = service.refresh(DueRefreshRequest("test"))
            snapshot = refreshed.snapshot
            assert snapshot is not None

            exported = service.export(snapshot)

            self.assertEqual(OperationStatus.SUCCEEDED, exported.status)
            self.assertTrue(exported.weekly_path and exported.weekly_path.is_file())
            self.assertTrue(exported.semester_path and exported.semester_path.is_file())

            paths.config_file.write_text(paths.config_file.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            conflict = service.export(snapshot)
            self.assertEqual(OperationStatus.CONFLICT, conflict.status)
            self.assertEqual("config_changed", conflict.issues[0].code)

    def test_refresh_includes_configured_gradescope_due_source_and_closes_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canvas = _Provider()
            gradescope = _GradescopeProvider()
            service, paths = self._service(root, canvas)
            config = AppConfig(
                profile="test",
                output_root=root / "library",
                platforms=PlatformsConfig(
                    canvas=CanvasPlatformConfig(
                        enabled=True,
                        base_url="https://canvas.example.edu/api/v1",
                        credential="env:TEST_CANVAS_TOKEN",
                    ),
                    gradescope=GradescopePlatformConfig(
                        enabled=True,
                        email_credential="env:TEST_GRADESCOPE_EMAIL",
                        password_credential="env:TEST_GRADESCOPE_PASSWORD",
                    ),
                ),
                courses=(
                    CourseConfig(
                        key="course-42",
                        folder="COMP0000",
                        confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        sources=(
                            CourseSource(
                                provider="canvas",
                                instance="canvas.example.edu",
                                course_id="42",
                                code="COMP0000",
                            ),
                        ),
                    ),
                ),
                daily_use=DailyUseConfig(
                    due=DueReportConfig(
                        gradescope_courses=(
                            GradescopeDueMapping(
                                logical_course_key="course-42",
                                course_id="99",
                                required=True,
                            ),
                        )
                    )
                ),
            )
            save_config(config, paths.config_file)
            service = DueReportService(
                paths_resolver=lambda profile: paths,
                secret_resolver=lambda reference: "synthetic",
                canvas_factory=lambda base_url, token: canvas,
                ed_factory=lambda token: (_ for _ in ()).throw(AssertionError),
                gradescope_factory=lambda email, password: gradescope,
                clock=lambda: datetime(2026, 10, 27, 12, tzinfo=timezone.utc),
            )

            result = service.refresh(DueRefreshRequest("test"))

            self.assertEqual(OperationStatus.SUCCEEDED, result.status)
            assert result.snapshot is not None
            self.assertEqual(2, result.snapshot.item_count)
            self.assertEqual(1, gradescope.catalog_calls)
            self.assertEqual(1, gradescope.due_calls)
            self.assertEqual(1, gradescope.close_calls)

    def test_provider_close_failure_downgrades_refresh_to_partial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            class CloseFailure(_Provider):
                def close(self) -> None:
                    raise RuntimeError("synthetic close failure")

            service, _ = self._service(root, CloseFailure())
            result = service.refresh(DueRefreshRequest("test"))
            self.assertEqual(OperationStatus.PARTIAL, result.status)
            self.assertIn("provider_close_failed", {item.code for item in result.issues})

    def test_export_honours_cancellation_between_create_only_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service, _ = self._service(root, _Provider())
            refreshed = service.refresh(DueRefreshRequest("test"))
            snapshot = refreshed.snapshot
            assert snapshot is not None
            cancellation = _Cancellation()
            from canvas_ed_mcp.application import due_reports

            publish = due_reports.publish_versioned_report

            def publish_then_cancel(*args, **kwargs):
                path = publish(*args, **kwargs)
                cancellation.cancellation_requested = True
                return path

            with patch(
                "canvas_ed_mcp.application.due_reports.publish_versioned_report",
                side_effect=publish_then_cancel,
            ):
                result = service.export(snapshot, cancellation=cancellation)

            self.assertEqual(OperationStatus.PARTIAL, result.status)
            self.assertTrue(result.weekly_path and result.weekly_path.is_file())
            self.assertIsNone(result.semester_path)
            self.assertEqual("export_cancelled_after_weekly", result.issues[0].code)

    def test_cancel_before_gradescope_does_not_resolve_its_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canvas = _Provider()
            service, paths = self._service(root, canvas)
            self._enable_gradescope(paths)
            cancellation = _Cancellation()
            resolved: list[str] = []

            def canvas_factory(base_url, token):
                cancellation.cancellation_requested = True
                return canvas

            service = DueReportService(
                paths_resolver=lambda profile: paths,
                secret_resolver=lambda reference: resolved.append(reference) or "synthetic",
                canvas_factory=canvas_factory,
                gradescope_factory=lambda email, password: (_ for _ in ()).throw(
                    AssertionError("Gradescope must not be created after cancellation")
                ),
            )

            result = service.refresh(
                DueRefreshRequest("test"), cancellation=cancellation
            )

            self.assertEqual(OperationStatus.CANCELLED, result.status)
            self.assertEqual(["env:TEST_CANVAS_TOKEN"], resolved)
            self.assertEqual(1, canvas.close_calls)

    def test_cancel_after_gradescope_factory_closes_without_catalog_request(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canvas = _Provider()
            gradescope = _GradescopeProvider()
            service, paths = self._service(root, canvas)
            self._enable_gradescope(paths)
            cancellation = _Cancellation()

            def gradescope_factory(email, password):
                cancellation.cancellation_requested = True
                return gradescope

            service = DueReportService(
                paths_resolver=lambda profile: paths,
                secret_resolver=lambda reference: "synthetic",
                canvas_factory=lambda base_url, token: canvas,
                gradescope_factory=gradescope_factory,
            )

            result = service.refresh(
                DueRefreshRequest("test"), cancellation=cancellation
            )

            self.assertEqual(OperationStatus.CANCELLED, result.status)
            self.assertEqual(0, gradescope.catalog_calls)
            self.assertEqual(1, gradescope.close_calls)
            self.assertEqual(1, canvas.close_calls)

    def test_cancel_during_gradescope_secret_resolution_skips_factory_and_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canvas = _Provider()
            service, paths = self._service(root, canvas)
            self._enable_gradescope(paths)
            cancellation = _Cancellation()
            resolved: list[str] = []

            def resolver(reference: str) -> str:
                resolved.append(reference)
                if reference == "env:TEST_GRADESCOPE_EMAIL":
                    cancellation.cancellation_requested = True
                return "synthetic"

            service = DueReportService(
                paths_resolver=lambda profile: paths,
                secret_resolver=resolver,
                canvas_factory=lambda base_url, token: canvas,
                gradescope_factory=lambda email, password: (_ for _ in ()).throw(
                    AssertionError("Gradescope factory must not run after cancellation")
                ),
            )

            result = service.refresh(
                DueRefreshRequest("test"), cancellation=cancellation
            )

            self.assertEqual(OperationStatus.CANCELLED, result.status)
            self.assertEqual(
                ["env:TEST_CANVAS_TOKEN", "env:TEST_GRADESCOPE_EMAIL"],
                resolved,
            )
            self.assertEqual(1, canvas.close_calls)


if __name__ == "__main__":
    unittest.main()
