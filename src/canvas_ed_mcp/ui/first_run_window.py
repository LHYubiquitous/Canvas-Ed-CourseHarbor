"""Real Profile → Connect → Discover host for the PySide6 setup journey."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from canvas_ed_mcp.application.contracts import (
    OperationStatus,
    ProgressEvent,
    SecretValue,
)
from canvas_ed_mcp.application.credentials import (
    CredentialController,
    CredentialOperationResult,
    CredentialTarget,
    credential_reference,
)
from canvas_ed_mcp.application.first_run import (
    FirstRunControllerResult,
    FirstRunDraft,
    build_first_run_controller,
    prepare_first_run,
)
from canvas_ed_mcp.application.profiles import (
    CatalogDiscoveryResult,
    discover_course_catalogs,
    inspect_profile,
)
from canvas_ed_mcp.paths import resolve_profile_paths, validate_profile_name
from canvas_ed_mcp.secrets import resolve_secret
from canvas_ed_mcp.ui import build_config_saving_setup_window
from canvas_ed_mcp.ui.branding import DISPLAY_NAME
from canvas_ed_mcp.ui.components import CollapsibleNotice


DiscoveryCallable = Callable[..., CatalogDiscoveryResult]


class _Cancellation:
    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancellation_requested(self) -> bool:
        return self._event.is_set()

    def request(self) -> None:
        self._event.set()


class _DiscoverySignals(QObject):
    progress = Signal(int, object)
    finished = Signal(int, str, object)


class _DiscoveryTask(QRunnable):
    def __init__(
        self,
        serial: int,
        draft: FirstRunDraft,
        *,
        discoverer: DiscoveryCallable,
        secret_resolver: Callable[[str], str],
        cancellation: _Cancellation,
    ) -> None:
        super().__init__()
        self.serial = serial
        self.draft = draft
        self.discoverer = discoverer
        self.secret_resolver = secret_resolver
        self.cancellation = cancellation
        self.signals = _DiscoverySignals()

    @Slot()
    def run(self) -> None:
        try:
            result: object = self.discoverer(
                self.draft.platforms,
                requested_providers=self.draft.requested_providers,
                secret_resolver=self.secret_resolver,
                cancellation=self.cancellation,
                progress=lambda event: self.signals.progress.emit(self.serial, event),
            )
        except Exception:
            # Never reflect provider exception text or credential-bearing objects.
            result = None
        self.signals.finished.emit(self.serial, self.draft.generation, result)


class _CredentialSignals(QObject):
    finished = Signal(int, str, object)


class _CredentialTask(QRunnable):
    def __init__(
        self,
        serial: int,
        target: CredentialTarget,
        *,
        controller: CredentialController,
        value: SecretValue | None,
        cancellation: _Cancellation,
    ) -> None:
        super().__init__()
        self.serial = serial
        self.target = target
        self.controller = controller
        self.value = value
        self.cancellation = cancellation
        self.signals = _CredentialSignals()

    @Slot()
    def run(self) -> None:
        try:
            if self.value is None:
                result: object = self.controller.test_existing(
                    self.target,
                    cancellation=self.cancellation,
                )
            else:
                result = self.controller.save_and_test(
                    self.target,
                    self.value,
                    replace_confirmed=True,
                    cancellation=self.cancellation,
                )
        except Exception:
            # Never reflect backend/provider exception text or the request object.
            result = None
        finally:
            self.value = None
        self.signals.finished.emit(self.serial, self.target.provider, result)


class _ProfilePage(QWizardPage):
    def __init__(self, owner: "FirstRunWindow") -> None:
        super().__init__()
        self.owner = owner
        self.setTitle("Profile")
        self.setSubTitle(
            "Choose the name for this new local profile."
        )
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.profile_form = form
        self.profile_edit = QLineEdit(owner.initial_profile)
        self.profile_edit.setAccessibleName("Profile name")
        self.config_path_label = QLabel()
        self.config_path_label.setWordWrap(True)
        self.config_path_label.setTextInteractionFlags(
            self.config_path_label.textInteractionFlags()
        )
        form.addRow("Profile name", self.profile_edit)
        form.addRow("Configuration file", self.config_path_label)
        layout.addLayout(form)
        note = QLabel(
            "The configuration must be new. Choose the Library root and individual "
            "course folders later on the Folders step."
        )
        note.setWordWrap(True)
        note.setObjectName("notice")
        layout.addWidget(note)
        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        layout.addStretch(1)
        self.profile_edit.textChanged.connect(self._refresh_config_path)
        self._refresh_config_path()

    def _refresh_config_path(self) -> None:
        path = self.owner.config_path_for_profile(self.profile_edit.text().strip())
        self.config_path_label.setText(str(path) if path is not None else "Invalid profile")

    def validatePage(self) -> bool:
        profile = self.profile_edit.text().strip()
        try:
            validate_profile_name(profile)
        except ValueError:
            self._error("Enter a valid profile name.")
            return False
        config_path = self.owner.config_path_for_profile(profile)
        if config_path is None:
            self._error("The configuration path could not be resolved safely.")
            return False
        inspected = inspect_profile(profile, config_path=config_path)
        if inspected.exists:
            self._error(
                "This profile already has a configuration. Use Configure courses instead."
            )
            return False
        if inspected.status is not OperationStatus.SUCCEEDED:
            self._error("The configuration location could not be checked safely.")
            return False
        self.error_label.hide()
        self.owner.profile = profile
        self.owner.config_path = inspected.config_path
        self.owner.connect_page.apply_profile_defaults(profile)
        return True

    def _error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()


class _ConnectPage(QWizardPage):
    def __init__(self, owner: "FirstRunWindow") -> None:
        super().__init__()
        self.owner = owner
        self._credential_serials = {"canvas": 0, "ed": 0}
        self._credential_success_keys: dict[str, tuple[str, ...]] = {}
        self._credential_operation_keys: dict[str, tuple[str, ...]] = {}
        self._credential_tasks: dict[str, _CredentialTask] = {}
        self._credential_cancellations: dict[str, _Cancellation] = {}
        self.canvas_reference = ""
        self.ed_reference = ""
        self.setTitle("Connect")
        self.setSubTitle(
            "Save and verify tokens securely. Disable any service you do not use."
        )
        layout = QVBoxLayout(self)

        self.canvas_group = QGroupBox("Use Canvas")
        self.canvas_group.setCheckable(True)
        self.canvas_group.setChecked(True)
        self.canvas_form = QFormLayout(self.canvas_group)
        self._prepare_form(self.canvas_form)
        self.canvas_url_edit = QLineEdit()
        self.canvas_url_edit.setPlaceholderText("https://<your-canvas-host>/api/v1")
        self.canvas_url_edit.setAccessibleName("Canvas API base URL")
        canvas_help = CollapsibleNotice(
            summary="How to create a Canvas token",
            text=(
                "In your institution's Canvas, open Account → Settings → Approved "
                "Integrations → New Access Token. CourseHarbor tests it with a read-only "
                "course-list request before saving it to the native credential store."
            ),
            dismissible=False,
            accessibility_subject="Canvas token instructions",
        )
        self.canvas_help_notice = canvas_help
        (
            self.canvas_token_edit,
            self.save_canvas_button,
            self.test_canvas_button,
            self.cancel_canvas_button,
            self.canvas_test_status,
        ) = self._credential_controls("Canvas", "canvas")
        self.canvas_form.addRow(canvas_help)
        self.canvas_form.addRow("API base URL", self.canvas_url_edit)
        self.canvas_form.addRow("API token", self.canvas_token_edit)
        self.canvas_form.addRow(
            "",
            self._action_row(
                self.save_canvas_button,
                self.test_canvas_button,
                self.cancel_canvas_button,
            ),
        )
        self.canvas_form.addRow("", self.canvas_test_status)

        self.ed_group = QGroupBox("Use Ed Discussion")
        self.ed_group.setCheckable(True)
        self.ed_group.setChecked(True)
        self.ed_form = QFormLayout(self.ed_group)
        self._prepare_form(self.ed_form)
        region = QLineEdit("global")
        region.setReadOnly(True)
        region.setAccessibleName("Ed region")
        ed_help = CollapsibleNotice(
            summary="How to create an Ed token",
            text=(
                "Open the API Tokens page for your signed-in Ed region. CourseHarbor "
                "tests it with a read-only course-list request before saving it to the "
                "native credential store."
            ),
            dismissible=False,
            accessibility_subject="Ed token instructions",
        )
        self.ed_help_notice = ed_help
        (
            self.ed_token_edit,
            self.save_ed_button,
            self.test_ed_button,
            self.cancel_ed_button,
            self.ed_test_status,
        ) = self._credential_controls("Ed", "ed")
        self.ed_form.addRow(ed_help)
        self.ed_form.addRow("Region", region)
        self.ed_form.addRow("API token", self.ed_token_edit)
        self.ed_form.addRow(
            "",
            self._action_row(
                self.save_ed_button,
                self.test_ed_button,
                self.cancel_ed_button,
            ),
        )
        self.ed_form.addRow("", self.ed_test_status)

        self.provider_tabs = QTabWidget()
        self.provider_tabs.setAccessibleName("Canvas and Ed connection settings")
        self.provider_tabs.addTab(self.canvas_group, "Canvas")
        self.provider_tabs.addTab(self.ed_group, "Ed")
        layout.addWidget(self.provider_tabs, 1)
        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        layout.addStretch(1)

        self.apply_profile_defaults(owner.initial_profile)
        self.canvas_url_edit.textChanged.connect(
            lambda: self._invalidate_credential_result("canvas")
        )
        self.canvas_group.toggled.connect(
            lambda: self._invalidate_credential_result("canvas")
        )
        self.ed_group.toggled.connect(
            lambda: self._invalidate_credential_result("ed")
        )

    @staticmethod
    def _prepare_form(form: QFormLayout) -> None:
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)

    def _credential_controls(
        self, display_name: str, provider: str
    ) -> tuple[QLineEdit, QPushButton, QPushButton, QPushButton, QLabel]:
        token_edit = QLineEdit()
        token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        token_edit.setAccessibleName(f"{display_name} API token")
        token_edit.setPlaceholderText("Paste token here")
        save_button = QPushButton("Save and test")
        save_button.setAccessibleName(f"Save and test {display_name} credential")
        save_button.clicked.connect(lambda: self._start_credential_save(provider))
        test_button = QPushButton("Test existing")
        test_button.setAccessibleName(f"Test existing {display_name} credential")
        test_button.clicked.connect(lambda: self._start_credential_test(provider))
        cancel_button = QPushButton("Cancel")
        cancel_button.setAccessibleName(f"Cancel {display_name} credential operation")
        cancel_button.clicked.connect(lambda: self._cancel_credential_operation(provider))
        cancel_button.hide()
        status = QLabel()
        status.setWordWrap(True)
        status.setAccessibleName(f"{display_name} credential status")
        status.hide()
        return token_edit, save_button, test_button, cancel_button, status

    @staticmethod
    def _action_row(*buttons: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        for button in buttons:
            row.addWidget(button)
        row.addStretch(1)
        return row

    @property
    def credential_operations_running(self) -> bool:
        return bool(self._credential_tasks)

    def apply_profile_defaults(self, profile: str) -> None:
        self.canvas_reference = credential_reference(profile, "canvas")
        self.ed_reference = credential_reference(profile, "ed")
        self.cancel_credential_tests()

    def _target(self, provider: str) -> CredentialTarget:
        return self.owner.credential_controller.target_for(
            self.owner.profile,
            provider,
            canvas_base_url=(
                self.canvas_url_edit.text().strip() or None
                if provider == "canvas"
                else None
            ),
            reference=(
                self.canvas_reference if provider == "canvas" else self.ed_reference
            ),
        )

    def _widgets(
        self, provider: str
    ) -> tuple[QLineEdit, QPushButton, QPushButton, QPushButton, QLabel]:
        if provider == "canvas":
            return (
                self.canvas_token_edit,
                self.save_canvas_button,
                self.test_canvas_button,
                self.cancel_canvas_button,
                self.canvas_test_status,
            )
        return (
            self.ed_token_edit,
            self.save_ed_button,
            self.test_ed_button,
            self.cancel_ed_button,
            self.ed_test_status,
        )

    def _invalidate_credential_result(self, provider: str) -> None:
        self._credential_success_keys.pop(provider, None)
        _, _, _, _, status = self._widgets(provider)
        status.hide()
        status.clear()

    def _start_credential_save(self, provider: str) -> None:
        token_edit, _, _, _, status = self._widgets(provider)
        raw_value = token_edit.text()
        if not raw_value:
            status.setStyleSheet("color: #263B63;")
            status.setText(
                "No replacement token was entered. The existing credential was kept; "
                "use Test existing to verify it."
            )
            status.show()
            return
        try:
            value = SecretValue(raw_value)
        except ValueError:
            token_edit.clear()
            self._show_failure(provider, "invalid_credential_value")
            return
        if not self.owner.confirm_credential_change(provider, self.owner.profile):
            token_edit.clear()
            status.setStyleSheet("color: #263B63;")
            status.setText("Credential replacement was cancelled. The existing value was kept.")
            status.show()
            return
        token_edit.clear()
        self._start_credential_operation(provider, value)

    def _start_credential_test(self, provider: str) -> None:
        self._start_credential_operation(provider, None)

    def _start_credential_operation(
        self, provider: str, value: SecretValue | None
    ) -> None:
        self._cancel_credential_operation(provider, superseded=True)
        try:
            target = self._target(provider)
        except ValueError:
            self._show_failure(provider, "invalid_connection")
            return
        self._credential_success_keys.pop(provider, None)
        self._credential_serials[provider] += 1
        serial = self._credential_serials[provider]
        self._credential_operation_keys[provider] = self._credential_test_key(provider)
        cancellation = _Cancellation()
        _, save_button, test_button, cancel_button, status = self._widgets(provider)
        status.setStyleSheet("color: #263B63;")
        status.setText(
            "Testing the new token before saving…"
            if value is not None
            else "Testing the existing credential with a read-only request…"
        )
        status.show()
        save_button.setEnabled(False)
        test_button.setEnabled(False)
        cancel_button.show()
        if provider == "canvas":
            self.canvas_url_edit.setEnabled(False)
        self.owner.button(QWizard.WizardButton.BackButton).setEnabled(False)
        self.owner.button(QWizard.WizardButton.NextButton).setEnabled(False)
        task = _CredentialTask(
            serial,
            target,
            controller=self.owner.credential_controller,
            value=value,
            cancellation=cancellation,
        )
        task.signals.finished.connect(self._credential_operation_finished)
        self._credential_tasks[provider] = task
        self._credential_cancellations[provider] = cancellation
        QThreadPool.globalInstance().start(task)

    def _cancel_credential_operation(
        self, provider: str, *, superseded: bool = False
    ) -> None:
        cancellation = self._credential_cancellations.get(provider)
        if cancellation is None:
            return
        cancellation.request()
        if not superseded:
            _, _, _, cancel_button, status = self._widgets(provider)
            cancel_button.setEnabled(False)
            status.setStyleSheet("color: #263B63;")
            status.setText(
                "Cancellation requested. The operation will stop at the next safe boundary."
            )
            status.show()

    def cancel_credential_tests(self) -> None:
        for provider in ("canvas", "ed"):
            token_edit, save_button, test_button, cancel_button, _ = self._widgets(provider)
            token_edit.clear()
            self._credential_serials[provider] += 1
            cancellation = self._credential_cancellations.pop(provider, None)
            if cancellation is not None:
                cancellation.request()
            self._credential_tasks.pop(provider, None)
            self._credential_operation_keys.pop(provider, None)
            save_button.setEnabled(True)
            test_button.setEnabled(True)
            cancel_button.setEnabled(True)
            cancel_button.hide()
        self.canvas_url_edit.setEnabled(True)
        self.owner.button(QWizard.WizardButton.BackButton).setEnabled(True)
        self.owner.button(QWizard.WizardButton.NextButton).setEnabled(True)

    def request_credential_shutdown(self) -> None:
        self.clear_token_inputs()
        for provider in tuple(self._credential_tasks):
            self._cancel_credential_operation(provider)

    def clear_token_inputs(self) -> None:
        self.canvas_token_edit.clear()
        self.ed_token_edit.clear()

    @Slot(int, str, object)
    def _credential_operation_finished(
        self, serial: int, provider: str, value: object
    ) -> None:
        if serial != self._credential_serials[provider]:
            return
        self._credential_tasks.pop(provider, None)
        self._credential_cancellations.pop(provider, None)
        operation_key = self._credential_operation_keys.pop(provider, None)
        _, save_button, test_button, cancel_button, status = self._widgets(provider)
        save_button.setEnabled(True)
        test_button.setEnabled(True)
        cancel_button.setEnabled(True)
        cancel_button.hide()
        if provider == "canvas":
            self.canvas_url_edit.setEnabled(True)
        if not self._credential_tasks:
            self.owner.button(QWizard.WizardButton.BackButton).setEnabled(True)
            self.owner.button(QWizard.WizardButton.NextButton).setEnabled(True)
        if operation_key != self._credential_test_key(provider):
            self._show_failure(provider, "connection_settings_changed")
            return
        if not isinstance(value, CredentialOperationResult):
            self._show_failure(provider, "credential_operation_failed")
            return
        if value.status is OperationStatus.CANCELLED:
            self._credential_success_keys.pop(provider, None)
            status.setStyleSheet("color: #263B63;")
            status.setText("Credential operation cancelled. No new value was saved.")
            status.show()
            return
        if value.status is not OperationStatus.SUCCEEDED or not value.verified:
            code = value.issues[0].code if value.issues else "credential_operation_failed"
            self._show_failure(provider, code)
            return
        count = value.course_count or 0
        noun = "course" if count == 1 else "courses"
        suffix = " Credential saved securely." if value.persisted else " Existing credential works."
        status.setStyleSheet("color: #155724; font-weight: 600;")
        status.setText(f"Connected successfully — {count} {noun} found.{suffix}")
        status.show()
        save_button.setText("Replace credential…")
        self._credential_success_keys[provider] = self._credential_test_key(provider)

    def _show_failure(self, provider: str, code: str) -> None:
        self._credential_success_keys.pop(provider, None)
        _, _, _, _, status = self._widgets(provider)
        message = {
            "authentication_failed": "Authentication failed. Enter a fresh token and try again.",
            "authorization_failed": "The token is valid but lacks access to the requested courses.",
            "request_failed": "The provider could not be reached. Check the connection and retry.",
            "missing_keyring": "No stored credential was found. Enter a token, then choose Save and test.",
            "missing_env": "The externally managed credential is not available.",
            "unsafe_backend": "A secure native credential store is unavailable; no provider request was sent.",
            "missing_dependency": "Native credential-store support is not installed; no provider request was sent.",
            "backend_error": "The native credential store could not be accessed; no provider request was sent.",
            "credential_state_unknown": "The token was verified, but the credential-store write failed. Use Test existing to diagnose its state.",
            "replacement_confirmation_required": "Confirm the targeted credential replacement before retrying.",
            "invalid_connection": "Enter a valid HTTPS Canvas API base URL before testing.",
            "connection_settings_changed": "Connection settings changed during the test. Run the credential test again.",
            "invalid_credential_value": "Enter a non-empty token without control characters.",
        }.get(code, "The credential could not be verified safely. Check the settings and retry.")
        status.setStyleSheet("color: #7A1E1E; font-weight: 600;")
        status.setText(message)
        status.show()

    def _credential_test_key(self, provider: str) -> tuple[str, ...]:
        if provider == "canvas":
            return (self.canvas_url_edit.text().strip(), self.canvas_reference)
        return (self.ed_reference,)

    def validatePage(self) -> bool:
        if self.credential_operations_running:
            self.error_label.setText(
                "Wait for the credential operation to finish, or cancel it before continuing."
            )
            self.error_label.show()
            return False
        enabled = (
            ("canvas", self.canvas_group.isChecked()),
            ("ed", self.ed_group.isChecked()),
        )
        for provider, is_enabled in enabled:
            if not is_enabled:
                continue
            token_edit, _, _, _, status = self._widgets(provider)
            if token_edit.text():
                status.setStyleSheet("color: #7A1E1E; font-weight: 600;")
                status.setText(
                    "A token is still in the replacement field. Choose Save and test, "
                    "or clear it to keep the existing credential."
                )
                status.show()
                self.provider_tabs.setCurrentIndex(0 if provider == "canvas" else 1)
                return False
            if self._credential_success_keys.get(provider) == self._credential_test_key(provider):
                continue
            status.setStyleSheet("color: #7A1E1E; font-weight: 600;")
            status.setText(
                f"Test the {provider.title()} token successfully before continuing."
            )
            status.show()
            self.provider_tabs.setCurrentIndex(0 if provider == "canvas" else 1)
            return False
        prepared = prepare_first_run(
            profile=self.owner.profile,
            output_root=self.owner.output_root,
            config_path=self.owner.config_path,
            canvas_enabled=self.canvas_group.isChecked(),
            canvas_base_url=self.canvas_url_edit.text().strip() or None,
            canvas_credential=self.canvas_reference,
            ed_enabled=self.ed_group.isChecked(),
            ed_credential=self.ed_reference,
        )
        if prepared.status is not OperationStatus.SUCCEEDED or prepared.draft is None:
            code = prepared.issues[0].code if prepared.issues else "invalid_connection"
            message = {
                "config_exists": "This configuration now exists; nothing will be overwritten.",
                "no_provider_selected": "Enable Canvas, Ed, or both.",
                "invalid_canvas_settings": "Enter a valid HTTPS Canvas API base URL.",
                "invalid_ed_settings": "The Ed connection settings are invalid.",
            }.get(code, "The connection settings could not be validated safely.")
            self.error_label.setText(message)
            self.error_label.show()
            return False
        self.error_label.hide()
        self.owner.set_draft(prepared.draft)
        return True


class _DiscoverPage(QWizardPage):
    def __init__(self, owner: "FirstRunWindow") -> None:
        super().__init__()
        self.owner = owner
        self.setTitle("Discover")
        self.setSubTitle(
            "Read the selected providers and review their results before choosing courses."
        )
        layout = QVBoxLayout(self)
        self.summary = QLabel("Discovery has not started.")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("notice")
        layout.addWidget(self.summary)
        self.provider_results = QPlainTextEdit()
        self.provider_results.setReadOnly(True)
        self.provider_results.setAccessibleName("Provider discovery results")
        layout.addWidget(self.provider_results, 1)
        actions = QHBoxLayout()
        self.retry_button = QPushButton("Retry discovery")
        self.cancel_button = QPushButton("Cancel discovery")
        self.accept_partial_button = QPushButton("Continue with available providers only")
        self.retry_button.clicked.connect(owner.start_discovery)
        self.cancel_button.clicked.connect(owner.cancel_discovery)
        self.accept_partial_button.clicked.connect(owner.accept_available_only)
        actions.addWidget(self.retry_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch(1)
        actions.addWidget(self.accept_partial_button)
        layout.addLayout(actions)
        self.retry_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.accept_partial_button.setEnabled(False)

    def initializePage(self) -> None:
        self.owner.start_discovery()

    def isComplete(self) -> bool:
        return bool(self.owner.controller_result and self.owner.controller_result.ready)


class FirstRunWindow(QWizard):
    """Production first-run host; the only effect before Review is read-only discovery."""

    def __init__(
        self,
        *,
        profile: str = "default",
        config_path: Path | None = None,
        output_root: Path | None = None,
        discoverer: DiscoveryCallable = discover_course_catalogs,
        secret_resolver: Callable[[str], str] = resolve_secret,
        credential_controller: CredentialController | None = None,
        credential_confirmation: Callable[[str, str], bool] | None = None,
        dashboard_opener: Callable[[str, Path], bool] | None = None,
        profile_chooser_opener: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.initial_profile = profile
        self.initial_config_path = config_path
        self.initial_output_root = (output_root or (Path.home() / "Desktop")).resolve(
            strict=False
        )
        self.profile = profile
        self.config_path = self.config_path_for_profile(profile) or Path()
        self.output_root = self.initial_output_root
        self.discoverer = discoverer
        self.secret_resolver = secret_resolver
        self.credential_controller = credential_controller or CredentialController()
        self._dashboard_opener = dashboard_opener
        self._profile_chooser_opener = profile_chooser_opener
        self._setup_destination_opened = False
        self._setup_close_handled = False
        self._profile_chooser_returned = False
        self._credential_confirmation = (
            credential_confirmation or self._default_credential_confirmation
        )
        self.draft: FirstRunDraft | None = None
        self.discovery_result: CatalogDiscoveryResult | None = None
        self.controller_result: FirstRunControllerResult | None = None
        self._discovery_serial = 0
        self._discovery_running = False
        self._cancellation: _Cancellation | None = None
        self._discovery_task: _DiscoveryTask | None = None
        self._setup_window: Any | None = None

        self.setWindowTitle(f"{DISPLAY_NAME} — Set up")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.resize(900, 650)
        self.setMinimumSize(760, 560)
        self.profile_page = _ProfilePage(self)
        self.connect_page = _ConnectPage(self)
        self.discover_page = _DiscoverPage(self)
        self.addPage(self.profile_page)
        self.addPage(self.connect_page)
        self.addPage(self.discover_page)
        self.setButtonText(QWizard.WizardButton.NextButton, "Continue")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Open course selection")
        self.currentIdChanged.connect(self._page_changed)
        self._apply_style()

    def confirm_credential_change(self, provider: str, profile: str) -> bool:
        return bool(self._credential_confirmation(provider, profile))

    def _default_credential_confirmation(self, provider: str, profile: str) -> bool:
        display_name = "Canvas" if provider == "canvas" else "Ed"
        reference = credential_reference(profile, provider)
        selected = QMessageBox.question(
            self,
            f"Save {display_name} credential?",
            f"Profile: {profile}\nProvider: {display_name}\nLocal target: {reference}\n\n"
            "The token will be tested with a read-only request first. If a local "
            "credential already exists at this target, a successful test will replace it. "
            "This does not create or revoke a remote token.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return selected == QMessageBox.StandardButton.Save

    def config_path_for_profile(self, profile: str) -> Path | None:
        try:
            validate_profile_name(profile)
            selected = self.initial_config_path or resolve_profile_paths(profile).config_file
            return Path(selected).resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return None

    def set_draft(self, draft: FirstRunDraft) -> None:
        if self._cancellation is not None:
            self._cancellation.request()
        self._discovery_serial += 1
        self.draft = draft
        self.discovery_result = None
        self.controller_result = None

    def _page_changed(self, page_id: int) -> None:
        if page_id != 1:
            self.connect_page.cancel_credential_tests()
        if page_id != 2 and self._discovery_running:
            self.cancel_discovery()

    def start_discovery(self) -> None:
        if self.draft is None or self._discovery_running:
            return
        inspected = inspect_profile(
            self.draft.profile,
            config_path=self.draft.config_path,
        )
        if inspected.exists:
            self.discovery_result = None
            self.controller_result = None
            self.discover_page.summary.setText(
                "This configuration was created by another process. Discovery was not "
                "started and nothing will be overwritten."
            )
            self.discover_page.provider_results.clear()
            self.discover_page.retry_button.setEnabled(False)
            self.discover_page.cancel_button.setEnabled(False)
            self.discover_page.accept_partial_button.setEnabled(False)
            self.discover_page.completeChanged.emit()
            return
        if inspected.status is not OperationStatus.SUCCEEDED:
            self.discovery_result = None
            self.controller_result = None
            self.discover_page.summary.setText(
                "The configuration target could not be checked safely. Discovery was not started."
            )
            self.discover_page.completeChanged.emit()
            return
        self._discovery_serial += 1
        serial = self._discovery_serial
        self.discovery_result = None
        self.controller_result = None
        self._discovery_running = True
        self._cancellation = _Cancellation()
        self.discover_page.summary.setText(
            "Connecting securely and reading course catalogues…"
        )
        self.discover_page.provider_results.clear()
        self.discover_page.retry_button.setEnabled(False)
        self.discover_page.cancel_button.setEnabled(True)
        self.discover_page.accept_partial_button.setEnabled(False)
        self.button(QWizard.WizardButton.BackButton).setEnabled(False)
        self.button(QWizard.WizardButton.FinishButton).setEnabled(False)
        task = _DiscoveryTask(
            serial,
            self.draft,
            discoverer=self.discoverer,
            secret_resolver=self.secret_resolver,
            cancellation=self._cancellation,
        )
        task.signals.progress.connect(self._discovery_progress)
        task.signals.finished.connect(self._discovery_finished)
        self._discovery_task = task
        QThreadPool.globalInstance().start(task)

    def cancel_discovery(self) -> None:
        if not self._discovery_running or self._cancellation is None:
            return
        self._cancellation.request()
        self.discover_page.summary.setText(
            "Cancellation requested. Finishing the current provider request safely…"
        )
        self.discover_page.cancel_button.setEnabled(False)

    @Slot(int, object)
    def _discovery_progress(self, serial: int, value: object) -> None:
        if serial != self._discovery_serial or not isinstance(value, ProgressEvent):
            return
        if value.provider:
            self.discover_page.summary.setText(
                f"Reading {value.provider.title()} courses…"
            )

    @Slot(int, str, object)
    def _discovery_finished(self, serial: int, generation: str, value: object) -> None:
        if serial != self._discovery_serial:
            return
        self._discovery_running = False
        self._discovery_task = None
        self.discover_page.cancel_button.setEnabled(False)
        self.discover_page.retry_button.setEnabled(True)
        self.button(QWizard.WizardButton.BackButton).setEnabled(True)
        if (
            self.draft is None
            or generation != self.draft.generation
            or not isinstance(value, CatalogDiscoveryResult)
        ):
            self.discover_page.summary.setText(
                "Course discovery could not be completed safely. You can retry."
            )
            self.discover_page.completeChanged.emit()
            return
        self.discovery_result = value
        lines: list[str] = []
        for result in value.providers:
            issue_codes = ", ".join(issue.code for issue in result.issues) or "none"
            lines.append(
                f"{result.provider.title()}: {result.status.value} — "
                f"{len(result.courses)} course(s) — issues: {issue_codes}"
            )
        self.discover_page.provider_results.setPlainText("\n".join(lines))
        built = build_first_run_controller(
            self.draft,
            value,
            generation=generation,
        )
        self.controller_result = built if built.ready else None
        if built.ready:
            course_count = sum(len(courses) for _, courses in built.catalogs)
            self.discover_page.summary.setText(
                f"Discovery complete: {course_count} course(s) are ready for selection."
            )
        elif value.status is OperationStatus.PARTIAL:
            self.discover_page.summary.setText(
                "Some providers failed. Retry, go back to change settings, or explicitly "
                "continue with successful providers only; failed providers will be disabled."
            )
            self.discover_page.accept_partial_button.setEnabled(True)
        elif value.status is OperationStatus.CANCELLED:
            self.discover_page.summary.setText("Discovery was cancelled. Nothing was saved.")
        else:
            self.discover_page.summary.setText(
                "No usable course catalogue was returned. Check the credential references "
                "and connection settings, then retry."
            )
        self.discover_page.completeChanged.emit()

    def accept_available_only(self) -> None:
        if self.draft is None or self.discovery_result is None:
            return
        built = build_first_run_controller(
            self.draft,
            self.discovery_result,
            generation=self.draft.generation,
            accept_available_only=True,
        )
        if not built.ready:
            self.discover_page.summary.setText(
                "There are no usable courses to continue with. Retry discovery."
            )
            return
        self.controller_result = built
        retained = ", ".join(provider for provider, _ in built.catalogs)
        self.discover_page.summary.setText(
            f"Continuing only with: {retained}. Failed providers will be disabled in the config."
        )
        self.discover_page.accept_partial_button.setEnabled(False)
        self.discover_page.completeChanged.emit()

    def accept(self) -> None:
        if (
            self.controller_result is None
            or not self.controller_result.ready
            or self.draft is None
        ):
            return
        embedded_dashboard_opener = (
            self._open_embedded_dashboard
            if self._dashboard_opener is not None
            else None
        )
        self._setup_window = build_config_saving_setup_window(
            self.controller_result.controller,
            self.draft.config_path,
            dashboard_opener=embedded_dashboard_opener,
        )
        self._setup_window.windowClosed.connect(self._setup_closed)
        self._setup_window.show()
        self._setup_window.raise_()
        self.hide()

    def _open_embedded_dashboard(self, profile: str, config_path: Path) -> bool:
        if self._dashboard_opener is None:
            return False
        # The application router closes this hidden wizard while replacing it
        # with the dashboard. Mark the handoff before invoking the router so
        # that the wizard's close event cannot reopen the profile chooser.
        self._setup_destination_opened = True
        try:
            opened = bool(self._dashboard_opener(profile, config_path))
        except Exception:
            self._setup_destination_opened = False
            raise
        if not opened:
            self._setup_destination_opened = False
        return opened

    def _return_to_profile_chooser(self) -> None:
        if (
            self._profile_chooser_returned
            or self._setup_destination_opened
            or self._profile_chooser_opener is None
        ):
            return
        self._profile_chooser_returned = True
        try:
            self._profile_chooser_opener()
        except Exception:
            self._profile_chooser_returned = False
            raise

    def _setup_closed(self, _saved: bool) -> None:
        if self._setup_close_handled:
            return
        self._setup_close_handled = True
        self._return_to_profile_chooser()
        self.close()

    def reject(self) -> None:
        self.connect_page.clear_token_inputs()
        if self._discovery_running:
            self.cancel_discovery()
            return
        if self.connect_page.credential_operations_running:
            self.connect_page.request_credential_shutdown()
            return
        self._return_to_profile_chooser()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.connect_page.clear_token_inputs()
        if self._discovery_running:
            self.cancel_discovery()
            event.ignore()
            return
        if self.connect_page.credential_operations_running:
            self.connect_page.request_credential_shutdown()
            event.ignore()
            return
        self._return_to_profile_chooser()
        super().closeEvent(event)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWizard, QWizardPage { background: #F6F8FB; color: #172033; }
            QWidget { font-family: "Segoe UI Variable", "Segoe UI"; font-size: 10pt; }
            QLineEdit, QPlainTextEdit { background: white; border: 1px solid #C7D0DE; border-radius: 5px; padding: 6px; }
            QLineEdit:focus, QPlainTextEdit:focus { border: 2px solid #2F6FED; }
            QGroupBox { background: white; border: 1px solid #DDE4F0; border-radius: 7px; margin-top: 10px; padding-top: 10px; font-weight: 650; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QGroupBox::indicator { width: 16px; height: 16px; border: 2px solid #65758B; border-radius: 3px; background: white; }
            QGroupBox::indicator:checked {
                background: #E7F0FF; border-color: #174EA6;
                image: url(:/qt-project.org/styles/commonstyle/images/standardbutton-apply-16.png);
            }
            QTabWidget::pane { border: 1px solid #C7D0DE; border-radius: 6px; background: #F6F8FB; padding-top: 12px; }
            QTabBar::tab { min-width: 120px; padding: 9px 18px; background: #E7ECF4; border: 1px solid #C7D0DE; }
            QTabBar::tab:selected { background: white; color: #174EA6; font-weight: 650; }
            QPushButton { min-height: 30px; padding: 2px 14px; background: white; border: 1px solid #B8C3D3; border-radius: 5px; }
            QPushButton:hover { background: #EDF3FF; border-color: #7FA6ED; }
            QPushButton:focus { border: 2px solid #174EA6; background: #EDF3FF; }
            QPushButton:disabled { color: #9AA3B2; background: #EEF1F5; border-color: #D8DEE8; }
            QLabel#notice { color: #263B63; background: #E7F0FF; border: 1px solid #9EBBEA; border-radius: 5px; padding: 9px; }
            QFrame#collapsible_notice { color: #263B63; background: #F3F7FF; border: 1px solid #C9D8F3; border-radius: 5px; }
            QFrame#collapsible_notice QLabel#notice_summary { color: #263B63; font-weight: 650; }
            QFrame#collapsible_notice QLabel#notice_details { color: #4D5E78; }
            QFrame#collapsible_notice QPushButton#notice_toggle_button { min-height: 24px; padding: 1px 8px; color: #214FA7; background: transparent; border: none; font-weight: 650; }
            QLabel#error { color: #7A1E1E; background: #FDE8E7; border: 1px solid #E8AAA6; border-radius: 5px; padding: 9px; }
            """
        )


def main(argv: list[str] | None = None) -> int:
    from .icon import apply_application_icon

    parser = argparse.ArgumentParser(description=f"{DISPLAY_NAME} first-run setup")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv)
    apply_application_icon(application)
    window = FirstRunWindow(
        profile=args.profile,
        config_path=args.config,
        output_root=args.output_root,
    )
    window.show()
    return application.exec()


if __name__ == "__main__":  # pragma: no cover - manual production entry point.
    raise SystemExit(main())


__all__ = ["FirstRunWindow", "main"]
