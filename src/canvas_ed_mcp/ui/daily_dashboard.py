"""PySide6 daily-use dashboard over typed application services."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QUrl, Signal, Slot, Qt
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
    ProgressEvent,
    ProgressPhase,
)
from canvas_ed_mcp.application.daily_sync import (
    DailySyncController,
    SyncApplyResult,
    SyncPreviewRequest,
    SyncPreviewResult,
    SyncReview,
    SyncSourceSummary,
)
from canvas_ed_mcp.application.folder_actions import (
    OpenOutputFolderRequest,
    OpenOutputFolderResult,
    OutputFolderService,
)
from canvas_ed_mcp.application.profile_status import (
    ProfileOverview,
    inspect_profile_overview,
)
from canvas_ed_mcp.application.sync_operation import ReviewedSyncOperation
from canvas_ed_mcp.ui.branding import DISPLAY_NAME
from canvas_ed_mcp.ui.components import CollapsibleNotice


ConfirmApply = Callable[[SyncReview, bool], bool]

_PHASE_COPY = {
    ProgressPhase.CONNECTING: "Connecting to course services",
    ProgressPhase.DISCOVERING: "Checking available course material",
    ProgressPhase.PLANNING: "Planning reviewed changes",
    ProgressPhase.VALIDATING: "Validating the reviewed plan",
    ProgressPhase.APPLYING: "Updating the local course library",
    ProgressPhase.COMMITTING: "Recording the completed update",
    ProgressPhase.CLEANING_UP: "Finishing safely",
}

_ISSUE_COPY = {
    "authentication_failed": "The saved credential was not accepted by the provider.",
    "authorization_failed": "Some material was not permitted by the provider.",
    "config_changed": "The profile changed. Build a new preview before applying.",
    "state_changed": "The local sync state changed. Build a new preview.",
    "plan_changed": "The reviewed plan is no longer current.",
    "profile_paths_changed": "The profile storage location changed. Build a new preview.",
    "operation_cancelled": (
        "The remaining course sources were not updated because cancellation "
        "was requested."
    ),
    "output_root_missing": (
        "The local destination folder does not exist. Restore the configured "
        "Library root before syncing."
    ),
    "invalid_output_root": (
        "The local destination is not a usable folder. Restore it as a directory "
        "before syncing; changing an existing Library root requires migration."
    ),
    "provider_close_failed": "A provider connection did not close cleanly.",
    "provider_discovery_failed": (
        "One course source was unavailable and was excluded from this apply."
    ),
    "item_schema_drift": "Some provider data used an unexpected format.",
    "reconcile_baseline_pending": (
        "This source needs another successful preview before its baseline is complete."
    ),
    "source_sync_failed": "One configured source could not be synchronized.",
    "sync_preview_failed": "The material preview could not be prepared.",
    "sync_apply_failed": "The reviewed update could not be completed.",
}


class _Cancellation:
    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancellation_requested(self) -> bool:
        return self._event.is_set()

    def request(self) -> None:
        self._event.set()


class _TaskSignals(QObject):
    progress = Signal(int, object)
    finished = Signal(int, str, object)


class _ControllerTask(QRunnable):
    def __init__(
        self,
        serial: int,
        kind: str,
        action: Callable[..., object],
        cancellation: _Cancellation,
    ) -> None:
        super().__init__()
        self.serial = serial
        self.kind = kind
        self.action = action
        self.cancellation = cancellation
        self.signals = _TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.action(
                progress=lambda event: self.signals.progress.emit(self.serial, event),
                cancellation=self.cancellation,
            )
        except Exception:
            result = None
        self.signals.finished.emit(self.serial, self.kind, result)


def _issue_code_text(code: str) -> str:
    return _ISSUE_COPY.get(
        code,
        "Course material needs attention before this action can finish "
        f"({code}).",
    )


def _issue_text(issue: ApplicationIssue) -> str:
    return _issue_code_text(issue.code)


def _action_text(actions: tuple[str, ...]) -> str:
    if not actions:
        return "No planned changes"
    counts = Counter(actions)
    labels = {
        "would_fetch": "to download",
        "install_new": "downloaded",
        "install_separately": "saved as a new revision",
        "skip": "unchanged",
        "record_only": "recorded",
        "policy_skip": "excluded by file policy",
    }
    return ", ".join(
        f"{count} {labels.get(action, action.replace('_', ' '))}"
        for action, count in sorted(counts.items())
    )


class DailyDashboardWindow(QMainWindow):
    """Daily material-sync window with an exact review/apply boundary."""

    syncFinished = Signal(object)

    def __init__(
        self,
        *,
        overview: ProfileOverview,
        controller: DailySyncController,
        request: SyncPreviewRequest,
        folder_service: OutputFolderService,
        confirm_apply: ConfirmApply | None = None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        if not isinstance(overview, ProfileOverview):
            raise TypeError("overview must be a ProfileOverview")
        if not isinstance(controller, DailySyncController):
            raise TypeError("controller must be a DailySyncController")
        if not isinstance(request, SyncPreviewRequest):
            raise TypeError("request must be a SyncPreviewRequest")
        super().__init__()
        self.overview = overview
        self.controller = controller
        self.request = request
        self.folder_service = folder_service
        self._confirm_apply_callback = confirm_apply
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._generation = 0
        self._active_task: _ControllerTask | None = None
        self._cancellation: _Cancellation | None = None
        self._review: SyncReview | None = None
        self._close_when_idle = False
        self._allow_close = False

        self.setObjectName("daily_dashboard")
        self.setWindowTitle(f"{DISPLAY_NAME} — Course library")
        self.resize(1040, 680)
        self.setMinimumSize(860, 580)
        self._build_window()
        self._apply_style()
        self._render_overview()
        self._set_idle_controls()

    @property
    def active_task(self) -> _ControllerTask | None:
        return self._active_task

    def _build_window(self) -> None:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_daily_workspace(), 1)
        self.setCentralWidget(root)
        root.setAccessibleName("CourseHarbor course library")
        root.setAccessibleDescription(
            "Review and update configured Canvas and Ed course material."
        )

    def _build_daily_workspace(self) -> QWidget:
        workspace = QWidget()
        workspace.setObjectName("daily_workspace")
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(12)
        brand_mark = QFrame()
        brand_mark.setObjectName("brand_mark")
        brand_mark.setFixedSize(4, 42)
        brand = QVBoxLayout()
        brand.setSpacing(0)
        eyebrow = QLabel(DISPLAY_NAME.upper())
        eyebrow.setObjectName("brand_eyebrow")
        title = QLabel("Course library")
        title.setObjectName("daily_title")
        brand.addWidget(eyebrow)
        brand.addWidget(title)
        header.addWidget(brand_mark)
        header.addLayout(brand)
        header.addStretch(1)

        metadata = QHBoxLayout()
        metadata.setSpacing(20)
        self.profile_value = self._header_value(metadata, "PROFILE")
        self.library_value = self._header_value(metadata, "LIBRARY")
        self.providers_value = self._header_value(metadata, "SOURCES")
        header.addLayout(metadata)
        layout.addLayout(header)

        self.output_value = QLabel("—")
        self.output_value.setAccessibleName("Local destination")
        self.output_value.hide()

        self.status_frame = QFrame()
        self.status_frame.setObjectName("daily_status")
        status_layout = QVBoxLayout(self.status_frame)
        status_layout.setContentsMargins(12, 7, 12, 7)
        self.status_label = QLabel()
        self.status_label.setObjectName("daily_status_text")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Profile status")
        status_layout.addWidget(self.status_label)
        layout.addWidget(self.status_frame)

        self.warning_frame = CollapsibleNotice(
            tone="warning",
            dismissible=True,
            accessibility_subject="material warnings",
        )
        self.warning_frame.setObjectName("warning_panel")
        self.warning_summary = self.warning_frame.summary_label
        self.warning_summary.setAccessibleName("Material warning summary")
        self.warning_toggle_button = self.warning_frame.toggle_button
        self.warning_close_button = self.warning_frame.close_button
        self.warning_label = self.warning_frame.details_label
        self.warning_label.setAccessibleName("Material sync warnings")
        self.warning_frame.hide()
        layout.addWidget(self.warning_frame)

        self.source_table = QTableWidget(0, 4)
        self.source_table.setObjectName("source_review")
        self.source_table.setAccessibleName("Material sync review")
        self.source_table.setHorizontalHeaderLabels(
            ("Course", "Source", "Reviewed action", "Status")
        )
        self.source_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.source_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.source_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.source_table.setAlternatingRowColors(True)
        self.source_table.verticalHeader().hide()
        header = self.source_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.source_table.setColumnWidth(0, 430)
        layout.addWidget(self.source_table, 1)

        progress_row = QHBoxLayout()
        self.progress_label = QLabel("Ready to preview")
        self.progress_label.setObjectName("progress_label")
        self.progress_label.setAccessibleName("Sync progress")
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedWidth(190)
        self.progress_bar.hide()
        self.progress_label.hide()
        progress_row.addWidget(self.progress_label, 1)
        progress_row.addWidget(self.progress_bar)
        layout.addLayout(progress_row)

        actions = QHBoxLayout()
        self.open_button = QPushButton("Open course folder")
        self.open_button.setAccessibleName("Open local course folder")
        self.open_button.clicked.connect(self.open_output_folder)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setAccessibleName("Cancel current operation")
        self.cancel_button.clicked.connect(self.cancel_active_operation)
        self.cancel_button.hide()
        self.preview_button = QPushButton("Preview course material")
        self.preview_button.setObjectName("preview_button")
        self.preview_button.setAccessibleName("Preview course material")
        self.preview_button.clicked.connect(self.start_preview)
        self.apply_button = QPushButton("Apply reviewed changes")
        self.apply_button.setObjectName("apply_button")
        self.apply_button.setAccessibleName("Apply reviewed course material")
        self.apply_button.clicked.connect(self.confirm_and_apply)
        actions.addWidget(self.open_button)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        QWidget.setTabOrder(self.source_table, self.open_button)
        QWidget.setTabOrder(self.open_button, self.preview_button)
        QWidget.setTabOrder(self.preview_button, self.apply_button)
        return workspace

    @staticmethod
    def _header_value(layout: QHBoxLayout, heading: str) -> QLabel:
        block = QVBoxLayout()
        block.setSpacing(0)
        label = QLabel(heading)
        label.setObjectName("meta_label")
        value = QLabel("—")
        value.setObjectName("meta_value")
        block.addWidget(label)
        block.addWidget(value)
        layout.addLayout(block)
        return value

    def _render_overview(self) -> None:
        self.profile_value.setText(self.overview.profile)
        self.library_value.setText(
            f"{self.overview.course_count} course"
            f"{'s' if self.overview.course_count != 1 else ''} · "
            f"{self.overview.source_count} sources"
        )
        connected = tuple(
            item.provider.title()
            for item in self.overview.providers
            if item.enabled and item.credential_configured
        )
        self.providers_value.setText(" + ".join(connected) if connected else "Not connected")
        self.output_value.setText(
            str(self.overview.output_root) if self.overview.output_root else "Not configured"
        )
        blocked = self.overview.status in {OperationStatus.FAILED, OperationStatus.CONFLICT}
        if blocked:
            message = (
                _issue_text(self.overview.issues[0])
                if self.overview.issues
                else "This profile is not ready for material sync."
            )
            self._set_status(message, "error")
        elif self.overview.status is OperationStatus.PARTIAL:
            self._set_status("The profile is usable, but local state needs attention.", "warning")
        else:
            self._set_status("Ready to preview.", "ready")
        self.preview_button.setEnabled(not blocked)
        self.open_button.setEnabled(self.overview.output_exists)

    def _set_status(self, message: str, tone: str) -> None:
        self.status_label.setText(message)
        self.status_frame.setProperty("tone", tone)
        style = self.status_frame.style()
        style.unpolish(self.status_frame)
        style.polish(self.status_frame)

    def _set_idle_controls(self) -> None:
        blocked = self.overview.status in {OperationStatus.FAILED, OperationStatus.CONFLICT}
        self.preview_button.setEnabled(not blocked)
        self.apply_button.setEnabled(self._review is not None)
        self.cancel_button.hide()
        self.progress_bar.hide()
        self.progress_label.hide()

    def _set_busy(self, message: str) -> None:
        self.preview_button.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.cancel_button.show()
        self.cancel_button.setEnabled(True)
        self.progress_label.setText(message)
        self.progress_label.show()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()
        self._set_status(message, "working")

    def _start_task(self, kind: str, action: Callable[..., object]) -> None:
        if self._active_task is not None:
            return
        self._generation += 1
        cancellation = _Cancellation()
        task = _ControllerTask(self._generation, kind, action, cancellation)
        task.signals.progress.connect(self._progress_received)
        task.signals.finished.connect(self._task_finished)
        self._active_task = task
        self._cancellation = cancellation
        self._set_busy(
            "Building a safe material preview…"
            if kind == "preview"
            else "Applying the reviewed course material…"
        )
        self._thread_pool.start(task)

    def start_preview(self) -> None:
        self._review = None
        self.source_table.setRowCount(0)
        self.warning_frame.hide()
        self.apply_button.setText("Apply reviewed changes")
        self._start_task(
            "preview",
            lambda **kwargs: self.controller.preview(self.request, **kwargs),
        )

    def _confirm_apply(self, review: SyncReview, partial: bool) -> bool:
        if self._confirm_apply_callback is not None:
            return bool(self._confirm_apply_callback(review, partial))
        action_count = sum(len(source.actions) for source in review.sources)
        remote_sources = "\n".join(
            f"• {source.course_display_name} — {source.source_display_name}"
            for source in review.sources
        )
        title = "Apply available items?" if partial else "Apply reviewed changes?"
        text = (
            f"Profile: {review.profile}\n"
            f"Local destination: {review.output_root}\n"
            f"Reviewed sources: {len(review.sources)}\n"
            f"Reviewed actions: {action_count}\n\n"
            f"Remote sources:\n{remote_sources}\n\n"
            "Only this exact reviewed plan will be applied."
        )
        answer = QMessageBox.question(
            self,
            title,
            text,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def confirm_and_apply(self) -> None:
        review = self._review
        if review is None or self._active_task is not None:
            return
        partial = self.apply_button.text() == "Apply available items"
        if not self._confirm_apply(review, partial):
            return
        self._review = None
        self._start_task(
            "apply",
            lambda **kwargs: self.controller.apply(review, **kwargs),
        )

    def cancel_active_operation(self) -> None:
        if self._cancellation is None:
            return
        self._cancellation.request()
        self.cancel_button.setEnabled(False)
        self.progress_label.setText(
            "Cancellation requested. The current safe boundary will finish first."
        )

    @Slot(int, object)
    def _progress_received(self, serial: int, event: object) -> None:
        if serial != self._generation or not isinstance(event, ProgressEvent):
            return
        label = _PHASE_COPY.get(event.phase, "Working")
        if event.total is not None and event.total > 0:
            self.progress_bar.setRange(0, event.total)
            self.progress_bar.setValue(event.completed)
            label += f" · {event.completed}/{event.total}"
        else:
            self.progress_bar.setRange(0, 0)
        self.progress_label.setText(label)

    @Slot(int, str, object)
    def _task_finished(self, serial: int, kind: str, result: object) -> None:
        if serial != self._generation:
            return
        self._active_task = None
        self._cancellation = None
        self.progress_bar.hide()
        self.cancel_button.hide()
        self.open_button.setEnabled(self.overview.output_exists)
        if kind == "preview":
            self._finish_preview(result)
        else:
            self._finish_apply(result)
        if self._close_when_idle:
            self._close_when_idle = False
            self._allow_close = self.controller.close()
            self.close()

    def _finish_preview(self, result: object) -> None:
        if not isinstance(result, SyncPreviewResult):
            self._review = None
            self.source_table.setRowCount(0)
            self._set_status("The material preview could not be prepared.", "error")
            self.progress_label.setText("Preview failed")
            self._set_idle_controls()
            return
        self._show_issues(result.issues)
        if result.status in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}:
            self._review = result.review
            self._render_sources(result.review.sources)
            partial = result.status is OperationStatus.PARTIAL
            self.apply_button.setText(
                "Apply available items" if partial else "Apply reviewed changes"
            )
            self._set_status(
                f"Preview ready · {len(result.review.sources)} sources reviewed",
                "warning" if partial else "ready",
            )
            self.progress_label.setText("Planning complete")
        else:
            self._review = None
            self.source_table.setRowCount(0)
            self._set_status(
                "The preview is no longer actionable. Check the warning and try again.",
                "error",
            )
            self.progress_label.setText(result.status.value.replace("_", " ").title())
        self._set_idle_controls()

    def _finish_apply(self, result: object) -> None:
        self._review = None
        if not isinstance(result, SyncApplyResult):
            self.source_table.setRowCount(0)
            self._set_status("The reviewed update could not be completed.", "error")
            self.progress_label.setText("Update failed")
            self._set_idle_controls()
            return
        self._render_sources(result.sources)
        self._show_issues(result.issues)
        if result.status is OperationStatus.SUCCEEDED:
            self._set_status("Course material update complete.", "success")
            self.progress_label.setText("Update complete")
        elif result.status is OperationStatus.PARTIAL:
            self._set_status("Available material was updated with reported warnings.", "warning")
            self.progress_label.setText("Update completed with warnings")
        elif result.status is OperationStatus.CONFLICT:
            self._set_status("The reviewed plan changed. Build a new preview.", "warning")
            self.progress_label.setText("New preview required")
        elif result.status is OperationStatus.CANCELLED:
            self._set_status("The material update was cancelled at a safe boundary.", "warning")
            self.progress_label.setText("Update cancelled")
        else:
            self._set_status("The reviewed update could not be completed.", "error")
            self.progress_label.setText("Update failed")
        self.syncFinished.emit(result)
        self._set_idle_controls()

    def _show_issues(self, issues: tuple[ApplicationIssue, ...]) -> None:
        visible = tuple(
            issue for issue in issues if issue.severity is not IssueSeverity.INFO
        )
        if not visible:
            self.warning_frame.hide()
            self.warning_summary.clear()
            self.warning_label.clear()
            return
        counts = Counter(issue.code for issue in visible)
        type_count = len(counts)
        occurrence_count = sum(counts.values())
        self.warning_summary.setText(
            f"{type_count} issue type{'s' if type_count != 1 else ''} · "
            f"{occurrence_count} occurrence{'s' if occurrence_count != 1 else ''}"
        )
        self.warning_frame.setTone(
            "error"
            if any(issue.severity is IssueSeverity.ERROR for issue in visible)
            else "warning"
        )
        self.warning_label.setText(
            "\n".join(
                f"• {count} × {_issue_code_text(code)}"
                for code, count in sorted(counts.items())
            )
        )
        self.warning_frame.setExpanded(False)
        self.warning_frame.show()

    def _render_sources(self, sources: tuple[SyncSourceSummary, ...]) -> None:
        self.source_table.setRowCount(len(sources))
        for row, source in enumerate(sources):
            issue_count = len(source.issue_codes)
            warning = (
                "Unavailable"
                if "provider_discovery_failed" in source.issue_codes
                else f"{issue_count} warning{'s' if issue_count != 1 else ''}"
                if issue_count
                else "Ready"
            )
            action = (
                "Excluded from apply"
                if "provider_discovery_failed" in source.issue_codes
                else _action_text(source.actions)
            )
            values = (
                source.course_display_name,
                source.source_display_name,
                action,
                warning,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(
                    "\n".join(_issue_code_text(code) for code in source.issue_codes)
                    if column == 3 and source.issue_codes
                    else value
                )
                self.source_table.setItem(row, column, item)
        if sources:
            self.source_table.selectRow(0)

    def open_output_folder(self) -> None:
        result = self.folder_service.open(
            OpenOutputFolderRequest(
                self.overview.profile,
                config_path=self.overview.config_path,
                expected_output_root=self.overview.output_root,
            )
        )
        if not isinstance(result, OpenOutputFolderResult):
            self._set_status("The local course folder could not be opened.", "error")
            return
        if result.status is OperationStatus.SUCCEEDED:
            self._set_status("Opened the local course folder.", "success")
        else:
            message = (
                _issue_text(result.issues[0])
                if result.issues
                else "The local course folder could not be opened."
            )
            self._set_status(message, "error")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        if self._active_task is not None:
            self._close_when_idle = True
            self.cancel_active_operation()
            self._set_status(
                "Waiting for the current safe boundary before closing…",
                "working",
            )
            event.ignore()
            return
        if self.controller.close():
            self._allow_close = True
            event.accept()
        else:
            event.ignore()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#daily_workspace { background: #F6F8FB; color: #172033; }
            QWidget { font-family: "Segoe UI Variable", "SF Pro Text", "Segoe UI"; font-size: 10pt; }
            QFrame#brand_mark { background: #2F6FED; border-radius: 2px; }
            QLabel#brand_eyebrow { color: #2F6FED; font-size: 8pt; font-weight: 750; letter-spacing: 1px; }
            QLabel#daily_title { font-size: 18pt; font-weight: 650; color: #172033; }
            QLabel#meta_label { color: #788398; font-size: 7.5pt; font-weight: 700; }
            QLabel#meta_value { color: #172033; font-size: 9pt; font-weight: 600; }
            QFrame#daily_status { border-radius: 5px; }
            QFrame#daily_status[tone="ready"] { background: #EAF0FF; border-left: 4px solid #3B6FF5; }
            QFrame#daily_status[tone="working"] { background: #EAF0FF; border-left: 4px solid #3B6FF5; }
            QFrame#daily_status[tone="success"] { background: #E4F3ED; border-left: 4px solid #16755B; }
            QFrame#daily_status[tone="warning"] { background: #FFF1D9; border-left: 4px solid #A35D00; }
            QFrame#daily_status[tone="error"] { background: #FCE9E8; border-left: 4px solid #A83A35; }
            QLabel#daily_status_text { color: #263B5D; font-weight: 600; }
            QFrame#warning_panel { border-radius: 5px; }
            QFrame#warning_panel[tone="warning"] { background: #FFF7E8; border: 1px solid #E6BD79; }
            QFrame#warning_panel[tone="error"] { background: #FCE9E8; border: 1px solid #DDA6A2; }
            QFrame#warning_panel QLabel#notice_summary { color: #7B4800; font-weight: 700; }
            QFrame#warning_panel QLabel#notice_details { color: #67441A; }
            QFrame#warning_panel QPushButton#notice_toggle_button { min-height: 24px; padding: 1px 8px; color: #7B4800; background: transparent; border: none; font-weight: 650; }
            QFrame#warning_panel QPushButton#notice_close_button { min-height: 24px; padding: 0; color: #7B4800; background: transparent; border: none; font-size: 13pt; }
            QFrame#warning_panel[tone="error"] QLabel#notice_summary,
            QFrame#warning_panel[tone="error"] QLabel#notice_details { color: #7A1E1E; }
            QFrame#warning_panel[tone="error"] QPushButton#notice_toggle_button,
            QFrame#warning_panel[tone="error"] QPushButton#notice_close_button { color: #7A1E1E; }
            QTableWidget#source_review { background: #FFFFFF; border: 1px solid #D9E1EC; border-radius: 5px; selection-background-color: #E5EDFF; selection-color: #15243A; gridline-color: #E8EDF4; }
            QTableWidget#source_review:focus { border: 2px solid #3B6FF5; }
            QHeaderView::section { background: #EDF2F8; color: #384961; padding: 8px; border: none; border-bottom: 1px solid #D9E1EC; font-weight: 650; }
            QTableWidget::item { padding: 7px; }
            QLabel#progress_label { color: #5F6C80; }
            QProgressBar { border: 1px solid #C7D2E2; border-radius: 3px; background: #E8EDF4; min-height: 8px; max-height: 8px; }
            QProgressBar::chunk { background: #3B6FF5; border-radius: 3px; }
            QPushButton { min-height: 32px; padding: 3px 15px; color: #172033; background: #FFFFFF; border: 1px solid #B9C5D5; border-radius: 5px; }
            QPushButton:hover { background: #EDF3FF; border-color: #7D9FE7; }
            QPushButton:focus { border: 2px solid #174EA6; background: #EDF3FF; }
            QPushButton:disabled { color: #99A3B2; background: #EEF1F5; border-color: #D8DEE8; }
            QPushButton#preview_button { color: #214FA7; font-weight: 650; }
            QPushButton#apply_button { background: #3B6FF5; color: #FFFFFF; border-color: #3B6FF5; font-weight: 700; }
            QPushButton#apply_button:hover { background: #315FCC; }
            QPushButton#apply_button:disabled { color: #99A3B2; background: #EEF1F5; border-color: #D8DEE8; }
            """
        )


def _desktop_folder_service() -> OutputFolderService:
    return OutputFolderService(
        opener=lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    )


def create_daily_dashboard(
    *,
    profile: str = "default",
    config_path: Path | None = None,
    controller: DailySyncController | None = None,
    folder_service: OutputFolderService | None = None,
) -> DailyDashboardWindow:
    overview = inspect_profile_overview(profile, config_path=config_path)
    operation_controller = controller or DailySyncController(ReviewedSyncOperation())
    request = SyncPreviewRequest(profile, config_path=overview.config_path)
    return DailyDashboardWindow(
        overview=overview,
        controller=operation_controller,
        request=request,
        folder_service=folder_service or _desktop_folder_service(),
    )


def main(argv: Sequence[str] | None = None) -> int:
    from .icon import apply_application_icon

    parser = argparse.ArgumentParser(description=f"{DISPLAY_NAME} daily desktop app")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv)
    apply_application_icon(application)
    window = create_daily_dashboard(profile=args.profile, config_path=args.config)
    window.show()
    window.raise_()
    return application.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["DailyDashboardWindow", "create_daily_dashboard", "main"]
