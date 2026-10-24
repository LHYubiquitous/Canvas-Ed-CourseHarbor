from __future__ import annotations

import ctypes
import os
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
            output_root=Path("C:/CanvasEdCourses"),
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

    def tearDown(self) -> None:
        # Avoid a modal discard prompt masking the original assertion when a
        # visible draft test fails before it reaches its normal hide step.
        self.window.hide()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()
        del self.window

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
        self.assertGreaterEqual(self.window.pending_list.minimumHeight(), 180)
        self.assertEqual(0, self.window.linked_list.count())
        self.assertEqual(0, self.window.separate_list.count())
        self.assertEqual("COMP2017-2026-S1", self.window.logical_key_edit.text())
        self.assertTrue(self.window.logical_key_label.font().bold())
        self.assertGreaterEqual(self.window.logical_key_label.font().pointSize(), 12)
        self.assertEqual("Treat as the same course", self.window.map_link_button.text())
        self.assertEqual(
            "Keep as separate courses", self.window.map_separate_button.text()
        )
        self.assertFalse(self.window.continue_button.isEnabled())

        self.window.resize(1040, 720)
        self.window.show()
        self.application.processEvents()
        pending_bottom = self.window.pending_list.mapTo(
            self.window, self.window.pending_list.rect().bottomLeft()
        ).y()
        logical_key_top = self.window.logical_key_edit.mapTo(
            self.window, self.window.logical_key_edit.rect().topLeft()
        ).y()
        self.assertGreater(logical_key_top, pending_bottom)
        self.assertGreater(
            self.window.map_scroll.verticalScrollBar().maximum(), 0
        )
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

    def test_exit_setup_confirms_before_discarding_a_visible_draft(self) -> None:
        self.window.show()
        self.application.processEvents()
        self.window.set_course_checked(self.canvas.key, True)

        with patch(
            "canvas_ed_mcp.ui.setup_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.window.exit_button.click()
        self.assertTrue(self.window.isVisible())

        with patch(
            "canvas_ed_mcp.ui.setup_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.window.exit_button.click()
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
        self.assertGreaterEqual(self.window.folder_table.rowHeight(0), 70)
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
        self.assertFalse(self.window.continue_button.isEnabled())
        self.assertIsNone(self.window.findChild(QPushButton, "commit_button"))

    def test_injected_config_only_save_requires_confirmation_and_writes_exact_plan(self) -> None:
        self.window.close()
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
            self.assertIsNotNone(self.window.findChild(QPushButton, "commit_button"))

            with patch(
                "canvas_ed_mcp.ui.setup_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Cancel,
            ), patch.object(saver, "save", wraps=saver.save) as save:
                self.window.commit_button.click()
            self.assertEqual(0, save.call_count)
            self.assertFalse(config_path.exists())

            with patch(
                "canvas_ed_mcp.ui.setup_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Save,
            ):
                self.window.commit_button.click()
            for _ in range(200):
                self.application.processEvents()
                if self.window.configuration_saved:
                    break
                QTest.qWait(5)

            self.assertTrue(self.window.configuration_saved)
            self.assertEqual(review.plan.config, load_config(config_path))  # type: ignore[union-attr]
            self.assertEqual("Close", self.window.exit_button.text())
            self.assertFalse(self.window.commit_button.isEnabled())
            self.assertFalse(self.window.install_launchers_button.isHidden())
            self.assertTrue(self.window.open_daily_menu_button.isHidden())

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
            self.assertIn("Setup is complete", self.window.review_notice.text())
            self.assertIn("Daily-use shortcuts are ready", self.window.status_label.text())

            with patch(
                "canvas_ed_mcp.ui.setup_window.QDesktopServices.openUrl",
                return_value=True,
            ) as open_url:
                self.window.open_daily_menu_button.click()
                self.window.open_launchers_button.click()
            self.assertEqual(2, open_url.call_count)

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
