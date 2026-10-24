from __future__ import annotations

import dataclasses
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    ContentSelection,
    CourseConfig,
    CourseSource,
    DownloadPolicy,
    EdPlatformConfig,
    PlatformsConfig,
    RetiredCourseSource,
    load_config,
    save_config,
)
from canvas_ed_mcp.configure_flow import (
    ConfigureChangeKind,
    ConfigureConflictError,
    InitialSyncMode,
    build_configure_plan,
    commit_configure,
    run_configure_wizard,
)
from canvas_ed_mcp.providers.base import CourseCandidate
from canvas_ed_mcp.course_mapping import SourceIdentity
from canvas_ed_mcp.cli import main


CONFIRMED_AT = datetime(2026, 10, 13, tzinfo=timezone.utc)


def course(
    course_id: str,
    *,
    folder: str | None = None,
    content: ContentSelection | None = None,
    policy: DownloadPolicy | None = None,
    confirmed_at: datetime = CONFIRMED_AT,
) -> CourseConfig:
    return CourseConfig(
        key=f"canvas:canvas.example.edu:{course_id}",
        folder=folder or f"TEST{course_id}",
        confirmed_at=confirmed_at,
        sources=(
            CourseSource(
                provider="canvas",
                instance="canvas.example.edu",
                course_id=course_id,
                code=f"TEST{course_id}",
                name=f"Synthetic Course {course_id}",
                term="2026 S2",
            ),
        ),
        content=content or ContentSelection(),
        policy=policy or DownloadPolicy(),
    )


def config(output_root: Path, courses: tuple[CourseConfig, ...]) -> AppConfig:
    return AppConfig(
        profile="default",
        output_root=output_root,
        platforms=PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_CANVAS_TOKEN",
            )
        ),
        courses=courses,
    )


