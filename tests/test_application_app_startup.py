from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from canvas_ed_mcp.application.app_startup import (
    StartupDestination,
    plan_app_startup,
)
from canvas_ed_mcp.config import AppConfig, save_config
from canvas_ed_mcp.paths import ProfilePaths


class AppStartupPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _paths(self, profile: str) -> ProfilePaths:
        return ProfilePaths(
            profile=profile,
            config_file=self.root / "config" / "profiles" / f"{profile}.toml",
            state_file=self.root / "state" / profile / "state-v4.json",
            state_backup_file=self.root / "state" / profile / "state-v4.backup.json",
            lock_dir=self.root / "state" / profile / "locks",
            log_dir=self.root / "logs" / profile,
            cache_dir=self.root / "cache" / profile,
        )

    def _save(self, profile: str) -> Path:
        path = self._paths(profile).config_file
        path.parent.mkdir(parents=True, exist_ok=True)
        output = self.root / "library" / profile
        output.mkdir(parents=True, exist_ok=True)
        save_config(AppConfig(profile=profile, output_root=output), path)
        return path

    def test_no_profile_routes_to_first_run_without_remote_work(self) -> None:
        plan = plan_app_startup(paths_resolver=self._paths)

        self.assertEqual(StartupDestination.FIRST_RUN, plan.destination)
        self.assertEqual("default", plan.requested_profile)
        self.assertEqual((), plan.profiles)

    def test_one_healthy_profile_routes_directly_to_shell(self) -> None:
        self._save("study")

        plan = plan_app_startup(paths_resolver=self._paths)

        self.assertEqual(StartupDestination.APP_SHELL, plan.destination)
        self.assertIsNotNone(plan.selected)
        self.assertEqual("study", plan.selected.profile)

    def test_multiple_profiles_route_to_profile_chooser_in_name_order(self) -> None:
        self._save("zeta")
        self._save("alpha")

        plan = plan_app_startup(paths_resolver=self._paths)

        self.assertEqual(StartupDestination.PROFILE_CHOOSER, plan.destination)
        self.assertEqual(["alpha", "zeta"], [item.profile for item in plan.profiles])
        self.assertIsNone(plan.selected)

    def test_corrupt_profile_is_visible_but_not_selected(self) -> None:
        path = self._paths("broken").config_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not = [valid", encoding="utf-8")

        plan = plan_app_startup(paths_resolver=self._paths)

        self.assertEqual(StartupDestination.PROFILE_CHOOSER, plan.destination)
        self.assertEqual("broken", plan.profiles[0].profile)
        self.assertFalse(plan.profiles[0].ready)

    def test_explicit_missing_profile_preserves_first_run_compatibility(self) -> None:
        plan = plan_app_startup(profile="named", paths_resolver=self._paths)

        self.assertEqual(StartupDestination.FIRST_RUN, plan.destination)
        self.assertEqual("named", plan.requested_profile)

    def test_explicit_healthy_profile_bypasses_chooser(self) -> None:
        config_path = self._save("named")

        plan = plan_app_startup(
            profile="named",
            config_path=config_path,
            paths_resolver=self._paths,
        )

        self.assertEqual(StartupDestination.APP_SHELL, plan.destination)
        self.assertEqual("named", plan.selected.profile)


if __name__ == "__main__":
    unittest.main()
