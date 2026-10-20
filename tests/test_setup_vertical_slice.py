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


class SetupVerticalSliceTests(unittest.TestCase):
    def run_package_code(self, code: str):
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

    def test_canvas_only_setup_selects_courses_individually_and_cancel_is_zero_write(self) -> None:
        result = self.run_package_code(
            """
            import json
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.config import CanvasPlatformConfig, ContentSelection, DownloadPolicy
            from canvas_ed_mcp.providers.base import CourseCandidate
            from canvas_ed_mcp.setup_flow import (
                CourseChoice,
                SetupRequest,
                build_setup_plan,
                commit_setup,
                discover_courses,
            )

            calls = {"canvas": 0, "ed": 0}
            catalog = (
                CourseCandidate("canvas", "canvas.example.edu", "101", "TEST2000", "Algorithms", "2026 S2"),
                CourseCandidate("canvas", "canvas.example.edu", "202", "TEST2000", "Algorithms Later", "2027 S1"),
            )
            def canvas_catalog():
                calls["canvas"] += 1
                return catalog
            def ed_catalog():
                calls["ed"] += 1
                raise AssertionError("disabled Ed was initialized")

            discovered = discover_courses({"canvas": canvas_catalog, "ed": ed_catalog}, {"canvas"})
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                request = SetupRequest(
                    profile="default",
                    output_root=base / "Courses",
                    canvas=CanvasPlatformConfig(
                        enabled=True,
                        base_url="https://canvas.example.edu/api/v1",
                        credential="env:SYNTHETIC_CANVAS_TOKEN",
                    ),
                    choices={
                        catalog[1].key: CourseChoice(
                            confirmed=True,
                            folder="TEST2000-2027S1",
                            content=ContentSelection(files=True, pages=True, lessons=False, resources=False),
                            policy=DownloadPolicy(max_file_bytes=1024, exclude_extensions=("mp4",)),
                        )
                    },
                )
                plan = build_setup_plan(request, discovered)
                config_path = base / "config.toml"
                committed = commit_setup(plan, config_path, confirmed=False)
                print(json.dumps({
                    "calls": calls,
                    "selected": [course.key for course in plan.config.courses],
                    "folder": plan.config.courses[0].folder,
                    "preview": plan.preview,
                    "committed": committed,
                    "config_exists": config_path.exists(),
                    "base_entries": list(base.iterdir()),
                }, default=str))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual({"canvas": 1, "ed": 0}, data["calls"])
        self.assertEqual(["canvas:canvas.example.edu:202"], data["selected"])
        self.assertEqual("TEST2000-2027S1", data["folder"])
        self.assertIn("2027 S1", data["preview"])
        self.assertIn("202", data["preview"])
        self.assertFalse(data["committed"])
        self.assertFalse(data["config_exists"])
        self.assertEqual([], data["base_entries"])

    def test_synthetic_canvas_setup_to_baseline_and_sync_dry_runs_writes_no_content_or_state(self) -> None:
        result = self.run_package_code(
            """
            import json
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.config import CanvasPlatformConfig
            from canvas_ed_mcp.dry_run import plan_baseline, plan_sync
            from canvas_ed_mcp.providers.base import CourseCandidate
            from canvas_ed_mcp.setup_flow import CourseChoice, SetupRequest, build_setup_plan, commit_setup

            candidate = CourseCandidate(
                "canvas", "canvas.example.edu", "303", "TEST3000", "Synthetic Systems", "2026 S2"
            )
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                config_path = base / "config" / "default.toml"
                output_root = base / "Courses"
                request = SetupRequest(
                    profile="default",
                    output_root=output_root,
                    canvas=CanvasPlatformConfig(
                        enabled=True,
                        base_url="https://canvas.example.edu/api/v1",
                        credential="env:SYNTHETIC_CANVAS_TOKEN",
                    ),
                    choices={candidate.key: CourseChoice(confirmed=True, folder="TEST3000")},
                )
                setup = build_setup_plan(request, {"canvas": (candidate,)})
                assert commit_setup(setup, config_path, confirmed=True)
                before = sorted(str(path.relative_to(base)) for path in base.rglob("*"))
                baseline = plan_baseline(setup.config)
                sync = plan_sync(setup.config, baseline_initialized=True)
                after = sorted(str(path.relative_to(base)) for path in base.rglob("*"))
                print(json.dumps({
                    "baseline": [action.operation for action in baseline.actions],
                    "sync": [action.operation for action in sync.actions],
                    "before": before,
                    "after": after,
                    "output_exists": output_root.exists(),
                }))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(["would_initialize_baseline"], data["baseline"])
        self.assertEqual(["would_scan_confirmed_course"], data["sync"])
        self.assertEqual(data["before"], data["after"])
        self.assertFalse(data["output_exists"])

    def test_interactive_wizard_defaults_to_no_and_cancel_writes_nothing(self) -> None:
        result = self.run_package_code(
            """
            import json
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.providers.base import CourseCandidate
            from canvas_ed_mcp.setup_flow import run_setup_wizard

            prompts = iter([
                "canvas",
                "https://canvas.example.edu/api/v1",
                "env:SYNTHETIC_CANVAS_TOKEN",
                "",
                "n",
                "y",
                "",
                "",
                "",
                "n",
            ])
            calls = {"secret": 0, "catalog": 0}
            output = []
            def secret_resolver(reference):
                calls["secret"] += 1
                assert reference == "env:SYNTHETIC_CANVAS_TOKEN"
                return "synthetic-token"
            def catalog_loader(base_url, token):
                calls["catalog"] += 1
                assert base_url == "https://canvas.example.edu/api/v1"
                assert token == "synthetic-token"
                return (
                    CourseCandidate("canvas", "canvas.example.edu", "101", "TEST2000", "Old", "2026 S2"),
                    CourseCandidate("canvas", "canvas.example.edu", "202", "TEST2000", "New", "2027 S1"),
                )

            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                committed = run_setup_wizard(
                    profile="default",
                    config_path=base / "config.toml",
                    input_fn=lambda prompt: next(prompts),
                    output_fn=output.append,
                    secret_resolver=secret_resolver,
                    canvas_catalog_loader=catalog_loader,
                    default_output_root=base / "Courses",
                )
                print(json.dumps({
                    "committed": committed,
                    "calls": calls,
                    "entries": list(base.iterdir()),
                    "output": output,
                }))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["committed"])
        self.assertEqual({"secret": 1, "catalog": 1}, data["calls"])
        self.assertEqual([], data["entries"])
        rendered = "\n".join(data["output"])
        self.assertIn("id=202", rendered)
        self.assertNotIn("synthetic-token", rendered)

    def test_interactive_wizard_blank_provider_cancels_before_secret_or_network(self) -> None:
        result = self.run_package_code(
            """
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.setup_flow import run_setup_wizard

            def forbidden(*args):
                raise AssertionError("cancelled setup used secret or network")
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                committed = run_setup_wizard(
                    profile="default",
                    config_path=base / "config.toml",
                    input_fn=lambda prompt: "",
                    output_fn=lambda message: None,
                    secret_resolver=forbidden,
                    canvas_catalog_loader=forbidden,
                    default_output_root=base / "Courses",
                )
                assert not committed
                assert list(base.iterdir()) == []
                print("cancelled")
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("cancelled", result.stdout.strip())

    def test_interactive_ed_only_persists_selected_source_mode_without_canvas_access(self) -> None:
        result = self.run_package_code(
            """
            import json
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.config import load_config
            from canvas_ed_mcp.providers.base import CourseCandidate
            from canvas_ed_mcp.setup_flow import run_setup_wizard

            prompts = iter([
                "ed", "", "env:SYNTHETIC_ED_TOKEN", "", "y", "import_all", "",
                "y", "", "", "", "y",
            ])
            calls = {"secret": [], "canvas": 0, "ed": 0}
            output = []
            def secret_resolver(reference):
                calls["secret"].append(reference)
                return "synthetic-ed-secret"
            def canvas_loader(*args):
                calls["canvas"] += 1
                raise AssertionError("Ed-only setup touched Canvas")
            def ed_loader(token):
                calls["ed"] += 1
                assert token == "synthetic-ed-secret"
                return (CourseCandidate(
                    "ed", "edstem.org", "ed-7", "SYNTH1000", "Synthetic", "2099 T1"
                ),)

            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                path = base / "config.toml"
                committed = run_setup_wizard(
                    profile="review", config_path=path,
                    input_fn=lambda prompt: next(prompts), output_fn=output.append,
                    secret_resolver=secret_resolver,
                    canvas_catalog_loader=canvas_loader,
                    ed_catalog_loader=ed_loader,
                    default_output_root=base / "Courses",
                )
                config = load_config(path)
                source = config.courses[0].sources[0]
                print(json.dumps({
                    "committed": committed, "calls": calls,
                    "provider": source.provider, "course_id": source.course_id,
                    "mode": source.initial_mode.value,
                    "content": config.courses[0].content.model_dump(),
                    "rendered": "\\n".join(output),
                }))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertTrue(data["committed"])
        self.assertEqual({"secret": ["env:SYNTHETIC_ED_TOKEN"], "canvas": 0, "ed": 1}, data["calls"])
        self.assertEqual(("ed", "ed-7", "import_all"), (data["provider"], data["course_id"], data["mode"]))
        self.assertTrue(data["content"]["lessons"])
        self.assertTrue(data["content"]["resources"])
        self.assertFalse(data["content"]["files"])
        self.assertNotIn("synthetic-ed-secret", data["rendered"])
        self.assertNotIn("SYNTHETIC_ED_TOKEN", data["rendered"])

    def test_interactive_both_explicitly_maps_sources_and_dry_run_is_zero_write(self) -> None:
        result = self.run_package_code(
            """
            import json
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.providers.base import CourseCandidate
            from canvas_ed_mcp.setup_flow import run_setup_wizard

            prompts = iter([
                "both", "https://canvas.example.edu/api/v1", "env:SYNTHETIC_CANVAS_TOKEN",
                "", "env:SYNTHETIC_ED_TOKEN", "",
                "y", "", "logical-synth",
                "y", "import_all", "logical-synth",
                "y", "SYNTH1000", "", "",
            ])
            calls = {"secret": [], "canvas": 0, "ed": 0}
            output = []
            def secret_resolver(reference):
                calls["secret"].append(reference)
                return "secret-for-" + reference.rsplit("_", 1)[-1]
            def canvas_loader(base_url, token):
                calls["canvas"] += 1
                return (CourseCandidate(
                    "canvas", "canvas.example.edu", "canvas-1", "SYNTH1000", "Same", "2099 T1"
                ),)
            def ed_loader(token):
                calls["ed"] += 1
                return (CourseCandidate(
                    "ed", "edstem.org", "ed-1", "SYNTH1000", "Same", "2099 T1"
                ),)

            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                committed = run_setup_wizard(
                    profile="review", config_path=base / "config.toml",
                    input_fn=lambda prompt: next(prompts), output_fn=output.append,
                    secret_resolver=secret_resolver,
                    canvas_catalog_loader=canvas_loader,
                    ed_catalog_loader=ed_loader,
                    default_output_root=base / "Courses", dry_run=True,
                )
                rendered = "\\n".join(output)
                print(json.dumps({
                    "committed": committed, "calls": calls,
                    "entries": list(base.iterdir()), "rendered": rendered,
                }))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["committed"])
        self.assertEqual(1, data["calls"]["canvas"])
        self.assertEqual(1, data["calls"]["ed"])
        self.assertEqual([], data["entries"])
        self.assertIn("canvas@canvas.example.edu id=canvas-1 [initial=baseline_from_now]", data["rendered"])
        self.assertIn("ed@edstem.org id=ed-1 [initial=import_all]", data["rendered"])
        self.assertNotIn("SYNTHETIC_CANVAS_TOKEN", data["rendered"])
        self.assertNotIn("SYNTHETIC_ED_TOKEN", data["rendered"])

    def test_setup_rejects_existing_config_before_input_or_network(self) -> None:
        result = self.run_package_code(
            """
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.setup_flow import SetupConflictError, run_setup_wizard

            def forbidden(*args):
                raise AssertionError("existing profile reached input, secret, or network")
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "config.toml"
                path.write_bytes(b"existing-profile")
                try:
                    run_setup_wizard(
                        profile="review", config_path=path,
                        input_fn=forbidden, output_fn=forbidden,
                        secret_resolver=forbidden, canvas_catalog_loader=forbidden,
                        default_output_root=Path(temporary) / "Courses",
                    )
                except SetupConflictError:
                    pass
                else:
                    raise AssertionError("existing profile was not rejected")
                assert path.read_bytes() == b"existing-profile"
                print("protected")
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("protected", result.stdout.strip())

    def test_catalog_control_characters_are_rejected_before_confirmation(self) -> None:
        result = self.run_package_code(
            """
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.providers.base import CourseCandidate
            from canvas_ed_mcp.setup_flow import run_setup_wizard

            prompts = iter(["canvas", "https://canvas.example.edu/api/v1", "env:TOKEN", ""])
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                try:
                    run_setup_wizard(
                        profile="review", config_path=base / "config.toml",
                        input_fn=lambda prompt: next(prompts), output_fn=lambda value: None,
                        secret_resolver=lambda reference: "synthetic",
                        canvas_catalog_loader=lambda base_url, token: (CourseCandidate(
                            "canvas", "canvas.example.edu", "1", "SAFE", "forged\\nSelect me", None
                        ),),
                        default_output_root=base / "Courses",
                    )
                except ValueError:
                    pass
                else:
                    raise AssertionError("control-bearing catalog text was accepted")
                assert not (base / "config.toml").exists()
                print("rejected")
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("rejected", result.stdout.strip())

    def test_committed_setup_reports_stable_cleanup_pending_marker(self) -> None:
        result = self.run_package_code(
            """
            import tempfile
            from pathlib import Path
            from unittest.mock import patch
            from canvas_ed_mcp.config import AppConfig
            from canvas_ed_mcp.setup_flow import SetupPlan, commit_setup

            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                plan = SetupPlan(AppConfig(profile="review", output_root=root / "Courses"), "preview")
                output = []
                with patch("canvas_ed_mcp.setup_flow.save_new_config", return_value=True):
                    committed = commit_setup(
                        plan, root / "config.toml", confirmed=True,
                        warning_fn=output.append,
                    )
                assert committed
                assert output == ["setup warning (config_cleanup_pending)"]
                print("reported")
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("reported", result.stdout.strip())

    def test_interactive_both_same_name_stays_separate_and_final_cancel_is_zero_write(self) -> None:
        result = self.run_package_code(
            """
            import json
            import tempfile
            from pathlib import Path
            from canvas_ed_mcp.providers.base import CourseCandidate
            from canvas_ed_mcp.setup_flow import run_setup_wizard

            prompts = iter([
                "both", "https://canvas.example.edu/api/v1", "env:SYNTHETIC_CANVAS_TOKEN",
                "", "env:SYNTHETIC_ED_TOKEN", "",
                "y", "baseline_from_now", "logical-canvas",
                "y", "import_all", "logical-ed",
                "y", "SAME1000-Canvas", "", "",
                "y", "SAME1000-Ed", "", "",
                "n",
            ])
            calls = {"secret": [], "canvas": 0, "ed": 0}
            output = []

            def secret_resolver(reference):
                calls["secret"].append(reference)
                values = {
                    "env:SYNTHETIC_CANVAS_TOKEN": "synthetic-canvas-secret",
                    "env:SYNTHETIC_ED_TOKEN": "synthetic-ed-secret",
                }
                return values[reference]

            def canvas_loader(base_url, token):
                calls["canvas"] += 1
                assert base_url == "https://canvas.example.edu/api/v1"
                assert token == "synthetic-canvas-secret"
                return (CourseCandidate(
                    "canvas", "canvas.example.edu", "canvas-1", "SAME1000",
                    "Identical Display Name", "2099 T1",
                ),)

            def ed_loader(token):
                calls["ed"] += 1
                assert token == "synthetic-ed-secret"
                return (CourseCandidate(
                    "ed", "edstem.org", "ed-1", "SAME1000",
                    "Identical Display Name", "2099 T1",
                ),)

            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                committed = run_setup_wizard(
                    profile="review",
                    config_path=base / "config.toml",
                    input_fn=lambda prompt: next(prompts),
                    output_fn=output.append,
                    secret_resolver=secret_resolver,
                    canvas_catalog_loader=canvas_loader,
                    ed_catalog_loader=ed_loader,
                    default_output_root=base / "Courses",
                )
                print(json.dumps({
                    "committed": committed,
                    "calls": calls,
                    "entries": list(base.iterdir()),
                    "rendered": "\\n".join(output),
                }))
            """
        )
        self.assertEqual(0, result.returncode, result.stderr)
        data = json.loads(result.stdout)
        self.assertFalse(data["committed"])
        self.assertEqual(
            {
                "secret": [
                    "env:SYNTHETIC_CANVAS_TOKEN",
                    "env:SYNTHETIC_ED_TOKEN",
                ],
                "canvas": 1,
                "ed": 1,
            },
            data["calls"],
        )
        self.assertEqual([], data["entries"])
        self.assertIn("- logical-canvas -> SAME1000-Canvas", data["rendered"])
        self.assertIn("- logical-ed -> SAME1000-Ed", data["rendered"])
        self.assertIn(
            "canvas@canvas.example.edu id=canvas-1 [initial=baseline_from_now]",
            data["rendered"],
        )
        self.assertIn(
            "ed@edstem.org id=ed-1 [initial=import_all]",
            data["rendered"],
        )
        for private_value in (
            "synthetic-canvas-secret",
            "synthetic-ed-secret",
            "SYNTHETIC_CANVAS_TOKEN",
            "SYNTHETIC_ED_TOKEN",
        ):
            self.assertNotIn(private_value, data["rendered"])


if __name__ == "__main__":
    unittest.main()
