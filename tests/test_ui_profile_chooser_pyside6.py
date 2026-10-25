from __future__ import annotations

import ctypes
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from PySide6.QtWidgets import QApplication, QMessageBox
except ImportError:  # pragma: no cover - optional desktop dependency
    QApplication = None  # type: ignore[assignment]
    QMessageBox = None  # type: ignore[assignment]

from canvas_ed_mcp.application.app_startup import (
    StartupDestination,
    StartupPlan,
    StartupProfile,
)
from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
)
from canvas_ed_mcp.application.profile_status import ProfileOverview
from canvas_ed_mcp.application.profile_lifecycle import ProfileLifecycleService
from canvas_ed_mcp.paths import ProfilePaths


def _windows_gui_available() -> bool:
    if os.name != "nt" or QApplication is None:
        return False
    try:
        return bool(ctypes.windll.user32.GetProcessWindowStation())
    except (AttributeError, OSError):
        return False


@unittest.skipUnless(_windows_gui_available(), "requires a normal Windows desktop session")
class ProfileChooserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _profile(self, name: str, status: OperationStatus) -> StartupProfile:
        issues = ()
        if status is OperationStatus.FAILED:
            issues = (
                ApplicationIssue("config_invalid", IssueSeverity.ERROR),
            )
        return StartupProfile(
            ProfileOverview(
                profile=name,
                status=status,
                config_path=self.root / f"{name}.toml",
                state_path=self.root / name / "state.json",
                output_root=self.root / "library" / name,
                course_count=3,
                source_count=5,
                issues=issues,
            )
        )

    def test_ready_profile_can_open_but_broken_profile_cannot(self) -> None:
        from canvas_ed_mcp.ui.profile_chooser import ProfileChooserWindow

        plan = StartupPlan(
            StartupDestination.PROFILE_CHOOSER,
            profiles=(
                self._profile("ready", OperationStatus.SUCCEEDED),
                self._profile("broken", OperationStatus.FAILED),
            ),
        )
        window = ProfileChooserWindow(plan)
        opened: list[str] = []
        window.profileSelected.connect(lambda value: opened.append(value.profile))
        try:
            window.profile_tree.setCurrentItem(window.profile_tree.topLevelItem(0))
            self.assertTrue(window.open_button.isEnabled())
            window.open_selected()
            self.assertEqual(["ready"], opened)

            window.profile_tree.setCurrentItem(window.profile_tree.topLevelItem(1))
            self.assertFalse(window.open_button.isEnabled())
            self.assertIn("cannot be opened", window.message.text())
        finally:
            window.close()

    def test_new_profile_name_is_validated_before_setup_signal(self) -> None:
        from canvas_ed_mcp.ui.profile_chooser import ProfileChooserWindow

        plan = StartupPlan(
            StartupDestination.PROFILE_CHOOSER,
            profiles=(self._profile("ready", OperationStatus.SUCCEEDED),),
        )
        window = ProfileChooserWindow(plan)
        requested: list[str] = []
        window.setupRequested.connect(requested.append)
        try:
            window.new_profile_edit.setText("not/a/profile")
            window.start_setup()
            self.assertEqual([], requested)
            self.assertIn("letters", window.message.text())

            window.new_profile_edit.setText("second-profile")
            window.start_setup()
            self.assertEqual(["second-profile"], requested)
        finally:
            window.close()

    def test_remove_profile_archives_config_and_refreshes_chooser(self) -> None:
        from canvas_ed_mcp.ui.profile_chooser import ProfileChooserWindow

        config_path = self.root / "config" / "profiles" / "ready.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("synthetic profile", encoding="utf-8")
        state_path = self.root / "state" / "ready" / "state-v4.json"
        state_path.parent.mkdir(parents=True)
        state_path.write_text("retained", encoding="utf-8")

        def paths(profile: str) -> ProfilePaths:
            return ProfilePaths(
                profile=profile,
                config_file=self.root / "config" / "profiles" / f"{profile}.toml",
                state_file=self.root / "state" / profile / "state-v4.json",
                state_backup_file=self.root / "state" / profile / "state-v4.backup.json",
                lock_dir=self.root / "state" / "locks",
                log_dir=self.root / "logs" / profile,
                cache_dir=self.root / "cache" / profile,
            )

        candidate = StartupProfile(
            ProfileOverview(
                profile="ready",
                status=OperationStatus.SUCCEEDED,
                config_path=config_path,
                state_path=state_path,
            )
        )
        window = ProfileChooserWindow(
            StartupPlan(
                StartupDestination.PROFILE_CHOOSER,
                profiles=(candidate,),
            ),
            profile_lifecycle=ProfileLifecycleService(paths_resolver=paths),
        )
        refreshed: list[bool] = []
        window.profilesChanged.connect(lambda: refreshed.append(True))
        try:
            window.profile_tree.setCurrentItem(window.profile_tree.topLevelItem(0))
            self.assertTrue(window.remove_button.isEnabled())
            with patch(
                "canvas_ed_mcp.ui.profile_chooser.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Cancel,
            ):
                window.remove_selected()
            self.assertTrue(config_path.exists())
            self.assertEqual([], refreshed)

            with patch(
                "canvas_ed_mcp.ui.profile_chooser.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                window.remove_selected()

            self.assertEqual([True], refreshed)
            self.assertFalse(config_path.exists())
            self.assertTrue(state_path.exists())
            self.assertEqual(1, len(tuple((config_path.parent / ".removed").glob("*.toml"))))
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
