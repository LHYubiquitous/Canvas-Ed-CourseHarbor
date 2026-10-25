"""Existing-profile Canvas and Ed credential management page."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from canvas_ed_mcp.application.contracts import OperationStatus, SecretValue
from canvas_ed_mcp.application.credentials import (
    CredentialController,
    CredentialOperationResult,
    credential_reference,
)
from canvas_ed_mcp.config import load_config


class _Cancellation:
    def __init__(self) -> None:
        self.cancellation_requested = False

    def request(self) -> None:
        self.cancellation_requested = True


class _Signals(QObject):
    finished = Signal(int, str, object)


class _CredentialTask(QRunnable):
    def __init__(self, serial, provider, operation, cancellation) -> None:
        super().__init__()
        self.serial = serial
        self.provider = provider
        self.operation = operation
        self.cancellation = cancellation
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation(cancellation=self.cancellation, progress=None)
        except Exception:
            result = None
        self.signals.finished.emit(self.serial, self.provider, result)


class ConnectionsPage(QWidget):
    """Save, replace, and test configured credentials without revealing them."""

    def __init__(
        self,
        *,
        profile: str,
        config_path: Path,
        controller: CredentialController | None = None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self.profile = profile
        self.config_path = config_path
        self.controller = controller or CredentialController()
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._serial = {"canvas": 0, "ed": 0}
        self._tasks: dict[str, _CredentialTask] = {}
        self._cancellations: dict[str, _Cancellation] = {}
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)
        heading = QLabel("Connections")
        heading.setObjectName("page_title")
        subtitle = QLabel(
            "Test existing credentials or replace them after a read-only provider check."
        )
        subtitle.setObjectName("page_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Canvas and Ed connection settings")
        self._cards = {
            "canvas": self._build_card("Canvas"),
            "ed": self._build_card("Ed"),
        }
        self.tabs.addTab(self._cards["canvas"]["frame"], "Canvas")
        self.tabs.addTab(self._cards["ed"]["frame"], "Ed")
        layout.addWidget(self.tabs, 1)

    def _build_card(self, title: str) -> dict[str, object]:
        provider = title.casefold()
        frame = QFrame()
        frame.setObjectName("connection_card")
        form = QFormLayout(frame)
        form.setContentsMargins(16, 16, 16, 16)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        state = QLabel("Not configured")
        state.setObjectName("connection_state")
        form.addRow("Status", state)
        endpoint = QLineEdit()
        endpoint.setReadOnly(True)
        endpoint.setAccessibleName(f"{title} connection endpoint")
        form.addRow("API base URL" if provider == "canvas" else "Region", endpoint)
        token = QLineEdit()
        token.setEchoMode(QLineEdit.EchoMode.Password)
        token.setPlaceholderText("Leave blank to keep the existing credential")
        token.setAccessibleName(f"Masked {title} credential input")
        form.addRow("Replacement token", token)
        actions = QHBoxLayout()
        save = QPushButton("Save and test")
        test = QPushButton("Test existing")
        cancel = QPushButton("Cancel")
        cancel.hide()
        save.clicked.connect(lambda: self._save(provider))
        test.clicked.connect(lambda: self._test_existing(provider))
        cancel.clicked.connect(lambda: self._cancel(provider))
        actions.addWidget(save)
        actions.addWidget(test)
        actions.addWidget(cancel)
        actions.addStretch(1)
        form.addRow("", actions)
        message = QLabel()
        message.setWordWrap(True)
        message.hide()
        form.addRow("", message)
        return {
            "frame": frame,
            "state": state,
            "endpoint": endpoint,
            "token": token,
            "save": save,
            "test": test,
            "cancel": cancel,
            "message": message,
            "reference": "",
            "enabled": False,
        }

    def reload(self) -> None:
        try:
            config = load_config(self.config_path)
        except Exception:
            for card in self._cards.values():
                card["state"].setText("Configuration unavailable")
                card["save"].setEnabled(False)
                card["test"].setEnabled(False)
            return
        canvas = config.platforms.canvas
        ed = config.platforms.ed
        self._configure_card(
            "canvas",
            enabled=canvas.enabled,
            endpoint=canvas.base_url or "—",
            reference=canvas.credential or "",
        )
        self._configure_card(
            "ed",
            enabled=ed.enabled,
            endpoint=ed.region,
            reference=ed.credential or "",
        )

    def _configure_card(
        self, provider: str, *, enabled: bool, endpoint: str, reference: str
    ) -> None:
        card = self._cards[provider]
        externally_managed = reference.startswith("env:")
        canonical = reference == credential_reference(self.profile, provider)
        manageable = not reference or externally_managed or canonical
        card["enabled"] = enabled
        card["reference"] = reference
        card["endpoint"].setText(endpoint)
        card["state"].setText(
            "Disabled"
            if not enabled
            else "Legacy credential reference · reconfigure this profile"
            if not manageable
            else "Externally managed · read-only"
            if externally_managed
            else "Stored securely"
            if reference
            else "Credential missing"
        )
        card["token"].setEnabled(enabled and manageable and not externally_managed)
        card["save"].setEnabled(enabled and manageable and not externally_managed)
        card["test"].setEnabled(enabled and manageable and bool(reference))
        if enabled and not manageable:
            self._message(
                provider,
                "This profile uses a legacy credential reference. Reconfigure the profile before managing this credential here.",
                error=True,
            )

    def _target(self, provider: str):
        card = self._cards[provider]
        endpoint = card["endpoint"].text() if provider == "canvas" else None
        return self.controller.target_for(
            self.profile,
            provider,
            canvas_base_url=endpoint,
            reference=card["reference"],
        )

    def _save(self, provider: str) -> None:
        card = self._cards[provider]
        raw = card["token"].text()
        if not raw:
            self._message(
                provider,
                "No replacement token was entered. The existing credential was kept.",
                error=False,
            )
            return
        try:
            value = SecretValue(raw)
        except ValueError:
            card["token"].clear()
            self._message(provider, "Enter a valid non-empty token.", error=True)
            return
        display = "Canvas" if provider == "canvas" else "Ed"
        answer = QMessageBox.question(
            self,
            f"Replace {display} credential?",
            f"CourseHarbor will test the replacement with a read-only request before saving it.\n\nProvider: {display}\nProfile: {self.profile}",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        card["token"].clear()
        if answer != QMessageBox.StandardButton.Save:
            self._message(provider, "Credential replacement cancelled.", error=False)
            return
        try:
            target = self._target(provider)
        except ValueError:
            self._message(
                provider,
                "This credential reference cannot be managed here. Reconfigure the profile first.",
                error=True,
            )
            return
        self._start(
            provider,
            lambda **kwargs: self.controller.save_and_test(
                target, value, replace_confirmed=True, **kwargs
            ),
            "Testing the replacement before saving…",
        )

    def _test_existing(self, provider: str) -> None:
        try:
            target = self._target(provider)
        except ValueError:
            self._message(provider, "The configured connection is invalid.", error=True)
            return
        self._start(
            provider,
            lambda **kwargs: self.controller.test_existing(target, **kwargs),
            "Testing the existing credential with a read-only request…",
        )

    def _start(self, provider: str, operation, message: str) -> None:
        if provider in self._tasks:
            return
        self._serial[provider] += 1
        cancellation = _Cancellation()
        task = _CredentialTask(
            self._serial[provider], provider, operation, cancellation
        )
        task.signals.finished.connect(self._finished)
        self._tasks[provider] = task
        self._cancellations[provider] = cancellation
        card = self._cards[provider]
        card["save"].setEnabled(False)
        card["test"].setEnabled(False)
        card["cancel"].show()
        self._message(provider, message, error=False)
        self._thread_pool.start(task)

    def _cancel(self, provider: str) -> None:
        cancellation = self._cancellations.get(provider)
        if cancellation is not None:
            cancellation.request()
            self._cards[provider]["cancel"].setEnabled(False)
            self._message(
                provider,
                "Cancellation requested. Waiting for the next safe boundary…",
                error=False,
            )

    @Slot(int, str, object)
    def _finished(self, serial: int, provider: str, result: object) -> None:
        if serial != self._serial[provider]:
            return
        self._tasks.pop(provider, None)
        self._cancellations.pop(provider, None)
        card = self._cards[provider]
        card["cancel"].setEnabled(True)
        card["cancel"].hide()
        self.reload()
        if not isinstance(result, CredentialOperationResult):
            self._message(provider, "The credential operation failed safely.", error=True)
            return
        if result.status is OperationStatus.CANCELLED:
            self._message(provider, "Credential operation cancelled.", error=False)
        elif result.status is OperationStatus.SUCCEEDED and result.verified:
            count = result.course_count or 0
            suffix = " Credential saved securely." if result.persisted else " Existing credential works."
            self._message(
                provider,
                f"Connected successfully — {count} course{'s' if count != 1 else ''} found.{suffix}",
                error=False,
            )
        else:
            code = result.issues[0].code if result.issues else "credential_failed"
            message = {
                "authentication_failed": "Authentication failed. Enter a fresh token and try again.",
                "authorization_failed": "The token works but lacks access to the requested courses.",
                "missing_keyring": "No stored credential was found.",
                "missing_env": "The externally managed credential is unavailable.",
                "unsafe_backend": "A secure native credential store is unavailable; no provider request was sent.",
                "backend_error": "The native credential store could not be accessed; no provider request was sent.",
                "credential_state_unknown": "The token was verified, but the credential-store write failed. Use Test existing to diagnose its state.",
                "request_failed": "The provider could not be reached.",
            }.get(code, "The credential could not be verified safely.")
            self._message(provider, message, error=True)

    def _message(self, provider: str, text: str, *, error: bool) -> None:
        label = self._cards[provider]["message"]
        label.setStyleSheet(
            "color: #8B2F2B; font-weight: 600;"
            if error
            else "color: #245B4A; font-weight: 600;"
        )
        label.setText(text)
        label.show()

    def request_shutdown(self) -> bool:
        if not self._tasks:
            for card in self._cards.values():
                card["token"].clear()
            return True
        for provider in tuple(self._tasks):
            self._cancel(provider)
        return False


__all__ = ["ConnectionsPage"]
