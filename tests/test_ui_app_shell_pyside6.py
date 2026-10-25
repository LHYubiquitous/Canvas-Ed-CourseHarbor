from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:  # pragma: no cover - optional desktop dependency
    QApplication = None  # type: ignore[assignment]
    QMessageBox = None  # type: ignore[assignment]

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
)
from canvas_ed_mcp.application.credentials import CredentialOperationResult
from canvas_ed_mcp.application.daily_sync import (
    SyncApplyResult,
    SyncItemSummary,
    SyncSourceSummary,
)
from canvas_ed_mcp.application.profile_status import inspect_profile_overview
from canvas_ed_mcp.application.sync_reports import SyncReportStore
from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseConfig,
    CourseSource,
    PlatformsConfig,
    load_config,
    save_config,
)


def _windows_gui_available() -> bool:
    if os.name != "nt" or QApplication is None:
        return False
    try:
        return bool(ctypes.windll.user32.GetProcessWindowStation())
    except (AttributeError, OSError):
        return False


@unittest.skipUnless(_windows_gui_available(), "requires a normal Windows desktop session")
class CourseHarborShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "library"
        self.output.mkdir()
        self.config_path = self.root / "profile.toml"
        save_config(
            AppConfig(
                profile="test",
                output_root=self.output,
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
                                name="Example course",
                            ),
                        ),
                    ),
                ),
            ),
            self.config_path,
        )
        self.overview = inspect_profile_overview(
            "test", config_path=self.config_path
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _window(self, *, report_store=None):
        from canvas_ed_mcp.ui.app_shell import CourseHarborWindow

        with patch(
            "canvas_ed_mcp.ui.app_shell.keyring_status",
            return_value=SimpleNamespace(available=True),
        ):
            return CourseHarborWindow(
                overview=self.overview,
                sync_report_store=report_store,
            )

    def test_all_requested_routes_are_real_ui_pages(self) -> None:
        window = self._window()
        try:
            self.assertEqual("CourseHarbor", window.windowTitle())
            self.assertEqual(
                {"library", "due_dates", "courses", "connections", "health"},
                set(window.pages),
            )
            for route in window.pages:
                window.show_route(route)
                self.assertIs(window.pages[route], window.stack.currentWidget())
                self.assertTrue(window.route_buttons[route].isChecked())
        finally:
            window._allow_close = True
            window.close()

    def test_navigation_rail_labels_keep_the_dark_rail_background(self) -> None:
        window = self._window()
        try:
            style = window.styleSheet()
            self.assertIn(
                "QFrame#navigation_rail QLabel { background: transparent;",
                style,
            )
            self.assertNotIn("QMainWindow, QStackedWidget, QWidget {", style)
        finally:
            window._allow_close = True
            window.close()

    def test_navigation_rail_exposes_profile_switch_request(self) -> None:
        window = self._window()
        requested: list[bool] = []
        window.profileSwitchRequested.connect(lambda: requested.append(True))
        try:
            self.assertEqual("Switch profile", window.switch_profile_button.text())
            window.switch_profile_button.click()
            self.assertEqual([True], requested)
        finally:
            window._allow_close = True
            window.close()

    def test_due_page_separates_weekly_and_semester_load_and_export_controls(self) -> None:
        window = self._window()
        try:
            due = window.pages["due_dates"]
            self.assertTrue(due.weekly_text.isReadOnly())
            self.assertTrue(due.semester_text.isReadOnly())
            self.assertEqual("Load this week", due.weekly_refresh_button.text())
            self.assertEqual("Load semester", due.semester_refresh_button.text())
            self.assertEqual("Export this week", due.weekly_export_button.text())
            self.assertEqual("Export semester", due.semester_export_button.text())
            self.assertIsNot(due.weekly_export_path, due.semester_export_path)
            self.assertTrue(due.weekly_choose_button.isEnabled())
            self.assertTrue(due.semester_choose_button.isEnabled())
            self.assertFalse(due.weekly_export_button.isEnabled())
            self.assertFalse(due.semester_export_button.isEnabled())
            self.assertEqual(OperationStatus.SUCCEEDED, self.overview.status)
        finally:
            window._allow_close = True
            window.close()

    def test_cancel_close_does_not_release_library_controller(self) -> None:
        window = self._window()
        controller = window.pages["library"].dashboard.controller
        with patch.object(controller, "close", wraps=controller.close) as close:
            with patch(
                "canvas_ed_mcp.ui.app_shell.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Cancel,
            ):
                window.close()
            close.assert_not_called()
            self.assertFalse(window._allow_close)
        window._allow_close = True
        window.close()

    def test_connections_explains_verified_but_unknown_keyring_write(self) -> None:
        window = self._window()
        try:
            page = window.pages["connections"]
            result = CredentialOperationResult(
                OperationStatus.FAILED,
                "canvas",
                verified=True,
                issues=(
                    ApplicationIssue(
                        "credential_state_unknown",
                        IssueSeverity.ERROR,
                        provider="canvas",
                    ),
                ),
            )
            page._cards["canvas"]["token"].clear()
            page._finished(0, "canvas", result)
            message = page._cards["canvas"]["message"].text()
            self.assertIn("verified", message)
            self.assertIn("write failed", message)
            self.assertEqual("", page._cards["canvas"]["token"].text())
        finally:
            window._allow_close = True
            window.close()

    def test_courses_launches_configure_ui_in_isolated_mode(self) -> None:
        window = self._window()
        try:
            with patch(
                "canvas_ed_mcp.ui.app_shell.QProcess.startDetached",
                return_value=True,
            ) as start:
                window.pages["courses"].open_configuration()
            program, arguments = start.call_args.args
            self.assertEqual(sys.executable, program)
            self.assertEqual("-I", arguments[0])
            self.assertEqual("-m", arguments[1])
            self.assertEqual("canvas_ed_mcp.ui.configure_window", arguments[2])
            self.assertIn("--profile", arguments)
            self.assertIn("--config", arguments)
        finally:
            window._allow_close = True
            window.close()

    def test_courses_expands_sync_runs_and_changed_file_destinations(self) -> None:
        from canvas_ed_mcp.paths import ProfilePaths

        paths = ProfilePaths(
            profile="test",
            config_file=self.config_path,
            state_file=self.root / "state" / "state-v4.json",
            state_backup_file=self.root / "state" / "state-v4.backup.json",
            lock_dir=self.root / "locks",
            log_dir=self.root / "logs",
            cache_dir=self.root / "cache",
        )
        store = SyncReportStore("test", paths_resolver=lambda profile: paths)
        window = self._window(report_store=store)
        try:
            destination = self.output / "COMP0000" / "Resources" / "notes.pdf"
            result = SyncApplyResult(
                OperationStatus.SUCCEEDED,
                sources=(
                    SyncSourceSummary(
                        course_key="course-42",
                        provider="canvas",
                        instance="canvas.example.edu",
                        course_id="42",
                        actions=("install_new",),
                        course_folder="COMP0000",
                        course_code="COMP0000",
                        course_name="Example course",
                        items=(
                            SyncItemSummary(
                                "notes.pdf",
                                "file",
                                "install_new",
                                "new_remote_item",
                                destination,
                            ),
                        ),
                    ),
                ),
            )

            courses = window.pages["courses"]
            courses.record_sync_result(result)

            course = courses.report_tree.topLevelItem(0)
            self.assertEqual(1, course.childCount())
            run = course.child(0)
            self.assertEqual(1, run.childCount())
            self.assertEqual("notes.pdf", run.child(0).text(0))
            self.assertEqual(str(destination), run.child(0).text(3))
            self.assertIn("1 new file", course.text(2))
        finally:
            window._allow_close = True
            window.close()

    def test_legacy_keyring_reference_is_read_only_and_fails_closed(self) -> None:
        config = load_config(self.config_path)
        save_config(
            config.model_copy(
                update={
                    "platforms": config.platforms.model_copy(
                        update={
                            "canvas": CanvasPlatformConfig(
                                enabled=True,
                                base_url="https://canvas.example.edu/api/v1",
                                credential="keyring:legacy/canvas",
                            )
                        }
                    )
                }
            ),
            self.config_path,
        )
        window = self._window()
        try:
            page = window.pages["connections"]
            card = page._cards["canvas"]
            self.assertIn("Legacy credential reference", card["state"].text())
            self.assertFalse(card["save"].isEnabled())
            self.assertFalse(card["token"].isEnabled())
            card["token"].setText("synthetic-replacement")
            with patch(
                "canvas_ed_mcp.ui.connections_page.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Save,
            ):
                page._save("canvas")
            self.assertEqual("", card["token"].text())
            self.assertIn("Reconfigure", card["message"].text())
        finally:
            window._allow_close = True
            window.close()


if __name__ == "__main__":
    unittest.main()
