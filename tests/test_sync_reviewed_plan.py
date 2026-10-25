from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
import unittest

from canvas_ed_mcp.config import (
    AppConfig,
    CourseConfig,
    CourseSource,
    EdPlatformConfig,
    PlatformsConfig,
    save_config,
)
from canvas_ed_mcp.providers.base import (
    CategoryCoverage,
    ContentBatch,
    CoverageStatus,
)
from canvas_ed_mcp.renderers.html import RenderedHtml
from canvas_ed_mcp.sync.locking import SyncLock
from canvas_ed_mcp.sync.model import RemoteItem, RemoteKind, SourceKey
from canvas_ed_mcp.sync.reviewed import (
    apply_reviewed_sync_plan,
    build_reviewed_sync_plan,
)
from canvas_ed_mcp.sync.state import new_state, save_state


class _Provider:
    provider_name = "ed"
    instance = "ed.invalid"
    supported_kinds = frozenset({RemoteKind.LESSON, RemoteKind.RESOURCE})

    def __init__(self, items: tuple[RemoteItem, ...]) -> None:
        self.items = items
        self.list_calls = 0
        self.binary_calls = 0
        self.render_calls = 0
        self.synthetic_secret = "synthetic-token-must-not-leak"
        self._current: tuple[RemoteItem, ...] = ()

    def list_items(
        self, course_id: str, *, kinds: frozenset[RemoteKind]
    ) -> ContentBatch:
        self.list_calls += 1
        self._current = tuple(item for item in self.items if item.key.kind in kinds)
        return ContentBatch(
            self.provider_name,
            self.instance,
            course_id,
            kinds,
            tuple(
                CategoryCoverage(kind, CoverageStatus.COMPLETE)
                for kind in sorted(kinds, key=lambda value: value.value)
            ),
            self._current,
            (),
        )

    @contextmanager
    def open_binary(self, item: RemoteItem, *, chunk_size: int = 64 * 1024):
        if item not in self._current:
            raise ValueError("item was not produced by this provider discovery")
        self.binary_calls += 1
        yield iter((b"reviewed ", b"content"))

    def render_html(self, item: RemoteItem) -> RenderedHtml:
        self.render_calls += 1
        return RenderedHtml("<!doctype html><p>reviewed</p>", False, ())


class _FailingDiscoveryProvider(_Provider):
    def list_items(
        self, course_id: str, *, kinds: frozenset[RemoteKind]
    ) -> ContentBatch:
        self.list_calls += 1
        raise RuntimeError("synthetic discovery failure")


class _ConcurrentDiscoveryProvider(_Provider):
    def __init__(self) -> None:
        super().__init__(())
        self._lock = Lock()
        self._overlap = Event()
        self._active = 0
        self.max_active = 0

    def list_items(
        self, course_id: str, *, kinds: frozenset[RemoteKind]
    ) -> ContentBatch:
        with self._lock:
            self.list_calls += 1
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            if self._active >= 2:
                self._overlap.set()
        self._overlap.wait(timeout=0.5)
        with self._lock:
            self._active -= 1
        return ContentBatch(
            self.provider_name,
            self.instance,
            course_id,
            kinds,
            tuple(
                CategoryCoverage(kind, CoverageStatus.COMPLETE)
                for kind in sorted(kinds, key=lambda value: value.value)
            ),
            (),
            (),
        )


def _course() -> tuple[CourseConfig, CourseSource]:
    source = CourseSource(
        provider="ed",
        instance="ed.invalid",
        course_id="course-1",
        initial_mode="import_all",
    )
    course = CourseConfig(
        key="logical-1",
        folder="COMP0001",
        confirmed_at=datetime.now(timezone.utc),
        sources=(source,),
    )
    return course, source


def _item() -> RemoteItem:
    return RemoteItem(
        SourceKey(
            "ed", "ed.invalid", "course-1", RemoteKind.RESOURCE, "resource-1"
        ),
        "revision-1",
        "notes.pdf",
        size_bytes=16,
        media_type="application/pdf",
    )


