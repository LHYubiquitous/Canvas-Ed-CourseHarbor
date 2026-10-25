from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ci.build_windows_portable import _pyinstaller_environment
from ci.verify_windows_portable import (
    PortableBundleAuditError,
    audit_windows_portable,
)


class WindowsPortableAuditTests(unittest.TestCase):
    def test_pyinstaller_environment_drops_injected_tool_paths(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "PATH": r"C:\private-tool\poppler",
                "PYTHONPATH": r"C:\private-tool\python",
            },
            clear=False,
        ):
            environment = _pyinstaller_environment()
        self.assertNotIn("private-tool", environment["PATH"])
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual("1", environment["PYTHONNOUSERSITE"])

    def _bundle(self, root: Path) -> Path:
        bundle = root / "CourseHarbor"
        (bundle / "_internal" / "PySide6" / "plugins" / "platforms").mkdir(
            parents=True
        )
        (bundle / "CourseHarbor.exe").write_bytes(b"gui")
        (bundle / "courseharbor-cli.exe").write_bytes(b"cli")
        (bundle / "_internal" / "PySide6" / "plugins" / "platforms" / "qwindows.dll").write_bytes(
            b"qt"
        )
        return bundle

    def test_inventory_requires_gui_cli_and_qt_with_expected_subsystems(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            result = audit_windows_portable(
                bundle,
                subsystem_reader=lambda path: 2 if path.name == "CourseHarbor.exe" else 3,
                icon_reader=lambda path: True,
            )
        self.assertEqual(3, result.file_count)
        self.assertTrue(result.qt_platform_plugin.endswith("qwindows.dll"))

    def test_build_machine_paths_fail_without_reflecting_the_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root)
            private_root = root / "private-owner"
            (bundle / "_internal" / "payload.bin").write_bytes(
                str(private_root).encode("utf-8")
            )
            with self.assertRaisesRegex(PortableBundleAuditError, "host_path") as caught:
                audit_windows_portable(
                    bundle,
                    forbidden_roots=(private_root,),
                    subsystem_reader=lambda path: (
                        2 if path.name == "CourseHarbor.exe" else 3
                    ),
                    icon_reader=lambda path: True,
                )
        self.assertNotIn("private-owner", str(caught.exception))

    def test_private_key_parser_constant_is_not_treated_as_key_material(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            (bundle / "_internal" / "tls.dll").write_bytes(
                b"parser supports -----BEGIN " + b"PRIVATE KEY----- headers"
            )
            result = audit_windows_portable(
                bundle,
                subsystem_reader=lambda path: (
                    2 if path.name == "CourseHarbor.exe" else 3
                ),
                icon_reader=lambda path: True,
            )
        self.assertEqual(4, result.file_count)

    def test_complete_private_key_pem_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            key_file = bundle / "_internal" / "secret.pem"
            begin = b"-----BEGIN " + b"PRIVATE KEY-----\n"
            end = b"\n-----END " + b"PRIVATE KEY-----\n"
            key_file.write_bytes(
                begin + (b"A" * 96) + end
            )
            with self.assertRaisesRegex(
                PortableBundleAuditError, "private_key:_internal/secret.pem"
            ):
                audit_windows_portable(
                    bundle,
                    subsystem_reader=lambda path: (
                        2 if path.name == "CourseHarbor.exe" else 3
                    ),
                    icon_reader=lambda path: True,
                )

    def test_executables_require_icon_resources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            with self.assertRaisesRegex(
                PortableBundleAuditError, "missing_icon_resource"
            ):
                audit_windows_portable(
                    bundle,
                    subsystem_reader=lambda path: (
                        2 if path.name == "CourseHarbor.exe" else 3
                    ),
                    icon_reader=lambda path: False,
                )


if __name__ == "__main__":
    unittest.main()
