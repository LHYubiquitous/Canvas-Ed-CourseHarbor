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

    def test_create_only_config_reports_commit_when_post_link_cleanup_fails(self) -> None:
        result = self.run_package_code(
            """
            import tempfile
            import warnings
            from pathlib import Path
            from unittest.mock import patch
            from canvas_ed_mcp.config import AppConfig, load_config, save_new_config

            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / "config.toml"
                config = AppConfig(profile="cleanup", output_root=root / "Courses")
                original_unlink = Path.unlink
                def fail_temporary_only(target, *args, **kwargs):
                    if target != path:
                        raise OSError("synthetic cleanup failure")
                    return original_unlink(target, *args, **kwargs)
                with patch.object(Path, "unlink", fail_temporary_only):
                    cleanup_pending = save_new_config(config, path)
                assert load_config(path) == config
                assert cleanup_pending
                print("committed")
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("committed", result.stdout.strip())

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
            from canvas_ed_mcp.initial import InitialSyncMode

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
                                    initial_mode=InitialSyncMode.IMPORT_ALL,
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
                    "initial_mode": loaded.courses[0].sources[0].initial_mode.value,
                    "same": loaded == config,
                }))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(2, data["schema"])
        self.assertEqual("default", data["profile"])
        self.assertTrue(data["canvas"])
        self.assertFalse(data["ed"])
        self.assertIsNone(data["ed_credential"])
        self.assertEqual("canvas:canvas.example.edu:101", data["course_key"])
        self.assertEqual("import_all", data["initial_mode"])
        self.assertTrue(data["same"])

    def test_schema_v1_loads_with_safe_daily_use_defaults(self) -> None:
        result = self.run_package_code(
            """
            import json
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.config import load_config

            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / "config.toml"
                path.write_text(
                    'schema_version = 1\\nprofile = "legacy"\\noutput_root = "' +
                    str(root / "Courses").replace('\\\\', '/') + '"\\n',
                    encoding="utf-8",
                )
                config = load_config(path)
                print(json.dumps({
                    "schema": config.schema_version,
                    "launchers": config.daily_use.launchers.enabled,
                    "timezone": config.daily_use.due.timezone,
                }))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {"schema": 2, "launchers": True, "timezone": "Australia/Sydney"},
            json.loads(result.stdout),
        )

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

    def test_gradescope_due_mapping_requires_enabled_provider_and_known_course(self) -> None:
        result = self.run_package_code(
            """
            from pathlib import Path
            from canvas_ed_mcp.config import AppConfig, DailyUseConfig, DueReportConfig, GradescopeDueMapping

            try:
                AppConfig(
                    output_root=Path.cwd(),
                    daily_use=DailyUseConfig(
                        due=DueReportConfig(
                            gradescope_courses=(GradescopeDueMapping(logical_course_key="missing", course_id="901"),)
                        )
                    ),
                )
            except ValueError:
                print("rejected")
            else:
                raise AssertionError("unsafe Gradescope due mapping was accepted")
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("rejected", result.stdout.strip())

    def test_required_outline_must_also_be_a_configured_outline(self) -> None:
        result = self.run_package_code(
            """
            from pathlib import Path
            from canvas_ed_mcp.config import DueReportConfig

            missing = (Path.cwd() / "missing-outline.md").resolve()
            try:
                DueReportConfig(required_outline_files=(missing,))
            except ValueError:
                print("rejected")
            else:
                raise AssertionError("unconfigured required Outline was accepted")
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("rejected", result.stdout.strip())

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
