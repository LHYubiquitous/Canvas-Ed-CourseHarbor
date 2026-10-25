"""PySide6 first-run setup window over the framework-neutral controller.

This module renders in-memory setup and exact preview state.  An injected save
port may add an explicit local-setup commit, followed by an explicit daily-use
launcher install.  It performs no discovery or provider calls.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from PySide6.QtCore import (
    QEvent,
    QObject,
    QProcess,
    QRunnable,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
    Slot,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QDesktopServices,
    QFont,
    QKeyEvent,
    QShowEvent,
    QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from canvas_ed_mcp.application.setup_controller import (
    CourseMatch,
    SetupController,
    SetupStep,
)
from canvas_ed_mcp.application.contracts import OperationStatus
from canvas_ed_mcp.application.profiles import SetupCommitResult
from canvas_ed_mcp.application.setup_session import (
    CatalogFilter,
    ReviewState,
    SetupDefaults,
    SetupReview,
)
from canvas_ed_mcp.application.setup_workflow import SetupSavePort
from canvas_ed_mcp.config import ContentSelection, DownloadPolicy
from canvas_ed_mcp.initial import InitialSyncMode
from canvas_ed_mcp.launchers import install_launchers, render_launchers
from canvas_ed_mcp.ui.branding import DISPLAY_NAME
from canvas_ed_mcp.ui.components import CollapsibleNotice
from canvas_ed_mcp.ui.frozen_runtime import (
    APP_SHELL_MODULE,
    detached_module_command,
    frozen_companion_executables,
)


_T = TypeVar("_T")
_COURSE_KEY_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_MATCH_ID_ROLE = int(Qt.ItemDataRole.UserRole)
_STEP_ORDER = (
    SetupStep.SELECT,
    SetupStep.MAP,
    SetupStep.FOLDERS,
    SetupStep.REVIEW,
)
_STEP_LABELS = ("Select", "Map", "Folders", "Review")


@dataclass(frozen=True, slots=True)
class FolderRowWidgets:
    """Widget references for one logical course folder row."""

    default_label: QLabel
    override_edit: QLineEdit
    browse_button: QPushButton
    initial_mode_combo: QComboBox


class _SaveSignals(QObject):
    finished = Signal(object)


class _ConfigSaveTask(QRunnable):
    def __init__(self, save_port: SetupSavePort, review: SetupReview) -> None:
        super().__init__()
        self.save_port = save_port
        self.review = review
        self.signals = _SaveSignals()

    @Slot()
    def run(self) -> None:
        try:
            result: object = self.save_port.save(self.review)
        except Exception:
            # The UI boundary never reflects exception text, provider payloads,
            # paths containing credentials, or secret values.
            result = None
        self.signals.finished.emit(result)


class SetupWindow(QMainWindow):
    """Four-step setup surface with an optional injected save capability."""

    windowClosed = Signal(bool)

    def __init__(
        self,
        controller: SetupController,
        *,
        save_port: SetupSavePort | None = None,
        mode: str = "setup",
        dashboard_opener: Callable[[str, Path], bool] | None = None,
    ) -> None:
        if not isinstance(controller, SetupController):
            raise TypeError("controller must be a SetupController")
        if mode not in {"setup", "configure"}:
            raise ValueError("mode must be setup or configure")
        super().__init__()
        self.controller = controller
        self._mode = mode
        self._updating_course_settings = False
        self._save_port = save_port
        self._dashboard_opener = dashboard_opener
        self._thread_pool = QThreadPool.globalInstance()
        self._save_task: _ConfigSaveTask | None = None
        self._save_in_progress = False
        self._configuration_saved = False
        self._configuration_save_warning = False
        self._launcher_directory: Path | None = None
        self._daily_menu_launcher: Path | None = None
        self._centered = False
        self._allow_close = False
        self._close_notified = False
        self._updating_courses = False
        self._focus_timer = QTimer(self)
        self._focus_timer.setSingleShot(True)
        self._focus_timer.timeout.connect(self._focus_current_step)
        self.folder_rows: dict[str, FolderRowWidgets] = {}
        self._course_by_key = {
            key: controller.course(key) for key in controller.course_keys
        }

        self.setObjectName("setup_window")
        self.setWindowTitle(
            f"{DISPLAY_NAME} — Configure courses"
            if mode == "configure"
            else f"{DISPLAY_NAME} — Set up courses"
        )
        self.resize(1040, 720)
        self.setMinimumSize(860, 620)
        self._build_window()
        self._apply_style()
        self._initialize_filters()
        self._refresh_all()

    # ---- Window frame -------------------------------------------------

    def _build_window(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(24, 16, 24, 16)
        root_layout.setSpacing(8)

        title = QLabel(
            "Update your local course library"
            if self._mode == "configure"
            else "Choose what belongs in your local course library"
        )
        title.setObjectName("window_title")
        root_layout.addWidget(title)
        root_layout.addWidget(self._build_step_rail())

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("setup_pages")
        self.page_stack.addWidget(self._build_select_page())
        self.page_stack.addWidget(self._build_map_page())
        self.page_stack.addWidget(self._build_folders_page())
        self.page_stack.addWidget(self._build_review_page())
        root_layout.addWidget(self.page_stack, 1)

        root_layout.addWidget(self._build_status_bar())
        self.navigation_bar = self._build_navigation()
        root_layout.addWidget(self.navigation_bar)
        self._configure_tab_order()
        self.setCentralWidget(root)

    def _configure_tab_order(self) -> None:
        chains = (
            (
                self.search_edit,
                self.term_filter,
                self.provider_filter,
                self.course_table,
                self.toggle_visible_checkbox,
            ),
            (
                self.pending_list,
                self.logical_key_edit,
                self.map_link_button,
                self.map_separate_button,
                self.linked_list,
                self.linked_undo_button,
                self.separate_list,
                self.separate_undo_button,
                self.manual_canvas_combo,
                self.manual_ed_combo,
                self.manual_pair_button,
            ),
            (
                self.canvas_pages_checkbox,
                self.canvas_files_checkbox,
                self.ed_lessons_checkbox,
                self.ed_resources_checkbox,
                self.max_file_mib_spin,
                self.folder_table,
            ),
        )
        for chain in chains:
            for current, following in zip(chain, chain[1:]):
                QWidget.setTabOrder(current, following)

    def _build_step_rail(self) -> QWidget:
        rail = QFrame()
        rail.setObjectName("step_rail")
        layout = QHBoxLayout(rail)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(6)
        self.step_labels: list[QLabel] = []
        self.step_numbers: list[QLabel] = []
        for index, label_text in enumerate(_STEP_LABELS, start=1):
            number = QLabel(str(index))
            number.setObjectName("step_number")
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label = QLabel(label_text)
            label.setObjectName("step_label")
            self.step_numbers.append(number)
            self.step_labels.append(label)
            layout.addWidget(number)
            layout.addWidget(label)
            if index != len(_STEP_LABELS):
                divider = QFrame()
                divider.setObjectName("step_divider")
                divider.setFrameShape(QFrame.Shape.HLine)
                layout.addWidget(divider, 1)
        return rail

    def _build_status_bar(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("status_frame")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        self.status_label = QLabel()
        self.status_label.setObjectName("status_label")
        self.status_label.setAccessibleName("Setup error")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        frame.hide()
        self.status_frame = frame
        return frame

    def _build_navigation(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        self.preview_badge = QLabel(
            "Local setup" if self._save_port is not None else "Preview only"
        )
        self.preview_badge.setObjectName("preview_badge")
        self.back_button = QPushButton("Back")
        self.back_button.setObjectName("back_button")
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("continue_button")
        self.continue_button.setDefault(True)
        continue_shadow = QGraphicsDropShadowEffect(self.continue_button)
        continue_shadow.setBlurRadius(22)
        continue_shadow.setOffset(0, 4)
        continue_shadow.setColor(QColor(47, 111, 237, 90))
        self.continue_button.setGraphicsEffect(continue_shadow)
        layout.addWidget(self.preview_badge)
        layout.addStretch(1)
        layout.addWidget(self.back_button)
        layout.addWidget(self.continue_button)
        self.back_button.clicked.connect(self._back)
        self.continue_button.clicked.connect(self._continue)
        return bar

    # ---- Select -------------------------------------------------------

    def _build_select_page(self) -> QWidget:
        page, layout = self._page_shell(
            "Select courses",
            "Filter the catalogue first, then select only the courses you want locally.",
        )
        filters = QGridLayout()
        filters.setHorizontalSpacing(12)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("search_edit")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setPlaceholderText("Search code, course, term, or provider")
        self.term_filter = QComboBox()
        self.term_filter.setObjectName("term_filter")
        self.provider_filter = QComboBox()
        self.provider_filter.setObjectName("provider_filter")
        self.search_label = QLabel("&Search")
        self.search_label.setBuddy(self.search_edit)
        self.term_label = QLabel("&Term")
        self.term_label.setBuddy(self.term_filter)
        self.provider_label = QLabel("&Provider")
        self.provider_label.setBuddy(self.provider_filter)
        filters.addWidget(self.search_label, 0, 0)
        filters.addWidget(self.term_label, 0, 1)
        filters.addWidget(self.provider_label, 0, 2)
        filters.addWidget(self.search_edit, 1, 0)
        filters.addWidget(self.term_filter, 1, 1)
        filters.addWidget(self.provider_filter, 1, 2)
        filters.setColumnStretch(0, 3)
        filters.setColumnStretch(1, 1)
        filters.setColumnStretch(2, 1)
        layout.addLayout(filters)

        self.course_model = QStandardItemModel(0, 4)
        self.course_model.setHorizontalHeaderLabels(
            ("Code", "Course", "Term", "Provider")
        )
        self.course_table = QTableView()
        self.course_table.setObjectName("course_table")
        self.course_table.setAccessibleName("Course catalogue")
        self.course_table.setAccessibleDescription(
            "Check the courses to include in the local course library."
        )
        self.course_table.setModel(self.course_model)
        self.course_table.installEventFilter(self)
        self.course_table.viewport().installEventFilter(self)
        self.course_table.setAlternatingRowColors(True)
        self.course_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.course_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.course_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.course_table.verticalHeader().hide()
        header = self.course_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.course_table, 1)

        actions = QHBoxLayout()
        self.toggle_visible_checkbox = QCheckBox("Select all visible")
        self.toggle_visible_checkbox.setObjectName("toggle_visible_checkbox")
        # Compatibility alias for the first preview's test/control vocabulary.
        self.select_visible_button = self.toggle_visible_checkbox
        self.selected_count_label = QLabel("Selected 0 courses")
        self.selected_count_label.setObjectName("selected_count_label")
        actions.addWidget(self.toggle_visible_checkbox)
        actions.addStretch(1)
        actions.addWidget(self.selected_count_label)
        layout.addLayout(actions)

        self.search_edit.textChanged.connect(self._filters_changed)
        self.term_filter.currentIndexChanged.connect(self._filters_changed)
        self.provider_filter.currentIndexChanged.connect(self._filters_changed)
        self.course_model.itemChanged.connect(self._course_item_changed)
        self.toggle_visible_checkbox.clicked.connect(
            self._toggle_visible_selection
        )
        return page

    def _initialize_filters(self) -> None:
        terms = list(self.controller.ordered_terms)
        if any(not course.term for course in self._course_by_key.values()):
            terms.append("Term unavailable")
        for widget in (self.term_filter, self.provider_filter):
            widget.blockSignals(True)
        try:
            self.term_filter.addItem("All terms", None)
            for term in terms:
                self.term_filter.addItem(term, term)
            self.provider_filter.addItem("All providers", None)
            self.provider_filter.addItem("Canvas", "canvas")
            self.provider_filter.addItem("Ed", "ed")
            filters = self.controller.filters
            self.search_edit.blockSignals(True)
            self.search_edit.setText(filters.query)
            self.search_edit.blockSignals(False)
            if len(filters.terms) == 1:
                self.term_filter.setCurrentIndex(
                    max(0, self.term_filter.findData(next(iter(filters.terms))))
                )
            if len(filters.providers) == 1:
                self.provider_filter.setCurrentIndex(
                    max(
                        0,
                        self.provider_filter.findData(next(iter(filters.providers))),
                    )
                )
        finally:
            self.term_filter.blockSignals(False)
            self.provider_filter.blockSignals(False)

    def _filters_changed(self, _value: object = None) -> None:
        provider = self.provider_filter.currentData()
        term = self.term_filter.currentData()
        filters = CatalogFilter(
            query=self.search_edit.text(),
            providers=frozenset((provider,)) if isinstance(provider, str) else frozenset(),
            terms=frozenset((term,)) if isinstance(term, str) else frozenset(),
        )
        self._run_action(lambda: self.controller.set_filters(filters))

    def _course_item_changed(self, item: QStandardItem) -> None:
        if self._updating_courses or item.column() != 0:
            return
        key = item.data(_COURSE_KEY_ROLE)
        if not isinstance(key, str):
            return
        selected = key in set(self.controller.selected_course_keys)
        checked = item.checkState() == Qt.CheckState.Checked
        if selected == checked:
            return
        action = (
            (lambda: self.controller.select_course(key))
            if checked
            else (lambda: self.controller.deselect_course(key))
        )
        try:
            action()
        except ValueError as exc:
            self._show_status(str(exc))
            return
        self._clear_status()
        # The checked item is already current in the model.  Rebuilding all 300
        # rows here would discard keyboard focus and the current table index.
        self._refresh_selection_summary()
        self._refresh_navigation()

    def _toggle_visible_selection(self) -> None:
        visible = self.controller.visible_rows
        all_selected = bool(visible) and all(row.selected for row in visible)
        self._run_action(
            self.controller.clear_visible
            if all_selected
            else self.controller.select_visible
        )

    def set_course_checked(self, candidate_key: str, checked: bool) -> None:
        for row in range(self.course_model.rowCount()):
            item = self.course_model.item(row, 0)
            if item.data(_COURSE_KEY_ROLE) == candidate_key:
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                return
        raise ValueError("course is not visible under the current filters")

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            watched in (self.course_table, self.course_table.viewport())
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
            and event.key() == Qt.Key.Key_Space
        ):
            if not event.isAutoRepeat():
                self._toggle_current_course_check()
            event.accept()
            return True
        return super().eventFilter(watched, event)

    def _toggle_current_course_check(self) -> None:
        current = self.course_table.currentIndex()
        if not current.isValid():
            if self.course_model.rowCount() == 0:
                return
            current = self.course_model.index(0, 0)
        check_index = self.course_model.index(current.row(), 0)
        self.course_table.setCurrentIndex(check_index)
        item = self.course_model.itemFromIndex(check_index)
        item.setCheckState(
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )

    def _refresh_select(self) -> None:
        self._updating_courses = True
        try:
            self.course_model.removeRows(0, self.course_model.rowCount())
            for row in self.controller.visible_rows:
                code = QStandardItem(row.code or "—")
                code.setCheckable(True)
                code.setCheckState(
                    Qt.CheckState.Checked if row.selected else Qt.CheckState.Unchecked
                )
                code.setData(row.candidate_key, _COURSE_KEY_ROLE)
                name = QStandardItem(row.name)
                term = QStandardItem(row.term or "Term unavailable")
                provider = QStandardItem(row.provider.title())
                provider.setForeground(
                    QColor("#2F6FED" if row.provider == "canvas" else "#7452C8")
                )
                for item in (code, name, term, provider):
                    item.setEditable(False)
                self.course_model.appendRow((code, name, term, provider))
        finally:
            self._updating_courses = False
        self._refresh_selection_summary()

    def _refresh_selection_summary(self) -> None:
        count = self.controller.selected_count
        noun = "course" if count == 1 else "courses"
        self.selected_count_label.setText(f"Selected {count} {noun}")
        visible = self.controller.visible_rows
        all_selected = bool(visible) and all(row.selected for row in visible)
        self.toggle_visible_checkbox.blockSignals(True)
        self.toggle_visible_checkbox.setChecked(all_selected)
        self.toggle_visible_checkbox.setText(
            "Clear visible" if all_selected else "Select all visible"
        )
        self.toggle_visible_checkbox.setEnabled(bool(visible))
        self.toggle_visible_checkbox.blockSignals(False)

    # ---- Map ----------------------------------------------------------

    def _build_map_page(self) -> QWidget:
        page, layout = self._page_shell(
            "Confirm course matches",
            "Matching suggestions appear below. Nothing is linked until you confirm it.",
        )
        layout.setSpacing(4)

        self.mapping_summary_label = QLabel()
        self.mapping_summary_label.setObjectName("mapping_summary_label")
        self.mapping_summary_label.setAccessibleName("Course mapping summary")
        self.mapping_summary_label.setWordWrap(True)
        layout.addWidget(self.mapping_summary_label)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        pending_group, self.pending_list = self._match_group(
            "Pending automatic suggestions"
        )
        linked_group, self.linked_list = self._match_group("Linked")
        separate_group, self.separate_list = self._match_group("Kept separate")
        self.pending_list.setAccessibleName("Pending course match suggestions")
        self.linked_list.setAccessibleName("Linked courses")
        self.separate_list.setAccessibleName("Courses kept separate")
        for match_list in (
            self.pending_list,
            self.linked_list,
            self.separate_list,
        ):
            match_list.setMinimumHeight(match_list.fontMetrics().lineSpacing() * 6 + 16)
            match_list.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )

        self.logical_key_label = QLabel("&Logical course key")
        self.logical_key_label.setObjectName("logical_key_label")
        logical_key_font = self.logical_key_label.font()
        logical_key_font.setBold(True)
        self.logical_key_label.setFont(logical_key_font)
        logical_key_hint = QLabel(
            "Shared course identity and folder name."
        )
        logical_key_hint.setObjectName("logical_key_hint")
        logical_key_hint.setWordWrap(True)
        logical_key_hint.setAccessibleDescription(
            "Used as the shared course identity and folder basis. You can edit the suggested value."
        )
        self.logical_key_edit = QLineEdit()
        self.logical_key_edit.setObjectName("logical_key_edit")
        self.logical_key_edit.setPlaceholderText("Select a pending suggestion")
        self.logical_key_label.setBuddy(self.logical_key_edit)
        pending_group.layout().addWidget(self.logical_key_label)
        pending_group.layout().addWidget(logical_key_hint)
        pending_group.layout().addWidget(self.logical_key_edit)

        pending_actions = QHBoxLayout()
        pending_actions.setSpacing(6)
        self.map_link_button = QPushButton("Link courses")
        self.map_link_button.setObjectName("map_link_button")
        self.map_link_button.setAccessibleName(
            "Treat selected courses as the same course"
        )
        self.map_link_button.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed
        )
        self.map_separate_button = QPushButton("Keep separate")
        self.map_separate_button.setObjectName("map_separate_button")
        self.map_separate_button.setAccessibleName(
            "Keep selected courses as separate courses"
        )
        self.map_separate_button.setSizePolicy(
            QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed
        )
        pending_actions.addWidget(self.map_link_button, 1)
        pending_actions.addWidget(self.map_separate_button, 1)
        pending_group.layout().addLayout(pending_actions)

        self.manual_remove_button = QPushButton("Remove manual pair")
        self.manual_remove_button.setObjectName("manual_remove_button")
        self.manual_remove_button.setMinimumWidth(160)
        self.manual_remove_button.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        pending_group.layout().addWidget(
            self.manual_remove_button, alignment=Qt.AlignmentFlag.AlignRight
        )

        self.linked_undo_button = QPushButton("Undo selected link")
        self.linked_undo_button.setObjectName("linked_undo_button")
        self.linked_undo_button.setMinimumWidth(160)
        self.linked_undo_button.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        linked_footer = QHBoxLayout()
        linked_footer.addStretch(1)
        linked_footer.addWidget(self.linked_undo_button)
        linked_group.layout().addLayout(linked_footer)

        self.separate_undo_button = QPushButton("Undo selected separate decision")
        self.separate_undo_button.setObjectName("separate_undo_button")
        self.separate_undo_button.setMinimumWidth(210)
        self.separate_undo_button.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        separate_footer = QHBoxLayout()
        separate_footer.addStretch(1)
        separate_footer.addWidget(self.separate_undo_button)
        separate_group.layout().addLayout(separate_footer)

        columns.addWidget(pending_group, 1)
        columns.addWidget(linked_group, 1)
        columns.addWidget(separate_group, 1)

        self.manual_pair_group = QGroupBox("Optional manual pairing")
        self.manual_pair_group.setObjectName("manual_pair_group")
        self.manual_pair_group.setToolTip(
            "Use this only when an automatic suggestion is wrong or the two platforms use different course codes."
        )
        manual_layout = QHBoxLayout(self.manual_pair_group)
        manual_layout.setSpacing(8)
        self.manual_canvas_label = QLabel("&Canvas course")
        manual_layout.addWidget(self.manual_canvas_label)
        self.manual_canvas_combo = QComboBox()
        self.manual_canvas_combo.setObjectName("manual_canvas_combo")
        self.manual_canvas_combo.setAccessibleName("Canvas course for manual pairing")
        self.manual_canvas_label.setBuddy(self.manual_canvas_combo)
        self.manual_canvas_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.manual_canvas_combo.setMinimumContentsLength(12)
        self.manual_canvas_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        manual_layout.addWidget(self.manual_canvas_combo, 1)
        self.manual_ed_label = QLabel("&Ed course")
        manual_layout.addWidget(self.manual_ed_label)
        self.manual_ed_combo = QComboBox()
        self.manual_ed_combo.setObjectName("manual_ed_combo")
        self.manual_ed_combo.setAccessibleName("Ed course for manual pairing")
        self.manual_ed_label.setBuddy(self.manual_ed_combo)
        self.manual_ed_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.manual_ed_combo.setMinimumContentsLength(12)
        self.manual_ed_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        manual_layout.addWidget(self.manual_ed_combo, 1)
        self.manual_pair_button = QPushButton("Confirm manual pair")
        self.manual_pair_button.setObjectName("manual_pair_button")
        self.manual_pair_button.setMinimumWidth(170)
        self.manual_pair_button.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        manual_layout.addWidget(self.manual_pair_button)
        layout.addWidget(self.manual_pair_group)
        layout.addLayout(columns, 2)
        self.map_link_button.clicked.connect(self._link_selected_match)
        self.map_separate_button.clicked.connect(self._separate_selected_match)
        self.manual_remove_button.clicked.connect(self._remove_selected_manual_pair)
        self.linked_undo_button.clicked.connect(self._undo_selected_link)
        self.separate_undo_button.clicked.connect(self._undo_selected_separate)
        self.manual_pair_button.clicked.connect(self._create_manual_pair)
        self.pending_list.currentItemChanged.connect(self._pending_match_changed)
        self.linked_list.currentItemChanged.connect(
            lambda _current, _previous: self._update_mapping_undo_buttons()
        )
        self.separate_list.currentItemChanged.connect(
            lambda _current, _previous: self._update_mapping_undo_buttons()
        )
        self.map_scroll = QScrollArea()
        self.map_scroll.setObjectName("map_scroll")
        self.map_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.map_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.map_scroll.setWidgetResizable(True)
        self.map_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.map_scroll.setWidget(page)
        return self.map_scroll

    @staticmethod
    def _match_group(title: str) -> tuple[QGroupBox, QListWidget]:
        group = QGroupBox(title)
        box = QVBoxLayout(group)
        box.setSpacing(4)
        items = QListWidget()
        items.setWordWrap(True)
        items.setAlternatingRowColors(True)
        box.addWidget(items)
        return group, items

    def _match_text(self, match: CourseMatch) -> str:
        canvas = self.controller.course(match.canvas_key)
        ed = self.controller.course(match.ed_key)
        canvas_code = canvas.code or "No code"
        ed_code = ed.code or "No code"
        code = (
            canvas_code
            if canvas_code.casefold() == ed_code.casefold()
            else f"{canvas_code} ↔ {ed_code}"
        )
        canvas_term = canvas.term or "Term unavailable"
        ed_term = ed.term or "Term unavailable"
        term = (
            canvas_term
            if canvas_term.casefold() == ed_term.casefold()
            else f"{canvas_term} ↔ {ed_term}"
        )
        return f"{code} · {term}\nCanvas: {canvas.name}\nEd: {ed.name}"

    def _add_match_item(self, target: QListWidget, match: CourseMatch) -> None:
        item = QListWidgetItem(self._match_text(match))
        item.setData(_MATCH_ID_ROLE, match.match_id)
        item.setToolTip(match.basis.replace("_", " "))
        target.addItem(item)

    @staticmethod
    def _manual_option_text(course: object) -> str:
        code = getattr(course, "code", "") or "No code"
        name = getattr(course, "name", "") or "Unnamed course"
        term = getattr(course, "term", None) or "Term unavailable"
        return f"{code} · {name} · {term}"

    def _refresh_map(self) -> None:
        for widget in (self.pending_list, self.linked_list, self.separate_list):
            widget.clear()
        for match in self.controller.pending_matches:
            self._add_match_item(self.pending_list, match)
        for match in self.controller.linked_matches:
            self._add_match_item(self.linked_list, match)
        for match in self.controller.separate_matches:
            self._add_match_item(self.separate_list, match)

        automatic_pending = sum(
            match.basis != "user_selected_manual_pair"
            for match in self.controller.pending_matches
        )
        manual_pending = len(self.controller.pending_matches) - automatic_pending
        automatic_label = (
            f"{automatic_pending} automatic suggestion"
            f"{'s' if automatic_pending != 1 else ''} pending"
        )
        summary_parts = [
            automatic_label,
            f"{manual_pending} manual pair{'s' if manual_pending != 1 else ''} pending",
            f"{len(self.controller.linked_matches)} linked",
            f"{len(self.controller.separate_matches)} kept separate",
            f"{len(self.controller.unmatched_selected_keys)} unmatched selected",
        ]
        self.mapping_summary_label.setText(" · ".join(summary_parts))

        self.manual_canvas_combo.clear()
        self.manual_ed_combo.clear()
        for course in self.controller.manual_pair_options("canvas"):
            self.manual_canvas_combo.addItem(
                self._manual_option_text(course), course.key
            )
        for course in self.controller.manual_pair_options("ed"):
            self.manual_ed_combo.addItem(self._manual_option_text(course), course.key)
        can_add_manual_pair = bool(
            self.manual_canvas_combo.count() and self.manual_ed_combo.count()
        )
        self.manual_pair_button.setEnabled(can_add_manual_pair)
        self.manual_pair_group.setVisible(can_add_manual_pair)

        if self.pending_list.count():
            self.pending_list.setCurrentRow(0)
        else:
            self.logical_key_edit.clear()
            self.logical_key_edit.setEnabled(False)
        if self.linked_list.count():
            self.linked_list.setCurrentRow(0)
        if self.separate_list.count():
            self.separate_list.setCurrentRow(0)
        has_pending = bool(self.pending_list.count())
        self.map_link_button.setEnabled(has_pending)
        self.map_separate_button.setEnabled(has_pending)
        self._update_manual_remove_button()
        self._update_mapping_undo_buttons()

    def _update_mapping_undo_buttons(self) -> None:
        def update(button: QPushButton, widget: QListWidget) -> None:
            item = widget.currentItem()
            match_id = None if item is None else item.data(_MATCH_ID_ROLE)
            reason = None
            if isinstance(match_id, str) and hasattr(self.controller, "match_lock_reason"):
                reason = self.controller.match_lock_reason(match_id)
            button.setEnabled(item is not None and reason is None)
            button.setToolTip(reason or "")

        update(self.linked_undo_button, self.linked_list)
        update(self.separate_undo_button, self.separate_list)

    def _create_manual_pair(self) -> None:
        canvas_key = self.manual_canvas_combo.currentData()
        ed_key = self.manual_ed_combo.currentData()
        if not isinstance(canvas_key, str) or not isinstance(ed_key, str):
            self._show_status("choose one Canvas course and one Ed course")
            return
        self._run_action(
            lambda: self.controller.create_manual_match(canvas_key, ed_key)
        )

    def _pending_match_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        self.logical_key_edit.setEnabled(current is not None)
        if current is None:
            self.logical_key_edit.clear()
            self._update_manual_remove_button()
            return
        match_id = current.data(_MATCH_ID_ROLE)
        match = next(
            (
                value
                for value in self.controller.pending_matches
                if value.match_id == match_id
            ),
            None,
        )
        self.logical_key_edit.setText(
            "" if match is None else match.suggested_logical_key
        )
        self._update_manual_remove_button()

    def _update_manual_remove_button(self) -> None:
        item = self.pending_list.currentItem()
        match_id = None if item is None else item.data(_MATCH_ID_ROLE)
        match = next(
            (
                value
                for value in self.controller.pending_matches
                if value.match_id == match_id
            ),
            None,
        )
        self.manual_remove_button.setEnabled(
            match is not None and match.basis == "user_selected_manual_pair"
        )

    @staticmethod
    def _selected_match_id(widget: QListWidget, message: str) -> str:
        item = widget.currentItem()
        if item is None:
            raise ValueError(message)
        match_id = item.data(_MATCH_ID_ROLE)
        if not isinstance(match_id, str):
            raise ValueError("selected match has no stable identity")
        return match_id

    def _link_selected_match(self) -> None:
        def action() -> None:
            match_id = self._selected_match_id(
                self.pending_list, "choose a pending match to link"
            )
            self.controller.link_match(
                match_id, logical_key=self.logical_key_edit.text().strip()
            )

        self._run_action(action)

    def _separate_selected_match(self) -> None:
        self._run_action(
            lambda: self.controller.keep_separate(
                self._selected_match_id(
                    self.pending_list, "choose a pending match to keep separate"
                )
            )
        )

    def _remove_selected_manual_pair(self) -> None:
        self._run_action(
            lambda: self.controller.remove_manual_match(
                self._selected_match_id(
                    self.pending_list, "choose a manual pair to remove"
                )
            )
        )

    def _undo_selected_link(self) -> None:
        self._run_action(
            lambda: self.controller.undo_link(
                self._selected_match_id(
                    self.linked_list, "choose a linked match to undo"
                )
            )
        )

    def _undo_selected_separate(self) -> None:
        self._run_action(
            lambda: self.controller.undo_separate(
                self._selected_match_id(
                    self.separate_list, "choose a separate match to undo"
                )
            )
        )

    # ---- Folders ------------------------------------------------------

    def _build_folders_page(self) -> QWidget:
        page, layout = self._page_shell(
            "Choose the library root and course folders",
            "Each course folder is a direct child of the Library root. Nothing is moved or created during review.",
        )
        library_group = QGroupBox("Library root")
        library_group.setObjectName("library_root_group")
        library_layout = QVBoxLayout(library_group)
        library_layout.setContentsMargins(10, 8, 10, 8)
        library_layout.setSpacing(4)
        library_group.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        library_row = QHBoxLayout()
        self.library_root_edit = QLineEdit(str(self.controller.output_root))
        self.library_root_edit.setObjectName("library_root_edit")
        self.library_root_edit.setAccessibleName("Library root")
        self.library_root_edit.setReadOnly(True)
        self.choose_library_root_button = QPushButton("Choose library…")
        self.choose_library_root_button.setObjectName("choose_library_root_button")
        self.choose_library_root_button.setAccessibleName("Choose Library root")
        self.choose_library_root_button.clicked.connect(self._browse_library_root)
        library_row.addWidget(self.library_root_edit, 1)
        library_row.addWidget(self.choose_library_root_button)
        library_layout.addLayout(library_row)
        library_hint = QLabel(
            "Course folders, launchers, and due reports use this root. Choose it "
            "before selecting course folders."
        )
        library_hint.setObjectName("library_root_hint")
        library_hint.setWordWrap(True)
        library_hint.setAccessibleDescription(
            "Course folders, Canvas Ed MCP Launchers, and due reports live under "
            "this root. Choose the root here, then choose each course folder below."
        )
        library_layout.addWidget(library_hint)
        if not self.controller.output_root_editable:
            self.choose_library_root_button.setEnabled(False)
            self.choose_library_root_button.setToolTip(
                "Changing an existing Library root requires a separate file migration."
            )
        layout.addWidget(library_group)

        content_group = QGroupBox("Content and file limits")
        content_group.setObjectName("content_defaults_group")
        content_group.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        content_layout = QGridLayout(content_group)
        content_layout.setContentsMargins(10, 8, 10, 8)
        content_layout.setHorizontalSpacing(10)
        content_layout.setVerticalSpacing(4)
        if self._mode == "configure":
            self.course_settings_label = QLabel("&Course to edit:")
            self.course_settings_combo = QComboBox()
            self.course_settings_combo.setObjectName("course_settings_combo")
            self.course_settings_label.setBuddy(self.course_settings_combo)
            self.course_settings_combo.currentIndexChanged.connect(
                self._course_settings_selection_changed
            )
            content_layout.addWidget(self.course_settings_label, 0, 0)
            content_layout.addWidget(self.course_settings_combo, 0, 1, 1, 3)
            content_row = 1
        else:
            content_row = 0
        self.canvas_pages_checkbox = QCheckBox("Canvas module pages")
        self.canvas_pages_checkbox.setObjectName("canvas_pages_checkbox")
        self.canvas_files_checkbox = QCheckBox(
            "Canvas files (Modules + course Files)"
        )
        self.canvas_files_checkbox.setObjectName("canvas_files_checkbox")
        self.ed_lessons_checkbox = QCheckBox("Ed lessons")
        self.ed_lessons_checkbox.setObjectName("ed_lessons_checkbox")
        self.ed_resources_checkbox = QCheckBox("Ed resources")
        self.ed_resources_checkbox.setObjectName("ed_resources_checkbox")
        content_layout.addWidget(self.canvas_pages_checkbox, content_row, 0)
        content_layout.addWidget(self.canvas_files_checkbox, content_row, 1)
        content_layout.addWidget(self.ed_lessons_checkbox, content_row + 1, 0)
        content_layout.addWidget(self.ed_resources_checkbox, content_row + 1, 1)

        self.max_file_label = QLabel("Maximum single &file size:")
        self.max_file_mib_spin = QSpinBox()
        self.max_file_mib_spin.setObjectName("max_file_mib_spin")
        self.max_file_mib_spin.setRange(0, 10240)
        self.max_file_mib_spin.setSuffix(" MiB")
        self.max_file_mib_spin.setSpecialValueText("Unlimited")
        self.max_file_label.setBuddy(self.max_file_mib_spin)
        content_layout.addWidget(self.max_file_label, content_row, 2)
        content_layout.addWidget(self.max_file_mib_spin, content_row, 3)
        content_hint = QLabel(
            "Pages are module-linked HTML. Files includes module-linked and "
            "course-area files."
        )
        content_hint.setObjectName("content_defaults_hint")
        content_hint.setWordWrap(True)
        content_hint.setAccessibleDescription(
            "Canvas Pages means HTML pages linked from Modules. Canvas Files includes "
            "module-linked files and the course Files area."
        )
        content_layout.addWidget(content_hint, content_row + 1, 2, 1, 2)
        content_layout.setColumnStretch(0, 1)
        content_layout.setColumnStretch(1, 1)
        content_layout.setColumnStretch(2, 1)
        layout.addWidget(content_group)

        for checkbox in (
            self.canvas_pages_checkbox,
            self.canvas_files_checkbox,
            self.ed_lessons_checkbox,
            self.ed_resources_checkbox,
        ):
            checkbox.toggled.connect(self._content_defaults_changed)
        self.max_file_mib_spin.valueChanged.connect(
            self._content_defaults_changed
        )

        per_course_hint = QLabel(
            "Choose the initial download for each course. Linked sources share one choice."
        )
        per_course_hint.setObjectName("per_course_initial_hint")
        per_course_hint.setWordWrap(True)
        per_course_hint.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        layout.addWidget(per_course_hint)

        self.folder_table = QTableWidget(0, 4)
        self.folder_table.setObjectName("folder_table")
        self.folder_table.setAccessibleName("Local course folder settings")
        self.folder_table.setAccessibleDescription(
            "Choose initial download separately for each course. Linked Canvas and Ed "
            "sources use the same choice."
        )
        self.folder_table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.folder_table.setHorizontalHeaderLabels(
            ("Course", "Sources", "Initial download", "Folder name")
        )
        vertical_header = self.folder_table.verticalHeader()
        vertical_header.setMinimumSectionSize(44)
        vertical_header.setDefaultSectionSize(48)
        vertical_header.hide()
        self.folder_table.setAlternatingRowColors(True)
        self.folder_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self.folder_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        header = self.folder_table.horizontalHeader()
        # A raw provider key can be much longer than its useful on-screen label.
        # Keep it from consuming the space needed by Sources and Override.
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(0, 190)
        header.resizeSection(2, 210)
        layout.addWidget(self.folder_table, 1)
        return page

    def _refresh_folders(self) -> None:
        folder_drafts = {
            logical_key: row.override_edit.text()
            for logical_key, row in self.folder_rows.items()
        }
        self.library_root_edit.setText(str(self.controller.output_root))
        defaults_widgets = (
            self.canvas_pages_checkbox,
            self.canvas_files_checkbox,
            self.ed_lessons_checkbox,
            self.ed_resources_checkbox,
            self.max_file_mib_spin,
        )
        for widget in defaults_widgets:
            widget.blockSignals(True)
        try:
            defaults = self.controller.defaults
            selected_content = defaults.content
            selected_policy = defaults.policy
            if self._mode == "configure":
                previous_key = self.course_settings_combo.currentData()
                self.course_settings_combo.blockSignals(True)
                self.course_settings_combo.clear()
                for choice in self.controller.mapping_choices:
                    self.course_settings_combo.addItem(choice.course_key, choice.course_key)
                selected_index = self.course_settings_combo.findData(previous_key)
                self.course_settings_combo.setCurrentIndex(max(0, selected_index))
                self.course_settings_combo.blockSignals(False)
                selected_key = self.course_settings_combo.currentData()
                selected_choice = next(
                    (
                        choice
                        for choice in self.controller.mapping_choices
                        if choice.course_key == selected_key
                    ),
                    None,
                )
                if selected_choice is not None:
                    selected_content = selected_choice.content
                    selected_policy = selected_choice.policy
            self.canvas_pages_checkbox.setChecked(selected_content.pages)
            self.canvas_files_checkbox.setChecked(selected_content.files)
            self.ed_lessons_checkbox.setChecked(selected_content.lessons)
            self.ed_resources_checkbox.setChecked(selected_content.resources)
            max_bytes = selected_policy.max_file_bytes
            self.max_file_mib_spin.setValue(
                0 if max_bytes is None else max(1, max_bytes // (1024 * 1024))
            )
            selected_providers = {
                self.controller.course(source.candidate_key).provider
                for choice in self.controller.mapping_choices
                for source in choice.sources
            }
            self.canvas_pages_checkbox.setEnabled("canvas" in selected_providers)
            self.canvas_files_checkbox.setEnabled("canvas" in selected_providers)
            self.ed_lessons_checkbox.setEnabled("ed" in selected_providers)
            self.ed_resources_checkbox.setEnabled("ed" in selected_providers)
        finally:
            for widget in defaults_widgets:
                widget.blockSignals(False)
        choices = self.controller.mapping_choices
        presentations = {
            row.logical_key: row for row in self.controller.folder_presentations
        }
        # QTableWidget defers deletion when a cell widget is replaced.  Rebuilding
        # the inactive page after every Select/Map click therefore left old editors
        # visibly stacked in the viewport on Windows.  Hide and detach the current
        # widgets before creating the new rows.
        for row_index in range(self.folder_table.rowCount()):
            for column in (2, 3):
                widget = self.folder_table.cellWidget(row_index, column)
                if widget is None:
                    continue
                self.folder_table.removeCellWidget(row_index, column)
                widget.hide()
                widget.deleteLater()
        self.folder_table.clearContents()
        self.folder_rows.clear()
        self.folder_table.setRowCount(len(choices))
        for row_index, choice in enumerate(choices):
            presentation = presentations[choice.course_key]
            sources = tuple(
                self.controller.course(source.candidate_key)
                for source in choice.sources
            )
            source_text = " + ".join(
                f"{source.provider.title()} · {source.code or source.name}"
                for source in sources
            )
            course_item = QTableWidgetItem(choice.course_key)
            course_item.setToolTip(choice.course_key)
            source_item = QTableWidgetItem(source_text)
            source_item.setToolTip(source_text)
            self.folder_table.setItem(row_index, 0, course_item)
            self.folder_table.setItem(row_index, 1, source_item)
            initial_mode_combo = QComboBox()
            initial_mode_combo.setObjectName(f"initial_mode_{row_index}")
            initial_mode_combo.setAccessibleName(
                f"Initial download for {choice.course_key}"
            )
            initial_mode_combo.setAccessibleDescription(
                "Start from now records the current remote state without importing "
                "older content. Import all history downloads all available content."
            )
            initial_mode_combo.addItem(
                "Start from now", InitialSyncMode.BASELINE_FROM_NOW
            )
            initial_mode_combo.addItem(
                "Import all history", InitialSyncMode.IMPORT_ALL
            )
            source_modes = {source.initial_mode for source in choice.sources}
            if len(source_modes) == 1:
                selected_mode = next(iter(source_modes))
                initial_mode_combo.setCurrentIndex(
                    initial_mode_combo.findData(selected_mode)
                )
            else:
                initial_mode_combo.insertItem(0, "Mixed — choose a mode", None)
                initial_mode_combo.setCurrentIndex(0)
            initial_mode_combo.currentIndexChanged.connect(
                lambda _index, key=choice.course_key, combo=initial_mode_combo: self._course_initial_mode_changed(
                    key, combo.currentData()
                )
            )
            if self._mode == "configure" and hasattr(
                self.controller, "initial_mode_editable"
            ):
                editable = self.controller.initial_mode_editable(choice.course_key)
                initial_mode_combo.setEnabled(editable)
                if not editable:
                    initial_mode_combo.setToolTip(
                        "Existing sources keep their original initial-download decision."
                    )
            initial_cell = QWidget()
            initial_cell.setObjectName("initial_mode_cell")
            initial_layout = QHBoxLayout(initial_cell)
            initial_layout.setContentsMargins(6, 0, 6, 0)
            initial_layout.addWidget(initial_mode_combo)
            self.folder_table.setCellWidget(row_index, 2, initial_cell)

            folder_cell = QWidget()
            folder_cell.setObjectName("folder_name_cell")
            folder_layout = QVBoxLayout(folder_cell)
            folder_layout.setContentsMargins(4, 1, 4, 1)
            folder_layout.setSpacing(2)
            default_label = QLabel(
                f"Default: {presentation.computed_default}"
            )
            default_label.setObjectName("default_folder_label")
            default_label.setToolTip(
                f"Effective folder: {presentation.effective}"
            )
            override_edit = QLineEdit(
                folder_drafts.get(
                    choice.course_key, presentation.override or ""
                )
            )
            override_edit.setPlaceholderText("Optional")
            override_edit.setObjectName(f"folder_override_{row_index}")
            override_edit.setAccessibleName(
                f"Folder name for {choice.course_key}"
            )
            override_edit.setAccessibleDescription(
                "Optional. Leave blank to use "
                f"{presentation.computed_default}, or browse to an existing folder "
                "directly inside the Library root."
            )
            override_edit.textEdited.connect(lambda _text: self._clear_status())
            browse_button = QPushButton("Browse…")
            browse_button.setObjectName(f"folder_browse_{row_index}")
            browse_button.setAccessibleName(
                f"Choose target folder for {choice.course_key}"
            )
            browse_button.setToolTip(
                "Choose an existing direct child of the Library root."
            )
            browse_button.clicked.connect(
                lambda _checked=False, key=choice.course_key: self._browse_course_folder(key)
            )
            if self._mode == "configure" and hasattr(
                self.controller, "is_existing_course"
            ) and self.controller.is_existing_course(choice.course_key):
                override_edit.setEnabled(False)
                browse_button.setEnabled(False)
                override_edit.setToolTip(
                    "Existing folder names are fixed in this version; files are never moved."
                )
            folder_layout.addWidget(default_label)
            folder_input_layout = QHBoxLayout()
            folder_input_layout.setContentsMargins(0, 0, 0, 0)
            folder_input_layout.addWidget(override_edit, 1)
            folder_input_layout.addWidget(browse_button)
            folder_layout.addLayout(folder_input_layout)
            self.folder_table.setCellWidget(row_index, 3, folder_cell)
            self.folder_rows[choice.course_key] = FolderRowWidgets(
                default_label,
                override_edit,
                browse_button,
                initial_mode_combo,
            )
            self.folder_table.setRowHeight(
                row_index,
                max(64, initial_cell.sizeHint().height(), folder_cell.sizeHint().height()),
            )

    def _browse_library_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Library root",
            str(self.controller.output_root),
        )
        if not selected:
            return
        try:
            root = self.controller.library_root_for_selected_directory(Path(selected))
        except ValueError as exc:
            self._show_status(str(exc), status="warning")
            return
        changed = self._run_action(lambda: self.controller.set_output_root(root))
        if changed:
            self._show_status(
                "Library root changed. Course folder names were kept and now resolve "
                "under the new root.",
                status="success",
            )

    def _browse_course_folder(self, logical_key: str) -> None:
        row = self.folder_rows.get(logical_key)
        if row is None:
            return
        selected = QFileDialog.getExistingDirectory(
            self,
            f"Choose target folder for {logical_key}",
            str(self.controller.output_root),
        )
        if not selected:
            return
        try:
            folder = self.controller.folder_name_for_selected_directory(Path(selected))
        except ValueError as exc:
            self._show_status(str(exc), status="warning")
            return
        row.override_edit.setText(folder)
        self._clear_status()

    def _course_initial_mode_changed(
        self, logical_key: str, value: object
    ) -> None:
        try:
            mode = InitialSyncMode(value)
        except (TypeError, ValueError):
            return
        self._run_action(
            lambda: self.controller.set_course_initial_mode(logical_key, mode)
        )

    def _content_defaults_changed(self, _value: object = None) -> None:
        if self._updating_course_settings:
            return
        defaults = self.controller.defaults
        max_mib = self.max_file_mib_spin.value()
        policy = defaults.policy
        if self._mode == "configure":
            selected_key = self.course_settings_combo.currentData()
            selected = next(
                (
                    choice
                    for choice in self.controller.mapping_choices
                    if choice.course_key == selected_key
                ),
                None,
            )
            if selected is None:
                return
            policy = selected.policy
            self._run_action(
                lambda: self.controller.set_course_content_policy(
                    selected.course_key,
                    content=ContentSelection(
                        files=self.canvas_files_checkbox.isChecked(),
                        pages=self.canvas_pages_checkbox.isChecked(),
                        lessons=self.ed_lessons_checkbox.isChecked(),
                        resources=self.ed_resources_checkbox.isChecked(),
                    ),
                    policy=DownloadPolicy(
                        max_file_bytes=(None if max_mib == 0 else max_mib * 1024 * 1024),
                        include_extensions=policy.include_extensions,
                        exclude_extensions=policy.exclude_extensions,
                        include_mime=policy.include_mime,
                        exclude_mime=policy.exclude_mime,
                    ),
                )
            )
            return
        self._run_action(
            lambda: self.controller.set_defaults(
                SetupDefaults(
                    initial_mode=defaults.initial_mode,
                    content=ContentSelection(
                        files=self.canvas_files_checkbox.isChecked(),
                        pages=self.canvas_pages_checkbox.isChecked(),
                        lessons=self.ed_lessons_checkbox.isChecked(),
                        resources=self.ed_resources_checkbox.isChecked(),
                    ),
                    policy=DownloadPolicy(
                        max_file_bytes=(
                            None if max_mib == 0 else max_mib * 1024 * 1024
                        ),
                        include_extensions=policy.include_extensions,
                        exclude_extensions=policy.exclude_extensions,
                        include_mime=policy.include_mime,
                        exclude_mime=policy.exclude_mime,
                    ),
                )
            )
        )

    def _course_settings_selection_changed(self, _index: int) -> None:
        if self._mode != "configure":
            return
        selected_key = self.course_settings_combo.currentData()
        choice = next(
            (
                item
                for item in self.controller.mapping_choices
                if item.course_key == selected_key
            ),
            None,
        )
        if choice is None:
            return
        widgets = (
            self.canvas_pages_checkbox,
            self.canvas_files_checkbox,
            self.ed_lessons_checkbox,
            self.ed_resources_checkbox,
            self.max_file_mib_spin,
        )
        self._updating_course_settings = True
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self.canvas_pages_checkbox.setChecked(choice.content.pages)
            self.canvas_files_checkbox.setChecked(choice.content.files)
            self.ed_lessons_checkbox.setChecked(choice.content.lessons)
            self.ed_resources_checkbox.setChecked(choice.content.resources)
            self.max_file_mib_spin.setValue(
                0
                if choice.policy.max_file_bytes is None
                else max(1, choice.policy.max_file_bytes // (1024 * 1024))
            )
            providers = {
                self.controller.course(source.candidate_key).provider
                for source in choice.sources
            }
            self.canvas_pages_checkbox.setEnabled("canvas" in providers)
            self.canvas_files_checkbox.setEnabled("canvas" in providers)
            self.ed_lessons_checkbox.setEnabled("ed" in providers)
            self.ed_resources_checkbox.setEnabled("ed" in providers)
        finally:
            for widget in widgets:
                widget.blockSignals(False)
            self._updating_course_settings = False

    # ---- Review -------------------------------------------------------

    def _build_review_page(self) -> QWidget:
        description = (
            "Verify the exact destinations and settings, then save this local setup."
            if self._save_port is not None
            else "Verify the exact destinations and settings in this preview."
        )
        page, layout = self._page_shell(
            "Review the exact plan",
            description,
        )
        notice = (
            "Review the exact plan below. Saving creates the configuration file and any "
            "missing empty course folders. It does not download course content."
            if self._save_port is not None
            else "Preview only — nothing has been saved. This window has no commit action."
        )
        self.review_notice = CollapsibleNotice(
            summary="What will be saved",
            text=notice,
            tone="warning",
            dismissible=False,
            expanded=False,
            accessibility_subject="setup explanation",
        )
        self.review_notice.setObjectName("review_notice")
        self.review_preview = QPlainTextEdit()
        self.review_preview.setObjectName("review_preview")
        self.review_preview.setAccessibleName("Exact setup preview")
        self.review_preview.setReadOnly(True)
        self.review_preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        preview_font = QFont("Cascadia Mono")
        preview_font.setStyleHint(QFont.StyleHint.Monospace)
        self.review_preview.setFont(preview_font)
        layout.addWidget(self.review_notice)
        layout.addWidget(self.review_preview, 1)
        if self._save_port is not None:
            save_actions = QHBoxLayout()
            save_actions.addStretch(1)
            self.install_launchers_button = QPushButton("Install CLI shortcuts")
            self.install_launchers_button.setObjectName("install_launchers_button")
            self.install_launchers_button.setMinimumWidth(220)
            self.install_launchers_button.clicked.connect(self._install_daily_launchers)
            self.install_launchers_button.hide()
            save_actions.addWidget(self.install_launchers_button)
            self.open_launchers_button = QPushButton("Open shortcuts folder")
            self.open_launchers_button.setObjectName("open_launchers_button")
            self.open_launchers_button.clicked.connect(self._open_launchers_folder)
            self.open_launchers_button.hide()
            save_actions.addWidget(self.open_launchers_button)
            self.open_daily_menu_button = QPushButton("Open daily dashboard")
            self.open_daily_menu_button.setObjectName("open_daily_menu_button")
            self.open_daily_menu_button.clicked.connect(self._open_daily_menu)
            self.open_daily_menu_button.hide()
            save_actions.addWidget(self.open_daily_menu_button)
            layout.addLayout(save_actions)
        return page

    def _refresh_review(self) -> None:
        review = self.controller.review
        self.review_preview.setPlainText(
            "Review has not been built."
            if review is None
            else review.plan.preview
        )

    @property
    def configuration_saved(self) -> bool:
        return self._configuration_saved

    def _request_config_save(self) -> None:
        if self._save_port is None or self._save_in_progress:
            return
        review = self.controller.review
        if (
            review is None
            or self.controller.step is not SetupStep.REVIEW
            or self.controller.review_state is not ReviewState.CURRENT
        ):
            self._show_status(
                "The review is no longer current. Go back and build it again."
            )
            return
        config = review.plan.config
        configure_mode = self._mode == "configure"
        save_prompt = (
            ("Update this configuration file?\n\n" if configure_mode else "Create this configuration file?\n\n")
            + f"Profile: {config.profile}\n"
            + f"Config file: {self._save_port.config_path}\n"
            + f"Output root: {config.output_root}\n"
            + f"Courses: {review.course_count}\n"
            + f"Sources: {review.source_count}\n\n"
            + (
                "The configuration will be updated and any missing empty course folders "
                "will be created. Existing files and sync history will not be moved or deleted."
                if configure_mode
                else "The configuration file and any missing empty course folders will be "
                "created. No course content will be downloaded, and sync state, credentials, "
                "or launchers will not be written."
            )
        )
        answer = QMessageBox.question(
            self,
            "Save configuration changes?" if configure_mode else "Save configuration?",
            save_prompt,
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Save:
            return
        self._save_in_progress = True
        self.continue_button.setEnabled(False)
        self.continue_button.setText("Saving…")
        self.back_button.setEnabled(False)
        self._show_status("Saving the reviewed configuration…", status="working")
        task = _ConfigSaveTask(self._save_port, review)
        task.signals.finished.connect(self._config_save_finished)
        self._save_task = task
        self._thread_pool.start(task)

    @Slot(object)
    def _config_save_finished(self, value: object) -> None:
        self._save_in_progress = False
        self._save_task = None
        if not isinstance(value, SetupCommitResult):
            self.continue_button.setEnabled(True)
            self.continue_button.setText("Save configuration")
            self.back_button.setEnabled(True)
            self._show_status("Configuration could not be saved safely.")
            return
        if value.status in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}:
            self._configuration_saved = True
            self._configuration_save_warning = value.status is OperationStatus.PARTIAL
            self.back_button.setEnabled(False)
            self.navigation_bar.hide()
            self.review_notice.setSummary(
                "Setup saved with a warning"
                if value.status is OperationStatus.PARTIAL
                else "Setup complete"
            )
            self.review_notice.setTone(
                "warning" if value.status is OperationStatus.PARTIAL else "success"
            )
            self.review_notice.setText(
                (
                    "Configuration changes saved and course folders are ready. Existing files "
                    "and sync history were not moved or deleted."
                    if self._mode == "configure"
                    else "Configuration saved and course folders are ready. No course content "
                    "was downloaded. You can open the dashboard or install CLI shortcuts."
                )
                + (
                    " Configuration cleanup is still pending."
                    if self._configuration_save_warning
                    else ""
                )
            )
            self.open_daily_menu_button.show()
            if self._mode == "setup":
                self.install_launchers_button.setText("Install CLI shortcuts")
                self.install_launchers_button.show()
            if value.status is OperationStatus.PARTIAL:
                self._show_status(
                    "Configuration saved, but temporary-file cleanup is still pending.",
                    status="warning",
                )
            else:
                self._show_status(
                    f"Configuration saved to {value.config_path}",
                    status="success",
                )
            return
        self.continue_button.setEnabled(
            self.controller.step is SetupStep.REVIEW
            and self.controller.review_state is ReviewState.CURRENT
        )
        self.continue_button.setText("Save configuration")
        self.back_button.setEnabled(True)
        issue_code = value.issues[0].code if value.issues else "config_write_failed"
        message = {
            "config_exists": (
                "A configuration already exists at this location. Nothing was overwritten."
            ),
            "review_required": (
                "The review is no longer current. Go back and build it again."
            ),
            "config_revision_failed": (
                "The configuration location could not be checked safely."
            ),
            "config_write_failed": "The configuration file could not be written safely.",
            "invalid_config_target": "The configuration location is invalid.",
            "save_already_completed": "This configuration has already been saved.",
            "save_in_progress": "The configuration is already being saved.",
            "config_changed": (
                "The configuration changed after this preview. Go back and build the preview again."
            ),
            "mapping_became_locked": (
                "Sync history or local files changed after this preview, so the mapping is now locked."
            ),
            "invalid_output_root": (
                "The Library root is unavailable or unsafe. Go back to Folders and choose an existing folder."
            ),
            "course_folder_target_unsafe": (
                "A course folder is no longer safely inside the Library root. Go back to Folders and choose it again."
            ),
            "course_folder_conflict": (
                "A course folder name is already used by a file or link. Go back to Folders and choose another folder name."
            ),
            "course_folder_create_failed": (
                "The course folders could not be created. Check access to the Library root and try again."
            ),
            "course_folder_cleanup_pending": (
                "Setup could not finish, and an empty folder may need manual cleanup before retrying."
            ),
        }.get(issue_code, "Configuration could not be saved safely.")
        self._show_status(message)

    def _install_daily_launchers(self) -> None:
        if not self._configuration_saved or self._save_port is None:
            return
        review = self.controller.review
        if review is None:
            self._show_status("The saved configuration is no longer available in this window.")
            return
        config = review.plan.config
        directory = (
            config.daily_use.launchers.directory
            or config.output_root / "Canvas Ed MCP Launchers"
        )
        try:
            launcher_arguments = {
                "profile": config.profile,
                "config_path": self._save_port.config_path.resolve(),
                "platform": sys.platform,
            }
            if bool(getattr(sys, "frozen", False)):
                gui_executable, cli_executable = frozen_companion_executables()
                rendered = render_launchers(
                    **launcher_arguments,
                    gui_executable=gui_executable,
                    cli_executable=cli_executable,
                )
            else:
                rendered = render_launchers(
                    **launcher_arguments,
                    python_executable=Path(sys.executable),
                )
            result = install_launchers(directory, rendered)
        except (OSError, ValueError):
            self._show_status(
                "The daily-use shortcuts could not be installed. Your configuration "
                "is still saved safely."
                + (
                    " Configuration cleanup is also still pending."
                    if self._configuration_save_warning
                    else ""
                ),
                status="warning",
            )
            return
        suffix = ".cmd" if sys.platform == "win32" else ".command" if sys.platform == "darwin" else ".sh"
        daily_launcher_name = f"canvas_ed_app{suffix}"
        daily_launcher_ready = daily_launcher_name in {
            *result.created,
            *result.reused,
        }
        self._launcher_directory = directory
        self._daily_menu_launcher = (
            directory / daily_launcher_name if daily_launcher_ready else None
        )
        self.install_launchers_button.hide()
        self.open_launchers_button.show()
        changed = len(result.created)
        reused = len(result.reused)
        if not daily_launcher_ready:
            self.review_notice.setTone("warning")
            self.review_notice.setText(
                "Setup is complete, but an edited daily-dashboard shortcut was preserved. "
                "Open the shortcuts folder to review the generated replacement."
                + (
                    " Configuration cleanup is also still pending."
                    if self._configuration_save_warning
                    else ""
                )
            )
            self._show_status(
                "The edited shortcut was preserved and was not opened. Review the generated "
                "replacement in the shortcuts folder.",
                status="warning",
            )
            return
        self.review_notice.setText(
            "Setup is complete. Open the daily dashboard to preview and confirm the first "
            "course sync, or open the shortcuts folder for direct actions."
            + (
                " Configuration cleanup is still pending."
                if self._configuration_save_warning
                else ""
            )
        )
        self.review_notice.setTone(
            "warning" if self._configuration_save_warning else "success"
        )
        if self._configuration_save_warning:
            self._show_status(
                "Daily-use shortcuts are ready, but configuration cleanup is still pending.",
                status="warning",
            )
        else:
            self._show_status(
                f"Daily-use shortcuts are ready in {directory} "
                f"({changed} created, {reused} already current).",
                status="success",
            )

    def _open_launchers_folder(self) -> None:
        if self._launcher_directory is None:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._launcher_directory))):
            self._show_status("The shortcuts folder could not be opened automatically.")

    def _open_daily_menu(self) -> None:
        if not self._configuration_saved or self._save_port is None:
            return
        review = self.controller.review
        if review is None:
            return
        if self._dashboard_opener is not None:
            self._allow_close = True
            try:
                opened = bool(
                    self._dashboard_opener(
                        review.plan.config.profile,
                        self._save_port.config_path.resolve(strict=False),
                    )
                )
            except Exception:
                opened = False
            if opened:
                self.close()
                return
            self._allow_close = False
            self._show_status(
                "The daily dashboard could not be opened safely in this app session."
            )
            return
        program, arguments = detached_module_command(
            APP_SHELL_MODULE,
            (
                "--profile",
                review.plan.config.profile,
                "--config",
                str(self._save_port.config_path),
            ),
        )
        launched = QProcess.startDetached(program, list(arguments))
        started = launched[0] if isinstance(launched, tuple) else bool(launched)
        if not started:
            self._show_status(
                "The daily dashboard could not be opened automatically."
            )

    # ---- Controller boundary -----------------------------------------

    def _continue(self) -> None:
        if self.controller.step is SetupStep.REVIEW:
            self._request_config_save()
            return
        action_by_step: dict[SetupStep, Callable[[], object]] = {
            SetupStep.SELECT: self.controller.open_mapping,
            SetupStep.MAP: self.controller.open_folders,
            SetupStep.FOLDERS: self._build_review_from_folder_inputs,
        }
        action = action_by_step.get(self.controller.step)
        if action is not None:
            self._run_action(
                action,
                refresh_on_error=self.controller.step is not SetupStep.FOLDERS,
            )

    def _build_review_from_folder_inputs(self) -> object:
        folders = {
            logical_key: (value if (value := row.override_edit.text().strip()) else None)
            for logical_key, row in self.folder_rows.items()
        }
        self.controller.set_folder_overrides(folders)
        return self.controller.build_review()

    def _back(self) -> None:
        self._run_action(self.controller.back)

    def _run_action(
        self, action: Callable[[], _T], *, refresh_on_error: bool = True
    ) -> _T | None:
        try:
            result = action()
            self._refresh_all()
        except ValueError as exc:
            self._show_status(str(exc))
            if refresh_on_error:
                try:
                    self._refresh_all()
                except ValueError:
                    # Keep the original user-facing failure visible. Controller
                    # mutations validate transactionally before this boundary.
                    pass
            return None
        self._clear_status()
        return result

    def _show_status(self, message: str, *, status: str = "error") -> None:
        safe = message.strip() or "This change could not be applied."
        self.status_label.setText(safe)
        self.status_frame.setProperty("status", status)
        self.status_frame.show()
        self.status_frame.style().unpolish(self.status_frame)
        self.status_frame.style().polish(self.status_frame)

    def _clear_status(self) -> None:
        self.status_label.clear()
        self.status_frame.hide()

    def _refresh_all(self) -> None:
        step = self.controller.step
        if step is SetupStep.SELECT:
            self._refresh_select()
        elif step is SetupStep.MAP:
            self._refresh_map()
        elif step is SetupStep.FOLDERS:
            self._refresh_folders()
        else:
            self._refresh_review()
        self._refresh_navigation()

    def _refresh_navigation(self) -> None:
        step_index = _STEP_ORDER.index(self.controller.step)
        previous_index = self.page_stack.currentIndex()
        self.page_stack.setCurrentIndex(step_index)
        if previous_index != step_index:
            self._focus_timer.start(0)
        self.back_button.setEnabled(step_index > 0)
        continue_enabled = self.controller.step is not SetupStep.REVIEW
        if self.controller.step is SetupStep.SELECT:
            continue_enabled = (
                self._mode == "configure" or self.controller.selected_count >= 1
            )
        elif self.controller.step is SetupStep.MAP:
            continue_enabled = not self.controller.pending_matches
        elif self.controller.step is SetupStep.REVIEW:
            continue_enabled = self._save_port is not None and not self._configuration_saved
        self.continue_button.setEnabled(continue_enabled)
        self.continue_button.setText(
            {
                SetupStep.SELECT: (
                    "Continue with no active courses"
                    if self._mode == "configure" and self.controller.selected_count == 0
                    else "Continue with 1 selected course"
                    if self.controller.selected_count == 1
                    else "Review mapping suggestions "
                    f"({self.controller.selected_count} selected)"
                ),
                SetupStep.MAP: (
                    f"Resolve {len(self.controller.pending_matches)} pending suggestion(s)"
                    if self.controller.pending_matches
                    else "Continue to folders"
                ),
                SetupStep.FOLDERS: "Build exact preview",
                SetupStep.REVIEW: (
                    "Save configuration"
                    if self._save_port is not None
                    else "Preview only"
                ),
            }[self.controller.step]
        )
        for index, (number, label) in enumerate(
            zip(self.step_numbers, self.step_labels)
        ):
            state = (
                "complete"
                if index < step_index
                else "active"
                if index == step_index
                else "upcoming"
            )
            for widget in (number, label):
                widget.setProperty("stepState", state)
                widget.style().unpolish(widget)
                widget.style().polish(widget)

    def _focus_current_step(self) -> None:
        """Move focus into a newly opened page without disrupting same-page edits."""

        if not self.isVisible():
            return
        if self.controller.step is SetupStep.SELECT:
            target: QWidget = self.search_edit
        elif self.controller.step is SetupStep.MAP:
            target = self.pending_list
        elif self.controller.step is SetupStep.FOLDERS:
            targets = (
                self.canvas_pages_checkbox,
                self.canvas_files_checkbox,
                self.ed_lessons_checkbox,
                self.ed_resources_checkbox,
                self.max_file_mib_spin,
                self.folder_table,
            )
            target = next(
                (widget for widget in targets if widget.isEnabled()),
                self.folder_table,
            )
        else:
            target = self.review_preview
        if target.isVisible() and target.isEnabled():
            target.setFocus(Qt.FocusReason.TabFocusReason)

    # ---- Layout and visual system ------------------------------------

    @staticmethod
    def _page_shell(title_text: str, description: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setAccessibleName(title_text)
        page.setAccessibleDescription(description)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)
        title = QLabel(title_text)
        title.setObjectName("page_title")
        copy = QLabel(description)
        copy.setObjectName("page_copy")
        copy.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(copy)
        return page, layout

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._focus_timer.start(0)
        if self._centered:
            return
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            frame = self.frameGeometry()
            frame.moveCenter(screen.availableGeometry().center())
            self.move(frame.topLeft())
        self._centered = True

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            self._focus_timer.stop()
            event.accept()
            self._emit_window_closed()
            return
        if self._save_in_progress:
            self._show_status(
                "Wait for the configuration save to finish before closing.",
                status="working",
            )
            event.ignore()
            return
        if not self.isVisible():
            self._focus_timer.stop()
            event.accept()
            self._emit_window_closed()
            return
        saved = self._configuration_saved
        answer = QMessageBox.question(
            self,
            "Close setup?",
            (
                "Your configuration is saved. Close this window?"
                if saved
                else "Your current selections and mapping decisions may not be saved. "
                "Close this window?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._focus_timer.stop()
            event.accept()
            self._emit_window_closed()
        else:
            event.ignore()

    def _emit_window_closed(self) -> None:
        if self._close_notified:
            return
        self._close_notified = True
        self.windowClosed.emit(self._configuration_saved)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #F6F8FB; color: #172033; }
            QWidget { font-family: "Segoe UI Variable", "Segoe UI"; font-size: 10pt; }
            QLabel#window_title { font-size: 18pt; font-weight: 650; color: #172033; }
            QLabel#window_subtitle, QLabel#page_copy { color: #5C667A; }
            QLabel#page_title { font-size: 13pt; font-weight: 650; color: #172033; }
            QFrame#step_rail { background: #FFFFFF; border: 1px solid #DDE4F0; border-radius: 8px; }
            QLabel#step_number { min-width: 22px; min-height: 22px; max-width: 22px; max-height: 22px; border-radius: 11px; font-weight: 700; }
            QLabel#step_number[stepState="active"] { background: #2F6FED; color: white; }
            QLabel#step_number[stepState="complete"] { background: #DCE8FF; color: #1E55B3; }
            QLabel#step_number[stepState="upcoming"] { background: #EDF1F7; color: #7A8497; }
            QLabel#step_label[stepState="active"] { color: #172033; font-weight: 650; }
            QLabel#step_label[stepState="complete"] { color: #2F6FED; }
            QLabel#step_label[stepState="upcoming"] { color: #7A8497; }
            QFrame#step_divider { color: #DDE4F0; }
            QLineEdit, QComboBox, QTableView, QTableWidget, QListWidget, QPlainTextEdit {
                background: #FFFFFF; border: 1px solid #C7D0DE; border-radius: 5px; selection-background-color: #DCE8FF; selection-color: #172033;
            }
            QLineEdit, QComboBox { min-height: 30px; padding: 2px 8px; }
            QLineEdit:focus, QComboBox:focus, QTableView:focus,
            QTableWidget:focus, QListWidget:focus, QPlainTextEdit:focus {
                border: 2px solid #2F6FED;
            }
            QCheckBox { spacing: 8px; }
            QCheckBox:focus {
                background: #EAF1FF; border: 2px solid #2F6FED;
                border-radius: 4px; padding: 2px;
            }
            QCheckBox::indicator {
                width: 18px; height: 18px; background: #FFFFFF;
                border: 2px solid #7D899D; border-radius: 4px;
            }
            QCheckBox::indicator:hover { border-color: #2F6FED; }
            QCheckBox::indicator:checked {
                background: #E7F0FF; border: 2px solid #2F6FED;
                image: url(:/qt-project.org/styles/commonstyle/images/standardbutton-apply-16.png);
            }
            QCheckBox:checked { font-weight: 650; }
            QHeaderView::section { background: #EDF1F7; color: #3C465A; padding: 5px; border: none; border-bottom: 1px solid #D5DDEA; font-weight: 650; }
            QTableView::item, QTableWidget::item, QListWidget::item { padding: 6px; }
            QGroupBox { background: #FFFFFF; border: 1px solid #DDE4F0; border-radius: 7px; margin-top: 10px; padding-top: 10px; font-weight: 650; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
            QPushButton { min-height: 30px; padding: 2px 14px; background: #FFFFFF; border: 1px solid #B8C3D3; border-radius: 5px; }
            QPushButton:hover { background: #EDF3FF; border-color: #7FA6ED; }
            QPushButton:focus { border: 2px solid #174EA6; background: #EDF3FF; }
            QPushButton:pressed { background: #DCE8FF; }
            QPushButton:disabled { color: #9AA3B2; background: #EEF1F5; border-color: #D8DEE8; }
            QPushButton#continue_button { background: #2F6FED; color: #FFFFFF; border-color: #2F6FED; font-weight: 650; }
            QPushButton#continue_button:hover { background: #285FCB; }
            QPushButton#install_launchers_button, QPushButton#open_daily_menu_button { background: #2F6FED; color: #FFFFFF; border-color: #2F6FED; font-weight: 650; }
            QPushButton#install_launchers_button:hover, QPushButton#open_daily_menu_button:hover { background: #285FCB; }
            QLabel#selected_count_label { color: #1E55B3; font-weight: 650; }
            QLabel#logical_key_label { font-weight: 700; color: #172033; padding-top: 2px; }
            QLineEdit#logical_key_edit { min-height: 36px; font-weight: 600; }
            QLabel#preview_badge { color: #5C667A; background: #E9EDF4; border-radius: 10px; padding: 3px 9px; }
            QFrame#review_notice { border-radius: 5px; }
            QFrame#review_notice[tone="warning"] { background: #FFF4CC; border: 1px solid #E8CF73; }
            QFrame#review_notice[tone="success"] { background: #E5F5EA; border: 1px solid #8BC79D; }
            QFrame#review_notice QLabel#notice_summary { color: #674D00; font-weight: 650; }
            QFrame#review_notice QLabel#notice_details { color: #674D00; }
            QFrame#review_notice QPushButton#notice_toggle_button { min-height: 24px; padding: 1px 8px; color: #674D00; background: transparent; border: none; font-weight: 650; }
            QFrame#review_notice[tone="success"] QLabel#notice_summary,
            QFrame#review_notice[tone="success"] QLabel#notice_details,
            QFrame#review_notice[tone="success"] QPushButton#notice_toggle_button { color: #155724; }
            QFrame#status_frame { border-radius: 5px; }
            QFrame#status_frame[status="error"] { background: #FDE8E7; border: 1px solid #E8AAA6; }
            QFrame#status_frame[status="success"] { background: #E5F5EA; border: 1px solid #8BC79D; }
            QFrame#status_frame[status="warning"] { background: #FFF4CC; border: 1px solid #E8CF73; }
            QFrame#status_frame[status="working"] { background: #E7F0FF; border: 1px solid #9EBBEA; }
            QLabel#status_label { color: #263B63; }
            QLabel#library_root_hint, QLabel#content_defaults_hint, QLabel#per_course_initial_hint, QLabel#logical_key_hint { font-size: 9pt; color: #5C667A; }
            QLabel#default_folder_label { font-family: "Cascadia Mono", Consolas; color: #263B63; padding: 1px; }
            QWidget#initial_mode_cell, QWidget#folder_name_cell { background: transparent; }
            """
        )


__all__ = ["FolderRowWidgets", "SetupWindow"]
