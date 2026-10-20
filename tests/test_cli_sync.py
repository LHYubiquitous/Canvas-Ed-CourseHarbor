from datetime import datetime, timezone
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canvas_ed_mcp.cli import main
from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseConfig,
    CourseSource,
    EdPlatformConfig,
    PlatformsConfig,
)
from canvas_ed_mcp.initial import InitialSyncMode
from canvas_ed_mcp.sync.engine import (
    ExecutionMode,
    RunTransactionResult,
    SourceSyncResult,
    SourceTransactionResult,
    StateCommitResult,
)
from canvas_ed_mcp.sync.state import new_state


class CliSyncTests(unittest.TestCase):
    def test_setup_failure_does_not_reflect_backend_exception_text(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(profile="test", config_file=root / "config.toml")
            sentinel = "SYNTHETIC-SECRET-IN-BACKEND-ERROR"
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.secrets.resolve_secret", side_effect=RuntimeError(sentinel)), \
                 patch("builtins.input", side_effect=["ed", "", "env:ED_TOKEN"]), \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout, \
                 patch("sys.stderr", new_callable=io.StringIO) as stderr:
                exit_code = main(["setup", "--profile", "test"])
            rendered = stdout.getvalue() + stderr.getvalue()
            self.assertEqual(2, exit_code)
            self.assertIn("setup failed (provider_unavailable)", rendered)
            self.assertNotIn(sentinel, rendered)

    def configured_ed(self, root: Path) -> AppConfig:
        return AppConfig(
            profile="test",
            output_root=root,
            platforms=PlatformsConfig(
                ed=EdPlatformConfig(
                    enabled=True, credential="env:CANVAS_ED_MCP_TEST_TOKEN"
                )
            ),
            courses=(CourseConfig(
                key="ed:edstem.org:101", folder="TEST1000",
                confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                sources=(CourseSource(
                    provider="ed", instance="edstem.org", course_id="101",
                    initial_mode=InitialSyncMode.IMPORT_ALL,
                ),),
            ),),
        )

    def configured_mixed(self, root: Path) -> AppConfig:
        ed = self.configured_ed(root)
        canvas_source = CourseSource(
            provider="canvas", instance="canvas.invalid", course_id="42",
            initial_mode=InitialSyncMode.IMPORT_ALL,
        )
        canvas_course = CourseConfig(
            key="canvas:canvas.invalid:42", folder="CANVAS0042",
            confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            sources=(canvas_source,),
        )
        return ed.model_copy(update={
            "platforms": PlatformsConfig(
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url="https://canvas.invalid/api/v1",
                    credential="env:CANVAS_TEST_TOKEN",
                ),
                ed=ed.platforms.ed,
            ),
            "courses": (canvas_course, *ed.courses),
        })

    def test_dry_run_uses_only_configured_ed_source_and_closes_provider(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(
                profile="test", config_file=root / "config.toml",
                state_file=root / "state.json", state_backup_file=root / "backup.json",
                lock_dir=root / ".locks", cache_dir=root / ".cache",
            )
            provider = Mock(instance="edstem.org")
            preview = SourceSyncResult(
                "ed:edstem.org:101", "ed", (), (), execution=ExecutionMode.DRY_RUN
            )
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.cli.load_config", return_value=self.configured_ed(root)), \
                 patch("canvas_ed_mcp.secrets.resolve_secret", return_value="synthetic-token") as secret, \
                 patch("canvas_ed_mcp.providers.ed.EdProvider", return_value=provider), \
                 patch("canvas_ed_mcp.sync.engine.preview_configured_source", return_value=preview) as dry, \
                 patch("canvas_ed_mcp.sync.engine.apply_configured_sources") as apply, \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = main(["sync", "--profile", "test", "--dry-run"])
            self.assertEqual(0, exit_code)
            secret.assert_called_once_with("env:CANVAS_ED_MCP_TEST_TOKEN")
            dry.assert_called_once()
            apply.assert_not_called()
            provider.close.assert_called_once_with()
            report = json.loads(stdout.getvalue())
            self.assertEqual(["ed"], [record["provider"] for record in report["sources"]])
            self.assertTrue(report["sources"][0]["dry_run"])

    def test_course_filter_builds_only_selected_canvas_provider(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(
                profile="test", config_file=root / "config.toml",
                state_file=root / "state.json", state_backup_file=root / "backup.json",
                lock_dir=root / ".locks", cache_dir=root / ".cache",
            )
            canvas_provider = Mock(instance="canvas.invalid")
            preview = SourceSyncResult(
                "canvas:canvas.invalid:42", "canvas", (), (),
                execution=ExecutionMode.DRY_RUN,
            )
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.cli.load_config", return_value=self.configured_mixed(root)), \
                 patch("canvas_ed_mcp.secrets.resolve_secret", return_value="synthetic-token") as secret, \
                 patch("canvas_ed_mcp.providers.canvas.CanvasProvider", return_value=canvas_provider) as canvas, \
                 patch("canvas_ed_mcp.providers.ed.EdProvider") as ed, \
                 patch("canvas_ed_mcp.sync.engine.preview_configured_source", return_value=preview), \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = main([
                    "sync", "--profile", "test", "--dry-run",
                    "--course", "canvas:canvas.invalid:42",
                ])

            self.assertEqual(0, exit_code)
            secret.assert_called_once_with("env:CANVAS_TEST_TOKEN")
            canvas.assert_called_once_with(
                "https://canvas.invalid/api/v1", "synthetic-token"
            )
            ed.assert_not_called()
            canvas_provider.close.assert_called_once_with()
            self.assertEqual("canvas", json.loads(stdout.getvalue())["sources"][0]["provider"])

    def test_apply_passes_exact_canvas_and_ed_provider_map(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(
                profile="test", config_file=root / "config.toml",
                state_file=root / "state.json", state_backup_file=root / "backup.json",
                lock_dir=root / ".locks", cache_dir=root / ".cache",
            )
            canvas_provider = Mock(instance="canvas.invalid")
            ed_provider = Mock(instance="edstem.org")
            run = RunTransactionResult(tuple(
                SourceTransactionResult(
                    SourceSyncResult(
                        key, provider, (), (), execution=ExecutionMode.APPLY
                    ),
                    StateCommitResult(new_state(profile="test", output_root=root), True),
                )
                for key, provider in (
                    ("canvas:canvas.invalid:42", "canvas"),
                    ("ed:edstem.org:101", "ed"),
                )
            ))
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.cli.load_config", return_value=self.configured_mixed(root)), \
                 patch("canvas_ed_mcp.secrets.resolve_secret", side_effect=["canvas-token", "ed-token"]) as secret, \
                 patch("canvas_ed_mcp.providers.canvas.CanvasProvider", return_value=canvas_provider), \
                 patch("canvas_ed_mcp.providers.ed.EdProvider", return_value=ed_provider), \
                 patch("canvas_ed_mcp.sync.engine.apply_configured_sources", return_value=run) as apply, \
                 patch("sys.stdout", new_callable=io.StringIO):
                exit_code = main(["sync", "--profile", "test"])

            self.assertEqual(0, exit_code)
            self.assertEqual(
                ["env:CANVAS_TEST_TOKEN", "env:CANVAS_ED_MCP_TEST_TOKEN"],
                [call.args[0] for call in secret.call_args_list],
            )
            self.assertEqual({
                ("canvas", "canvas.invalid"): canvas_provider,
                ("ed", "edstem.org"): ed_provider,
            }, apply.call_args.kwargs["providers"])
            self.assertNotIn("provider", apply.call_args.kwargs)
            canvas_provider.close.assert_called_once_with()
            ed_provider.close.assert_called_once_with()

    def test_sync_refuses_config_without_enabled_ed_before_secret_resolution(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(profile="test", config_file=root / "config.toml")
            config = AppConfig(profile="test", output_root=root)
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.cli.load_config", return_value=config), \
                 patch("canvas_ed_mcp.secrets.resolve_secret") as secret, \
                 patch("sys.stderr"):
                exit_code = main(["sync", "--profile", "test", "--dry-run"])
            self.assertEqual(2, exit_code)
            secret.assert_not_called()

    def test_source_failure_is_reported_without_exception_text(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(
                profile="test", config_file=root / "config.toml", state_file=root / "state.json",
            )
            provider = Mock(instance="edstem.org")
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.cli.load_config", return_value=self.configured_ed(root)), \
                 patch("canvas_ed_mcp.secrets.resolve_secret", return_value="synthetic-token"), \
                 patch("canvas_ed_mcp.providers.ed.EdProvider", return_value=provider), \
                 patch("canvas_ed_mcp.sync.engine.preview_configured_source", side_effect=RuntimeError("secret-value")), \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = main(["sync", "--profile", "test", "--dry-run"])
            self.assertEqual(1, exit_code)
            self.assertNotIn("secret-value", stdout.getvalue())
            report = json.loads(stdout.getvalue())
            self.assertEqual(["source_sync_failed"], report["sources"][0]["issue_codes"])
            provider.close.assert_called_once_with()

    def test_provider_close_failure_is_sanitized_after_source_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(
                profile="test", config_file=root / "config.toml", state_file=root / "state.json",
            )
            provider = Mock(instance="edstem.org")
            provider.close.side_effect = RuntimeError("secret-value")
            preview = SourceSyncResult(
                "ed:edstem.org:101", "ed", (), (), execution=ExecutionMode.DRY_RUN
            )
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.cli.load_config", return_value=self.configured_ed(root)), \
                 patch("canvas_ed_mcp.secrets.resolve_secret", return_value="synthetic-token"), \
                 patch("canvas_ed_mcp.providers.ed.EdProvider", return_value=provider), \
                 patch("canvas_ed_mcp.sync.engine.preview_configured_source", return_value=preview), \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = main(["sync", "--profile", "test", "--dry-run"])
            self.assertEqual(1, exit_code)
            self.assertNotIn("secret-value", stdout.getvalue())
            report = json.loads(stdout.getvalue())
            self.assertEqual(["provider_close_failed"], report["run_issue_codes"])
            self.assertEqual(1, len(report["sources"]))

    def test_apply_reports_committed_transaction(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(
                profile="test", config_file=root / "config.toml",
                state_file=root / "state.json", state_backup_file=root / "backup.json",
                lock_dir=root / ".locks", cache_dir=root / ".cache",
            )
            provider = Mock(instance="edstem.org")
            source_result = SourceSyncResult(
                "ed:edstem.org:101", "ed", (), (), execution=ExecutionMode.APPLY
            )
            transaction = SourceTransactionResult(
                source_result,
                StateCommitResult(new_state(profile="test", output_root=root), True),
            )
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.cli.load_config", return_value=self.configured_ed(root)), \
                 patch("canvas_ed_mcp.secrets.resolve_secret", return_value="synthetic-token"), \
                 patch("canvas_ed_mcp.providers.ed.EdProvider", return_value=provider), \
                 patch("canvas_ed_mcp.sync.engine.apply_configured_sources", return_value=RunTransactionResult((transaction,))) as apply, \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = main(["sync", "--profile", "test"])
            self.assertEqual(0, exit_code)
            apply.assert_called_once()
            report = json.loads(stdout.getvalue())
            self.assertTrue(report["sources"][0]["committed"])

    def test_apply_reports_committed_render_quality_issues_and_nonzero_exit(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(
                profile="test", config_file=root / "config.toml",
                state_file=root / "state.json", state_backup_file=root / "backup.json",
                lock_dir=root / ".locks", cache_dir=root / ".cache",
            )
            provider = Mock(instance="edstem.org")
            source_result = SourceSyncResult(
                "ed:edstem.org:101",
                "ed",
                (),
                (),
                issue_codes=("unsafe_image_url",),
                execution=ExecutionMode.APPLY,
            )
            transaction = SourceTransactionResult(
                source_result,
                StateCommitResult(new_state(profile="test", output_root=root), True),
            )
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.cli.load_config", return_value=self.configured_ed(root)), \
                 patch("canvas_ed_mcp.secrets.resolve_secret", return_value="synthetic-token"), \
                 patch("canvas_ed_mcp.providers.ed.EdProvider", return_value=provider), \
                 patch("canvas_ed_mcp.sync.engine.apply_configured_sources", return_value=RunTransactionResult((transaction,))), \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = main(["sync", "--profile", "test"])

            self.assertEqual(1, exit_code)
            report = json.loads(stdout.getvalue())
            self.assertTrue(report["sources"][0]["committed"])
            self.assertEqual(
                ["unsafe_image_url"],
                report["sources"][0]["issue_codes"],
            )

    def test_apply_run_failure_is_sanitized_without_false_source_result(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(
                profile="test", config_file=root / "config.toml",
                state_file=root / "state.json", state_backup_file=root / "backup.json",
                lock_dir=root / ".locks", cache_dir=root / ".cache",
            )
            provider = Mock(instance="edstem.org")
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.cli.load_config", return_value=self.configured_ed(root)), \
                 patch("canvas_ed_mcp.secrets.resolve_secret", return_value="synthetic-token"), \
                 patch("canvas_ed_mcp.providers.ed.EdProvider", return_value=provider), \
                 patch("canvas_ed_mcp.sync.engine.apply_configured_sources", side_effect=RuntimeError("secret-value")), \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = main(["sync", "--profile", "test"])
            self.assertEqual(1, exit_code)
            self.assertNotIn("secret-value", stdout.getvalue())
            report = json.loads(stdout.getvalue())
            self.assertEqual(["sync_run_failed"], report["run_issue_codes"])
            self.assertEqual([], report["sources"])

    def test_apply_reports_committed_prefix_when_later_source_fails(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = SimpleNamespace(
                profile="test", config_file=root / "config.toml",
                state_file=root / "state.json", state_backup_file=root / "backup.json",
                lock_dir=root / ".locks", cache_dir=root / ".cache",
            )
            config = self.configured_ed(root)
            second_source = CourseSource(
                provider="ed", instance="edstem.org", course_id="202",
                initial_mode=InitialSyncMode.IMPORT_ALL,
            )
            second_course = CourseConfig(
                key="ed:edstem.org:202", folder="TEST2000",
                confirmed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                sources=(second_source,),
            )
            config = config.model_copy(
                update={"courses": (*config.courses, second_course)}
            )
            provider = Mock(instance="edstem.org")
            source_result = SourceSyncResult(
                "ed:edstem.org:101", "ed", (), (), execution=ExecutionMode.APPLY
            )
            transaction = SourceTransactionResult(
                source_result,
                StateCommitResult(new_state(profile="test", output_root=root), True),
            )
            run = RunTransactionResult(
                (transaction,), "source_transaction_failed", 1
            )
            with patch("canvas_ed_mcp.cli.resolve_profile_paths", return_value=paths), \
                 patch("canvas_ed_mcp.cli.load_config", return_value=config), \
                 patch("canvas_ed_mcp.secrets.resolve_secret", return_value="synthetic-token"), \
                 patch("canvas_ed_mcp.providers.ed.EdProvider", return_value=provider), \
                 patch("canvas_ed_mcp.sync.engine.apply_configured_sources", return_value=run), \
                 patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = main(["sync", "--profile", "test"])
            self.assertEqual(1, exit_code)
            report = json.loads(stdout.getvalue())
            self.assertEqual(["source_transaction_failed"], report["run_issue_codes"])
            self.assertEqual(2, report["selected_source_count"])
            self.assertEqual(1, report["executed_source_count"])
            self.assertEqual(1, len(report["sources"]))
            self.assertTrue(report["sources"][0]["committed"])


if __name__ == "__main__":
    unittest.main()
