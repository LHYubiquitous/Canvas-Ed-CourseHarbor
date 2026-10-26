from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from ci.build_macos_dmg import (
    MacOSDMGBuildError,
    _attach_readonly,
    _build_host_roots,
    _install_cli_companion,
    _publish_create_only,
    _pyinstaller_environment,
    _smoke_bundle,
    build_macos_dmg,
    dmg_artifact_name,
)


class MacOSDMGBuildTests(unittest.TestCase):
    def test_artifact_name_is_versioned_and_architecture_bound(self) -> None:
        self.assertEqual(
            "CourseHarbor-v0.2.0rc18-macos-arm64.dmg",
            dmg_artifact_name("0.2.0rc18", "arm64"),
        )
        self.assertEqual(
            "CourseHarbor-v0.2.0rc18-macos-x86_64.dmg",
            dmg_artifact_name("0.2.0rc18", "x86_64"),
        )
        with self.assertRaisesRegex(MacOSDMGBuildError, "unsupported_architecture"):
            dmg_artifact_name("0.2.0rc18", "universal2")

    def test_pyinstaller_environment_drops_injected_tool_paths(self) -> None:
        with patch.dict(
            "os.environ",
            {"PATH": "/private/tools", "PYTHONPATH": "/private/python"},
            clear=False,
        ):
            environment = _pyinstaller_environment()
        self.assertNotIn("/private/tools", environment["PATH"])
        self.assertNotIn("PYTHONPATH", environment)
        self.assertEqual("1", environment["PYTHONNOUSERSITE"])
        self.assertIn(str(Path("/usr/bin")), environment["PATH"])

    def test_host_path_policy_covers_checkout_home_and_python_installation(self) -> None:
        repository = Path.cwd() / "repository"
        roots = _build_host_roots(repository)
        self.assertEqual(repository, roots[0])
        self.assertIn(Path.home().resolve(strict=False), roots)
        self.assertIn(Path(sys.executable).resolve().parent, roots)
        self.assertIn(Path(sys.base_prefix).resolve(), roots)

    def test_build_fails_before_writing_on_non_macos_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "CourseHarbor.dmg"
            with patch("ci.build_macos_dmg.sys.platform", "win32"):
                with self.assertRaisesRegex(MacOSDMGBuildError, "macos_required"):
                    build_macos_dmg(root, output)
            self.assertFalse(output.exists())

    def test_partial_attach_is_cleaned_up_without_reflecting_tool_output(self) -> None:
        payload = plistlib.dumps(
            {
                "system-entities": [
                    {"dev-entry": "/dev/disk99"},
                    {
                        "dev-entry": "/dev/disk99s1",
                        "mount-point": "/private/mounted",
                    },
                ]
            }
        )
        attach_result = subprocess.CompletedProcess([], 1, stdout=payload)
        detach_result = subprocess.CompletedProcess([], 0, stdout=b"")
        with patch(
            "ci.build_macos_dmg.subprocess.run",
            side_effect=[attach_result, detach_result, detach_result],
        ) as run:
            with self.assertRaisesRegex(
                MacOSDMGBuildError, "hdiutil_attach_failed"
            ) as caught:
                _attach_readonly(
                    Path("candidate.dmg"),
                    Path("/private/mounted"),
                    cwd=Path.cwd(),
                    environment={},
                )
        self.assertEqual(3, run.call_count)
        self.assertNotIn("disk99", str(caught.exception))

    def test_publish_is_create_only_and_rolls_back_a_partial_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dmg = root / "candidate.dmg"
            candidate_manifest = root / "candidate.json"
            destination = root / "CourseHarbor.dmg"
            manifest_destination = root / "CourseHarbor.dmg.manifest.json"
            candidate_dmg.write_bytes(b"dmg")
            candidate_manifest.write_text("{}", encoding="utf-8")

            _publish_create_only(
                candidate_dmg,
                candidate_manifest,
                destination,
                manifest_destination,
            )
            self.assertEqual(b"dmg", destination.read_bytes())
            self.assertEqual("{}", manifest_destination.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dmg = root / "candidate.dmg"
            candidate_manifest = root / "candidate.json"
            destination = root / "CourseHarbor.dmg"
            manifest_destination = root / "CourseHarbor.dmg.manifest.json"
            candidate_dmg.write_bytes(b"new-dmg")
            candidate_manifest.write_text("new", encoding="utf-8")
            manifest_destination.write_text("existing", encoding="utf-8")

            with self.assertRaisesRegex(MacOSDMGBuildError, "output_exists"):
                _publish_create_only(
                    candidate_dmg,
                    candidate_manifest,
                    destination,
                    manifest_destination,
                )
            self.assertFalse(destination.exists())
            self.assertEqual("existing", manifest_destination.read_text(encoding="utf-8"))

    def test_standalone_cli_is_installed_create_only_and_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "CourseHarbor.app"
            (app / "Contents" / "MacOS").mkdir(parents=True)
            standalone = root / "courseharbor-cli"
            standalone.write_bytes(b"standalone-cli")

            installed = _install_cli_companion(app, standalone)

            self.assertEqual(
                app / "Contents" / "MacOS" / "courseharbor-cli", installed
            )
            self.assertEqual(b"standalone-cli", installed.read_bytes())
            if os.name != "nt":
                self.assertTrue(installed.stat().st_mode & 0o111)
            with self.assertRaisesRegex(
                MacOSDMGBuildError, "cli_companion_destination_exists"
            ):
                _install_cli_companion(app, standalone)

    def test_cli_install_rejects_missing_or_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = root / "CourseHarbor.app"
            (app / "Contents" / "MacOS").mkdir(parents=True)
            with self.assertRaisesRegex(
                MacOSDMGBuildError, "cli_companion_missing"
            ):
                _install_cli_companion(app, root / "missing")

            target = root / "target"
            target.write_bytes(b"cli")
            link = root / "courseharbor-cli"
            try:
                link.symlink_to(target)
            except OSError as exc:  # pragma: no cover - Windows policy dependent
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaisesRegex(
                MacOSDMGBuildError, "cli_companion_missing"
            ):
                _install_cli_companion(app, link)

    def test_cli_smoke_failure_reports_only_a_safe_category(self) -> None:
        gui_result = subprocess.CompletedProcess([], 0, stdout="")
        cli_version_result = subprocess.CompletedProcess([], 0, stdout="0.2.0rc18")
        cli_result = subprocess.CompletedProcess(
            [],
            3,
            stdout=(
                "ignored runtime warning\n"
                '{"backend":"unavailable","keyring":"backend_error"}\n'
            ),
            stderr="ignored private diagnostic",
        )
        with patch(
            "ci.build_macos_dmg.subprocess.run",
            side_effect=[gui_result, cli_version_result, cli_result],
        ):
            with self.assertRaisesRegex(
                MacOSDMGBuildError, "cli_smoke_failed:backend_error"
            ) as caught:
                _smoke_bundle(Path("CourseHarbor.app"), cwd=Path.cwd(), environment={})
        self.assertNotIn("unavailable", str(caught.exception))

    def test_cli_wrapper_redacts_keyring_backend_exceptions(self) -> None:
        wrapper = Path(__file__).parents[1] / "packaging" / "windows" / "courseharbor_cli.py"
        spec = spec_from_file_location("courseharbor_cli_smoke_test", wrapper)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        output = StringIO()
        with patch(
            "canvas_ed_mcp.secrets.keyring_status",
            side_effect=RuntimeError("SENSITIVE_BACKEND_DETAIL"),
        ), redirect_stdout(output):
            result = module._bundle_smoke()
        self.assertEqual(3, result)
        self.assertEqual(
            {"backend": "unavailable", "keyring": "backend_error"},
            json.loads(output.getvalue()),
        )
        self.assertNotIn("SENSITIVE_BACKEND_DETAIL", output.getvalue())


if __name__ == "__main__":
    unittest.main()
