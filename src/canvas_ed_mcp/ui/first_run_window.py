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
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from canvas_ed_mcp.application.contracts import OperationStatus, ProgressEvent
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


class _ProfilePage(QWizardPage):
    def __init__(self, owner: "FirstRunWindow") -> None:
        super().__init__()
        self.owner = owner
        self.setTitle("Profile")
        self.setSubTitle(
            "Choose where this new profile and its local course library belong."
        )
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.profile_form = form
        self.profile_edit = QLineEdit(owner.initial_profile)
        self.profile_edit.setAccessibleName("Profile name")
        self.output_edit = QLineEdit(str(owner.initial_output_root))
        self.output_edit.setAccessibleName("Course library location")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_edit, 1)
        output_row.addWidget(browse)
        self.config_path_label = QLabel()
        self.config_path_label.setWordWrap(True)
        self.config_path_label.setTextInteractionFlags(
            self.config_path_label.textInteractionFlags()
        )
        form.addRow("Profile name", self.profile_edit)
        form.addRow("Local course library", output_row)
        form.addRow("Configuration file", self.config_path_label)
        layout.addLayout(form)
        note = QLabel(
            "The configuration must be new. Nothing is created on this page."
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

    def _browse_output(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose local course library", self.output_edit.text()
        )
        if selected:
            self.output_edit.setText(selected)

    def _refresh_config_path(self) -> None:
        path = self.owner.config_path_for_profile(self.profile_edit.text().strip())
        self.config_path_label.setText(str(path) if path is not None else "Invalid profile")

    def validatePage(self) -> bool:
        profile = self.profile_edit.text().strip()
        try:
            validate_profile_name(profile)
            output_root = Path(self.output_edit.text().strip()).expanduser()
            if not output_root.is_absolute():
                raise ValueError
            output_root = output_root.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            self._error("Enter a valid profile name and an absolute course-library path.")
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
        self.owner.output_root = output_root
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
        self._test_serials = {"canvas": 0, "ed": 0}
        self._test_generations: dict[str, str] = {}
        self._test_success_keys: dict[str, tuple[str, ...]] = {}
        self._test_tasks: dict[str, _DiscoveryTask] = {}
        self._test_cancellations: dict[str, _Cancellation] = {}
        self.setTitle("Connect")
        self.setSubTitle(
            "Set up and test each service. Disable any service you do not use."
        )
        layout = QVBoxLayout(self)

        self.canvas_group = QGroupBox("Use Canvas")
        self.canvas_group.setCheckable(True)
        self.canvas_group.setChecked(True)
        canvas_form = QFormLayout(self.canvas_group)
        canvas_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        canvas_form.setHorizontalSpacing(18)
        canvas_form.setVerticalSpacing(12)
        self.canvas_form = canvas_form
        self.canvas_url_edit = QLineEdit("https://canvas.sydney.edu.au/api/v1")
        self.canvas_url_edit.setAccessibleName("Canvas API base URL")
        canvas_token_help = QLabel(
            "<ul>"
            "<li>Create a token: Account → Settings → Approved Integrations → New Access Token.</li>"
            "<li>Copy and run the command below. Paste the token at the Password prompt.</li>"
            "<li>Click Test. Normally leave Stored token location unchanged.</li>"
            "</ul>"
        )
        canvas_token_help.setWordWrap(True)
        self.canvas_setup_command_edit = QLineEdit()
        self.canvas_setup_command_edit.setReadOnly(True)
        self.canvas_setup_command_edit.setAccessibleName("Canvas token setup command")
        self.copy_canvas_command_button = QPushButton("Copy command")
        self.copy_canvas_command_button.setAccessibleName(
            "Copy Canvas token setup command"
        )
        self.copy_canvas_command_button.clicked.connect(
            lambda: self._copy_setup_command("Canvas", self.canvas_setup_command_edit)
        )
        canvas_command_row = QHBoxLayout()
        canvas_command_row.addWidget(self.canvas_setup_command_edit, 1)
        canvas_command_row.addWidget(self.copy_canvas_command_button)
        self.canvas_copy_status = QLabel()
        self.canvas_copy_status.setAccessibleName("Canvas command copy status")
        self.canvas_copy_status.hide()
        self.canvas_credential_edit = QLineEdit()
        self.canvas_credential_edit.setAccessibleName("Canvas credential reference")
        self.test_canvas_button = QPushButton("Test")
        self.test_canvas_button.setAccessibleName("Test Canvas token")
        self.test_canvas_button.clicked.connect(
            lambda: self._start_credential_test("canvas")
        )
        canvas_credential_row = QHBoxLayout()
        canvas_credential_row.addWidget(self.canvas_credential_edit, 1)
        canvas_credential_row.addWidget(self.test_canvas_button)
        self.canvas_test_status = QLabel()
        self.canvas_test_status.setWordWrap(True)
        self.canvas_test_status.setAccessibleName("Canvas token test status")
        self.canvas_test_status.hide()
        canvas_form.addRow(canvas_token_help)
        canvas_form.addRow("API base URL", self.canvas_url_edit)
        canvas_form.addRow("Save token command", canvas_command_row)
        canvas_form.addRow("", self.canvas_copy_status)
        canvas_form.addRow("Stored token location", canvas_credential_row)
        canvas_form.addRow("", self.canvas_test_status)

        self.ed_group = QGroupBox("Use Ed Discussion")
        self.ed_group.setCheckable(True)
        self.ed_group.setChecked(True)
        ed_form = QFormLayout(self.ed_group)
        ed_form.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        ed_form.setHorizontalSpacing(18)
        ed_form.setVerticalSpacing(12)
        self.ed_form = ed_form
        region = QLineEdit("global")
        region.setReadOnly(True)
        region.setAccessibleName("Ed region")
        ed_token_help = QLabel(
            "<ul>"
            "<li>Create a token: Ed Settings → API Tokens.</li>"
            "<li>Copy and run the command below. Paste the token at the Password prompt.</li>"
            "<li>Click Test. Normally leave Stored token location unchanged.</li>"
            "</ul>"
        )
        ed_token_help.setWordWrap(True)
        self.ed_setup_command_edit = QLineEdit()
        self.ed_setup_command_edit.setReadOnly(True)
        self.ed_setup_command_edit.setAccessibleName("Ed token setup command")
        self.copy_ed_command_button = QPushButton("Copy command")
        self.copy_ed_command_button.setAccessibleName("Copy Ed token setup command")
        self.copy_ed_command_button.clicked.connect(
            lambda: self._copy_setup_command("Ed", self.ed_setup_command_edit)
        )
        ed_command_row = QHBoxLayout()
        ed_command_row.addWidget(self.ed_setup_command_edit, 1)
        ed_command_row.addWidget(self.copy_ed_command_button)
        self.ed_copy_status = QLabel()
        self.ed_copy_status.setAccessibleName("Ed command copy status")
        self.ed_copy_status.hide()
        self.ed_credential_edit = QLineEdit()
        self.ed_credential_edit.setAccessibleName("Ed credential reference")
        self.test_ed_button = QPushButton("Test")
        self.test_ed_button.setAccessibleName("Test Ed token")
        self.test_ed_button.clicked.connect(lambda: self._start_credential_test("ed"))
        ed_credential_row = QHBoxLayout()
        ed_credential_row.addWidget(self.ed_credential_edit, 1)
        ed_credential_row.addWidget(self.test_ed_button)
        self.ed_test_status = QLabel()
        self.ed_test_status.setWordWrap(True)
        self.ed_test_status.setAccessibleName("Ed token test status")
        self.ed_test_status.hide()
        ed_form.addRow(ed_token_help)
        ed_form.addRow("Region", region)
        ed_form.addRow("Save token command", ed_command_row)
        ed_form.addRow("", self.ed_copy_status)
        ed_form.addRow("Stored token location", ed_credential_row)
        ed_form.addRow("", self.ed_test_status)

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
            lambda: self._invalidate_credential_test("canvas")
        )
        self.canvas_credential_edit.textChanged.connect(
            lambda: self._invalidate_credential_test("canvas")
        )
        self.ed_credential_edit.textChanged.connect(
            lambda: self._invalidate_credential_test("ed")
        )
        self.canvas_group.toggled.connect(
            lambda: self._invalidate_credential_test("canvas")
        )
        self.ed_group.toggled.connect(lambda: self._invalidate_credential_test("ed"))

    def apply_profile_defaults(self, profile: str) -> None:
        old_canvas_default = getattr(self, "_canvas_default", "")
        old_ed_default = getattr(self, "_ed_default", "")
        new_canvas = f"keyring:canvas-ed-mcp/{profile}/canvas"
        new_ed = f"keyring:canvas-ed-mcp/{profile}/ed"
        self.canvas_setup_command_edit.setText(
            self._keyring_setup_command(profile, "canvas")
        )
        self.ed_setup_command_edit.setText(self._keyring_setup_command(profile, "ed"))
        self.canvas_setup_command_edit.setCursorPosition(0)
        self.ed_setup_command_edit.setCursorPosition(0)
        if not self.canvas_credential_edit.text() or self.canvas_credential_edit.text() == old_canvas_default:
            self.canvas_credential_edit.setText(new_canvas)
        if not self.ed_credential_edit.text() or self.ed_credential_edit.text() == old_ed_default:
            self.ed_credential_edit.setText(new_ed)
        self._canvas_default = new_canvas
        self._ed_default = new_ed

    @staticmethod
    def _keyring_setup_command(profile: str, account: str) -> str:
        service = f"canvas-ed-mcp/{profile}"
        if sys.platform == "win32":
            return f'& "{sys.executable}" -m keyring set {service} {account}'
        return f'"{sys.executable}" -m keyring set {service} {account}'

    def _copy_setup_command(self, provider: str, command_edit: QLineEdit) -> None:
        QApplication.clipboard().setText(command_edit.text())
        status = (
            self.canvas_copy_status
            if provider.casefold() == "canvas"
            else self.ed_copy_status
        )
        status.setText("Command copied.")
        status.show()

    def _invalidate_credential_test(self, provider: str) -> None:
        self._test_serials[provider] += 1
        cancellation = self._test_cancellations.pop(provider, None)
        if cancellation is not None:
            cancellation.request()
        self._test_tasks.pop(provider, None)
        self._test_generations.pop(provider, None)
        self._test_success_keys.pop(provider, None)
        status_label, test_button, copy_button = self._test_widgets(provider)
        status_label.hide()
        status_label.clear()
        test_button.setText("Test")
        test_button.setEnabled(True)
        copy_button.setText("Copy command")

    def cancel_credential_tests(self) -> None:
        for provider in ("canvas", "ed"):
            self._test_serials[provider] += 1
            cancellation = self._test_cancellations.pop(provider, None)
            if cancellation is not None:
                cancellation.request()
            self._test_tasks.pop(provider, None)
            self._test_generations.pop(provider, None)
            _, test_button, _ = self._test_widgets(provider)
            test_button.setText("Test")
            test_button.setEnabled(True)

    def _start_credential_test(self, provider: str) -> None:
        self._invalidate_credential_test(provider)
        prepared = prepare_first_run(
            profile=self.owner.profile,
            output_root=self.owner.output_root,
            config_path=self.owner.config_path,
            canvas_enabled=provider == "canvas",
            canvas_base_url=self.canvas_url_edit.text().strip() or None,
            canvas_credential=self.canvas_credential_edit.text().strip() or None,
            ed_enabled=provider == "ed",
            ed_credential=self.ed_credential_edit.text().strip() or None,
        )
        if prepared.status is not OperationStatus.SUCCEEDED or prepared.draft is None:
            self._credential_test_failed(provider)
            return
        serial = self._test_serials[provider]
        cancellation = _Cancellation()
        status_label, test_button, _ = self._test_widgets(provider)
        status_label.setStyleSheet("color: #263B63;")
        status_label.setText("Testing the stored token and reading courses…")
        status_label.show()
        test_button.setText("Testing…")
        test_button.setEnabled(False)
        task = _DiscoveryTask(
            serial,
            prepared.draft,
            discoverer=self.owner.discoverer,
            secret_resolver=self.owner.secret_resolver,
            cancellation=cancellation,
        )
        task.signals.finished.connect(
            lambda result_serial, generation, value, selected=provider: (
                self._credential_test_finished(
                    selected, result_serial, generation, value
                )
            )
        )
        self._test_generations[provider] = prepared.draft.generation
        self._test_cancellations[provider] = cancellation
        self._test_tasks[provider] = task
        QThreadPool.globalInstance().start(task)

    def _credential_test_finished(
        self, provider: str, serial: int, generation: str, value: object
    ) -> None:
        if (
            serial != self._test_serials[provider]
            or generation != self._test_generations.get(provider)
        ):
            return
        self._test_tasks.pop(provider, None)
        self._test_cancellations.pop(provider, None)
        _, test_button, _ = self._test_widgets(provider)
        test_button.setText("Test")
        test_button.setEnabled(True)
        if not isinstance(value, CatalogDiscoveryResult):
            self._credential_test_failed(provider)
            return
        result = next(
            (item for item in value.providers if item.provider == provider), None
        )
        if result is None or result.status is OperationStatus.FAILED:
            self._credential_test_failed(provider)
            return
        status_label, _, copy_button = self._test_widgets(provider)
        count = len(result.courses)
        noun = "course" if count == 1 else "courses"
        warning = (
            " Provider warnings were reported."
            if result.status is OperationStatus.PARTIAL
            else ""
        )
        status_label.setStyleSheet("color: #155724; font-weight: 600;")
        status_label.setText(
            f"Connected successfully — {count} {noun} found.{warning}"
        )
        status_label.show()
        copy_button.setText("Copy command")
        self._test_success_keys[provider] = self._credential_test_key(provider)

    def _credential_test_failed(self, provider: str) -> None:
        self._test_success_keys.pop(provider, None)
        status_label, test_button, copy_button = self._test_widgets(provider)
        test_button.setText("Test")
        test_button.setEnabled(True)
        copy_button.setText("Copy reset command")
        status_label.setStyleSheet("color: #7A1E1E; font-weight: 600;")
        status_label.setText(
            "Token test failed. Copy and rerun the Save token command above, paste a "
            "fresh token at the Password prompt, then test again."
        )
        status_label.show()

    def _test_widgets(
        self, provider: str
    ) -> tuple[QLabel, QPushButton, QPushButton]:
        if provider == "canvas":
            return (
                self.canvas_test_status,
                self.test_canvas_button,
                self.copy_canvas_command_button,
            )
        return self.ed_test_status, self.test_ed_button, self.copy_ed_command_button

    def _credential_test_key(self, provider: str) -> tuple[str, ...]:
        if provider == "canvas":
            return (
                self.canvas_url_edit.text().strip(),
                self.canvas_credential_edit.text().strip(),
            )
        return (self.ed_credential_edit.text().strip(),)

    def validatePage(self) -> bool:
        enabled = (
            ("canvas", self.canvas_group.isChecked()),
            ("ed", self.ed_group.isChecked()),
        )
        for provider, is_enabled in enabled:
            if not is_enabled:
                continue
            if self._test_success_keys.get(provider) == self._credential_test_key(
                provider
            ):
                continue
            status_label, _, copy_button = self._test_widgets(provider)
            status_label.setStyleSheet("color: #7A1E1E; font-weight: 600;")
            status_label.setText(
                f"Test the {provider.title()} token successfully before continuing."
            )
            status_label.show()
            copy_button.setText("Copy reset command")
            self.provider_tabs.setCurrentIndex(0 if provider == "canvas" else 1)
            return False
        prepared = prepare_first_run(
            profile=self.owner.profile,
            output_root=self.owner.output_root,
            config_path=self.owner.config_path,
            canvas_enabled=self.canvas_group.isChecked(),
            canvas_base_url=self.canvas_url_edit.text().strip() or None,
            canvas_credential=self.canvas_credential_edit.text().strip() or None,
            ed_enabled=self.ed_group.isChecked(),
            ed_credential=self.ed_credential_edit.text().strip() or None,
        )
        if prepared.status is not OperationStatus.SUCCEEDED or prepared.draft is None:
            code = prepared.issues[0].code if prepared.issues else "invalid_connection"
            message = {
                "config_exists": "This configuration now exists; nothing will be overwritten.",
                "no_provider_selected": "Enable Canvas, Ed, or both.",
                "invalid_credential_reference": (
                    "Use an env:VARIABLE_NAME or keyring:service/account credential reference."
                ),
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
        self.draft: FirstRunDraft | None = None
        self.discovery_result: CatalogDiscoveryResult | None = None
        self.controller_result: FirstRunControllerResult | None = None
        self._discovery_serial = 0
        self._discovery_running = False
        self._cancellation: _Cancellation | None = None
        self._discovery_task: _DiscoveryTask | None = None
        self._setup_window: Any | None = None

        self.setWindowTitle("Set up Canvas & Ed")
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
        self._setup_window = build_config_saving_setup_window(
            self.controller_result.controller,
            self.draft.config_path,
        )
        self._setup_window.show()
        self._setup_window.raise_()
        self.hide()

    def reject(self) -> None:
        if self._discovery_running:
            self.cancel_discovery()
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._discovery_running:
            self.cancel_discovery()
            event.ignore()
            return
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
            QGroupBox::indicator:checked { background: #2F6FED; border-color: #174EA6; }
            QTabWidget::pane { border: 1px solid #C7D0DE; border-radius: 6px; background: #F6F8FB; }
            QTabBar::tab { min-width: 120px; padding: 9px 18px; background: #E7ECF4; border: 1px solid #C7D0DE; }
            QTabBar::tab:selected { background: white; color: #174EA6; font-weight: 650; }
            QPushButton { min-height: 30px; padding: 2px 14px; background: white; border: 1px solid #B8C3D3; border-radius: 5px; }
            QPushButton:hover { background: #EDF3FF; border-color: #7FA6ED; }
            QPushButton:focus { border: 2px solid #174EA6; background: #EDF3FF; }
            QPushButton:disabled { color: #9AA3B2; background: #EEF1F5; border-color: #D8DEE8; }
            QLabel#notice { color: #263B63; background: #E7F0FF; border: 1px solid #9EBBEA; border-radius: 5px; padding: 9px; }
            QLabel#error { color: #7A1E1E; background: #FDE8E7; border: 1px solid #E8AAA6; border-radius: 5px; padding: 9px; }
            """
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canvas & Ed first-run setup")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv)
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