class ConfigurePlanningTests(unittest.TestCase):
    def test_add_remove_change_diff_is_deterministic_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            unchanged = course("101")
            removed = course("202")
            current = config(output, (unchanged, removed, course("303")))
            changed = course(
                "303",
                folder="RENAMED303",
                policy=DownloadPolicy(max_file_bytes=2048),
                confirmed_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
            )
            added = course("404")
            proposed = config(output, (added, changed, unchanged))

            plan = build_configure_plan(
                current,
                proposed,
                new_course_modes={added.key: InitialSyncMode.IMPORT_ALL},
            )

        self.assertEqual(
            (
                ConfigureChangeKind.ADD,
                ConfigureChangeKind.REMOVE,
                ConfigureChangeKind.CHANGE,
            ),
            tuple(change.kind for change in plan.changes),
        )
        self.assertEqual((added.key, removed.key, changed.key), plan.changed_keys)
        self.assertEqual(("folder", "policy"), plan.changes[2].fields)
        self.assertEqual(InitialSyncMode.IMPORT_ALL, plan.initial_sync[0].mode)
        configured_added = next(item for item in plan.config.courses if item.key == added.key)
        self.assertEqual(
            InitialSyncMode.IMPORT_ALL,
            configured_added.sources[0].initial_mode,
        )
        self.assertIn(f"+ {added.key}", plan.preview)
        self.assertIn(f"- {removed.key}", plan.preview)
        self.assertIn("local files and historical state retained", plan.preview)
        self.assertIn("folder, policy", plan.preview)
        self.assertNotIn("SYNTHETIC_CANVAS_TOKEN", plan.preview)

    def test_multiple_added_courses_require_individual_initial_sync_modes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            current = config(output, ())
            first = course("101")
            second = course("202")
            proposed = config(output, (first, second))

            plan = build_configure_plan(
                current,
                proposed,
                new_course_modes={
                    first.key: InitialSyncMode.BASELINE_FROM_NOW,
                    second.key: InitialSyncMode.IMPORT_ALL,
                },
            )
            self.assertEqual(
                (
                    (first.key, InitialSyncMode.BASELINE_FROM_NOW),
                    (second.key, InitialSyncMode.IMPORT_ALL),
                ),
                tuple((item.course_key, item.mode) for item in plan.initial_sync),
            )

            for modes in (
                {},
                {first.key: InitialSyncMode.BASELINE_FROM_NOW},
                {
                    first.key: InitialSyncMode.BASELINE_FROM_NOW,
                    second.key: InitialSyncMode.IMPORT_ALL,
                    "unknown": InitialSyncMode.BASELINE_FROM_NOW,
                },
            ):
                with self.subTest(modes=modes), self.assertRaises(ValueError):
                    build_configure_plan(
                        current,
                        proposed,
                        new_course_modes=modes,
                    )

    def test_initial_sync_mode_must_be_typed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            added = course("101")
            with self.assertRaises(ValueError):
                build_configure_plan(
                    config(output, ()),
                    config(output, (added,)),
                    new_course_modes={added.key: "baseline_from_now"},  # type: ignore[dict-item]
                )

    def test_noop_preserves_existing_course_and_confirmation_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            existing = course("101")
            reconfirmed = course(
                "101",
                confirmed_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
            )
            current = config(output, (existing,))
            proposed = config(output, (reconfirmed,))
            plan = build_configure_plan(current, proposed, new_course_modes={})

        self.assertFalse(plan.has_changes)
        self.assertEqual((), plan.changes)
        self.assertEqual(existing, plan.config.courses[0])
        self.assertIn("No operational changes", plan.preview)

    def test_platform_change_is_reported_without_exposing_credential_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            current = config(output, ())
            proposed = current.model_copy(
                update={
                    "platforms": PlatformsConfig(
                        canvas=CanvasPlatformConfig(
                            enabled=True,
                            base_url="https://canvas.other.example/api/v1",
                            credential="keyring:canvas-ed-mcp/default/canvas-private",
                        )
                    )
                }
            )
            plan = build_configure_plan(current, proposed, new_course_modes={})

        self.assertTrue(plan.platforms_changed)
        self.assertTrue(plan.has_changes)
        self.assertIn("Platform settings: changed", plan.preview)
        self.assertNotIn("canvas-private", plan.preview)

    def test_profile_or_output_root_change_requires_separate_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            current = config(base / "Courses", ())
            cases = (
                current.model_copy(update={"profile": "other"}),
                current.model_copy(update={"output_root": base / "Other"}),
            )
            for proposed in cases:
                with self.subTest(proposed=proposed), self.assertRaises(ValueError):
                    build_configure_plan(current, proposed, new_course_modes={})

    def test_existing_source_move_requires_one_time_next_sync_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            existing = course("101")
            current = config(output, (existing,))
            moved = existing.model_copy(update={"key": "COMP101-2026-S2"})
            proposed = config(output, (moved,))
            identity = SourceIdentity.from_source(existing.sources[0])

            with self.assertRaisesRegex(ValueError, "next-sync mode"):
                build_configure_plan(current, proposed, new_source_modes={})

            plan = build_configure_plan(
                current,
                proposed,
                new_source_modes={identity: InitialSyncMode.IMPORT_ALL},
            )

        self.assertEqual(
            (ConfigureChangeKind.ADD, ConfigureChangeKind.REMOVE),
            tuple(change.kind for change in plan.changes),
        )
        self.assertEqual(1, len(plan.initial_sync))
        moved_source = plan.config.courses[0].sources[0]
        self.assertEqual(InitialSyncMode.IMPORT_ALL, moved_source.initial_mode)
        self.assertEqual(1, moved_source.reconcile_generation)

    def test_existing_source_mode_change_increments_generation_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            existing = course("101")
            identity = SourceIdentity.from_source(existing.sources[0])
            proposed_source = existing.sources[0].model_copy(
                update={"initial_mode": InitialSyncMode.IMPORT_ALL}
            )
            proposed = config(
                output,
                (existing.model_copy(update={"sources": (proposed_source,)}),),
            )

            plan = build_configure_plan(
                config(output, (existing,)),
                proposed,
                new_source_modes={identity: InitialSyncMode.IMPORT_ALL},
            )

        updated = plan.config.courses[0].sources[0]
        self.assertEqual(InitialSyncMode.IMPORT_ALL, updated.initial_mode)
        self.assertEqual(1, updated.reconcile_generation)
        self.assertIn("one-time, then incremental", plan.preview)

    def test_reactivate_inactive_source_requires_explicit_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            inactive_course = course("101")
            inactive_source = inactive_course.sources[0].model_copy(
                update={"reconcile_generation": 4}
            )
            current = config(output, ()).model_copy(
                update={
                    "retired_sources": (
                        RetiredCourseSource(
                            source=inactive_source,
                            former_course_key=inactive_course.key,
                        ),
                    )
                }
            )
            restored_course = inactive_course.model_copy(
                update={"sources": (inactive_source,)}
            )
            proposed = config(output, (restored_course,))
            identity = SourceIdentity.from_source(inactive_source)

            with self.assertRaisesRegex(ValueError, "next-sync mode"):
                build_configure_plan(current, proposed, new_source_modes={})
            plan = build_configure_plan(
                current,
                proposed,
                new_source_modes={identity: InitialSyncMode.BASELINE_FROM_NOW},
            )

        restored = plan.config.courses[0].sources[0]
        self.assertEqual(5, restored.reconcile_generation)
        self.assertEqual((), plan.config.retired_sources)

    def test_change_records_are_immutable_and_well_formed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            added = course("101")
            plan = build_configure_plan(
                config(output, ()),
                config(output, (added,)),
                new_course_modes={added.key: InitialSyncMode.BASELINE_FROM_NOW},
            )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            plan.changes[0].fields = ("folder",)  # type: ignore[misc]


