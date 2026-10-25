from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from canvas_ed_mcp.application.contracts import OperationStatus, ProgressPhase
from canvas_ed_mcp.application.daily_sync import SyncPreviewRequest
from canvas_ed_mcp.application.sync_operation import ReviewedSyncOperation
from canvas_ed_mcp.config import (
    AppConfig,
    ContentSelection,
    CourseConfig,
    CourseSource,
    EdPlatformConfig,
    PlatformsConfig,
    save_config,
)
from canvas_ed_mcp.paths import ProfilePaths
from canvas_ed_mcp.providers.base import (
    CategoryCoverage,
    ContentBatch,
    CoverageStatus,
)
from canvas_ed_mcp.renderers.html import RenderedHtml
from canvas_ed_mcp.sync.model import RemoteItem, RemoteKind, SourceKey


class _Provider:
    provider_name = "ed"
    instance = "ed.invalid"
    supported_kinds = frozenset({RemoteKind.LESSON, RemoteKind.RESOURCE})

    def __init__(self) -> None:
        self.list_calls = 0
        self.binary_calls = 0
        self.close_calls = 0
        self._current: tuple[RemoteItem, ...] = ()
        self.cancel_on_list = None
        self.cancel_on_binary = None

    def list_items(
        self, course_id: str, *, kinds: frozenset[RemoteKind]
    ) -> ContentBatch:
        self.list_calls += 1
        item = RemoteItem(
            SourceKey("ed", self.instance, course_id, RemoteKind.RESOURCE, "r1"),
            "revision-1",
            "notes.pdf",
        )
        if RemoteKind.RESOURCE in kinds and item not in self._current:
            self._current = (*self._current, item)
        if self.cancel_on_list is not None:
            self.cancel_on_list()
        return ContentBatch(
            "ed",
            self.instance,
            course_id,
            kinds,
            tuple(
                CategoryCoverage(kind, CoverageStatus.COMPLETE)
                for kind in sorted(kinds, key=lambda value: value.value)
            ),
            (item,) if RemoteKind.RESOURCE in kinds else (),
            (),
        )

    @contextmanager
    def open_binary(self, item: RemoteItem, *, chunk_size: int = 64 * 1024):
        if item not in self._current:
            raise ValueError("stale item")
        self.binary_calls += 1
        if self.cancel_on_binary is not None:
            self.cancel_on_binary()
        yield iter((b"content",))

    def render_html(self, item: RemoteItem) -> RenderedHtml:
        return RenderedHtml("<!doctype html>", False, ())

    def close(self) -> None:
        self.close_calls += 1


def _paths(root: Path) -> ProfilePaths:
    return ProfilePaths(
        profile="test",
        config_file=root / "profile.toml",
        state_file=root / "state.json",
        state_backup_file=root / "state.backup.json",
        lock_dir=root / ".locks",
        log_dir=root / "logs",
        cache_dir=root / ".cache",
    )


def _write_config(
    root: Path,
    paths: ProfilePaths,
    *,
    course_count: int = 1,
    output_root: Path | None = None,
) -> None:
    courses = tuple(
        CourseConfig(
            key=f"logical-{index}",
            folder=f"COMP{index:04d}",
            confirmed_at=datetime.now(timezone.utc),
            sources=(
                CourseSource(
                    provider="ed",
                    instance="ed.invalid",
                    course_id=f"course-{index}",
                    code=f"COMP{index:04d}",
                    name=f"Human readable course {index}",
                    term="Semester 2 2026",
                    initial_mode="import_all",
                ),
            ),
            content=ContentSelection(
                files=False,
                pages=False,
                lessons=True,
                resources=True,
            ),
        )
        for index in range(1, course_count + 1)
    )
    save_config(
        AppConfig(
            profile="test",
            output_root=output_root or root,
            platforms=PlatformsConfig(
                ed=EdPlatformConfig(
                    enabled=True,
                    credential="keyring:canvas-ed-mcp/test/ed",
                )
            ),
            courses=courses,
        ),
        paths.config_file,
    )


