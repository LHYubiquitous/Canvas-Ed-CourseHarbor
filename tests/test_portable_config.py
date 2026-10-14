from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


class PortablePackageTests(unittest.TestCase):
    def run_package_code(self, code: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            return subprocess.run(
                [sys.executable, "-B", "-c", textwrap.dedent(code)],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_console_module_exposes_stable_help_without_loading_legacy_server(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-B", "-m", "canvas_ed_mcp.cli", "--help"],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("setup", result.stdout)
        self.assertIn("configure", result.stdout)
        self.assertIn("sync", result.stdout)
        self.assertIn("doctor", result.stdout)
        self.assertIn("serve", result.stdout)

    def test_canvas_only_config_round_trips_without_ed_credential(self) -> None:
        result = self.run_package_code(
            """
            import json
            import tempfile
            from datetime import datetime, timezone
            from pathlib import Path

            from canvas_ed_mcp.config import (
                AppConfig,
                CanvasPlatformConfig,
                CourseConfig,
                CourseSource,
                PlatformsConfig,
                load_config,
                save_config,
            )

            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                path = base / "config" / "profiles" / "default.toml"
                config = AppConfig(
                    profile="default",
                    output_root=base / "Courses",
                    platforms=PlatformsConfig(
                        canvas=CanvasPlatformConfig(
                            enabled=True,
                            base_url="https://canvas.example.edu/api/v1",
                            credential="keyring:canvas-ed-mcp/default/canvas",
                        )
                    ),
                    courses=[
                        CourseConfig(
                            key="canvas:canvas.example.edu:101",
                            folder="TEST1000",
                            confirmed_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
                            sources=[
                                CourseSource(
                                    provider="canvas",
                                    instance="canvas.example.edu",
                                    course_id="101",
                                    code="TEST1000",
                                    name="Synthetic Course",
                                    term="2026 Semester 2",
                                )
                            ],
                        )
                    ],
                )
                save_config(config, path)
                loaded = load_config(path)
                print(json.dumps({
                    "schema": loaded.schema_version,
                    "profile": loaded.profile,
                    "canvas": loaded.platforms.canvas.enabled,
                    "ed": loaded.platforms.ed.enabled,
                    "ed_credential": loaded.platforms.ed.credential,
                    "course_key": loaded.courses[0].key,
                    "same": loaded == config,
                }))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(1, data["schema"])
        self.assertEqual("default", data["profile"])
        self.assertTrue(data["canvas"])
        self.assertFalse(data["ed"])
        self.assertIsNone(data["ed_credential"])
        self.assertEqual("canvas:canvas.example.edu:101", data["course_key"])
        self.assertTrue(data["same"])

    def test_unknown_config_schema_fails_closed(self) -> None:
        result = self.run_package_code(
            """
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.config import ConfigLoadError, load_config

            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "config.toml"
                path.write_text(
                    'schema_version = 99\\nprofile = "default"\\noutput_root = "/synthetic"\\n',
                    encoding="utf-8",
                )
                try:
                    load_config(path)
                except ConfigLoadError as error:
                    print(error.category)
                else:
                    raise AssertionError("unknown schema was accepted")
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("unsupported_schema", result.stdout.strip())

    def test_atomic_config_replace_failure_preserves_previous_file(self) -> None:
        result = self.run_package_code(
            """
            import tempfile
            from pathlib import Path
            from unittest.mock import patch
            from canvas_ed_mcp.config import AppConfig, save_config

            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                path = base / "profiles" / "default.toml"
                path.parent.mkdir(parents=True)
                path.write_bytes(b"previous-config")
                config = AppConfig(profile="default", output_root=base / "Courses")
                with patch("pathlib.Path.replace", side_effect=OSError("synthetic failure")):
                    try:
                        save_config(config, path)
                    except OSError:
                        pass
                    else:
                        raise AssertionError("replace failure was swallowed")
                assert path.read_bytes() == b"previous-config"
                assert [item.name for item in path.parent.iterdir()] == ["default.toml"]
                print("preserved")
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("preserved", result.stdout.strip())

    def test_profile_paths_are_isolated_and_reject_traversal(self) -> None:
        result = self.run_package_code(
            """
            import json
            import tempfile
            from pathlib import Path
            from types import SimpleNamespace
            from canvas_ed_mcp.paths import resolve_profile_paths

            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                dirs = SimpleNamespace(
                    user_config_path=base / "config",
                    user_state_path=base / "state",
                    user_log_path=base / "logs",
                    user_cache_path=base / "cache",
                )
                first = resolve_profile_paths("default", dirs=dirs)
                second = resolve_profile_paths("semester-2", dirs=dirs)
                try:
                    resolve_profile_paths("../escape", dirs=dirs)
                except ValueError:
                    traversal_rejected = True
                else:
                    traversal_rejected = False
                print(json.dumps({
                    "distinct_config": first.config_file != second.config_file,
                    "distinct_state": first.state_file != second.state_file,
                    "shared_lock_dir": first.lock_dir == second.lock_dir,
                    "first_profile": first.profile,
                    "contained": str(first.config_file).startswith(str(base)),
                    "traversal_rejected": traversal_rejected,
                    "no_directories_created": not any(base.iterdir()),
                }))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["distinct_config"])
        self.assertTrue(data["distinct_state"])
        self.assertTrue(data["shared_lock_dir"])
        self.assertEqual("default", data["first_profile"])
        self.assertTrue(data["contained"])
        self.assertTrue(data["traversal_rejected"])
        self.assertTrue(data["no_directories_created"])


if __name__ == "__main__":
    unittest.main()
