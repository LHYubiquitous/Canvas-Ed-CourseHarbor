"""PySide6 due-date page backed by the typed application workflow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from canvas_ed_mcp.application.contracts import OperationStatus, ProgressEvent
from canvas_ed_mcp.application.due_reports import (
    DueExportResult,
    DueRefreshRequest,
    DueRefreshResult,
    DueReportScope,
    DueReportService,
    DueReportSnapshot,
)

from .components import CollapsibleNotice


class _Cancellation:
    def __init__(self) -> None:
        self.cancellation_requested = False

    def request(self) -> None:
        self.cancellation_requested = True


class _Signals(QObject):
    progress = Signal(int, object)
    finished = Signal(int, str, object)


class _DueTask(QRunnable):
    def __init__(
        self,
        generation: int,
        action: str,
        operation: Callable[..., object],
        cancellation: _Cancellation,
    ) -> None:
        super().__init__()
        self.generation = generation
        self.action = action
        self.operation = operation
        self.cancellation = cancellation
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation(
                progress=lambda event: self.signals.progress.emit(
                    self.generation, event
                ),
                cancellation=self.cancellation,
            )
        except Exception:
            result = None
        self.signals.finished.emit(self.generation, self.action, result)


_ISSUE_TEXT = {
    "authentication_failed": "A provider credential was rejected.",
    "authorization_failed": "A provider did not permit part of the due-date request.",
    "backend_error": "The native credential store could not be accessed.",
    "config_changed": "Configuration changed after refresh. Refresh due dates again.",
    "config_missing": "This profile does not have a saved configuration.",
    "due_disabled": "Due-date reports are disabled for this profile.",
    "due_export_failed": "The reports could not be exported to the selected folder.",
    "due_not_publishable": "Required due-date sources are unavailable, so export is disabled.",
    "due_refresh_failed": "Due dates could not be refreshed safely.",
    "export_directory_invalid": "Choose an accessible export folder.",
    "export_cancelled_after_weekly": "Export stopped after the weekly report was created; the semester report was not written.",
    "invalid_due_state": "The saved due-date cache is invalid. Refresh could not continue.",
    "missing_env": "An externally managed credential is unavailable.",
    "missing_keyring": "A stored credential is missing.",
    "provider_unavailable": "A configured provider could not be reached.",
    "provider_close_failed": "A provider connection did not close cleanly after refresh.",
    "semester_export_failed": "The weekly report was exported, but the semester report failed.",
    "source_request_failed": "One due-date source could not be refreshed.",
    "unsafe_backend": "A secure native credential store is unavailable.",
}


def _issue_text(code: str) -> str:
    return _ISSUE_TEXT.get(code, "A due-date source needs attention.")


class DueDatesPage(QWidget):
    """Load and export weekly and semester due reports independently."""

    def __init__(
        self,
        *,
        profile: str,
        config_path: Path | None = None,
        service: DueReportService | None = None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__()
        self.profile = profile
        self.config_path = config_path
        self.service = service or DueReportService()
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._generation = 0
        self._active_task: _DueTask | None = None
        self._cancellation: _Cancellation | None = None
        self._snapshot: DueReportSnapshot | None = None
        self._snapshots: dict[DueReportScope, DueReportSnapshot] = {}
        self._export_directories: dict[DueReportScope, Path] = {}
        self._build_ui()

    @property
    def snapshot(self) -> DueReportSnapshot | None:
        return self._snapshot

    @property
    def active_task(self) -> _DueTask | None:
        return self._active_task

    def _build_ui(self) -> None:
        self.setObjectName("due_dates_page")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        heading = QLabel("Due dates")
        heading.setObjectName("page_title")
        description = QLabel(
            "Load only the report you need. This week and semester exports use independent folders."
        )
        description.setObjectName("page_subtitle")
        description.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)

        self.notice = CollapsibleNotice(
            tone="warning",
            dismissible=True,
            accessibility_subject="due-date warnings",
        )
        self.notice.hide()
        layout.addWidget(self.notice)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("due_report_tabs")
        (
            weekly_card,
            self.weekly_text,
            self.weekly_export_path,
            self.weekly_choose_button,
            self.weekly_refresh_button,
            self.weekly_export_button,
        ) = self._report_pane("This week", DueReportScope.THIS_WEEK)
        (
            semester_card,
            self.semester_text,
            self.semester_export_path,
            self.semester_choose_button,
            self.semester_refresh_button,
            self.semester_export_button,
        ) = self._report_pane("Semester", DueReportScope.SEMESTER)
        self.tabs.addTab(weekly_card, "This week")
        self.tabs.addTab(semester_card, "Semester")
        self.tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.tabs, 1)

        # Compatibility aliases for older adapters; new UI code uses scoped fields.
        self.refresh_button = self.weekly_refresh_button
        self.export_button = self.weekly_export_button
        self.export_path = self.weekly_export_path
        self.choose_button = self.weekly_choose_button

        status_row = QHBoxLayout()
        self.status_label = QLabel("Ready to refresh.")
        self.status_label.setObjectName("due_status")
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.hide()
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.progress_bar)
        layout.addLayout(status_row)

        actions = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_active_operation)
        self.cancel_button.hide()
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)

    def _report_pane(
        self, title: str, scope: DueReportScope
    ) -> tuple[QFrame, QPlainTextEdit, QLineEdit, QPushButton, QPushButton, QPushButton]:
        card = QFrame()
        card.setObjectName("report_card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)
        label = QLabel(title)
        label.setObjectName("report_heading")
        label.setContentsMargins(12, 9, 12, 9)
        destination = QWidget()
        destination_layout = QHBoxLayout(destination)
        destination_layout.setContentsMargins(12, 8, 12, 8)
        destination_layout.addWidget(QLabel("Export folder"))
        export_path = QLineEdit()
        export_path.setReadOnly(True)
        export_path.setPlaceholderText("Choose a folder now, or load the profile default")
        export_path.setAccessibleName(f"{title} due report export folder")
        choose_button = QPushButton("Choose…")
        choose_button.clicked.connect(
            lambda checked=False, selected=scope: self.choose_export_directory(selected)
        )
        destination_layout.addWidget(export_path, 1)
        destination_layout.addWidget(choose_button)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        text.setPlaceholderText(f"Load {title.casefold()} due dates to build this report.")
        text.setAccessibleName(f"{title} due-date report")
        buttons = QHBoxLayout()
        buttons.setContentsMargins(12, 8, 12, 10)
        buttons.addStretch(1)
        refresh_button = QPushButton(
            "Load this week" if scope is DueReportScope.THIS_WEEK else "Load semester"
        )
        refresh_button.setObjectName("refresh_due_button")
        refresh_button.clicked.connect(
            lambda checked=False, selected=scope: self.start_refresh(selected)
        )
        export_button = QPushButton(
            "Export this week" if scope is DueReportScope.THIS_WEEK else "Export semester"
        )
        export_button.setObjectName("export_due_button")
        export_button.setEnabled(False)
        export_button.clicked.connect(
            lambda checked=False, selected=scope: self.start_export(selected)
        )
        buttons.addWidget(refresh_button)
        buttons.addWidget(export_button)
        card_layout.addWidget(label)
        card_layout.addWidget(destination)
        card_layout.addWidget(text, 1)
        card_layout.addLayout(buttons)
        return card, text, export_path, choose_button, refresh_button, export_button

    def _scope_widgets(
        self, scope: DueReportScope
    ) -> tuple[QPlainTextEdit, QLineEdit, QPushButton, QPushButton, QPushButton]:
        if scope is DueReportScope.THIS_WEEK:
            return (
                self.weekly_text,
                self.weekly_export_path,
                self.weekly_choose_button,
                self.weekly_refresh_button,
                self.weekly_export_button,
            )
        return (
            self.semester_text,
            self.semester_export_path,
            self.semester_choose_button,
            self.semester_refresh_button,
            self.semester_export_button,
        )

    def _set_busy(self, busy: bool, message: str) -> None:
        self.status_label.setText(message)
        for scope in (DueReportScope.THIS_WEEK, DueReportScope.SEMESTER):
            _, _, choose, refresh, export = self._scope_widgets(scope)
            refresh.setEnabled(not busy)
            choose.setEnabled(not busy)
            snapshot = self._snapshots.get(scope)
            export.setEnabled(not busy and snapshot is not None and snapshot.publishable)
        self.cancel_button.setVisible(busy)
        self.progress_bar.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)

    def _start_task(self, action: str, operation: Callable[..., object]) -> None:
        if self._active_task is not None:
            return
        self._generation += 1
        cancellation = _Cancellation()
        task = _DueTask(self._generation, action, operation, cancellation)
        task.signals.progress.connect(self._on_progress)
        task.signals.finished.connect(self._on_finished)
        self._cancellation = cancellation
        self._active_task = task
        self._set_busy(
            True,
            "Loading due dates…"
            if action.startswith("refresh:")
            else "Preparing the selected export folder…"
            if action.startswith("retarget:")
            else "Exporting reports…",
        )
        self._thread_pool.start(task)

    def start_refresh(self, scope: DueReportScope = DueReportScope.THIS_WEEK) -> None:
        directory = self._export_directories.get(scope)
        if directory is None and scope in self._snapshots:
            directory = self._snapshots[scope].export_directory
        request = DueRefreshRequest(
            self.profile,
            config_path=self.config_path,
            export_directory=directory,
            scope=scope,
        )
        self._start_task(
            f"refresh:{scope.value}",
            lambda **kwargs: self.service.refresh(request, **kwargs),
        )

    def start_export(self, scope: DueReportScope = DueReportScope.THIS_WEEK) -> None:
        snapshot = self._snapshots.get(scope)
        if snapshot is None or not snapshot.publishable:
            return
        self._start_task(
            f"export:{scope.value}",
            lambda **kwargs: self.service.export(snapshot, **kwargs),
        )

    def choose_export_directory(
        self, scope: DueReportScope = DueReportScope.THIS_WEEK
    ) -> None:
        snapshot = self._snapshots.get(scope)
        start = str(self._export_directories.get(scope) or (snapshot.export_directory if snapshot else Path.home()))
        selected = QFileDialog.getExistingDirectory(
            self,
            f"Choose {'weekly' if scope is DueReportScope.THIS_WEEK else 'semester'} report export folder",
            start,
        )
        if not selected:
            return
        target = Path(selected)
        self._export_directories[scope] = target
        _, path_field, _, _, _ = self._scope_widgets(scope)
        path_field.setText(str(target))
        path_field.setToolTip(str(target))
        if snapshot is None:
            self.status_label.setText("Export folder selected. Load the report when ready.")
            return
        self._start_task(
            f"retarget:{scope.value}",
            lambda **kwargs: self.service.retarget(
                snapshot, target, **kwargs
            ),
        )

    def cancel_active_operation(self) -> None:
        if self._cancellation is None:
            return
        self._cancellation.request()
        self.cancel_button.setEnabled(False)
        self.status_label.setText(
            "Cancellation requested. Waiting for the next safe boundary…"
        )

    @Slot(int, object)
    def _on_progress(self, generation: int, event: object) -> None:
        if generation != self._generation or not isinstance(event, ProgressEvent):
            return
        if event.total and event.total > 0:
            self.progress_bar.setRange(0, event.total)
            self.progress_bar.setValue(event.completed)
        else:
            self.progress_bar.setRange(0, 0)

    @Slot(int, str, object)
    def _on_finished(self, generation: int, action: str, result: object) -> None:
        if generation != self._generation:
            return
        self._active_task = None
        self._cancellation = None
        self.cancel_button.setEnabled(True)
        verb, _, scope_value = action.partition(":")
        try:
            scope = DueReportScope(scope_value)
        except ValueError:
            self._set_busy(False, "The operation could not finish safely.")
            return
        if verb in {"refresh", "retarget"} and isinstance(result, DueRefreshResult):
            self._apply_refresh_result(
                result,
                scope=scope,
                retargeted=verb == "retarget",
                preserve_on_failure=verb == "retarget",
            )
            return
        if verb == "export" and isinstance(result, DueExportResult):
            self._apply_export_result(scope, result)
            return
        self._set_busy(False, "The operation could not finish safely.")

    def _apply_refresh_result(
        self,
        result: DueRefreshResult,
        *,
        scope: DueReportScope,
        retargeted: bool = False,
        preserve_on_failure: bool = False,
    ) -> None:
        if result.snapshot is None:
            if not preserve_on_failure:
                self._snapshots.pop(scope, None)
                text, path_field, _, _, _ = self._scope_widgets(scope)
                text.clear()
                if scope not in self._export_directories:
                    path_field.clear()
                self.notice.hide()
            message = (
                _issue_text(result.issues[0].code)
                if result.issues
                else "Due-date refresh was cancelled."
                if result.status is OperationStatus.CANCELLED
                else "Due dates could not be refreshed safely."
            )
            self._set_busy(False, message)
            return
        if result.snapshot.scope is not scope:
            self._set_busy(False, "The report scope changed unexpectedly. Load it again.")
            return
        self._snapshot = result.snapshot
        self._snapshots[scope] = result.snapshot
        self._export_directories[scope] = result.snapshot.export_directory
        text, path_field, _, _, _ = self._scope_widgets(scope)
        path_field.setText(str(result.snapshot.export_directory))
        path_field.setToolTip(str(result.snapshot.export_directory))
        text.setPlainText(
            result.snapshot.weekly_text
            if scope is DueReportScope.THIS_WEEK
            else result.snapshot.semester_markdown
        )
        self._show_issues(result.snapshot)
        if retargeted:
            message = "Export folder updated. No report was written."
        else:
            message = (
                f"{result.snapshot.week_item_count} due this week"
                if scope is DueReportScope.THIS_WEEK
                else f"{result.snapshot.item_count} items across the semester"
            )
            if result.snapshot.used_last_good:
                message += " · using saved data for at least one source"
        self._set_busy(False, message)

    def _show_issues(self, snapshot: DueReportSnapshot) -> None:
        codes = sorted({issue.code for issue in snapshot.issues})
        if not snapshot.publishable:
            codes.append("due_not_publishable")
        if not codes:
            self.notice.hide()
            return
        messages = tuple(dict.fromkeys(_issue_text(code) for code in codes))
        self.notice.setContent(
            f"{len(messages)} due-date issue{'s' if len(messages) != 1 else ''}",
            "\n".join(f"• {message}" for message in messages),
            tone="warning",
        )
        self.notice.setExpanded(False)
        self.notice.show()

    def _apply_export_result(
        self, scope: DueReportScope, result: DueExportResult
    ) -> None:
        if result.status is OperationStatus.SUCCEEDED:
            self._set_busy(
                False,
                "This week report exported."
                if scope is DueReportScope.THIS_WEEK
                else "Semester report exported.",
            )
        elif result.status is OperationStatus.PARTIAL:
            code = result.issues[0].code if result.issues else "semester_export_failed"
            self._set_busy(False, _issue_text(code))
        elif result.status is OperationStatus.CANCELLED:
            self._set_busy(False, "Export cancelled before any report was written.")
        else:
            message = (
                _issue_text(result.issues[0].code)
                if result.issues
                else "The reports could not be exported."
            )
            self._set_busy(False, message)

    def request_shutdown(self) -> bool:
        if self._active_task is not None:
            self.cancel_active_operation()
            return False
        return True


__all__ = ["DueDatesPage"]
