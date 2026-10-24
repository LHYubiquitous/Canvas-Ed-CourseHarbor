from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from canvas_ed_mcp.launchers import install_launchers, render_launchers


class ProfileLauncherTests(unittest.TestCase):
    def test_windows_launchers_are_english_and_quote_paths(self) -> None:
        rendered = render_launchers(
            profile="semester-2",
            config_path=Path(r"C:\Config Folder\semester-2.toml"),
            python_executable=Path(r"C:\Program Files\Python\python.exe"),
            platform="win32",
        )
        self.assertEqual(
            {
                "canvas_ed_app.cmd",
                "configure_courses.cmd",
                "course_material_sync.cmd",
                "health_check.cmd",
                "open_course_folder.cmd",
                "this_semester_due.cmd",
                "this_week_due.cmd",
            },
            set(rendered),
        )
        content = rendered["course_material_sync.cmd"]
        self.assertIn('"C:\\Program Files\\Python\\python.exe" -I -m canvas_ed_mcp sync', content)
        self.assertIn('--profile "semester-2"', content)
        self.assertIn('--config "C:\\Config Folder\\semester-2.toml"', content)
        self.assertNotIn("credential", content.casefold())
        self.assertIn(
            "canvas_ed_mcp configure-gui",
            rendered["configure_courses.cmd"],
        )

    def test_windows_launcher_escapes_percent_expansion(self) -> None:
        rendered = render_launchers(
            profile="default",
            config_path=Path(r"C:\Config%20Folder\default.toml"),
            python_executable=Path(r"C:\Python\python.exe"),
            platform="win32",
        )
        self.assertIn(r"Config%%20Folder", rendered["canvas_ed_app.cmd"])

    def test_posix_launchers_forward_arguments_without_shell_interpolation(self) -> None:
        rendered = render_launchers(
            profile="default",
            config_path=Path("/Users/Test User/config.toml"),
            python_executable=Path("/Users/Test User/.venv/bin/python"),
            platform="darwin",
        )
        self.assertIn("canvas_ed_app.command", rendered)
        content = rendered["this_week_due.command"]
        self.assertIn("#!/bin/sh", content)
        self.assertIn("exec '/Users/Test User/.venv/bin/python' -I -m canvas_ed_mcp due week", content)
        self.assertIn('"$@"', content)

    def test_install_is_create_only_and_preserves_user_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            existing = target / "canvas_ed_app.cmd"
            existing.write_text("user content", encoding="utf-8")
            rendered = {
                "canvas_ed_app.cmd": "managed content\n",
                "health_check.cmd": "health\n",
            }
            result = install_launchers(target, rendered, executable_names=())
            self.assertEqual("user content", existing.read_text(encoding="utf-8"))
            self.assertEqual("managed content\n", (target / "canvas_ed_app.cmd.new").read_text(encoding="utf-8"))
            self.assertEqual("health\n", (target / "health_check.cmd").read_text(encoding="utf-8"))
            self.assertEqual(("canvas_ed_app.cmd.new", "health_check.cmd"), result.created)
            self.assertEqual(("canvas_ed_app.cmd",), result.preserved)


if __name__ == "__main__":
    unittest.main()