class ConfigureCommitTests(unittest.TestCase):
    def test_cancel_and_commit_never_touch_course_output_or_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            removed = course("101")
            current = config(output, (removed,))
            proposed = config(output, ())
            config_path = base / "config" / "default.toml"
            state_path = base / "state" / "state-v4.json"
            course_file = output / removed.folder / "notes.txt"
            state_path.parent.mkdir(parents=True)
            course_file.parent.mkdir(parents=True)
            state_path.write_bytes(b"historical-state")
            course_file.write_bytes(b"user-course-material")
            save_config(current, config_path)
            original_config = config_path.read_bytes()
            plan = build_configure_plan(current, proposed, new_course_modes={})

            self.assertFalse(commit_configure(plan, config_path, confirmed=False))
            self.assertEqual(original_config, config_path.read_bytes())
            self.assertEqual(b"historical-state", state_path.read_bytes())
            self.assertEqual(b"user-course-material", course_file.read_bytes())

            self.assertTrue(commit_configure(plan, config_path, confirmed=True))
            committed = load_config(config_path)
            self.assertEqual((), committed.courses)
            self.assertEqual(1, len(committed.retired_sources))
            self.assertEqual(removed.key, committed.retired_sources[0].former_course_key)
            self.assertEqual(removed.sources[0], committed.retired_sources[0].source)
            self.assertEqual(b"historical-state", state_path.read_bytes())
            self.assertEqual(b"user-course-material", course_file.read_bytes())

    def test_commit_rejects_config_changed_after_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            current = config(output, (course("101"),))
            proposed = config(output, ())
            path = base / "config.toml"
            save_config(current, path)
            plan = build_configure_plan(current, proposed, new_course_modes={})
            concurrent = current.model_copy(
                update={
                    "platforms": PlatformsConfig(
                        canvas=CanvasPlatformConfig(
                            enabled=True,
                            base_url="https://canvas.other.example/api/v1",
                            credential="env:SYNTHETIC_OTHER_TOKEN",
                        )
                    )
                }
            )
            save_config(concurrent, path)

            with self.assertRaises(ConfigureConflictError):
                commit_configure(plan, path, confirmed=True)
            self.assertEqual(concurrent, load_config(path))


