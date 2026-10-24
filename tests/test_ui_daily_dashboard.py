from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

try:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - optional desktop dependency.
    QTest = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
    ProgressEvent,
    ProgressPhase,
    ReviewProof,
)
from canvas_ed_mcp.application.daily_sync import (
    DailySyncController,
    SyncApplyResult,
    SyncPreviewRequest,
    SyncPreviewResult,
    SyncReview,
    SyncSourceSummary,
)
from canvas_ed_mcp.application.folder_actions import OpenOutputFolderResult
from canvas_ed_mcp.application.profile_status import ProfileOverview, ProviderOverview


def _windows_gui_available() -> bool:
    if os.name != "nt" or QApplication is None:
        return False
    try:
        return bool(ctypes.windll.user32.GetProcessWindowStation())
    except (AttributeError, OSError):
        return False


class _Port:
    def __init__(self, preview: SyncPreviewResult, apply: SyncApplyResult) -> None:
        self.preview_result = preview
        self.apply_result = apply
        self.preview_calls = 0
        self.apply_calls = 0
        self.discard_calls = 0
        self.close_calls = 0
        self.applied_review = None

    def preview(self, request, *, progress, cancellation):
        self.preview_calls += 1
        if progress is not None:
            progress(
                ProgressEvent(
                    "material-sync-preview",
                    ProgressPhase.DISCOVERING,
                    1,
                    1,
                    code=self.preview_result.status.value,
                )
            )
        return self.preview_result

    def apply(self, review, *, progress, cancellation):
        self.apply_calls += 1
        self.applied_review = review
        if progress is not None:
            progress(
                ProgressEvent(
                    "material-sync-apply",
                    ProgressPhase.CLEANING_UP,
                    1,
                    1,
                    code=self.apply_result.status.value,
                )
            )
        return self.apply_result

    def discard(self, review):
        self.discard_calls += 1

    def close(self):
        self.close_calls += 1


class _BlockingPort(_Port):
    def __init__(self, preview: SyncPreviewResult, apply: SyncApplyResult) -> None:
        super().__init__(preview, apply)
        self.entered = Event()
        self.pause = Event()
        self.cancellation_seen = False

    def preview(self, request, *, progress, cancellation):
        self.preview_calls += 1
        self.entered.set()
        for _ in range(500):
            if cancellation.cancellation_requested:
                self.cancellation_seen = True
                return SyncPreviewResult(OperationStatus.CANCELLED)
            self.pause.wait(0.01)
        return self.preview_result


class _FolderService:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.requests = []

    def open(self, request):
        self.requests.append(request)
        return OpenOutputFolderResult(OperationStatus.SUCCEEDED, self.path)


def _review(root: Path, *, partial: bool = False) -> SyncReview:
    source = SyncSourceSummary(
        "comp1000",
        "canvas",
        "canvas.invalid",
        "42",
        ("would_fetch", "skip"),
        ("authorization_failed",) if partial else (),
        course_folder="COMP1000",
        course_code="COMP1000",
        course_name="Introduction to Testing",
        course_term="Semester 2 2026",
    )
    return SyncReview(
        ReviewProof("sync", "1" * 64, "2" * 64, "missing"),
        "test",
        root / "profile.toml",
        root / "state.json",
        root,
        (source,),
    )


def _overview(root: Path) -> ProfileOverview:
    return ProfileOverview(
        profile="test",
        status=OperationStatus.SUCCEEDED,
        config_path=root / "profile.toml",
        state_path=root / "state.json",
        output_root=root,
        output_exists=True,
        state_exists=False,
        course_count=1,
        source_count=2,
        providers=(
            ProviderOverview("canvas", True, True),
            ProviderOverview("ed", True, True),
        ),
        config_revision="2" * 64,
        state_revision="missing",
    )


