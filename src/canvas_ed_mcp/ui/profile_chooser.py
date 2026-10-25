"""Profile selection surface for the CourseHarbor desktop application."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from canvas_ed_mcp.application.app_startup import StartupPlan, StartupProfile
from canvas_ed_mcp.application.contracts import OperationStatus
from canvas_ed_mcp.application.profile_lifecycle import ProfileLifecycleService
from canvas_ed_mcp.paths import validate_profile_name

from .branding import DISPLAY_NAME


class ProfileChooserWindow(QMainWindow):
    """Choose a ready local profile or begin a separate first-run setup."""

    profileSelected = Signal(object)
    setupRequested = Signal(str)
    profilesChanged = Signal()

    def __init__(
        self,
        plan: StartupPlan,
        *,
        profile_lifecycle: ProfileLifecycleService | None = None,
    ) -> None:
        super().__init__()
        self.plan = plan
        self._profiles = plan.profiles
        self._profile_lifecycle = profile_lifecycle or ProfileLifecycleService()
        self.setWindowTitle(f"{DISPLAY_NAME} — Profiles")
        self.setMinimumSize(720, 460)
        self.resize(820, 520)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        title = QLabel("Choose a CourseHarbor profile")
        title.setObjectName("chooser_title")
        subtitle = QLabel(
            "Profiles keep course selections, local destinations, state, and credential "
            "references separate. No provider connection is made on this page."
        )
        subtitle.setObjectName("chooser_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.profile_tree = QTreeWidget()
        self.profile_tree.setHeaderLabels(
            ["Profile", "Status", "Courses", "Sources", "Providers"]
        )
        self.profile_tree.setRootIsDecorated(False)
        self.profile_tree.setAlternatingRowColors(True)
        self.profile_tree.currentItemChanged.connect(self._selection_changed)
        self.profile_tree.itemDoubleClicked.connect(
            lambda _item, _column: self.open_selected()
        )
        for index, candidate in enumerate(self._profiles):
            overview = candidate.overview
            providers = " + ".join(
                provider.provider.title()
                for provider in overview.providers
                if provider.enabled
            ) or "None"
            status = "Ready" if candidate.ready else "Needs attention"
            item = QTreeWidgetItem(
                [
                    candidate.profile,
                    status,
                    str(overview.course_count),
                    str(overview.source_count),
                    providers,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            self.profile_tree.addTopLevelItem(item)
        self.profile_tree.header().setStretchLastSection(True)
        for column in range(4):
            self.profile_tree.resizeColumnToContents(column)
        layout.addWidget(self.profile_tree, 1)

        action_row = QHBoxLayout()
        self.remove_button = QPushButton("Remove profile…")
        self.remove_button.setObjectName("remove_profile_button")
        self.remove_button.clicked.connect(self.remove_selected)
        self.remove_button.setEnabled(False)
        self.open_button = QPushButton("Open selected profile")
        self.open_button.setObjectName("primary_button")
        self.open_button.clicked.connect(self.open_selected)
        self.open_button.setEnabled(False)
        action_row.addWidget(self.remove_button)
        action_row.addStretch(1)
        action_row.addWidget(self.open_button)
        layout.addLayout(action_row)

        new_card = QFrame()
        new_card.setObjectName("new_profile_card")
        new_layout = QHBoxLayout(new_card)
        new_layout.setContentsMargins(12, 10, 12, 10)
        new_label = QLabel("New profile")
        self.new_profile_edit = QLineEdit()
        self.new_profile_edit.setPlaceholderText("e.g. semester-2")
        self.new_profile_edit.setAccessibleName("New profile name")
        self.setup_button = QPushButton("Set up new profile")
        self.setup_button.clicked.connect(self.start_setup)
        new_layout.addWidget(new_label)
        new_layout.addWidget(self.new_profile_edit, 1)
        new_layout.addWidget(self.setup_button)
        layout.addWidget(new_card)

        self.message = QLabel("")
        self.message.setObjectName("chooser_message")
        self.message.setWordWrap(True)
        self.message.hide()
        layout.addWidget(self.message)
        self.setCentralWidget(root)
        self._apply_style()

        if self.profile_tree.topLevelItemCount():
            self.profile_tree.setCurrentItem(self.profile_tree.topLevelItem(0))

    def _selected_profile(self) -> StartupProfile | None:
        item = self.profile_tree.currentItem()
        if item is None:
            return None
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or not 0 <= index < len(self._profiles):
            return None
        return self._profiles[index]

    def _selection_changed(self) -> None:
        selected = self._selected_profile()
        ready = selected is not None and selected.ready
        self.open_button.setEnabled(ready)
        self.remove_button.setEnabled(selected is not None)
        if selected is None or ready:
            self.message.hide()
            return
        details = ", ".join(selected.issue_codes) or "local configuration error"
        self.message.setText(
            f"This profile cannot be opened until its local configuration is repaired "
            f"({details}). Existing files will not be overwritten."
        )
        self.message.show()

    def open_selected(self) -> None:
        selected = self._selected_profile()
        if selected is None or not selected.ready:
            self._selection_changed()
            return
        self.profileSelected.emit(selected.overview)

    def remove_selected(self) -> None:
        selected = self._selected_profile()
        if selected is None:
            return
        reviewed = self._profile_lifecycle.review_removal(
            selected.profile,
            expected_config_path=selected.overview.config_path,
        )
        if reviewed.status is not OperationStatus.SUCCEEDED or reviewed.review is None:
            code = reviewed.issues[0].code if reviewed.issues else "profile_removal_unavailable"
            self.message.setText(
                f"This profile could not be prepared for safe removal ({code}). Nothing changed."
            )
            self.message.show()
            return
        answer = QMessageBox.question(
            self,
            "Remove CourseHarbor profile?",
            f"Remove profile '{selected.profile}' from CourseHarbor?\n\n"
            f"Config: {reviewed.review.config_path}\n\n"
            "The config will move to a recoverable local archive. Downloaded course "
            "files, native credentials, sync state, logs, cache, and remote tokens "
            "will be retained.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        result = self._profile_lifecycle.commit_removal(reviewed.review)
        if result.status is OperationStatus.SUCCEEDED:
            self.profilesChanged.emit()
            return
        code = result.issues[0].code if result.issues else "profile_archive_failed"
        self.message.setText(
            f"The profile was not removed ({code}). Its configuration is unchanged."
        )
        self.message.show()

    def start_setup(self) -> None:
        profile = self.new_profile_edit.text().strip()
        try:
            profile = validate_profile_name(profile)
        except ValueError:
            self.message.setText(
                "Use 1–64 letters, numbers, dots, underscores, or hyphens for the profile name."
            )
            self.message.show()
            return
        if any(candidate.profile == profile for candidate in self._profiles):
            self.message.setText(
                "That profile already exists. Select it above; CourseHarbor will not overwrite it."
            )
            self.message.show()
            return
        self.message.hide()
        self.setupRequested.emit(profile)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #F6F8FB; color: #172033; font-family: "Segoe UI Variable", "Segoe UI"; font-size: 10pt; }
            QLabel#chooser_title { font-size: 20pt; font-weight: 700; color: #172033; }
            QLabel#chooser_subtitle { color: #5F6C80; }
            QTreeWidget, QLineEdit { background: #FFFFFF; border: 1px solid #C7D2E2; border-radius: 5px; }
            QTreeWidget::item { min-height: 30px; }
            QHeaderView::section { background: #EDF2F8; color: #384961; padding: 8px; border: none; border-bottom: 1px solid #D9E1EC; font-weight: 650; }
            QFrame#new_profile_card { background: #FFFFFF; border: 1px solid #D9E1EC; border-radius: 6px; }
            QLineEdit { padding: 7px; }
            QPushButton { min-height: 32px; padding: 3px 14px; background: #FFFFFF; border: 1px solid #B9C5D5; border-radius: 5px; }
            QPushButton:hover { background: #EDF3FF; border-color: #7D9FE7; }
            QPushButton#primary_button { background: #2F6FED; color: #FFFFFF; border-color: #2F6FED; font-weight: 700; }
            QPushButton#remove_profile_button { color: #8A2E2A; border-color: #D8A6A2; }
            QPushButton#remove_profile_button:hover { background: #FDECEB; border-color: #C97872; }
            QPushButton:disabled { color: #99A3B2; background: #EEF1F5; border-color: #D8DEE8; }
            QLabel#chooser_message { color: #7A1E1E; background: #FDE8E7; border: 1px solid #E8AAA6; border-radius: 5px; padding: 8px; }
            """
        )


__all__ = ["ProfileChooserWindow"]