class ReviewedSyncPlanTests(unittest.TestCase):
    def _fixture(self, root: Path):
        course, source = _course()
        config_path = root / "profile.toml"
        state_path = root / "state.json"
        backup_path = root / "state.backup.json"
        lock_dir = root / ".locks"
        cache_dir = root / ".cache"
        save_config(
            AppConfig(
                profile="test",
                output_root=root,
                platforms=PlatformsConfig(
                    ed=EdPlatformConfig(
                        enabled=True,
                        credential="keyring:canvas-ed-mcp/test/ed",
                    )
                ),
                courses=(course,),
            ),
            config_path,
        )
        provider = _Provider((_item(),))
        plan = build_reviewed_sync_plan(
            profile="test",
            config_path=config_path,
            state_path=state_path,
            output_root=root,
            selections=((course, source),),
            providers={("ed", "ed.invalid"): provider},
        )
        return plan, provider, config_path, state_path, backup_path, lock_dir, cache_dir

    def test_apply_executes_reviewed_batch_without_second_discovery(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (
                plan,
                provider,
                config_path,
                state_path,
                backup_path,
                lock_dir,
                cache_dir,
            ) = self._fixture(root)

            self.assertEqual(1, provider.list_calls)
            self.assertEqual(0, provider.binary_calls)
            self.assertEqual("would_fetch", plan.sources[0].preview.items[0].action)

            result = apply_reviewed_sync_plan(
                plan,
                expected_plan_digest=plan.digest,
                providers={("ed", "ed.invalid"): provider},
                config_path=config_path,
                state_path=state_path,
                backup_path=backup_path,
                lock_dir=lock_dir,
                cache_dir=cache_dir,
            )

            self.assertTrue(result.committed)
            self.assertEqual(1, provider.list_calls)
            self.assertEqual(1, provider.binary_calls)
            self.assertEqual("install_new", result.transactions[0].source_result.items[0].action)
            self.assertTrue(any(path.is_file() for path in (root / "COMP0001").rglob("*")))

    def test_independent_sources_discover_concurrently_but_keep_config_order(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            selections = []
            courses = []
            for index in (1, 2):
                source = CourseSource(
                    provider="ed",
                    instance="ed.invalid",
                    course_id=f"course-{index}",
                    initial_mode="import_all",
                )
                course = CourseConfig(
                    key=f"logical-{index}",
                    folder=f"COMP{index:04d}",
                    confirmed_at=datetime.now(timezone.utc),
                    sources=(source,),
                )
                courses.append(course)
                selections.append((course, source))
            config_path = root / "profile.toml"
            save_config(
                AppConfig(
                    profile="test",
                    output_root=root,
                    platforms=PlatformsConfig(
                        ed=EdPlatformConfig(
                            enabled=True,
                            credential="keyring:canvas-ed-mcp/test/ed",
                        )
                    ),
                    courses=tuple(courses),
                ),
                config_path,
            )
            provider = _ConcurrentDiscoveryProvider()

            plan = build_reviewed_sync_plan(
                profile="test",
                config_path=config_path,
                state_path=root / "state.json",
                output_root=root,
                selections=tuple(selections),
                providers={("ed", "ed.invalid"): provider},
            )

            self.assertGreaterEqual(provider.max_active, 2)
            self.assertEqual(
                ["logical-1", "logical-2"],
                [source.course.key for source in plan.sources],
            )

    def test_discovery_failure_is_preserved_as_a_reviewable_empty_source(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = _course()
            config_path = root / "profile.toml"
            save_config(
                AppConfig(
                    profile="test",
                    output_root=root,
                    platforms=PlatformsConfig(
                        ed=EdPlatformConfig(
                            enabled=True,
                            credential="keyring:canvas-ed-mcp/test/ed",
                        )
                    ),
                    courses=(course,),
                ),
                config_path,
            )
            provider = _FailingDiscoveryProvider(())

            plan = build_reviewed_sync_plan(
                profile="test",
                config_path=config_path,
                state_path=root / "state.json",
                output_root=root,
                selections=((course, source),),
                providers={("ed", "ed.invalid"): provider},
            )

            self.assertEqual(1, provider.list_calls)
            self.assertEqual((), plan.sources[0].batch.items)
            self.assertIn(
                "provider_discovery_failed", plan.sources[0].preview.issue_codes
            )

            result = apply_reviewed_sync_plan(
                plan,
                expected_plan_digest=plan.digest,
                providers={("ed", "ed.invalid"): provider},
                config_path=config_path,
                state_path=root / "state.json",
                backup_path=root / "state.backup.json",
                lock_dir=root / ".locks",
                cache_dir=root / ".cache",
            )

            self.assertTrue(result.committed)
            self.assertEqual(1, provider.list_calls)
            self.assertEqual(
                ("provider_discovery_failed",),
                result.transactions[0].source_result.issue_codes,
            )
            self.assertFalse((root / "state.json").exists())

    def test_config_drift_conflicts_before_provider_or_output_io(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (
                plan,
                provider,
                config_path,
                state_path,
                backup_path,
                lock_dir,
                cache_dir,
            ) = self._fixture(root)
            config_path.write_bytes(config_path.read_bytes() + b"\n# changed\n")

            result = apply_reviewed_sync_plan(
                plan,
                expected_plan_digest=plan.digest,
                providers={("ed", "ed.invalid"): provider},
                config_path=config_path,
                state_path=state_path,
                backup_path=backup_path,
                lock_dir=lock_dir,
                cache_dir=cache_dir,
            )

            self.assertEqual("config_changed", result.issue_code)
            self.assertEqual(0, provider.binary_calls)
            self.assertFalse((root / "COMP0001").exists())

    def test_state_drift_conflicts_before_provider_or_output_io(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (
                plan,
                provider,
                config_path,
                state_path,
                backup_path,
                lock_dir,
                cache_dir,
            ) = self._fixture(root)
            state_lock = SyncLock.for_state(
                lock_dir=lock_dir, profile="test", output_root=root
            )
            with state_lock:
                save_state(
                    new_state(profile="test", output_root=root),
                    path=state_path,
                    backup_path=backup_path,
                    lock=state_lock,
                )

            result = apply_reviewed_sync_plan(
                plan,
                expected_plan_digest=plan.digest,
                providers={("ed", "ed.invalid"): provider},
                config_path=config_path,
                state_path=state_path,
                backup_path=backup_path,
                lock_dir=lock_dir,
                cache_dir=cache_dir,
            )

            self.assertEqual("state_changed", result.issue_code)
            self.assertEqual(0, provider.binary_calls)
            self.assertFalse((root / "COMP0001").exists())

    def test_plan_digest_mismatch_and_repr_fail_closed_without_secret_leak(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (
                plan,
                provider,
                config_path,
                state_path,
                backup_path,
                lock_dir,
                cache_dir,
            ) = self._fixture(root)

            self.assertNotIn(provider.synthetic_secret, repr(plan))
            self.assertNotIn("ContentBatch", repr(plan))
            result = apply_reviewed_sync_plan(
                plan,
                expected_plan_digest="0" * 64,
                providers={("ed", "ed.invalid"): provider},
                config_path=config_path,
                state_path=state_path,
                backup_path=backup_path,
                lock_dir=lock_dir,
                cache_dir=cache_dir,
            )

            self.assertEqual("plan_changed", result.issue_code)
            self.assertEqual(0, provider.binary_calls)
            self.assertFalse((root / "COMP0001").exists())


if __name__ == "__main__":
    unittest.main()