@unittest.skipUnless(
    sys.platform == "darwin" and QApplication is not None,
    "PySide6 macOS GUI is NOT_RUN",
)
class DailyDashboardStyleContractTests(unittest.TestCase):
    def setUp(self) -> None:
        from PySide6.QtGui import QColor, QPalette
        from canvas_ed_mcp.ui.daily_dashboard import DailyDashboardWindow

        self.application = QApplication.instance() or QApplication([])
        self.application.setQuitOnLastWindowClosed(False)
        self.original_palette = QPalette(self.application.palette())
        dark_palette = QPalette(self.original_palette)
        dark_palette.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
        self.application.setPalette(dark_palette)
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        review = _review(self.root)
        self.window = DailyDashboardWindow(
            overview=_overview(self.root),
            controller=DailySyncController(
                _Port(
                    SyncPreviewResult(OperationStatus.SUCCEEDED, review),
                    SyncApplyResult(OperationStatus.SUCCEEDED, review.sources),
                )
            ),
            request=SyncPreviewRequest(
                "test", config_path=self.root / "profile.toml"
            ),
            folder_service=_FolderService(self.root),
            confirm_apply=lambda review, partial: False,
        )
        self.window.ensurePolished()

    def tearDown(self) -> None:
        self.window.close()
        self.application.setPalette(self.original_palette)
        self.temporary.cleanup()

    def test_action_buttons_define_dark_text_for_a_dark_system_palette(self) -> None:
        from PySide6.QtGui import QPalette

        self.window.open_button.ensurePolished()
        foreground = self.window.open_button.palette().color(
            QPalette.ColorRole.ButtonText
        )

        self.assertEqual("#15243a", foreground.name())

    def test_confirmation_buttons_keep_dark_text_on_the_light_button_surface(self) -> None:
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QMessageBox

        dialog = QMessageBox(
            QMessageBox.Icon.Question,
            "Apply reviewed changes?",
            "Only this exact reviewed plan will be applied.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self.window,
        )
        try:
            dialog.ensurePolished()
            for standard_button in (
                QMessageBox.StandardButton.Yes,
                QMessageBox.StandardButton.No,
            ):
                button = dialog.button(standard_button)
                button.ensurePolished()
                foreground = button.palette().color(
                    QPalette.ColorRole.ButtonText
                )
                self.assertEqual("#15243a", foreground.name())
        finally:
            dialog.close()


