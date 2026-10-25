from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QPushButton
except ImportError:  # pragma: no cover - PySide6 remains an optional local UI extra.
    Qt = None  # type: ignore[assignment]
    QCoreApplication = None  # type: ignore[assignment]
    QEvent = None  # type: ignore[assignment]
    QTest = None  # type: ignore[assignment]
    QApplication = None  # type: ignore[assignment]
    QMessageBox = None  # type: ignore[assignment]
    QPushButton = None  # type: ignore[assignment]
    QLineEdit = None  # type: ignore[assignment]

from canvas_ed_mcp.application.setup_controller import SetupController, SetupStep
from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
)
from canvas_ed_mcp.application.profiles import SetupCommitResult
from canvas_ed_mcp.config import CanvasPlatformConfig, EdPlatformConfig, load_config
from canvas_ed_mcp.initial import InitialSyncMode
from canvas_ed_mcp.launchers import LauncherInstallResult
from canvas_ed_mcp.providers.base import CourseCandidate
from canvas_ed_mcp.ui import build_config_saving_setup_window


def _windows_gui_available() -> bool:
    if os.name != "nt" or QApplication is None:
        return False
    try:
        user32 = ctypes.windll.user32
        return bool(user32.GetProcessWindowStation() and user32.GetDesktopWindow())
    except (AttributeError, OSError):
        return False


def _course(
    provider: str,
    course_id: str,
    code: str,
    name: str,
    term: str,
) -> CourseCandidate:
    return CourseCandidate(
        provider=provider,
        instance="canvas.example.edu" if provider == "canvas" else "edstem.org",
        course_id=course_id,
        code=code,
        name=name,
        term=term,
    )


