from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from canvas_ed_mcp.launchers import install_launchers, render_launchers


class ProfileLauncherTests(unittest.TestCase):
    def test_frozen_windows_launchers_use_gui_and_cli_siblings(self) -> None:
        rendered = render_launchers(
            profile="semester-2",
            config_path=Path("C:/CourseHarborData/config.toml"),
            gui_executable=Path("D:/CourseHarbor/CourseHarbor.exe"),
            cli_executable=Path("D:/CourseHarbor/courseharbor-cli.exe"),
            platform="win32",
        )
        self.assertIn(
            '"D:\\CourseHarbor\\CourseHarbor.exe" --profile "semester-2"',
            rendered["canvas_ed_app.cmd"],
        )
        self.assertIn(
            '"D:\\CourseHarbor\\CourseHarbor.exe" --configure --profile "semester-2"',
            rendered["configure_courses.cmd"],
        )
        self.assertIn(
            '"D:\\CourseHarbor\\courseharbor-cli.exe" sync --profile "semester-2"',
            rendered["course_material_sync.cmd"],
        )
        self.assertNotIn(" -m ", rendered["canvas_ed_app.cmd"])

    def test_launcher_runtime_modes_are_unambiguous(self) -> None:
        common = {
            "profile": "default",
            "config_path": Path("C:/config.toml"),
            "platform": "win32",
        }
        with self.assertRaisesRegex(ValueError, "exactly one"):
            render_launchers(**common)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            render_launchers(
                **common,
                python_executable=Path("python.exe"),
                gui_executable=Path("CourseHarbor.exe"),
                cli_executable=Path("courseharbor-cli.exe"),
            )

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
        daily_app = rendered["canvas_ed_app.cmd"]
        self.assertIn(
            "-m canvas_ed_mcp.ui.app_shell",
            daily_app,
        )
        self.assertNotIn("-m canvas_ed_mcp app", daily_app)

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
        daily_app = rendered["canvas_ed_app.command"]
        self.assertIn("-m canvas_ed_mcp.ui.app_shell", daily_app)
        self.assertNotIn("-m canvas_ed_mcp app", daily_app)

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

    def test_install_upgrades_exact_managed_terminal_menu_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            legacy = target / "canvas_ed_app.cmd"
            legacy.write_text(
                "@echo off\r\n"
                "setlocal\r\n"
                '"C:\\Python\\python.exe" -I -m canvas_ed_mcp app '
                '--profile "default" --config "C:\\Config\\default.toml" %*\r\n'
                'set "CANVAS_ED_EXIT=%ERRORLEVEL%"\r\n'
                'if not "%CANVAS_ED_NO_PAUSE%"=="1" pause\r\n'
                "exit /b %CANVAS_ED_EXIT%\r\n",
                encoding="utf-8",
                newline="",
            )
            rendered = render_launchers(
                profile="default",
                config_path=Path(r"C:\Config\default.toml"),
                python_executable=Path(r"C:\Python\python.exe"),
                platform="win32",
            )

            result = install_launchers(target, rendered, executable_names=())

            self.assertEqual(
                rendered["canvas_ed_app.cmd"],
                legacy.read_bytes().decode("utf-8"),
            )
            self.assertFalse((target / "canvas_ed_app.cmd.new").exists())
            self.assertIn("canvas_ed_app.cmd", result.created)
            self.assertNotIn("canvas_ed_app.cmd", result.preserved)

    def test_install_upgrades_exact_posix_terminal_menu_launchers(self) -> None:
        for platform, suffix in (("darwin", ".command"), ("linux", ".sh")):
            with self.subTest(platform=platform), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                rendered = render_launchers(
                    profile="default",
                    config_path=Path("/tmp/config.toml"),
                    python_executable=Path("/opt/python/bin/python"),
                    platform=platform,
                )
                name = f"canvas_ed_app{suffix}"
                legacy = rendered[name].replace(
                    "-m canvas_ed_mcp.ui.app_shell --profile",
                    "-m canvas_ed_mcp app --profile",
                    1,
                )
                (target / name).write_text(legacy, encoding="utf-8", newline="")

                result = install_launchers(target, rendered, executable_names=())

                self.assertEqual(rendered[name], (target / name).read_text(encoding="utf-8"))
                self.assertIn(name, result.created)
                self.assertNotIn(name, result.preserved)


if __name__ == "__main__":
    unittest.main()