class ConfigureWizardTests(unittest.TestCase):
    @staticmethod
    def answers(*values: str):
        iterator = iter(values)
        return lambda _prompt: next(iterator)

    def test_default_retention_dry_run_does_not_resolve_or_discover(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.toml"
            current = config(Path(temporary) / "Courses", (course("101"),))
            save_config(current, path)
            original = path.read_bytes()
            calls: list[str] = []

            committed = run_configure_wizard(
                profile="default",
                config_path=path,
                input_fn=self.answers("canvas", "", "", ""),
                output_fn=lambda _line: None,
                secret_resolver=lambda _reference: calls.append("secret") or "token",
                canvas_catalog_loader=lambda _url, _token: calls.append("canvas") or (),
                ed_catalog_loader=lambda _token: calls.append("ed") or (),
                dry_run=True,
            )

            self.assertFalse(committed)
            self.assertEqual([], calls)
            self.assertEqual(original, path.read_bytes())

    def test_detach_one_side_of_mixed_course_is_preview_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "config.toml"
            canvas_course = course("101")
            ed_source = CourseSource(
                provider="ed",
                instance="edstem.org",
                course_id="202",
                code="TEST101",
                initial_mode=InitialSyncMode.IMPORT_ALL,
            )
            mixed = canvas_course.model_copy(
                update={"sources": canvas_course.sources + (ed_source,)}
            )
            current = AppConfig(
                profile="default",
                output_root=root / "Courses",
                platforms=PlatformsConfig(
                    canvas=config(root / "Courses", ()).platforms.canvas,
                    ed=EdPlatformConfig(
                        enabled=True,
                        credential="env:SYNTHETIC_ED_TOKEN",
                    ),
                ),
                courses=(mixed,),
            )
            save_config(current, path)
            output: list[str] = []

            committed = run_configure_wizard(
                profile="default",
                config_path=path,
                input_fn=self.answers("ed", "y", "", ""),
                output_fn=output.append,
                secret_resolver=lambda _reference: self.fail("secret resolution"),
                canvas_catalog_loader=lambda _url, _token: self.fail("Canvas discovery"),
                ed_catalog_loader=lambda _token: self.fail("Ed discovery"),
                dry_run=True,
            )

            self.assertFalse(committed)
            self.assertEqual(current, load_config(path))
            self.assertIn("ed@edstem.org", "\n".join(output))

    def test_enable_ed_and_attach_exact_candidate_to_existing_course(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "config.toml"
            existing = course("101")
            save_config(config(root / "Courses", (existing,)), path)
            candidate = CourseCandidate(
                provider="ed",
                instance="edstem.org",
                course_id="202",
                code="TEST101",
                name="Synthetic Ed Course",
                term="2026 S2",
            )
            calls: list[str] = []

            committed = run_configure_wizard(
                profile="default",
                config_path=path,
                input_fn=self.answers(
                    "ed", "y", "", "", "y", "import_all", existing.key, "y", "", "", "y"
                ),
                output_fn=lambda _line: None,
                secret_resolver=lambda reference: calls.append(reference) or "token",
                canvas_catalog_loader=lambda _url, _token: self.fail("Canvas discovery"),
                ed_catalog_loader=lambda token: (candidate,) if token == "token" else (),
            )

            self.assertTrue(committed)
            updated = load_config(path)
            self.assertTrue(updated.platforms.ed.enabled)
            self.assertEqual(1, len(calls))
            self.assertEqual(("canvas", "ed"), tuple(
                source.provider for source in updated.courses[0].sources
            ))
            self.assertEqual(
                InitialSyncMode.IMPORT_ALL,
                updated.courses[0].sources[1].initial_mode,
            )

    def test_edit_preserves_initial_mode_and_policy_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "config.toml"
            existing = course(
                "101",
                policy=DownloadPolicy(
                    max_file_bytes=5 * 1024 * 1024,
                    include_extensions=(".pdf",),
                    exclude_extensions=(".mov",),
                    include_mime=("application/pdf",),
                ),
            )
            existing = existing.model_copy(
                update={
                    "sources": (
                        existing.sources[0].model_copy(
                            update={"initial_mode": InitialSyncMode.IMPORT_ALL}
                        ),
                    )
                }
            )
            save_config(config(root / "Courses", (existing,)), path)

            committed = run_configure_wizard(
                profile="default",
                config_path=path,
                input_fn=self.answers("canvas", "", "", "y", "RENAMED", "", "7", "y"),
                output_fn=lambda _line: None,
                secret_resolver=lambda _reference: self.fail("secret resolution"),
                canvas_catalog_loader=lambda _url, _token: self.fail("discovery"),
            )

            self.assertTrue(committed)
            updated = load_config(path).courses[0]
            self.assertEqual("RENAMED", updated.folder)
            self.assertEqual(InitialSyncMode.IMPORT_ALL, updated.sources[0].initial_mode)
            self.assertEqual(7 * 1024 * 1024, updated.policy.max_file_bytes)
            self.assertEqual((".pdf",), updated.policy.include_extensions)
            self.assertEqual((".mov",), updated.policy.exclude_extensions)
            self.assertEqual(("application/pdf",), updated.policy.include_mime)

    def test_one_side_edit_preserves_other_provider_content(self) -> None:
        for selected_provider, selected_content in (
            ("canvas", "files"),
            ("ed", "lessons"),
        ):
            with self.subTest(provider=selected_provider), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / "config.toml"
                canvas_course = course("101")
                mixed = canvas_course.model_copy(
                    update={
                        "sources": canvas_course.sources
                        + (
                            CourseSource(
                                provider="ed",
                                instance="edstem.org",
                                course_id="202",
                                code="TEST101",
                            ),
                        )
                    }
                )
                current = AppConfig(
                    profile="default",
                    output_root=root / "Courses",
                    platforms=PlatformsConfig(
                        canvas=config(root / "Courses", ()).platforms.canvas,
                        ed=EdPlatformConfig(
                            enabled=True,
                            credential="env:SYNTHETIC_ED_TOKEN",
                        ),
                    ),
                    courses=(mixed,),
                )
                save_config(current, path)

                self.assertTrue(
                    run_configure_wizard(
                        profile="default",
                        config_path=path,
                        input_fn=self.answers(
                            selected_provider,
                            "",
                            "",
                            "y",
                            "",
                            selected_content,
                            "",
                            "y",
                        ),
                        output_fn=lambda _line: None,
                        secret_resolver=lambda _reference: self.fail("secret resolution"),
                        canvas_catalog_loader=lambda _url, _token: self.fail("discovery"),
                        ed_catalog_loader=lambda _token: self.fail("discovery"),
                    )
                )
                updated = load_config(path).courses[0].content
                if selected_provider == "canvas":
                    self.assertTrue(updated.files)
                    self.assertFalse(updated.pages)
                    self.assertTrue(updated.lessons)
                    self.assertTrue(updated.resources)
                else:
                    self.assertTrue(updated.files)
                    self.assertTrue(updated.pages)
                    self.assertTrue(updated.lessons)
                    self.assertFalse(updated.resources)

    def test_blank_limit_edit_preserves_exact_existing_bytes(self) -> None:
        for limit in (None, 512 * 1024, 5 * 1024 * 1024 + 123):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / "config.toml"
                existing = course(
                    "101", policy=DownloadPolicy(max_file_bytes=limit)
                )
                current = config(root / "Courses", (existing,))
                save_config(current, path)

                output: list[str] = []
                self.assertTrue(
                    run_configure_wizard(
                        profile="default",
                        config_path=path,
                        input_fn=self.answers(
                            "canvas", "", "", "y", "RENAMED", "", "", "y"
                        ),
                        output_fn=output.append,
                        secret_resolver=lambda _reference: self.fail("secret resolution"),
                        canvas_catalog_loader=lambda _url, _token: self.fail("discovery"),
                    )
                )
                updated = load_config(path).courses[0]
                self.assertEqual("RENAMED", updated.folder)
                self.assertEqual(limit, updated.policy.max_file_bytes)

    def test_confirmed_noop_reports_unchanged_without_rewriting_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "config.toml"
            save_config(config(root / "Courses", (course("101"),)), path)
            before = path.read_bytes()
            output: list[str] = []

            succeeded = run_configure_wizard(
                profile="default",
                config_path=path,
                input_fn=self.answers("canvas", "", "", ""),
                output_fn=output.append,
                secret_resolver=lambda _reference: self.fail("secret resolution"),
                canvas_catalog_loader=lambda _url, _token: self.fail("discovery"),
            )

            self.assertTrue(succeeded)
            self.assertEqual(before, path.read_bytes())
            self.assertIn("No changes; configuration remains unchanged.", output)

    def test_cli_sanitizes_unexpected_provider_failure(self) -> None:
        stderr = io.StringIO()
        with patch(
            "canvas_ed_mcp.configure_flow.run_configure_wizard",
            side_effect=RuntimeError("secret-value-from-provider"),
        ), redirect_stderr(stderr):
            exit_code = main(
                [
                    "configure",
                    "--profile",
                    "default",
                    "--config",
                    "synthetic.toml",
                    "--dry-run",
                ]
            )

        self.assertEqual(2, exit_code)
        self.assertEqual("configure failed (provider_unavailable)", stderr.getvalue().strip())
        self.assertNotIn("secret-value", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
