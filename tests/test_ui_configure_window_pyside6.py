from __future__ import annotations

import ctypes
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None  # type: ignore[assignment]

from canvas_ed_mcp.application.configure_controller import ConfigureController
from canvas_ed_mcp.application.configure_safety import SourceRelocationEligibility
from canvas_ed_mcp.application.setup_controller import SetupStep
from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseConfig,
    CourseSource,
    EdPlatformConfig,
    PlatformsConfig,
)
from canvas_ed_mcp.course_mapping import SourceIdentity
from canvas_ed_mcp.providers.base import CourseCandidate


def _windows_gui_available() -> bool:
    if os.name != "nt" or QApplication is None:
        return False
    try:
        return bool(ctypes.windll.user32.GetProcessWindowStation())
    except (AttributeError, OSError):
        return False


@unittest.skipUnless(_windows_gui_available(), "PySide6 Windows GUI is NOT_RUN")
class ConfigureWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from canvas_ed_mcp.ui.setup_window import SetupWindow

        cls.SetupWindow = SetupWindow
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = (Path(self.temporary.name) / "Courses").resolve()
        canvas = CourseCandidate(
            "canvas", "canvas.example.edu", "101", "COMP101", "Computing", "2026-S2"
        )
        ed = CourseCandidate(
            "ed", "edstem.org", "202", "COMP101", "Computing", "2026-S2"
        )
        sources = (
            CourseSource(provider="canvas", instance=canvas.instance, course_id=canvas.course_id, code=canvas.code),
            CourseSource(provider="ed", instance=ed.instance, course_id=ed.course_id, code=ed.code),
        )
        current = AppConfig(
            profile="default",
            output_root=root,
            platforms=PlatformsConfig(
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url="https://canvas.example.edu/api/v1",
                    credential="env:CANVAS_TOKEN",
                ),
                ed=EdPlatformConfig(enabled=True, credential="env:ED_TOKEN"),
            ),
            courses=(
                CourseConfig(
                    key="COMP101-2026-S2",
                    folder="COMP101",
                    confirmed_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
                    sources=sources,
                ),
            ),
        )
        eligibility = tuple(
            SourceRelocationEligibility(
                SourceIdentity.from_source(source),
                False,
                "This source has sync history, so changing its course mapping requires file migration.",
            )
            for source in sources
        )
        self.controller = ConfigureController(
            current=current,
            catalogs={"canvas": (canvas,), "ed": (ed,)},
            eligibility=eligibility,
        )
        self.window = self.SetupWindow(self.controller, mode="configure")

    def tearDown(self) -> None:
        self.window.hide()
        self.window.close()
        self.application.processEvents()
        self.temporary.cleanup()

    def test_existing_profile_is_preselected_and_non_destructive_edits_are_available(self) -> None:
        self.assertEqual("Reconfigure Canvas & Ed courses", self.window.windowTitle())
        self.assertEqual(2, self.controller.selected_count)

        self.window.continue_button.click()
        self.assertEqual(SetupStep.MAP, self.controller.step)
        self.assertEqual(1, self.window.linked_list.count())
        self.assertTrue(self.window.linked_undo_button.isEnabled())

        self.window.continue_button.click()
        self.assertEqual(SetupStep.FOLDERS, self.controller.step)
        row = self.window.folder_rows["COMP101-2026-S2"]
        self.assertFalse(row.override_edit.isEnabled())
        self.assertTrue(row.initial_mode_combo.isEnabled())
        self.assertEqual(1, self.window.course_settings_combo.count())


if __name__ == "__main__":
    unittest.main()
