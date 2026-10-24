from __future__ import annotations

import ctypes
import os
import tempfile
import unittest
from pathlib import Path

try:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFormLayout
except ImportError:  # pragma: no cover - optional desktop dependency.
    QTest = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]
    QFormLayout = None  # type: ignore[assignment]

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
)
from canvas_ed_mcp.application.profiles import (
    CatalogDiscoveryResult,
    ProviderCatalogResult,
)
from canvas_ed_mcp.providers.base import CourseCandidate


def _windows_gui_available() -> bool:
    if os.name != "nt" or QApplication is None:
        return False
    try:
        user32 = ctypes.windll.user32
        return bool(user32.GetProcessWindowStation() and user32.GetDesktopWindow())
    except (AttributeError, OSError):
        return False


@unittest.skipUnless(_windows_gui_available(), "Windows GUI session is unavailable")
class FirstRunWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        from canvas_ed_mcp.ui.first_run_window import FirstRunWindow

        cls.FirstRunWindow = FirstRunWindow

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config_path = self.root / "profiles" / "default.toml"
        self.calls = 0

    def tearDown(self) -> None:
        if hasattr(self, "window"):
            child = getattr(self.window, "_setup_window", None)
            if child is not None:
                child.close()
            self.window.close()
        self.application.processEvents()
        self.temporary.cleanup()

    @staticmethod
    def _canvas() -> CourseCandidate:
        return CourseCandidate(
            "canvas",
            "canvas.example.edu",
            "42",
            "COMP1000",
            "Real Canvas course",
            "2027 S1",
        )

    @staticmethod
    def _ed() -> CourseCandidate:
        return CourseCandidate(
            "ed",
            "edstem.org",
            "84",
            "COMP1000",
            "Real Ed course",
            "2027 S1",
        )

    def _success(self, *args, **kwargs) -> CatalogDiscoveryResult:
        self.calls += 1
        return CatalogDiscoveryResult(
            OperationStatus.SUCCEEDED,
            (
                ProviderCatalogResult(
                    "canvas", OperationStatus.SUCCEEDED, (self._canvas(),)
                ),
                ProviderCatalogResult("ed", OperationStatus.SUCCEEDED, (self._ed(),)),
            ),
        )

    def _open_discover(self, discoverer=None) -> None:
        actual_discoverer = discoverer or self._success
        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=self._success,
            secret_resolver=lambda reference: "synthetic-provider-token",
        )
        self.window.show()
        self.application.processEvents()
        self.assertTrue(self.window.profile_page.validatePage())
        self.window.next()
        self.assertEqual(1, self.window.currentId())
        for button in (
            self.window.connect_page.test_canvas_button,
            self.window.connect_page.test_ed_button,
        ):
            button.click()
            for _ in range(200):
                self.application.processEvents()
                if button.text() == "Test":
                    break
                QTest.qWait(5)
        self.calls = 0
        self.window.discoverer = actual_discoverer
        self.assertTrue(self.window.connect_page.validatePage())
        self.window.next()
        self.assertEqual(2, self.window.currentId())
        for _ in range(200):
            self.application.processEvents()
            if not self.window._discovery_running:
                break
            QTest.qWait(5)

    def test_existing_profile_is_rejected_before_discovery(self) -> None:
        self.config_path.parent.mkdir(parents=True)
        original = b"existing-profile"
        self.config_path.write_bytes(original)
        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=self._success,
            secret_resolver=lambda reference: self.fail("secret was resolved"),
        )

        self.assertFalse(self.window.profile_page.validatePage())
        self.assertIn("already", self.window.profile_page.error_label.text())
        self.assertEqual(0, self.calls)
        self.assertEqual(original, self.config_path.read_bytes())

    def test_connect_page_explains_and_copies_profile_specific_commands(self) -> None:
        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=self._success,
        )
        page = self.window.connect_page

        expected_growth = QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        self.assertEqual(
            expected_growth, self.window.profile_page.profile_form.fieldGrowthPolicy()
        )
        self.assertEqual(expected_growth, page.canvas_form.fieldGrowthPolicy())
        self.assertEqual(expected_growth, page.ed_form.fieldGrowthPolicy())

        self.assertEqual(2, page.provider_tabs.count())
        self.assertEqual("Canvas", page.provider_tabs.tabText(0))
        self.assertEqual("Ed", page.provider_tabs.tabText(1))
        self.assertTrue(page.canvas_setup_command_edit.isReadOnly())
        self.assertTrue(page.ed_setup_command_edit.isReadOnly())
        self.assertEqual(0, page.canvas_setup_command_edit.cursorPosition())
        self.assertEqual(0, page.ed_setup_command_edit.cursorPosition())
        self.assertIn(
            "keyring set canvas-ed-mcp/default canvas",
            page.canvas_setup_command_edit.text(),
        )
        self.assertIn(
            "keyring set canvas-ed-mcp/default ed",
            page.ed_setup_command_edit.text(),
        )
        self.assertEqual(
            "keyring:canvas-ed-mcp/default/canvas",
            page.canvas_credential_edit.text(),
        )

        page.copy_canvas_command_button.click()
        self.application.processEvents()
        self.assertEqual(
            page.canvas_setup_command_edit.text(), self.application.clipboard().text()
        )
        self.assertEqual("Command copied.", page.canvas_copy_status.text())

        page.apply_profile_defaults("semester-2")
        self.assertIn(
            "keyring set canvas-ed-mcp/semester-2 canvas",
            page.canvas_setup_command_edit.text(),
        )
        self.assertEqual(
            "keyring:canvas-ed-mcp/semester-2/ed",
            page.ed_credential_edit.text(),
        )

    def test_canvas_token_test_reports_discovered_course_count(self) -> None:
        requested: list[tuple[str, ...]] = []

        def canvas_only(*args, **kwargs) -> CatalogDiscoveryResult:
            requested.append(kwargs["requested_providers"])
            return CatalogDiscoveryResult(
                OperationStatus.SUCCEEDED,
                (
                    ProviderCatalogResult(
                        "canvas", OperationStatus.SUCCEEDED, (self._canvas(),)
                    ),
                ),
            )

        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=canvas_only,
            secret_resolver=lambda reference: "synthetic-provider-token",
        )
        self.window.show()
        self.application.processEvents()
        self.assertTrue(self.window.profile_page.validatePage())
        self.window.next()

        page = self.window.connect_page
        page.test_canvas_button.click()
        for _ in range(200):
            self.application.processEvents()
            if page.test_canvas_button.text() == "Test":
                break
            QTest.qWait(5)

        self.assertEqual([("canvas",)], requested)
        self.assertIn("Connected successfully", page.canvas_test_status.text())
        self.assertIn("1 course found", page.canvas_test_status.text())
        self.assertTrue(page.canvas_test_status.isVisible())

    def test_failed_ed_token_test_offers_copyable_reset_command(self) -> None:
        def failed_ed(*args, **kwargs) -> CatalogDiscoveryResult:
            return CatalogDiscoveryResult(
                OperationStatus.FAILED,
                (
                    ProviderCatalogResult(
                        "ed",
                        OperationStatus.FAILED,
                        issues=(
                            ApplicationIssue(
                                "authentication_failed",
                                IssueSeverity.ERROR,
                                provider="ed",
                            ),
                        ),
                    ),
                ),
            )

        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=failed_ed,
            secret_resolver=lambda reference: "synthetic-provider-token",
        )
        self.window.show()
        self.application.processEvents()
        self.assertTrue(self.window.profile_page.validatePage())
        self.window.next()

        page = self.window.connect_page
        page.test_ed_button.click()
        for _ in range(200):
            self.application.processEvents()
            if page.test_ed_button.text() == "Test":
                break
            QTest.qWait(5)

        self.assertIn("Token test failed", page.ed_test_status.text())
        self.assertEqual("Copy reset command", page.copy_ed_command_button.text())
        page.canvas_group.setChecked(False)
        self.assertFalse(page.validatePage())
        self.assertEqual(1, page.provider_tabs.currentIndex())
        self.assertIn("before continuing", page.ed_test_status.text())
        page.copy_ed_command_button.click()
        self.assertEqual(
            page.ed_setup_command_edit.text(), self.application.clipboard().text()
        )

    def test_successful_discovery_hands_real_catalog_to_existing_setup_window(self) -> None:
        self._open_discover()

        self.assertEqual(1, self.calls)
        self.assertTrue(self.window.discover_page.isComplete())
        self.assertIn("2 course", self.window.discover_page.summary.text())
        self.assertFalse(self.config_path.exists())
        self.window.accept()
        self.application.processEvents()

        self.assertFalse(self.window.isVisible())
        self.assertIsNotNone(self.window._setup_window)
        self.assertEqual(
            2, len(self.window._setup_window.controller.course_keys)
        )
        self.assertEqual(
            self.config_path.resolve(strict=False),
            self.window._setup_window._save_port.config_path,
        )
        self.assertFalse((self.root / "Courses").exists())

    def test_partial_discovery_requires_explicit_available_only_action(self) -> None:
        def partial(*args, **kwargs) -> CatalogDiscoveryResult:
            self.calls += 1
            return CatalogDiscoveryResult(
                OperationStatus.PARTIAL,
                (
                    ProviderCatalogResult(
                        "canvas", OperationStatus.SUCCEEDED, (self._canvas(),)
                    ),
                    ProviderCatalogResult(
                        "ed",
                        OperationStatus.FAILED,
                        issues=(
                            ApplicationIssue(
                                "authentication_failed",
                                IssueSeverity.ERROR,
                                provider="ed",
                            ),
                        ),
                    ),
                ),
            )

        self._open_discover(partial)

        self.assertFalse(self.window.discover_page.isComplete())
        self.assertTrue(self.window.discover_page.accept_partial_button.isEnabled())
        self.window.accept_available_only()
        self.assertTrue(self.window.discover_page.isComplete())
        self.assertIn("canvas", self.window.discover_page.summary.text())
        self.assertFalse(self.window.controller_result.platforms.ed.enabled)

    def test_stale_worker_result_is_ignored(self) -> None:
        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=self._success,
        )
        current_serial = self.window._discovery_serial

        self.window._discovery_finished(
            current_serial - 1,
            "0" * 64,
            self._success(),
        )

        self.assertIsNone(self.window.discovery_result)
        self.assertIsNone(self.window.controller_result)

    def test_retry_rechecks_config_before_secret_or_network_access(self) -> None:
        self._open_discover()
        self.assertEqual(1, self.calls)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text("created elsewhere", encoding="utf-8")

        self.window.start_discovery()
        self.application.processEvents()

        self.assertEqual(1, self.calls)
        self.assertIsNone(self.window.discovery_result)
        self.assertIsNone(self.window.controller_result)
        self.assertIn("another process", self.window.discover_page.summary.text())


if __name__ == "__main__":
    unittest.main()
