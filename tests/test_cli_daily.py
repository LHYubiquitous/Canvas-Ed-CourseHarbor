from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from canvas_ed_mcp.cli import (
    _due_providers,
    _due,
    _launcher_python_executable,
    _menu_sync,
    _setup,
    build_parser,
)
from canvas_ed_mcp.due.model import DueItem, DuePrecision, DueSourceKey
from canvas_ed_mcp.due.service import DueCollection
from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseConfig,
    CourseSource,
    EdPlatformConfig,
    PlatformsConfig,
)


class DailyCliTests(unittest.TestCase):
    def test_launcher_python_preserves_venv_symlink_path(self) -> None:
        venv_python = Path.cwd() / ".venv" / "bin" / "python"
        resolved_base_python = Path.cwd() / "uv-python" / "bin" / "python3.15"

        with patch.object(Path, "resolve", return_value=resolved_base_python) as resolve:
            selected = _launcher_python_executable(str(venv_python))

        self.assertEqual(venv_python, selected)
        resolve.assert_not_called()

    @staticmethod
    def _mixed_config() -> AppConfig:
        return AppConfig(
            output_root=Path.cwd(),
            platforms=PlatformsConfig(
                canvas=CanvasPlatformConfig(enabled=True, base_url="https://canvas.example.edu/api/v1", credential="env:CANVAS"),
                ed=EdPlatformConfig(enabled=True, credential="env:ED"),
            ),
            courses=(
                CourseConfig(
                    key="canvas-course", folder="CANVAS",
                    confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    sources=(CourseSource(provider="canvas", instance="canvas.example.edu", course_id="1"),),
                ),
                CourseConfig(
                    key="ed-course", folder="ED",
                    confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    sources=(CourseSource(provider="ed", instance="edstem.org", course_id="2"),),
                ),
            ),
        )

    def test_parser_exposes_daily_commands_with_profile_and_config(self) -> None:
        parser = build_parser()
        cases = (
            ["app", "--profile", "daily", "--config", "daily.toml"],
            ["due", "week", "--profile", "daily", "--config", "daily.toml"],
            ["due", "semester", "--profile", "daily", "--config", "daily.toml"],
            ["launchers", "install", "--profile", "daily", "--config", "daily.toml"],
            ["open-course-folder", "--profile", "daily", "--config", "daily.toml"],
        )
        for argv in cases:
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertEqual("daily", args.profile)
                self.assertEqual("daily.toml", str(args.config))
                self.assertTrue(callable(args.handler))

    def test_committed_setup_installs_launchers_but_dry_run_does_not(self) -> None:
        args = Namespace(profile="daily", config=Path("daily.toml"), dry_run=False)
        with patch("canvas_ed_mcp.setup_flow.run_setup_wizard", return_value=True), patch(
            "canvas_ed_mcp.cli._install_launchers_after_commit"
        ) as install:
            self.assertEqual(0, _setup(args))
            install.assert_called_once_with("daily", Path("daily.toml"))

        args.dry_run = True
        with patch("canvas_ed_mcp.setup_flow.run_setup_wizard", return_value=False), patch(
            "canvas_ed_mcp.cli._install_launchers_after_commit"
        ) as install:
            self.assertEqual(0, _setup(args))
            install.assert_not_called()

    def test_menu_sync_previews_then_requires_exact_confirmation(self) -> None:
        with patch("canvas_ed_mcp.cli.main", side_effect=[0, 0]) as invoke:
            self.assertEqual(
                0, _menu_sync(["--profile", "daily"], input_fn=lambda _: "y")
            )
        self.assertEqual(
            [
                ((["sync", "--profile", "daily", "--dry-run"],), {}),
                ((["sync", "--profile", "daily"],), {}),
            ],
            invoke.call_args_list,
        )

        with patch("canvas_ed_mcp.cli.main", return_value=0) as invoke:
            self.assertEqual(
                0, _menu_sync(["--profile", "daily"], input_fn=lambda _: "")
            )
        invoke.assert_called_once()

    def test_menu_sync_can_apply_complete_actionable_preview_with_source_warnings(self) -> None:
        preview = {
            "executed_source_count": 2,
            "run_issue_codes": [],
            "selected_source_count": 2,
            "sources": [
                {
                    "actions": ["would_fetch"],
                    "committed": False,
                    "course_key": "course",
                    "dry_run": True,
                    "issue_codes": ["authorization_failed"],
                    "provider": "canvas",
                },
                {
                    "actions": ["would_fetch"],
                    "committed": False,
                    "course_key": "course",
                    "dry_run": True,
                    "issue_codes": [],
                    "provider": "ed",
                },
            ],
        }
        applied = {
            **preview,
            "sources": [
                {**source, "committed": True, "dry_run": False}
                for source in preview["sources"]
            ],
        }

        def invoke(argv: list[str]) -> int:
            report = preview if "--dry-run" in argv else applied
            print(json.dumps(report))
            return 1

        with patch("canvas_ed_mcp.cli.main", side_effect=invoke) as main_call:
            exit_code = _menu_sync(
                ["--profile", "daily"], input_fn=lambda _: "y"
            )

        self.assertEqual(0, exit_code)
        self.assertEqual(2, main_call.call_count)

    def test_menu_sync_blocks_warning_preview_when_a_source_has_no_actions(self) -> None:
        preview = {
            "executed_source_count": 2,
            "run_issue_codes": [],
            "selected_source_count": 2,
            "sources": [
                {
                    "actions": [],
                    "committed": False,
                    "dry_run": True,
                    "issue_codes": ["authorization_failed"],
                    "provider": "canvas",
                },
                {
                    "actions": ["would_fetch"],
                    "committed": False,
                    "dry_run": True,
                    "issue_codes": [],
                    "provider": "ed",
                },
            ],
        }

        def invoke(_: list[str]) -> int:
            print(json.dumps(preview))
            return 1

        with patch("canvas_ed_mcp.cli.main", side_effect=invoke) as main_call:
            exit_code = _menu_sync(
                ["--profile", "daily"], input_fn=lambda _: "y"
            )

        self.assertEqual(1, exit_code)
        main_call.assert_called_once()

    def test_due_provider_startup_failure_is_isolated_per_provider(self) -> None:
        class FakeEd:
            instance = "edstem.org"

            def __init__(self, token: str) -> None:
                self.token = token

            def close(self) -> None:
                pass

        def resolve(reference: str) -> str:
            if reference == "env:CANVAS":
                raise RuntimeError("missing")
            return "ed-token"

        with patch("canvas_ed_mcp.secrets.resolve_secret", side_effect=resolve), patch(
            "canvas_ed_mcp.providers.ed.EdProvider", FakeEd
        ):
            providers = _due_providers(self._mixed_config())
        self.assertNotIn(("canvas", "canvas.example.edu"), providers)
        self.assertIn(("ed", "edstem.org"), providers)

    def test_week_due_prints_filtered_report_count_not_collection_count(self) -> None:
        sydney = ZoneInfo("Australia/Sydney")
        now = datetime.now(sydney)
        monday = now - timedelta(days=now.weekday())

        def due(remote_id: str, due_at: datetime) -> DueItem:
            return DueItem(
                key=DueSourceKey(
                    "canvas", "canvas.example.edu", "42", "assignment", remote_id
                ),
                logical_course_key="course-42",
                course_label="COMP0000",
                title=f"Assessment {remote_id}",
                due_at=due_at,
                precision=DuePrecision.DATETIME,
                points=Decimal("10"),
                weight_percent=None,
            )

        in_week = tuple(due(str(index), monday + timedelta(days=index)) for index in range(3))
        outside_week = tuple(
            due(str(index), monday + timedelta(days=index + 7)) for index in range(3, 23)
        )
        collection = DueCollection(
            items=in_week + outside_week,
            issues=(),
            used_last_good=False,
            publishable=True,
            state_bytes=b"",
        )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = AppConfig(profile="daily", output_root=root)
            paths = SimpleNamespace(state_file=root / "state-v4.json")
            output = StringIO()
            with patch("canvas_ed_mcp.cli._config_for_args", return_value=(paths, root / "daily.toml", config)), patch(
                "canvas_ed_mcp.cli._due_providers", return_value={}
            ), patch(
                "canvas_ed_mcp.due.service.collect_configured_due_items",
                return_value=collection,
            ), redirect_stdout(output):
                exit_code = _due(Namespace(due_command="week", profile="daily", config=None))

        self.assertEqual(0, exit_code)
        self.assertIn("Due items in report: 3", output.getvalue())
        self.assertNotIn("Due items in report: 23", output.getvalue())

    def test_failed_gradescope_discovery_closes_candidate(self) -> None:
        from canvas_ed_mcp.config import DailyUseConfig, DueReportConfig, GradescopeDueMapping, GradescopePlatformConfig

        closed: list[bool] = []

        class FakeGradescope:
            instance = "gradescope.com"

            def __init__(self, email: str, password: str) -> None:
                pass

            def list_courses(self) -> None:
                raise RuntimeError("discovery failed")

            def close(self) -> None:
                closed.append(True)

        base = self._mixed_config()
        config = base.model_copy(update={
            "platforms": base.platforms.model_copy(update={"gradescope": GradescopePlatformConfig(enabled=True, email_credential="env:EMAIL", password_credential="env:PASSWORD")}),
            "daily_use": DailyUseConfig(due=DueReportConfig(gradescope_courses=(GradescopeDueMapping(logical_course_key="canvas-course", course_id="3"),))),
        })
        with patch("canvas_ed_mcp.secrets.resolve_secret", return_value="secret"), patch(
            "canvas_ed_mcp.providers.gradescope.GradescopeProvider", FakeGradescope
        ):
            providers = _due_providers(config)
        self.assertNotIn(("gradescope", "gradescope.com"), providers)
        self.assertEqual([True], closed)
        for provider in providers.values():
            provider.close()


if __name__ == "__main__":
    unittest.main()