@unittest.skipUnless(_windows_gui_available(), "PySide6 Windows GUI is NOT_RUN")
class SetupWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from canvas_ed_mcp.ui.setup_window import SetupWindow

        cls.SetupWindow = SetupWindow
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.output_root = (Path(self.temporary.name) / "Courses").resolve()
        self.output_root.mkdir()
        self.canvas = _course(
            "canvas", "c-1", "COMP2017", "Systems Programming", "2026-S1"
        )
        self.ed = _course(
            "ed", "e-1", "COMP2017", "Systems Programming", "2026-S1"
        )
        self.info = _course(
            "canvas", "c-2", "INFO1110", "Introduction to Programming", "2025-S2"
        )
        self.data = _course(
            "ed", "e-2", "DATA2001", "Data Science", "2025-S2"
        )
        self.controller = SetupController(
            profile="ui-test",
            output_root=self.output_root,
            catalogs={
                "canvas": (self.canvas, self.info),
                "ed": (self.ed, self.data),
            },
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_CANVAS_TOKEN",
            ),
            ed=EdPlatformConfig(
                enabled=True,
                credential="env:SYNTHETIC_ED_TOKEN",
            ),
        )
        self.window = self.SetupWindow(self.controller)

    def _dispose_window(self) -> None:
        # Avoid a modal discard prompt masking the original assertion when a
        # visible draft test fails before it reaches its normal hide step.
        window = self.window
        window.hide()
        window.close()
        window.deleteLater()
        del self.window
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

    def tearDown(self) -> None:
        self._dispose_window()
        self.temporary.cleanup()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.application.closeAllWindows()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.application.processEvents()

    def _select_pair(self) -> None:
        self.window.set_course_checked(self.canvas.key, True)
        self.window.set_course_checked(self.ed.key, True)

    def test_close_cancels_pending_focus_delivery(self) -> None:
        self.window.show()
        self.application.processEvents()
        self.window._focus_timer.start(60_000)
        self.assertTrue(self.window._focus_timer.isActive())

        with patch(
            "canvas_ed_mcp.ui.setup_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.close()

        self.assertFalse(self.window._focus_timer.isActive())

    def _open_folders_with_linked_pair(self) -> str:
        self._select_pair()
        self.window.continue_button.click()
        self.assertEqual(SetupStep.MAP, self.controller.step)
        self.window.pending_list.setCurrentRow(0)
        match_id = self.window.pending_list.currentItem().data(Qt.ItemDataRole.UserRole)
        self.window.map_link_button.click()
        logical_key = self.controller.linked_matches[0].suggested_logical_key
        self.window.continue_button.click()
        self.assertEqual(SetupStep.FOLDERS, self.controller.step)
        return logical_key

    def test_window_is_injected_not_auto_shown_and_uses_four_persistent_steps(self) -> None:
        self.assertIs(self.controller, self.window.controller)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(4, self.window.page_stack.count())
        self.assertEqual(
            ("Select", "Map", "Folders", "Review"),
            tuple(label.text() for label in self.window.step_labels),
        )
        self.assertEqual(SetupStep.SELECT, self.controller.step)
        self.assertEqual(0, self.window.page_stack.currentIndex())
        self.assertFalse(self.window.back_button.isEnabled())
        self.assertFalse(self.window.continue_button.isEnabled())
        self.assertIsNotNone(self.window.continue_button.graphicsEffect())

        self.window.show()
        self.application.processEvents()
        self.assertGreater(self.window.course_table.rowHeight(0), 0)
        screen_center = self.window.screen().availableGeometry().center()
        window_center = self.window.frameGeometry().center()
        self.assertLessEqual(abs(screen_center.x() - window_center.x()), 32)
        self.assertLessEqual(abs(screen_center.y() - window_center.y()), 32)

    def test_keyboard_focus_and_accessible_labels_survive_course_selection(self) -> None:
        self.assertIs(self.window.search_edit, self.window.search_label.buddy())
        self.assertIs(self.window.term_filter, self.window.term_label.buddy())
        self.assertIs(self.window.provider_filter, self.window.provider_label.buddy())
        self.assertEqual("Course catalogue", self.window.course_table.accessibleName())
        self.assertEqual(
            "Course mapping summary",
            self.window.mapping_summary_label.accessibleName(),
        )
        self.assertEqual(
            "Local course folder settings",
            self.window.folder_table.accessibleName(),
        )
        self.assertEqual("Exact setup preview", self.window.review_preview.accessibleName())
        self.assertIs(
            self.window.logical_key_edit, self.window.logical_key_label.buddy()
        )
        self.assertIs(
            self.window.max_file_mib_spin, self.window.max_file_label.buddy()
        )
        self.assertIs(
            self.window.manual_canvas_combo, self.window.manual_canvas_label.buddy()
        )
        self.assertIs(
            self.window.manual_ed_combo, self.window.manual_ed_label.buddy()
        )
        self.assertEqual(
            "Pending course match suggestions",
            self.window.pending_list.accessibleName(),
        )
        self.assertEqual("Linked courses", self.window.linked_list.accessibleName())
        self.assertEqual(
            "Courses kept separate", self.window.separate_list.accessibleName()
        )

        self.window.show()
        self.application.processEvents()
        self.window.course_table.setFocus()
        current = self.window.course_model.index(2, 0)
        self.window.course_table.setCurrentIndex(current)
        self.window.course_model.item(2, 0).setCheckState(Qt.CheckState.Checked)
        self.application.processEvents()

        self.assertIs(self.window.course_table, self.application.focusWidget())
        self.assertEqual(2, self.window.course_table.currentIndex().row())
        self.assertEqual(1, self.controller.selected_count)
        self.window.hide()

    def test_space_toggles_the_current_course_from_any_table_column(self) -> None:
        self.window.show()
        self.application.processEvents()
        self.window.course_table.setFocus()

        self.window.course_table.setCurrentIndex(self.window.course_model.index(0, 1))
        QTest.keyClick(self.window.course_table, Qt.Key.Key_Space)
        self.application.processEvents()
        self.assertEqual(1, self.controller.selected_count)
        self.assertEqual(0, self.window.course_table.currentIndex().column())

        self.window.course_table.setCurrentIndex(self.window.course_model.index(0, 3))
        QTest.keyClick(self.window.course_table, Qt.Key.Key_Space)
        self.application.processEvents()
        self.assertEqual(0, self.controller.selected_count)
        self.window.hide()

    def test_focus_moves_into_each_new_step_without_stealing_same_page_focus(self) -> None:
        self.window.show()
        self.application.processEvents()
        self.assertIs(self.window.search_edit, self.application.focusWidget())

        self._select_pair()
        self.window.continue_button.click()
        self.application.processEvents()
        self.assertEqual(SetupStep.MAP, self.controller.step)
        self.assertIs(self.window.pending_list, self.application.focusWidget())

        self.window.pending_list.setCurrentRow(0)
        self.window.map_link_button.click()
        self.application.processEvents()
        self.assertIs(self.window.pending_list, self.application.focusWidget())

        self.window.continue_button.click()
        self.application.processEvents()
        self.assertEqual(SetupStep.FOLDERS, self.controller.step)
        self.assertIn(
            self.application.focusWidget(),
            (
                self.window.canvas_pages_checkbox,
                self.window.canvas_files_checkbox,
                self.window.ed_lessons_checkbox,
                self.window.ed_resources_checkbox,
                self.window.max_file_mib_spin,
                self.window.folder_table,
            ),
        )

        self.window.continue_button.click()
        self.application.processEvents()
        self.assertEqual(SetupStep.REVIEW, self.controller.step)
        self.assertIs(self.window.review_preview, self.application.focusWidget())
        self.window.hide()

    def test_select_table_filters_visible_bulk_selection_and_retained_count(self) -> None:
        self.assertEqual(
            ("Code", "Course", "Term", "Provider"),
            tuple(
                self.window.course_model.horizontalHeaderItem(column).text()
                for column in range(4)
            ),
        )
        self.assertEqual(4, self.window.course_model.rowCount())
        self.assertTrue(self.window.course_model.item(0, 0).isCheckable())
        self.assertEqual(
            ("2026-S1", "2026-S1", "2025-S2", "2025-S2"),
            tuple(
                self.window.course_model.item(row, 2).text()
                for row in range(self.window.course_model.rowCount())
            ),
        )
        self.assertEqual(
            ("2026-S1", "2025-S2"),
            tuple(
                self.window.term_filter.itemData(index)
                for index in range(1, self.window.term_filter.count())
            ),
        )

        self.window.search_edit.setText("programming")
        self.assertEqual(3, self.window.course_model.rowCount())
        self.window.provider_filter.setCurrentIndex(
            self.window.provider_filter.findData("canvas")
        )
        self.assertEqual(2, self.window.course_model.rowCount())
        self.window.term_filter.setCurrentIndex(
            self.window.term_filter.findData("2025-S2")
        )
        self.assertEqual(1, self.window.course_model.rowCount())

        self.window.select_visible_button.click()
        self.assertEqual(1, self.controller.selected_count)
        self.assertEqual("Selected 1 course", self.window.selected_count_label.text())
        self.assertEqual("Clear visible", self.window.select_visible_button.text())
        self.window.select_visible_button.click()
        self.assertEqual(0, self.controller.selected_count)
        self.assertEqual("Select all visible", self.window.select_visible_button.text())
        self.window.select_visible_button.click()
        self.window.search_edit.setText("no result")
        self.assertEqual(0, self.window.course_model.rowCount())
        self.assertEqual(1, self.controller.selected_count)
        self.assertTrue(self.window.continue_button.isEnabled())

    def test_one_selected_course_can_reach_folders_without_mapping(self) -> None:
        self.window.set_course_checked(self.info.key, True)
        self.assertTrue(self.window.continue_button.isEnabled())
        self.assertEqual(
            "Continue with 1 selected course", self.window.continue_button.text()
        )

        self.window.continue_button.click()
        self.assertEqual(SetupStep.MAP, self.controller.step)
        self.assertEqual(0, self.window.pending_list.count())
        self.assertTrue(self.window.continue_button.isEnabled())
        self.window.continue_button.click()

        self.assertEqual(SetupStep.FOLDERS, self.controller.step)
        self.assertEqual(1, self.window.folder_table.rowCount())

    def test_map_has_pending_linked_separate_and_reversible_actions(self) -> None:
        self._select_pair()
        self.window.continue_button.click()
        self.assertEqual(1, self.window.pending_list.count())
        self.assertIn(
            "1 automatic suggestion", self.window.mapping_summary_label.text()
        )
        self.assertGreaterEqual(
            self.window.pending_list.minimumHeight(),
            self.window.pending_list.fontMetrics().lineSpacing() * 5,
        )
        self.assertEqual(0, self.window.linked_list.count())
        self.assertEqual(0, self.window.separate_list.count())
        self.assertEqual("COMP2017-2026-S1", self.window.logical_key_edit.text())
        self.assertTrue(self.window.logical_key_label.font().bold())
        self.assertLessEqual(
            self.window.logical_key_label.font().pointSizeF(),
            self.application.font().pointSizeF() + 1.0,
        )
        self.assertEqual("Link courses", self.window.map_link_button.text())
        self.assertEqual("Keep separate", self.window.map_separate_button.text())
        self.assertEqual(
            "Treat selected courses as the same course",
            self.window.map_link_button.accessibleName(),
        )
        self.assertEqual(
            "Keep selected courses as separate courses",
            self.window.map_separate_button.accessibleName(),
        )
        self.assertFalse(self.window.continue_button.isEnabled())

        self.window.resize(1040, 710)
        self.window.show()
        self.application.processEvents()
        pending_bottom = self.window.pending_list.mapTo(
            self.window, self.window.pending_list.rect().bottomLeft()
        ).y()
        logical_key_top = self.window.logical_key_edit.mapTo(
            self.window, self.window.logical_key_edit.rect().topLeft()
        ).y()
        self.assertGreater(logical_key_top, pending_bottom)
        viewport = self.window.map_scroll.viewport()
        for button in (self.window.map_link_button, self.window.map_separate_button):
            button_bottom = button.mapTo(viewport, button.rect().bottomLeft()).y()
            self.assertLessEqual(button_bottom + 8, viewport.rect().bottom())
            self.assertGreaterEqual(button.width(), button.sizeHint().width())
        separate_right = self.window.separate_list.mapTo(
            self.window, self.window.separate_list.rect().bottomRight()
        ).x()
        viewport_right = self.window.map_scroll.viewport().mapTo(
            self.window, self.window.map_scroll.viewport().rect().bottomRight()
        ).x()
        self.assertLessEqual(separate_right, viewport_right)
        self.window.hide()

        self.window.pending_list.setCurrentRow(0)
        self.window.logical_key_edit.setText("COMP2017-custom")
        self.window.map_link_button.click()
        self.assertEqual(0, self.window.pending_list.count())
        self.assertEqual(1, self.window.linked_list.count())
        self.assertGreaterEqual(self.window.linked_undo_button.minimumWidth(), 150)
        self.assertEqual("COMP2017-custom", self.controller.logical_groups[0].logical_key)
        self.assertTrue(self.window.continue_button.isEnabled())
        self.assertTrue(self.window.manual_pair_group.isHidden())
        self.window.linked_list.setCurrentRow(0)
        self.window.linked_undo_button.click()
        self.assertEqual(1, self.window.pending_list.count())
        self.assertFalse(self.window.continue_button.isEnabled())

        self.window.pending_list.setCurrentRow(0)
        self.window.map_separate_button.click()
        self.assertEqual(1, self.window.separate_list.count())
        self.assertGreaterEqual(self.window.separate_undo_button.minimumWidth(), 180)
        self.assertTrue(self.window.continue_button.isEnabled())
        self.window.separate_list.setCurrentRow(0)
        self.window.separate_undo_button.click()
        self.assertEqual(1, self.window.pending_list.count())
        self.assertFalse(self.window.continue_button.isEnabled())

    def test_courses_without_a_suggestion_can_be_manually_paired(self) -> None:
        self.window.set_course_checked(self.info.key, True)
        self.window.set_course_checked(self.data.key, True)
        self.window.continue_button.click()

        self.assertEqual(SetupStep.MAP, self.controller.step)
        self.assertEqual(0, self.window.pending_list.count())
        self.assertEqual(1, self.window.manual_canvas_combo.count())
        self.assertEqual(1, self.window.manual_ed_combo.count())
        self.assertFalse(self.window.manual_pair_group.isHidden())
        self.window.resize(1040, 720)
        self.window.show()
        self.application.processEvents()
        self.assertEqual("Confirm manual pair", self.window.manual_pair_button.text())
        self.assertGreaterEqual(self.window.manual_pair_button.width(), 150)
        self.window.manual_pair_button.click()

        self.assertEqual(1, self.window.pending_list.count())
        self.assertIn("INFO1110", self.window.pending_list.item(0).text())
        self.assertIn("DATA2001", self.window.pending_list.item(0).text())
        self.assertTrue(self.window.manual_pair_group.isHidden())
        self.assertTrue(self.window.manual_remove_button.isEnabled())
        self.assertFalse(self.window.continue_button.isEnabled())

        self.window.manual_remove_button.click()
        self.assertEqual(0, self.window.pending_list.count())
        self.assertEqual(1, self.window.manual_canvas_combo.count())
        self.assertEqual(1, self.window.manual_ed_combo.count())
        self.assertTrue(self.window.continue_button.isEnabled())
        self.window.hide()

    def test_title_bar_close_confirms_before_discarding_a_visible_draft(self) -> None:
        self.window.show()
        self.application.processEvents()
        self.window.set_course_checked(self.canvas.key, True)

        with patch(
            "canvas_ed_mcp.ui.setup_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.window.close()
        self.assertTrue(self.window.isVisible())

        with patch(
            "canvas_ed_mcp.ui.setup_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.close()
        self.application.processEvents()
        self.assertFalse(self.window.isVisible())

    def test_folders_show_exact_default_and_allow_override_then_clear(self) -> None:
        logical_key = self._open_folders_with_linked_pair()
        self.window.resize(1264, 900)
        self.window.show()
        self.application.processEvents()
        row = self.window.folder_rows[logical_key]
        self.assertEqual(
            f"Folder name for {logical_key}", row.override_edit.accessibleName()
        )
        self.assertEqual("Optional", row.override_edit.placeholderText())
        self.assertIn("COMP2017", row.override_edit.accessibleDescription())
        self.assertEqual(
            f"Initial download for {logical_key}",
            row.initial_mode_combo.accessibleName(),
        )
        self.assertEqual("Default: COMP2017", row.default_label.text())
        self.assertEqual(str(self.output_root), self.window.library_root_edit.text())
        self.assertEqual("Library root", self.window.library_root_edit.accessibleName())
        self.assertTrue(self.window.choose_library_root_button.isEnabled())
        self.assertGreaterEqual(self.window.folder_table.rowHeight(0), 64)
        header = self.window.folder_table.horizontalHeader()
        self.assertEqual(190, header.sectionSize(0))
        self.assertGreaterEqual(header.sectionSize(1), 220)
        self.assertEqual(210, header.sectionSize(2))
        self.assertGreaterEqual(header.sectionSize(3), 260)
        self.assertEqual(4, self.window.folder_table.columnCount())
        self.assertEqual(
            1,
            len(
                [
                    edit
                    for edit in self.window.folder_table.findChildren(QLineEdit)
                    if edit.objectName().startswith("folder_override_")
                    and edit.isVisible()
                ]
            ),
        )
        self.assertFalse(
            any(
                button.text() in {"Apply", "Clear"}
                for button in self.window.folder_table.findChildren(QPushButton)
            )
        )
        self.window.hide()
        self.assertEqual(
            InitialSyncMode.BASELINE_FROM_NOW,
            row.initial_mode_combo.currentData(),
        )
        self.assertTrue(self.window.canvas_pages_checkbox.isChecked())
        self.assertTrue(self.window.canvas_files_checkbox.isChecked())
        self.assertTrue(self.window.ed_lessons_checkbox.isChecked())
        self.assertTrue(self.window.ed_resources_checkbox.isChecked())
        self.assertEqual(100, self.window.max_file_mib_spin.value())

        self.window.canvas_pages_checkbox.setChecked(False)
        self.window.ed_resources_checkbox.setChecked(False)
        self.window.max_file_mib_spin.setValue(64)
        self.assertFalse(self.controller.defaults.content.pages)
        self.assertFalse(self.controller.defaults.content.resources)
        self.assertEqual(
            64 * 1024 * 1024,
            self.controller.defaults.policy.max_file_bytes,
        )
        row = self.window.folder_rows[logical_key]

        row.initial_mode_combo.setCurrentIndex(
            row.initial_mode_combo.findData(InitialSyncMode.IMPORT_ALL)
        )
        self.assertTrue(
            all(
                source.initial_mode is InitialSyncMode.IMPORT_ALL
                for choice in self.controller.mapping_choices
                for source in choice.sources
            )
        )
        row = self.window.folder_rows[logical_key]

        row.override_edit.setText("COMP2017-local")
        self.window.continue_button.click()
        self.assertEqual(SetupStep.REVIEW, self.controller.step)
        self.assertEqual(
            "COMP2017-local", self.controller.mapping_choices[0].folder
        )
        self.window.back_button.click()
        row = self.window.folder_rows[logical_key]
        row.override_edit.clear()
        self.window.continue_button.click()
        self.assertEqual("COMP2017", self.controller.mapping_choices[0].folder)

    def test_folders_keep_two_course_rows_visible_at_scaled_desktop_height(self) -> None:
        self._select_pair()
        self.window.set_course_checked(self.info.key, True)
        self.window.set_course_checked(self.data.key, True)
        self.window.continue_button.click()
        self.window.pending_list.setCurrentRow(0)
        self.window.map_link_button.click()
        self.window.continue_button.click()
        self.assertGreaterEqual(self.window.folder_table.rowCount(), 2)

        self.window.resize(1040, 710)
        self.window.show()
        self.application.processEvents()

        second = self.window.folder_table.item(1, 0)
        self.assertIsNotNone(second)
        second_rect = self.window.folder_table.visualItemRect(second)
        self.assertLessEqual(
            second_rect.bottom(),
            self.window.folder_table.viewport().rect().bottom(),
        )
        self.assertGreaterEqual(
            self.window.folder_table.viewport().height(),
            2 * self.window.folder_table.rowHeight(0),
        )

    def test_course_folder_browse_uses_controller_validation_and_updates_draft(self) -> None:
        logical_key = self._open_folders_with_linked_pair()
        row = self.window.folder_rows[logical_key]
        selected = self.output_root / "COMP2017 Desktop"
        selected.mkdir()

        with patch(
            "canvas_ed_mcp.ui.setup_window.QFileDialog.getExistingDirectory",
            return_value=str(selected),
        ), patch.object(
            self.controller,
            "folder_name_for_selected_directory",
            return_value="COMP2017 Desktop",
        ) as validate:
            row.browse_button.click()

        validate.assert_called_once_with(selected)
        self.assertEqual("COMP2017 Desktop", row.override_edit.text())
        self.assertEqual(
            f"Choose target folder for {logical_key}",
            row.browse_button.accessibleName(),
        )

    def test_invalid_course_folder_can_recover_by_changing_library_root(self) -> None:
        logical_key = self._open_folders_with_linked_pair()
        other_root = (Path(self.temporary.name) / "Desktop").resolve()
        selected = other_root / "COMP2017"
        selected.mkdir(parents=True)
        row = self.window.folder_rows[logical_key]

        with patch(
            "canvas_ed_mcp.ui.setup_window.QFileDialog.getExistingDirectory",
            return_value=str(selected),
        ):
            row.browse_button.click()

        self.assertIn("directly inside", self.window.status_label.text())
        self.assertTrue(self.window.back_button.isEnabled())
        self.assertTrue(self.window.choose_library_root_button.isEnabled())

        with patch(
            "canvas_ed_mcp.ui.setup_window.QFileDialog.getExistingDirectory",
            return_value=str(other_root),
        ):
            self.window.choose_library_root_button.click()

        self.assertEqual(other_root, self.controller.output_root)
        self.assertEqual(str(other_root), self.window.library_root_edit.text())
        self.assertIn("Library root changed", self.window.status_label.text())
        row = self.window.folder_rows[logical_key]
        with patch(
            "canvas_ed_mcp.ui.setup_window.QFileDialog.getExistingDirectory",
            return_value=str(selected),
        ):
            row.browse_button.click()
        self.assertEqual("COMP2017", row.override_edit.text())
        self.assertEqual("", self.window.status_label.text())

        self.window.continue_button.click()
        self.assertEqual(SetupStep.REVIEW, self.controller.step)
        review = self.controller.review
        self.assertIsNotNone(review)
        self.assertEqual(other_root, review.plan.config.output_root)  # type: ignore[union-attr]

    def test_duplicate_folder_override_is_safe_and_keeps_the_first_value(self) -> None:
        self._select_pair()
        self.window.set_course_checked(self.info.key, True)
        self.window.set_course_checked(self.data.key, True)
        self.window.continue_button.click()
        self.window.pending_list.setCurrentRow(0)
        self.window.map_link_button.click()
        self.window.continue_button.click()

        first_row = self.window.folder_rows[self.info.key]
        second_row = self.window.folder_rows[self.data.key]
        first_row.initial_mode_combo.setCurrentIndex(
            first_row.initial_mode_combo.findData(InitialSyncMode.IMPORT_ALL)
        )
        choices = {
            choice.course_key: choice for choice in self.controller.mapping_choices
        }
        self.assertTrue(
            all(
                source.initial_mode is InitialSyncMode.IMPORT_ALL
                for source in choices[self.info.key].sources
            )
        )
        self.assertTrue(
            all(
                source.initial_mode is InitialSyncMode.BASELINE_FROM_NOW
                for source in choices[self.data.key].sources
            )
        )
        first_row = self.window.folder_rows[self.info.key]
        second_row = self.window.folder_rows[self.data.key]
        first_row.override_edit.setText("shared-folder")
        second_row.override_edit.setText("shared-folder")
        self.window.continue_button.click()

        folders = {
            choice.course_key: choice.folder
            for choice in self.controller.mapping_choices
        }
        self.assertEqual(SetupStep.FOLDERS, self.controller.step)
        self.assertNotEqual("shared-folder", folders[self.info.key])
        self.assertNotEqual("shared-folder", folders[self.data.key])
        self.assertIn("must be unique", self.window.status_label.text())
        self.assertEqual(
            "shared-folder",
            self.window.folder_rows[self.info.key].override_edit.text(),
        )
        self.assertEqual(
            "shared-folder",
            self.window.folder_rows[self.data.key].override_edit.text(),
        )

    def test_folder_draft_survives_other_folder_setting_changes(self) -> None:
        logical_key = self._open_folders_with_linked_pair()
        row = self.window.folder_rows[logical_key]
        row.override_edit.setText("COMP2017-draft")

        self.window.canvas_pages_checkbox.setChecked(False)
        row = self.window.folder_rows[logical_key]
        self.assertEqual("COMP2017-draft", row.override_edit.text())

        row.initial_mode_combo.setCurrentIndex(
            row.initial_mode_combo.findData(InitialSyncMode.IMPORT_ALL)
        )
        self.assertEqual(
            "COMP2017-draft",
            self.window.folder_rows[logical_key].override_edit.text(),
        )

    def test_checkboxes_have_an_explicit_visible_indicator_style(self) -> None:
        style = self.window.styleSheet()
        self.assertIn("QCheckBox::indicator", style)
        self.assertIn("QCheckBox::indicator:checked", style)
        self.assertIn("QPushButton:focus", style)
        self.assertIn("QTableView:focus", style)

    def test_review_is_exact_preview_only_and_has_no_commit_action(self) -> None:
        logical_key = self._open_folders_with_linked_pair()
        self.window.continue_button.click()

        self.assertEqual(SetupStep.REVIEW, self.controller.step)
        self.assertIn(logical_key, self.window.review_preview.toPlainText())
        self.assertIn("Preview only", self.window.review_notice.text())
        self.assertIn("nothing has been saved", self.window.review_notice.text())
        self.assertTrue(self.window.review_notice.details_label.isHidden())
        self.assertFalse(self.window.continue_button.isEnabled())
        self.assertIsNone(self.window.findChild(QPushButton, "commit_button"))

    def test_injected_config_only_save_requires_confirmation_and_writes_exact_plan(self) -> None:
        # Destroy the default fixture before replacing it. Leaving its Qt
        # wrapper for Python to collect while the replacement is active causes
        # intermittent 0xC0000005 teardown failures on Windows/Python 3.14.
        self._dispose_window()
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "profiles" / "ui-test.toml"
            self.window = build_config_saving_setup_window(
                self.controller,
                config_path,
            )
            saver = self.window._save_port
            self.assertIsNotNone(saver)
            logical_key = self._open_folders_with_linked_pair()
            self.window.continue_button.click()
            review = self.controller.review
            self.assertIsNotNone(review)
            self.assertIn(logical_key, self.window.review_preview.toPlainText())
            self.assertIsNone(self.window.findChild(QPushButton, "commit_button"))
            self.assertEqual("Save configuration", self.window.continue_button.text())

            with patch(
                "canvas_ed_mcp.ui.setup_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Cancel,
            ), patch.object(saver, "save", wraps=saver.save) as save:
                self.window.continue_button.click()
            self.assertEqual(0, save.call_count)
            self.assertFalse(config_path.exists())

            with patch(
                "canvas_ed_mcp.ui.setup_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Save,
            ):
                self.window.continue_button.click()
            for _ in range(200):
                self.application.processEvents()
                if self.window.configuration_saved:
                    break
                QTest.qWait(5)

            self.assertTrue(self.window.configuration_saved)
            self.assertEqual(review.plan.config, load_config(config_path))  # type: ignore[union-attr]
            self.assertTrue(self.window.navigation_bar.isHidden())
            self.assertEqual("success", self.window.review_notice.property("tone"))
            self.assertFalse(self.window.install_launchers_button.isHidden())
            self.assertFalse(self.window.open_daily_menu_button.isHidden())

            with patch(
                "canvas_ed_mcp.ui.setup_window.QProcess.startDetached",
                return_value=(True, 123),
            ) as start_dashboard:
                self.window.open_daily_menu_button.click()
            start_dashboard.assert_called_once_with(
                sys.executable,
                [
                    "-I",
                    "-m",
            "canvas_ed_mcp.ui.app_shell",
                    "--profile",
                    "ui-test",
                    "--config",
                    str(config_path.resolve()),
                ],
            )

            installed = LauncherInstallResult(
                created=("canvas_ed_app.cmd",), preserved=(), reused=()
            )
            with patch(
                "canvas_ed_mcp.ui.setup_window.install_launchers",
                return_value=installed,
            ) as install:
                self.window.install_launchers_button.click()

            install.assert_called_once()
            self.assertTrue(self.window.install_launchers_button.isHidden())
            self.assertFalse(self.window.open_daily_menu_button.isHidden())
            self.assertFalse(self.window.open_launchers_button.isHidden())
            self.assertEqual(
                "Open daily dashboard",
                self.window.open_daily_menu_button.text(),
            )
            self.assertIn("Setup is complete", self.window.review_notice.text())
            self.assertIn("Daily-use shortcuts are ready", self.window.status_label.text())

            with patch(
                "canvas_ed_mcp.ui.setup_window.QDesktopServices.openUrl",
                return_value=True,
            ) as open_url:
                self.window.open_launchers_button.click()
            self.assertEqual(1, open_url.call_count)

            self.window.install_launchers_button.show()
            preserved = LauncherInstallResult(
                created=("canvas_ed_app.cmd.new",),
                preserved=("canvas_ed_app.cmd",),
                reused=(),
            )
            with patch(
                "canvas_ed_mcp.ui.setup_window.install_launchers",
                return_value=preserved,
            ):
                self.window.install_launchers_button.click()

            self.assertFalse(self.window.open_daily_menu_button.isHidden())
            self.assertIsNone(self.window._daily_menu_launcher)
            self.assertIn("edited shortcut was preserved", self.window.status_label.text())

    def test_embedded_dashboard_opener_stays_in_process(self) -> None:
        self._dispose_window()
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "profiles" / "ui-test.toml"
            opened: list[tuple[str, Path]] = []

            def open_dashboard(profile: str, path: Path) -> bool:
                opened.append((profile, path))
                return True

            self.window = build_config_saving_setup_window(
                self.controller,
                config_path,
                dashboard_opener=open_dashboard,
            )
            self._open_folders_with_linked_pair()
            self.window.continue_button.click()
            self.window._config_save_finished(
                SetupCommitResult(OperationStatus.SUCCEEDED, config_path.resolve())
            )

            with patch(
                "canvas_ed_mcp.ui.setup_window.QProcess.startDetached"
            ) as detached:
                self.window.open_daily_menu_button.click()

            detached.assert_not_called()
            self.assertEqual(
                [("ui-test", config_path.resolve())],
                opened,
            )

    def test_setup_close_notifies_first_run_host_once(self) -> None:
        closed: list[bool] = []
        self.window.windowClosed.connect(closed.append)
        self.window.show()
        self.application.processEvents()

        with patch(
            "canvas_ed_mcp.ui.setup_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.close()
            self.application.processEvents()

        self.assertEqual([False], closed)

    def test_shortcut_success_does_not_hide_partial_save_cleanup_warning(self) -> None:
        self._dispose_window()
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "profiles" / "ui-test.toml"
            self.window = build_config_saving_setup_window(
                self.controller,
                config_path,
            )
            self._open_folders_with_linked_pair()
            self.window.continue_button.click()
            partial = SetupCommitResult(
                OperationStatus.PARTIAL,
                config_path,
                (
                    ApplicationIssue(
                        "config_cleanup_pending",
                        IssueSeverity.WARNING,
                    ),
                ),
            )

            self.window._config_save_finished(partial)
            installed = LauncherInstallResult(
                created=("canvas_ed_app.cmd",), preserved=(), reused=()
            )
            with patch(
                "canvas_ed_mcp.ui.setup_window.install_launchers",
                return_value=installed,
            ):
                self.window.install_launchers_button.click()

            self.assertEqual("warning", self.window.review_notice.property("tone"))
            self.assertIn("cleanup", self.window.review_notice.text().lower())
            self.assertIn("cleanup", self.window.status_label.text().lower())

    def test_shortcut_failure_does_not_hide_partial_save_cleanup_warning(self) -> None:
        self._dispose_window()
        with tempfile.TemporaryDirectory() as temporary:
            config_path = Path(temporary) / "profiles" / "ui-test.toml"
            self.window = build_config_saving_setup_window(
                self.controller,
                config_path,
            )
            self._open_folders_with_linked_pair()
            self.window.continue_button.click()
            self.window._config_save_finished(
                SetupCommitResult(
                    OperationStatus.PARTIAL,
                    config_path,
                    (
                        ApplicationIssue(
                            "config_cleanup_pending",
                            IssueSeverity.WARNING,
                        ),
                    ),
                )
            )

            with patch(
                "canvas_ed_mcp.ui.setup_window.install_launchers",
                side_effect=OSError("synthetic install failure"),
            ):
                self.window.install_launchers_button.click()

            self.assertEqual("warning", self.window.review_notice.property("tone"))
            self.assertIn("cleanup", self.window.review_notice.text().lower())
            self.assertIn("cleanup", self.window.status_label.text().lower())

    def test_value_errors_become_safe_status_messages(self) -> None:
        self.window.set_course_checked(self.canvas.key, True)
        self.assertEqual(SetupStep.SELECT, self.controller.step)
        self.assertTrue(self.window.continue_button.isEnabled())

        logical_key = self._open_folders_with_linked_pair()
        row = self.window.folder_rows[logical_key]
        row.override_edit.setText("bad/path")
        self.window.continue_button.click()
        self.assertIn("folder override", self.window.status_label.text())
        self.assertEqual(SetupStep.FOLDERS, self.controller.step)
        self.assertEqual("COMP2017", self.controller.mapping_choices[0].folder)


if __name__ == "__main__":
    unittest.main()
