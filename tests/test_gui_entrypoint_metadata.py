from __future__ import annotations

from pathlib import Path
import unittest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


class GuiEntrypointMetadataTests(unittest.TestCase):
    def test_gui_extra_and_desktop_commands_are_declared(self) -> None:
        root = Path(__file__).resolve().parents[1]
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn("PySide6>=6.8,<7", project["project"]["optional-dependencies"]["gui"])
        self.assertEqual(
            "canvas_ed_mcp.ui.app_shell:main",
            project["project"]["scripts"]["canvas-ed-app"],
        )
        self.assertEqual(
            "canvas_ed_mcp.ui.app_shell:main",
            project["project"]["gui-scripts"]["courseharbor"],
        )
        self.assertEqual(
            "canvas_ed_mcp.ui.first_run_window:main",
            project["project"]["scripts"]["canvas-ed-setup"],
        )
        self.assertEqual(
            "canvas_ed_mcp.cli:configure_gui_main",
            project["project"]["scripts"]["canvas-ed-configure"],
        )

    def test_first_run_credential_ui_uses_typed_masked_boundary(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "src/canvas_ed_mcp/ui/first_run_window.py").read_text(
            encoding="utf-8"
        )

        for required in (
            "CredentialController",
            "SecretValue",
            "QLineEdit.EchoMode.Password",
            'QPushButton("Save and test")',
            'QPushButton("Test existing")',
            "request_credential_shutdown",
        ):
            self.assertIn(required, source)
        for forbidden in (
            "store_secret",
            "keyring set",
            "QApplication.clipboard",
            "canvas.sydney.edu.au",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