@unittest.skipUnless(_windows_gui_available(), "PySide6 Windows GUI is NOT_RUN")
class DailyDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from canvas_ed_mcp.ui.daily_dashboard import DailyDashboardWindow

        cls.DailyDashboardWindow = DailyDashboardWindow
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.review = _review(self.root)
        self.port = _Port(
            SyncPreviewResult(OperationStatus.SUCCEEDED, self.review),
            SyncApplyResult(OperationStatus.SUCCEEDED, self.review.sources),
        )
        self.controller = DailySyncController(self.port)
        self.folder_service = _FolderService(self.root)
        self.confirm = False
        self.window = self.DailyDashboardWindow(
            overview=_overview(self.root),
            controller=self.controller,
            request=SyncPreviewRequest("test", config_path=self.root / "profile.toml"),
            folder_service=self.folder_service,
            confirm_apply=lambda review, partial: self.confirm,
        )
        self.window.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        for _ in range(20):
            self.application.processEvents()
            QTest.qWait(2)
        self.temporary.cleanup()

    def _wait_idle(self) -> None:
        for _ in range(300):
            self.application.processEvents()
            if self.window.active_task is None:
                return
            QTest.qWait(5)
        self.fail("dashboard worker did not finish")

    def test_status_is_visible_without_credential_references(self) -> None:
        self.assertEqual("test", self.window.profile_value.text())
        self.assertIn("1 course", self.window.library_value.text())
        self.assertIn("Canvas", self.window.providers_value.text())
        self.assertIn("Ed", self.window.providers_value.text())
        self.assertEqual(str(self.root), self.window.output_value.text())
        self.assertNotIn("credential", self.window.centralWidget().accessibleDescription())
        self.assertTrue(self.window.preview_button.isEnabled())
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertIn(
            "QPushButton#apply_button:disabled", self.window.styleSheet()
        )
        self.assertIn(
            "QPushButton { min-height: 32px; padding: 3px 15px; "
            "color: #15243A; background: #FFFFFF;",
            self.window.styleSheet(),
        )

    def test_missing_output_root_is_specific_and_never_actionable(self) -> None:
        self.port.preview_result = SyncPreviewResult(
            OperationStatus.FAILED,
            issues=(
                ApplicationIssue(
                    "output_root_missing",
                    IssueSeverity.ERROR,
                ),
            ),
        )

        self.window.preview_button.click()
        self._wait_idle()

        self.assertIn(
            "local destination folder does not exist",
            self.window.warning_label.text().lower(),
        )
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertEqual(0, self.window.source_table.rowCount())

    def test_preview_runs_in_worker_and_renders_reviewed_sources(self) -> None:
        self.window.preview_button.click()
        self.assertIsNotNone(self.window.active_task)
        self._wait_idle()

        self.assertEqual(1, self.port.preview_calls)
        self.assertEqual(1, self.window.source_table.rowCount())
        course_label = self.window.source_table.item(0, 0).text()
        source_label = self.window.source_table.item(0, 1).text()
        self.assertEqual(
            "COMP1000 — Introduction to Testing · Semester 2 2026",
            course_label,
        )
        self.assertEqual("Canvas", source_label)
        self.assertNotIn("comp1000", course_label)
        self.assertNotIn("canvas.invalid", source_label)
        self.assertNotIn("42", source_label)
        self.assertTrue(self.window.apply_button.isEnabled())
        self.assertIn("Planning", self.window.progress_label.text())

    def test_apply_requires_confirmation_and_uses_the_same_review_once(self) -> None:
        self.window.preview_button.click()
        self._wait_idle()
        self.window.apply_button.click()
        self.application.processEvents()
        self.assertEqual(0, self.port.apply_calls)
        self.assertIs(self.review, self.controller.review)

        self.confirm = True
        self.window.apply_button.click()
        self._wait_idle()

        self.assertEqual(1, self.port.apply_calls)
        self.assertIs(self.review, self.port.applied_review)
        self.assertFalse(self.window.apply_button.isEnabled())
        self.assertIn("complete", self.window.status_label.text().lower())

    def test_default_confirmation_uses_human_readable_course_and_source_names(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        self.window._confirm_apply_callback = None
        with patch(
            "canvas_ed_mcp.ui.daily_dashboard.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            self.assertFalse(self.window._confirm_apply(self.review, False))

        confirmation_text = question.call_args.args[2]
        self.assertIn(
            "COMP1000 — Introduction to Testing · Semester 2 2026 — Canvas",
            confirmation_text,
        )
        self.assertNotIn("canvas.invalid", confirmation_text)
        self.assertNotIn("ID 42", confirmation_text)
        self.assertNotIn("comp1000", confirmation_text)

    def test_partial_preview_keeps_warning_visible_and_changes_apply_copy(self) -> None:
        warning = ApplicationIssue(
            "authorization_failed",
            IssueSeverity.WARNING,
            provider="canvas",
            course_key="comp1000",
        )
        partial_review = _review(self.root, partial=True)
        self.port.preview_result = SyncPreviewResult(
            OperationStatus.PARTIAL,
            partial_review,
            (warning,),
        )
        self.window.preview_button.click()
        self._wait_idle()

        self.assertEqual("Apply available items", self.window.apply_button.text())
        self.assertTrue(self.window.warning_frame.isVisible())
        self.assertIn("permitted", self.window.warning_label.text().lower())
        source_warning = self.window.source_table.item(0, 3).text()
        self.assertIn("permitted", source_warning.lower())
        self.assertNotIn("authorization_failed", source_warning)

    def test_open_folder_uses_typed_service_with_expected_root(self) -> None:
        self.window.open_button.click()

        self.assertEqual(1, len(self.folder_service.requests))
        request = self.folder_service.requests[0]
        self.assertEqual(self.root, request.expected_output_root)
        self.assertEqual(self.root / "profile.toml", request.config_path)

    def test_close_with_review_discards_pending_provider_lease(self) -> None:
        self.window.preview_button.click()
        self._wait_idle()

        self.window.close()
        self.application.processEvents()

        self.assertEqual(1, self.port.discard_calls)
        self.assertEqual(1, self.port.close_calls)

    def test_close_during_preview_requests_cancellation_then_releases_port(self) -> None:
        self.window.close()
        self.application.processEvents()
        blocking = _BlockingPort(
            SyncPreviewResult(OperationStatus.SUCCEEDED, self.review),
            SyncApplyResult(OperationStatus.SUCCEEDED, self.review.sources),
        )
        self.window = self.DailyDashboardWindow(
            overview=_overview(self.root),
            controller=DailySyncController(blocking),
            request=SyncPreviewRequest("test", config_path=self.root / "profile.toml"),
            folder_service=self.folder_service,
            confirm_apply=lambda review, partial: False,
        )
        self.window.show()
        self.window.preview_button.click()
        for _ in range(200):
            self.application.processEvents()
            if blocking.entered.is_set():
                break
            QTest.qWait(5)
        self.assertTrue(blocking.entered.is_set())

        self.window.close()
        self.assertTrue(self.window.isVisible())
        self._wait_idle()

        self.assertTrue(blocking.cancellation_seen)
        self.assertEqual(1, blocking.close_calls)
        self.assertFalse(self.window.isVisible())


if __name__ == "__main__":
    unittest.main()
