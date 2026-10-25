from __future__ import annotations

import unittest
from pathlib import Path

from canvas_ed_mcp.ui.frozen_runtime import (
    APP_SHELL_MODULE,
    CONFIGURE_MODULE,
    detached_module_command,
    frozen_companion_executables,
)
from canvas_ed_mcp.ui.icon import icon_path


class FrozenRuntimeTests(unittest.TestCase):
    def test_packaged_application_icon_exists(self) -> None:
        self.assertTrue(icon_path().is_file())

    def test_source_modules_use_isolated_python_module_invocation(self) -> None:
        executable = Path("C:/Python/python.exe")
        program, arguments = detached_module_command(
            CONFIGURE_MODULE,
            ("--profile", "default"),
            executable=executable,
            frozen=False,
        )
        self.assertEqual(str(executable), program)
        self.assertEqual(
            ("-I", "-m", CONFIGURE_MODULE, "--profile", "default"),
            arguments,
        )

    def test_frozen_gui_routes_are_dispatched_by_the_same_executable(self) -> None:
        executable = Path("D:/Portable/CourseHarbor/CourseHarbor.exe")
        self.assertEqual(
            (str(executable), ("--profile", "default")),
            detached_module_command(
                APP_SHELL_MODULE,
                ("--profile", "default"),
                executable=executable,
                frozen=True,
            ),
        )
        self.assertEqual(
            (str(executable), ("--configure", "--profile", "default")),
            detached_module_command(
                CONFIGURE_MODULE,
                ("--profile", "default"),
                executable=executable,
                frozen=True,
            ),
        )

    def test_frozen_bundle_requires_known_routes_and_has_cli_sibling(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported frozen"):
            detached_module_command(
                "unknown.module",
                (),
                executable="CourseHarbor.exe",
                frozen=True,
            )
        gui, cli = frozen_companion_executables(
            "D:/Portable/CourseHarbor/CourseHarbor.exe",
            platform="win32",
        )
        self.assertEqual("CourseHarbor.exe", gui.name)
        self.assertEqual("courseharbor-cli.exe", cli.name)


if __name__ == "__main__":
    unittest.main()