class ApplicationSyncOperationTests(unittest.TestCase):
    def _operation(
        self,
        root: Path,
        provider: _Provider,
        resolved: list[str],
        *,
        course_count: int = 1,
    ):
        paths = _paths(root)
        _write_config(root, paths, course_count=course_count)

        def secret_resolver(reference: str) -> str:
            resolved.append(reference)
            return "synthetic-token"

        return (
            ReviewedSyncOperation(
                paths_resolver=lambda profile: paths,
                secret_resolver=secret_resolver,
                canvas_factory=lambda base_url, token: (_ for _ in ()).throw(
                    AssertionError("Canvas must not be initialized")
                ),
                ed_factory=lambda token: provider,
            ),
            paths,
        )

    def test_preview_and_apply_keep_one_provider_lease_and_close_it(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            provider = _Provider()
            resolved: list[str] = []
            operation, paths = self._operation(root, provider, resolved)
            progress = []

            preview = operation.preview(
                SyncPreviewRequest("test", config_path=paths.config_file),
                progress=progress.append,
                cancellation=_NeverCancelled(),
            )
            self.assertEqual(OperationStatus.SUCCEEDED, preview.status)
            self.assertIsNotNone(preview.review)
            source_summary = preview.review.sources[0]
            self.assertEqual("COMP0001", source_summary.course_code)
            self.assertEqual(
                "Human readable course 1", source_summary.course_name
            )
            self.assertEqual("Semester 2 2026", source_summary.course_term)
            self.assertEqual("COMP0001", source_summary.course_folder)
            self.assertEqual(
                "COMP0001 — Human readable course 1 · Semester 2 2026",
                source_summary.course_display_name,
            )
            self.assertEqual("Ed", source_summary.source_display_name)
            self.assertEqual(1, provider.list_calls)
            self.assertEqual(0, provider.binary_calls)
            self.assertEqual(0, provider.close_calls)

            applied = operation.apply(
                preview.review,
                progress=progress.append,
                cancellation=_NeverCancelled(),
            )

            self.assertEqual(OperationStatus.SUCCEEDED, applied.status)
            self.assertEqual(
                source_summary.course_display_name,
                applied.sources[0].course_display_name,
            )
            self.assertEqual(
                source_summary.source_display_name,
                applied.sources[0].source_display_name,
            )
            self.assertEqual(1, provider.list_calls)
            self.assertEqual(1, provider.binary_calls)
            self.assertEqual(1, provider.close_calls)
            self.assertEqual(1, len(applied.sources[0].items))
            item = applied.sources[0].items[0]
            self.assertEqual("notes.pdf", item.display_name)
            self.assertEqual("resource", item.kind)
            self.assertEqual("install_new", item.action)
            self.assertIsNotNone(item.destination)
            assert item.destination is not None
            self.assertTrue(item.destination.is_absolute())
            self.assertTrue(item.destination.is_file())
            self.assertTrue(
                item.destination.is_relative_to((root / "COMP0001").resolve())
            )
            self.assertEqual(["keyring:canvas-ed-mcp/test/ed"], resolved)
            self.assertEqual(
                [
                    ProgressPhase.VALIDATING,
                    ProgressPhase.VALIDATING,
                    ProgressPhase.CONNECTING,
                    ProgressPhase.CONNECTING,
                    ProgressPhase.DISCOVERING,
                    ProgressPhase.DISCOVERING,
                    ProgressPhase.PLANNING,
                    ProgressPhase.VALIDATING,
                    ProgressPhase.VALIDATING,
                    ProgressPhase.APPLYING,
                    ProgressPhase.CLEANING_UP,
                ],
                [event.phase for event in progress],
            )
            self.assertEqual(OperationStatus.SUCCEEDED.value, progress[-1].code)

    def test_preview_reports_missing_output_root_before_provider_io(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            paths = _paths(root)
            missing_output = root / "missing-course-library"
            _write_config(root, paths, output_root=missing_output)
            provider = _Provider()
            resolved: list[str] = []

            operation = ReviewedSyncOperation(
                paths_resolver=lambda profile: paths,
                secret_resolver=lambda reference: resolved.append(reference) or "token",
                canvas_factory=lambda base_url, token: (_ for _ in ()).throw(
                    AssertionError("Canvas must not be initialized")
                ),
                ed_factory=lambda token: provider,
            )

            preview = operation.preview(
                SyncPreviewRequest("test", config_path=paths.config_file),
                progress=None,
                cancellation=_NeverCancelled(),
            )

            self.assertEqual(OperationStatus.FAILED, preview.status)
            self.assertEqual("output_root_missing", preview.issues[0].code)
            self.assertEqual([], resolved)
            self.assertEqual(0, provider.list_calls)
            self.assertEqual(0, provider.close_calls)

    def test_discard_closes_pending_provider_without_apply_io(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            provider = _Provider()
            operation, paths = self._operation(root, provider, [])
            preview = operation.preview(
                SyncPreviewRequest("test", config_path=paths.config_file),
                progress=None,
                cancellation=_NeverCancelled(),
            )

            operation.discard(preview.review)
            operation.discard(preview.review)

            self.assertEqual(0, provider.binary_calls)
            self.assertEqual(1, provider.close_calls)

    def test_apply_reports_config_conflict_and_closes_before_fetch(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            provider = _Provider()
            operation, paths = self._operation(root, provider, [])
            preview = operation.preview(
                SyncPreviewRequest("test", config_path=paths.config_file),
                progress=None,
                cancellation=_NeverCancelled(),
            )
            paths.config_file.write_bytes(
                paths.config_file.read_bytes() + b"\n# changed\n"
            )

            applied = operation.apply(
                preview.review,
                progress=None,
                cancellation=_NeverCancelled(),
            )

            self.assertEqual(OperationStatus.CONFLICT, applied.status)
            self.assertEqual("config_changed", applied.issues[0].code)
            self.assertEqual(0, provider.binary_calls)
            self.assertEqual(1, provider.close_calls)

    def test_apply_rejects_profile_path_drift_before_fetch_or_output(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = _paths(root)
            changed = ProfilePaths(
                profile="test",
                config_file=original.config_file,
                state_file=original.state_file,
                state_backup_file=root / "other" / "state.backup.json",
                lock_dir=root / "other" / ".locks",
                log_dir=original.log_dir,
                cache_dir=root / "other" / ".cache",
            )
            _write_config(root, original)
            provider = _Provider()
            resolutions = iter((original, changed))
            operation = ReviewedSyncOperation(
                paths_resolver=lambda profile: next(resolutions),
                secret_resolver=lambda reference: "synthetic-token",
                canvas_factory=lambda base_url, token: (_ for _ in ()).throw(
                    AssertionError("Canvas must not be initialized")
                ),
                ed_factory=lambda token: provider,
            )
            preview = operation.preview(
                SyncPreviewRequest("test", config_path=original.config_file),
                progress=None,
                cancellation=_NeverCancelled(),
            )

            applied = operation.apply(
                preview.review,
                progress=None,
                cancellation=_NeverCancelled(),
            )

            self.assertEqual(OperationStatus.CONFLICT, applied.status)
            self.assertEqual("profile_paths_changed", applied.issues[0].code)
            self.assertEqual(0, provider.binary_calls)
            self.assertEqual(1, provider.close_calls)
            self.assertFalse(original.state_file.exists())
            self.assertEqual(0, len(tuple(root.glob("COMP0001/**/*"))))

    def test_apply_rejects_same_path_retargeted_after_preview(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target_a = root / "runtime-a"
            target_b = root / "runtime-b"
            target_a.mkdir()
            target_b.mkdir()
            runtime_link = root / "runtime-current"
            try:
                runtime_link.symlink_to(target_a, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"directory symlink unavailable: {exc}")
            paths = ProfilePaths(
                profile="test",
                config_file=root / "profile.toml",
                state_file=runtime_link / "state.json",
                state_backup_file=runtime_link / "state.backup.json",
                lock_dir=runtime_link / ".locks",
                log_dir=root / "logs",
                cache_dir=runtime_link / ".cache",
            )
            _write_config(root, paths)
            provider = _Provider()
            operation = ReviewedSyncOperation(
                paths_resolver=lambda profile: paths,
                secret_resolver=lambda reference: "synthetic-token",
                canvas_factory=lambda base_url, token: (_ for _ in ()).throw(
                    AssertionError("Canvas must not be initialized")
                ),
                ed_factory=lambda token: provider,
            )
            preview = operation.preview(
                SyncPreviewRequest("test", config_path=paths.config_file),
                progress=None,
                cancellation=_NeverCancelled(),
            )
            runtime_link.unlink()
            runtime_link.symlink_to(target_b, target_is_directory=True)

            applied = operation.apply(
                preview.review,
                progress=None,
                cancellation=_NeverCancelled(),
            )

            self.assertEqual(OperationStatus.CONFLICT, applied.status)
            self.assertEqual("profile_paths_changed", applied.issues[0].code)
            self.assertEqual(0, provider.binary_calls)
            self.assertEqual(1, provider.close_calls)
            self.assertFalse((target_a / "state.json").exists())
            self.assertFalse((target_b / "state.json").exists())

    def test_preview_cancels_concurrent_discovery_and_closes_lease(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            provider = _Provider()
            cancellation = _MutableCancellation()
            provider.cancel_on_list = cancellation.request
            operation, paths = self._operation(
                root, provider, [], course_count=2
            )

            preview = operation.preview(
                SyncPreviewRequest("test", config_path=paths.config_file),
                progress=None,
                cancellation=cancellation,
            )

            self.assertEqual(OperationStatus.CANCELLED, preview.status)
            self.assertGreaterEqual(provider.list_calls, 1)
            self.assertLessEqual(provider.list_calls, 2)
            self.assertEqual(0, provider.binary_calls)
            self.assertEqual(1, provider.close_calls)
            self.assertFalse(paths.state_file.exists())

    def test_apply_cancels_before_next_source_and_returns_partial_prefix(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            provider = _Provider()
            cancellation = _MutableCancellation()
            operation, paths = self._operation(
                root, provider, [], course_count=2
            )
            preview = operation.preview(
                SyncPreviewRequest("test", config_path=paths.config_file),
                progress=None,
                cancellation=cancellation,
            )
            provider.cancel_on_binary = cancellation.request

            applied = operation.apply(
                preview.review,
                progress=None,
                cancellation=cancellation,
            )

            self.assertEqual(OperationStatus.PARTIAL, applied.status)
            self.assertEqual("operation_cancelled", applied.issues[0].code)
            self.assertEqual(1, provider.binary_calls)
            self.assertEqual(1, provider.close_calls)
            self.assertTrue((root / "COMP0001").exists())
            self.assertFalse((root / "COMP0002").exists())

    def test_raising_progress_callback_does_not_change_apply_or_leak_lease(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            provider = _Provider()
            operation, paths = self._operation(root, provider, [])
            preview = operation.preview(
                SyncPreviewRequest("test", config_path=paths.config_file),
                progress=None,
                cancellation=_NeverCancelled(),
            )

            applied = operation.apply(
                preview.review,
                progress=lambda event: (_ for _ in ()).throw(
                    RuntimeError("synthetic-secret-must-not-leak")
                ),
                cancellation=_NeverCancelled(),
            )

            self.assertEqual(OperationStatus.SUCCEEDED, applied.status)
            self.assertEqual(1, provider.binary_calls)
            self.assertEqual(1, provider.close_calls)


class _NeverCancelled:
    cancellation_requested = False


class _MutableCancellation:
    cancellation_requested = False

    def request(self) -> None:
        self.cancellation_requested = True


if __name__ == "__main__":
    unittest.main()
