from __future__ import annotations

import ctypes
import os
import tempfile
import unittest
from pathlib import Path

try:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFormLayout, QLineEdit
except ImportError:  # pragma: no cover - optional desktop dependency.
    QTest = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]
    QFormLayout = None  # type: ignore[assignment]
    QLineEdit = None  # type: ignore[assignment]

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
    SecretValue,
)
from canvas_ed_mcp.application.credentials import (
    CredentialOperationResult,
    CredentialTarget,
    credential_reference,
)
from canvas_ed_mcp.application.profiles import (
    CatalogDiscoveryResult,
    ProviderCatalogResult,
)
from canvas_ed_mcp.providers.base import CourseCandidate


class _FakeCredentialController:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int | None, bool | None]] = []
        self.failed_providers: set[str] = set()

    def target_for(
        self,
        profile: str,
        provider: str,
        *,
        canvas_base_url: str | None = None,
        reference: str | None = None,
    ) -> CredentialTarget:
        return CredentialTarget(
            profile,
            provider,
            reference or credential_reference(profile, provider),
            canvas_base_url,
        )

    def test_existing(self, target, *, cancellation, progress=None):
        self.calls.append(("test_existing", target.provider, None, None))
        if target.provider in self.failed_providers:
            return CredentialOperationResult(
                OperationStatus.FAILED,
                target.provider,
                issues=(
                    ApplicationIssue(
                        "authentication_failed",
                        IssueSeverity.ERROR,
                        provider=target.provider,
                    ),
                ),
            )
        return CredentialOperationResult(
            OperationStatus.SUCCEEDED,
            target.provider,
            verified=True,
            course_count=1,
        )

    def save_and_test(
        self,
        target,
        value,
        *,
        replace_confirmed=False,
        cancellation,
        progress=None,
    ):
        assert isinstance(value, SecretValue)
        self.calls.append(
            ("save_and_test", target.provider, len(value.value), replace_confirmed)
        )
        if target.provider in self.failed_providers:
            return CredentialOperationResult(
                OperationStatus.FAILED,
                target.provider,
                issues=(
                    ApplicationIssue(
                        "authentication_failed",
                        IssueSeverity.ERROR,
                        provider=target.provider,
                    ),
                ),
            )
        return CredentialOperationResult(
            OperationStatus.SUCCEEDED,
            target.provider,
            verified=True,
            persisted=True,
            course_count=1,
        )


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
        self.credential_controller = _FakeCredentialController()

    def tearDown(self) -> None:
        if hasattr(self, "window"):
            child = getattr(self.window, "_setup_window", None)
            if child is not None:
                child.hide()
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

    def _open_discover(
        self,
        discoverer=None,
        *,
        dashboard_opener=None,
        profile_chooser_opener=None,
    ) -> None:
        actual_discoverer = discoverer or self._success
        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=self._success,
            secret_resolver=lambda reference: "synthetic-provider-token",
            credential_controller=self.credential_controller,
            credential_confirmation=lambda provider, profile: True,
            dashboard_opener=dashboard_opener,
            profile_chooser_opener=profile_chooser_opener,
        )
        self.window.show()
        self.application.processEvents()
        self.assertTrue(self.window.profile_page.validatePage())
        self.window.next()
        self.assertEqual(1, self.window.currentId())
        self.window.connect_page.canvas_url_edit.setText(
            "https://canvas.example.edu/api/v1"
        )
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
            credential_controller=self.credential_controller,
            credential_confirmation=lambda provider, profile: True,
        )

        self.assertFalse(self.window.profile_page.validatePage())
        self.assertIn("already", self.window.profile_page.error_label.text())
        self.assertEqual(0, self.calls)
        self.assertEqual(original, self.config_path.read_bytes())

    def test_connect_page_uses_masked_transient_inputs_without_clipboard_leak(self) -> None:
        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=self._success,
            credential_controller=self.credential_controller,
            credential_confirmation=lambda provider, profile: True,
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
        self.assertEqual(QLineEdit.EchoMode.Password, page.canvas_token_edit.echoMode())
        self.assertEqual(QLineEdit.EchoMode.Password, page.ed_token_edit.echoMode())
        self.assertEqual(
            "https://<your-canvas-host>/api/v1",
            page.canvas_url_edit.placeholderText(),
        )
        self.assertEqual(
            "keyring:canvas-ed-mcp/default/canvas", page.canvas_reference
        )

        canary = "SYNTHETIC_UI_TOKEN_CANARY"
        self.application.clipboard().setText("clipboard-sentinel")
        page.canvas_url_edit.setText("https://canvas.example.edu/api/v1")
        page.canvas_token_edit.setText(canary)
        page.save_canvas_button.click()
        self.assertEqual("", page.canvas_token_edit.text())
        for _ in range(200):
            self.application.processEvents()
            if not page.credential_operations_running:
                break
            QTest.qWait(5)

        self.assertIn(("save_and_test", "canvas", len(canary), True), self.credential_controller.calls)
        self.assertEqual("clipboard-sentinel", self.application.clipboard().text())
        visible = self.window.findChildren(type(page.canvas_test_status))
        self.assertNotIn(canary, " ".join(widget.text() for widget in visible))
        self.assertNotIn(canary, page.canvas_token_edit.accessibleName())
        self.assertNotIn(canary, page.canvas_token_edit.accessibleDescription())
        self.assertIn("saved", page.canvas_test_status.text().casefold())

        page.apply_profile_defaults("semester-2")
        self.assertEqual(
            "keyring:canvas-ed-mcp/semester-2/ed", page.ed_reference
        )

    def test_connect_provider_cards_have_checkbox_glyph_and_tab_spacing(self) -> None:
        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=self._success,
            credential_controller=self.credential_controller,
            credential_confirmation=lambda provider, profile: True,
        )
        self.window.resize(1120, 540)
        self.window.show()
        self.application.processEvents()
        self.window.next()
        self.application.processEvents()
        page = self.window.connect_page

        tab_bar = page.provider_tabs.tabBar()
        tab_bottom = tab_bar.mapTo(
            self.window, tab_bar.rect().bottomLeft()
        ).y()
        group_top = page.canvas_group.mapTo(
            self.window, page.canvas_group.rect().topLeft()
        ).y()
        self.assertGreaterEqual(group_top - tab_bottom - 1, 10)

        style = self.window.styleSheet()
        checked_rule = style.split("QGroupBox::indicator:checked", 1)[1].split("}", 1)[0]
        self.assertIn("standardbutton-apply-16.png", checked_rule)
        self.assertTrue(page.canvas_group.isCheckable())
        self.assertTrue(page.canvas_group.isChecked())
        self.assertTrue(page.canvas_help_notice.details_label.isHidden())
        self.assertTrue(page.ed_help_notice.details_label.isHidden())
        page.canvas_help_notice.toggle_button.click()
        self.assertFalse(page.canvas_help_notice.details_label.isHidden())

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
            credential_controller=self.credential_controller,
            credential_confirmation=lambda provider, profile: True,
        )
        self.window.show()
        self.application.processEvents()
        self.assertTrue(self.window.profile_page.validatePage())
        self.window.next()

        page = self.window.connect_page
        page.canvas_url_edit.setText("https://canvas.example.edu/api/v1")
        page.test_canvas_button.click()
        for _ in range(200):
            self.application.processEvents()
            if page.test_canvas_button.text() == "Test":
                break
            QTest.qWait(5)

        self.assertEqual([], requested)
        self.assertIn(
            ("test_existing", "canvas", None, None),
            self.credential_controller.calls,
        )
        self.assertIn("Connected successfully", page.canvas_test_status.text())
        self.assertIn("1 course found", page.canvas_test_status.text())
        self.assertTrue(page.canvas_test_status.isVisible())

    def test_failed_ed_token_test_shows_sanitized_recovery(self) -> None:
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
            credential_controller=self.credential_controller,
            credential_confirmation=lambda provider, profile: True,
        )
        self.window.show()
        self.application.processEvents()
        self.assertTrue(self.window.profile_page.validatePage())
        self.window.next()

        page = self.window.connect_page
        self.credential_controller.failed_providers.add("ed")
        page.test_ed_button.click()
        for _ in range(200):
            self.application.processEvents()
            if page.test_ed_button.text() == "Test":
                break
            QTest.qWait(5)

        self.assertIn("authentication", page.ed_test_status.text().casefold())
        page.canvas_group.setChecked(False)
        self.assertFalse(page.validatePage())
        self.assertEqual(1, page.provider_tabs.currentIndex())
        self.assertIn("before continuing", page.ed_test_status.text())

    def test_changed_target_and_running_operation_cannot_reuse_old_success(self) -> None:
        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=self._success,
            credential_controller=self.credential_controller,
            credential_confirmation=lambda provider, profile: True,
        )
        page = self.window.connect_page
        old_url = "https://old.example.edu/api/v1"
        page.canvas_url_edit.setText(old_url)
        serial = page._credential_serials["canvas"]
        page._credential_operation_keys["canvas"] = (
            old_url,
            page.canvas_reference,
        )
        page._credential_tasks["canvas"] = object()
        page.canvas_url_edit.setText("https://new.example.edu/api/v1")

        self.assertFalse(page.validatePage())
        page._credential_operation_finished(
            serial,
            "canvas",
            CredentialOperationResult(
                OperationStatus.SUCCEEDED,
                "canvas",
                verified=True,
                persisted=True,
                course_count=1,
            ),
        )

        self.assertNotIn("canvas", page._credential_success_keys)
        self.assertIn("changed", page.canvas_test_status.text().casefold())

    def test_shutdown_clears_all_token_fields(self) -> None:
        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=self._success,
            credential_controller=self.credential_controller,
            credential_confirmation=lambda provider, profile: True,
        )
        page = self.window.connect_page
        page.canvas_token_edit.setText("SYNTHETIC_CANVAS_INPUT")
        page.ed_token_edit.setText("SYNTHETIC_ED_INPUT")

        page.request_credential_shutdown()

        self.assertEqual("", page.canvas_token_edit.text())
        self.assertEqual("", page.ed_token_edit.text())

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
        self.assertFalse(hasattr(self.window.profile_page, "output_edit"))
        self.assertEqual(
            str((self.root / "Courses").resolve(strict=False)),
            self.window._setup_window.library_root_edit.text(),
        )

    def test_embedded_dashboard_opener_is_forwarded_to_setup(self) -> None:
        opened: list[tuple[str, Path]] = []

        def open_dashboard(profile: str, config_path: Path) -> bool:
            opened.append((profile, config_path))
            return True

        self._open_discover(dashboard_opener=open_dashboard)
        self.window.accept()
        self.application.processEvents()

        embedded = self.window._setup_window._dashboard_opener
        self.assertTrue(callable(embedded))
        self.assertTrue(embedded("default", self.config_path))
        self.assertEqual([("default", self.config_path)], opened)
        self.assertTrue(self.window._setup_destination_opened)

    def test_closing_embedded_setup_returns_to_profile_chooser(self) -> None:
        returned: list[bool] = []
        self._open_discover(
            profile_chooser_opener=lambda: returned.append(True),
        )
        self.window.accept()
        self.application.processEvents()

        self.window._setup_window.windowClosed.emit(False)
        self.application.processEvents()

        self.assertEqual([True], returned)

    def test_closing_first_run_window_returns_to_profile_chooser(self) -> None:
        returned: list[bool] = []
        self.window = self.FirstRunWindow(
            config_path=self.config_path,
            output_root=self.root / "Courses",
            discoverer=self._success,
            secret_resolver=lambda reference: "synthetic-provider-token",
            credential_controller=self.credential_controller,
            credential_confirmation=lambda provider, profile: True,
            profile_chooser_opener=lambda: returned.append(True),
        )
        self.window.show()
        self.application.processEvents()

        self.window.close()
        self.application.processEvents()

        self.assertEqual([True], returned)

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
            credential_controller=self.credential_controller,
            credential_confirmation=lambda provider, profile: True,
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
