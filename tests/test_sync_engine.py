from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canvas_ed_mcp.config import ContentSelection, CourseConfig, CourseSource, DownloadPolicy
from canvas_ed_mcp.providers.base import (
    CategoryCoverage, ContentBatch, ContentIssue, CoverageStatus, IssueScope,
)
from canvas_ed_mcp.renderers.html import RenderedHtml
from canvas_ed_mcp.sync.engine import (
    ExecutionMode, apply_configured_source, apply_configured_sources,
    apply_source_transaction, commit_source_result, merge_source_result, sync_course_source,
)
from canvas_ed_mcp.sync.locking import SyncLock
from canvas_ed_mcp.sync.model import (
    InitialSyncMode, PreviousSource,
    RemoteItem,
    RemoteKind,
    SourceKey,
)
from canvas_ed_mcp.sync.state import load_state, new_state


class FakeProvider:
    provider_name = "ed"
    instance = "ed.invalid"
    supported_kinds = frozenset({RemoteKind.LESSON, RemoteKind.RESOURCE})

    def __init__(self, items: tuple[RemoteItem, ...]) -> None:
        self.items = items
        self.list_calls = 0
        self.binary_calls = 0
        self.render_calls = 0

    def list_items(self, course_id: str, *, kinds: frozenset[RemoteKind]) -> ContentBatch:
        self.list_calls += 1
        selected = tuple(item for item in self.items if item.key.kind in kinds)
        return ContentBatch(
            provider=self.provider_name,
            instance=self.instance,
            course_id=course_id,
            requested_kinds=kinds,
            coverage=tuple(CategoryCoverage(kind, CoverageStatus.COMPLETE) for kind in sorted(kinds, key=lambda value: value.value)),
            items=selected,
            issues=(),
        )

    @contextmanager
    def open_binary(self, item: RemoteItem, *, chunk_size: int = 64 * 1024):
        self.binary_calls += 1
        yield iter((b"course ", b"content"))

    def render_html(self, item: RemoteItem) -> RenderedHtml:
        self.render_calls += 1
        return RenderedHtml("<!doctype html><p>lesson</p>", False, ())


def item(kind: RemoteKind, remote_id: str, signature: str = "r1") -> RemoteItem:
    return RemoteItem(
        SourceKey("ed", "ed.invalid", "course-1", kind, remote_id),
        signature,
        f"{remote_id}.bin" if kind is RemoteKind.RESOURCE else f"{remote_id}.html",
    )


class SyncEngineTests(unittest.TestCase):
    def _apply_paths(self, root: Path) -> dict[str, Path]:
        return {
            "state_path": root / "state.json",
            "backup_path": root / "state.backup.json",
            "lock_dir": root / ".locks",
            "cache_dir": root / ".cache",
        }

    def test_reconcile_import_all_is_consumed_once_then_incremental(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, baseline_source = self.course(InitialSyncMode.BASELINE_FROM_NOW)
            provider = FakeProvider((item(RemoteKind.RESOURCE, "existing"),))
            apply_configured_source(
                profile="test", course=course, source=baseline_source,
                provider=provider, output_root=root, **self._apply_paths(root),
            )
            self.assertEqual(0, provider.binary_calls)

            import_source = baseline_source.model_copy(update={
                "initial_mode": InitialSyncMode.IMPORT_ALL,
                "reconcile_generation": 1,
            })
            import_course = course.model_copy(update={"sources": (import_source,)})
            first = apply_configured_source(
                profile="test", course=import_course, source=import_source,
                provider=provider, output_root=root, **self._apply_paths(root),
            )
            second = apply_configured_source(
                profile="test", course=import_course, source=import_source,
                provider=provider, output_root=root, **self._apply_paths(root),
            )

        self.assertTrue(first.commit.committed)
        self.assertTrue(second.commit.committed)
        self.assertEqual(1, provider.binary_calls)
        self.assertEqual("skip", second.source_result.items[0].action)

    def test_reconcile_baseline_records_change_without_fetching_or_rebinding_old_bytes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            provider = FakeProvider((item(RemoteKind.RESOURCE, "notes", "r1"),))
            first = apply_configured_source(
                profile="test", course=course, source=source,
                provider=provider, output_root=root, **self._apply_paths(root),
            )
            old_record = first.commit.state.sources[0]
            old_path = root / Path(*old_record.stored_file.output_relative_path.parts)
            old_bytes = old_path.read_bytes()
            baseline_source = source.model_copy(update={
                "initial_mode": InitialSyncMode.BASELINE_FROM_NOW,
                "reconcile_generation": 1,
            })
            baseline_course = course.model_copy(update={"sources": (baseline_source,)})
            provider.items = (item(RemoteKind.RESOURCE, "notes", "r2"),)
            reset = apply_configured_source(
                profile="test", course=baseline_course, source=baseline_source,
                provider=provider, output_root=root, **self._apply_paths(root),
            )
            old_after = old_path.read_bytes()

        self.assertTrue(reset.commit.committed)
        self.assertEqual(1, provider.binary_calls)
        self.assertIsNone(reset.commit.state.sources[0].stored_file)
        self.assertEqual(old_bytes, old_after)

    def test_rebind_ignores_old_folder_history_and_archives_without_deleting_old_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            provider = FakeProvider((item(RemoteKind.RESOURCE, "notes"),))
            first = apply_configured_source(
                profile="test", course=course, source=source,
                provider=provider, output_root=root, **self._apply_paths(root),
            )
            old_record = first.commit.state.sources[0]
            old_path = root / Path(*old_record.stored_file.output_relative_path.parts)
            old_bytes = old_path.read_bytes()
            rebound_source = source.model_copy(update={"reconcile_generation": 1})
            rebound_course = course.model_copy(update={
                "key": "logical-2", "folder": "COMP0002",
                "sources": (rebound_source,),
            })
            rebound = apply_configured_source(
                profile="test", course=rebound_course, source=rebound_source,
                provider=provider, output_root=root, **self._apply_paths(root),
            )
            durable = load_state(
                root / "state.json", profile="test", output_root=root
            )
            old_after = old_path.read_bytes()
            archived_key = durable.archived_sources[0].course_key
            active_key = durable.initialized_sources[0].course_key
            has_new_file = any(
                entry.stored_file is not None
                and entry.stored_file.output_relative_path.parts[0] == "COMP0002"
                for entry in durable.sources
            )

        self.assertTrue(rebound.commit.committed)
        self.assertEqual(old_bytes, old_after)
        self.assertEqual("logical-1", archived_key)
        self.assertEqual("logical-2", active_key)
        self.assertTrue(has_new_file)

    def course(self, mode: InitialSyncMode) -> tuple[CourseConfig, CourseSource]:
        source = CourseSource(
            provider="ed",
            instance="ed.invalid",
            course_id="course-1",
            initial_mode=mode,
        )
        return (
            CourseConfig(
                key="logical-1",
                folder="COMP0001",
                confirmed_at=datetime.now(timezone.utc),
                sources=(source,),
            ),
            source,
        )

    def test_mixed_course_does_not_report_sibling_provider_kinds_as_partial(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ed_source = CourseSource(
                provider="ed", instance="ed.invalid", course_id="course-1"
            )
            canvas_source = CourseSource(
                provider="canvas", instance="canvas.invalid", course_id="canvas-1"
            )
            course = CourseConfig(
                key="logical-mixed", folder="MIXED",
                confirmed_at=datetime.now(timezone.utc),
                sources=(canvas_source, ed_source),
            )
            result = sync_course_source(
                course=course, source=ed_source,
                provider=FakeProvider((item(RemoteKind.LESSON, "lesson"),)),
                output_root=root, previous=(), execution=ExecutionMode.DRY_RUN,
                initialized=False,
            )
        self.assertNotIn("content_capability_partial", result.issue_codes)
        self.assertEqual(1, len(result.items))

    def test_mixed_course_skips_source_with_no_selected_owned_kinds(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ed_source = CourseSource(
                provider="ed", instance="ed.invalid", course_id="course-1"
            )
            canvas_source = CourseSource(
                provider="canvas", instance="canvas.invalid", course_id="canvas-1"
            )
            course = CourseConfig(
                key="logical-mixed", folder="MIXED",
                confirmed_at=datetime.now(timezone.utc),
                sources=(canvas_source, ed_source),
                content=ContentSelection(
                    files=True, pages=True, lessons=False, resources=False
                ),
            )
            provider = FakeProvider((item(RemoteKind.LESSON, "lesson"),))
            result = sync_course_source(
                course=course, source=ed_source, provider=provider,
                output_root=root, previous=(), execution=ExecutionMode.DRY_RUN,
                initialized=False,
            )
        self.assertEqual((), result.issue_codes)
        self.assertEqual((), result.items)
        self.assertEqual(0, provider.list_calls)

    def test_mixed_course_reports_missing_provider_owned_capability(self) -> None:
        class LessonOnlyProvider(FakeProvider):
            supported_kinds = frozenset({RemoteKind.LESSON})

        with TemporaryDirectory() as directory:
            root = Path(directory)
            ed_source = CourseSource(
                provider="ed", instance="ed.invalid", course_id="course-1"
            )
            canvas_source = CourseSource(
                provider="canvas", instance="canvas.invalid", course_id="canvas-1"
            )
            course = CourseConfig(
                key="logical-mixed", folder="MIXED",
                confirmed_at=datetime.now(timezone.utc),
                sources=(canvas_source, ed_source),
                content=ContentSelection(
                    files=False, pages=False, lessons=True, resources=True
                ),
            )
            result = sync_course_source(
                course=course, source=ed_source,
                provider=LessonOnlyProvider((item(RemoteKind.LESSON, "lesson"),)),
                output_root=root, previous=(), execution=ExecutionMode.DRY_RUN,
                initialized=False,
            )
        self.assertIn("content_capability_partial", result.issue_codes)

    def test_course_reports_selected_kind_without_corresponding_provider_source(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ed_source = CourseSource(
                provider="ed", instance="ed.invalid", course_id="course-1"
            )
            course = CourseConfig(
                key="ed-only", folder="ED",
                confirmed_at=datetime.now(timezone.utc), sources=(ed_source,),
                content=ContentSelection(
                    files=True, pages=False, lessons=False, resources=False
                ),
            )
            provider = FakeProvider(())
            result = sync_course_source(
                course=course, source=ed_source, provider=provider,
                output_root=root, previous=(), execution=ExecutionMode.DRY_RUN,
                initialized=False,
            )
        self.assertEqual(("content_capability_unavailable",), result.issue_codes)
        self.assertEqual(0, provider.list_calls)

    def test_baseline_records_signatures_without_content_io_or_output_write(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.BASELINE_FROM_NOW)
            provider = FakeProvider((item(RemoteKind.RESOURCE, "notes"),))

            result = sync_course_source(
                course=course,
                source=source,
                provider=provider,
                output_root=root,
                previous=(),
                execution=ExecutionMode.APPLY,
                initialized=False,
            )

            self.assertEqual("record_only", result.items[0].action)
            self.assertEqual(1, len(result.state_updates))
            self.assertEqual(0, provider.binary_calls)
            self.assertFalse((root / "COMP0001").exists())

    def test_import_all_publishes_binary_without_overwriting(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            provider = FakeProvider((item(RemoteKind.RESOURCE, "notes"),))
            lock = SyncLock.for_course(
                lock_dir=root / ".locks",
                profile="test",
                output_root=root,
                course_key=course.key,
                course_root=PurePosixPath(course.folder),
            )
            with lock:
                result = sync_course_source(
                    course=course,
                    source=source,
                    provider=provider,
                    output_root=root,
                    previous=(),
                    execution=ExecutionMode.APPLY,
                    initialized=False,
                    course_lock=lock,
                    cache_dir=root / ".cache",
                )

            stored = result.state_updates[0].stored_file
            self.assertIsNotNone(stored)
            self.assertEqual(b"course content", (root / Path(*stored.output_relative_path.parts)).read_bytes())
            self.assertEqual(1, provider.binary_calls)

    def test_rendered_item_issues_are_promoted_to_source_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            provider = FakeProvider((item(RemoteKind.LESSON, "lesson"),))
            provider.render_html = lambda item: RenderedHtml(
                "<!doctype html><p>lesson</p>",
                False,
                ("unsafe_image_url",),
            )
            lock = SyncLock.for_course(
                lock_dir=root / ".locks",
                profile="test",
                output_root=root,
                course_key=course.key,
                course_root=PurePosixPath(course.folder),
            )

            with lock:
                result = sync_course_source(
                    course=course,
                    source=source,
                    provider=provider,
                    output_root=root,
                    previous=(),
                    execution=ExecutionMode.APPLY,
                    initialized=False,
                    course_lock=lock,
                    cache_dir=root / ".cache",
                )

            self.assertEqual(("unsafe_image_url",), result.items[0].issue_codes)
            self.assertIn("unsafe_image_url", result.issue_codes)

    def test_dry_run_never_fetches_or_writes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            provider = FakeProvider((item(RemoteKind.LESSON, "week-1"),))

            result = sync_course_source(
                course=course,
                source=source,
                provider=provider,
                output_root=root,
                previous=(),
                execution=ExecutionMode.DRY_RUN,
                initialized=False,
            )

            self.assertEqual("would_fetch", result.items[0].action)
            self.assertEqual((), result.state_updates)
            self.assertEqual(0, provider.render_calls)
            self.assertEqual([], list(root.iterdir()))

    def test_dry_run_result_cannot_be_merged_into_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.BASELINE_FROM_NOW)
            result = sync_course_source(
                course=course, source=source, provider=FakeProvider(()), output_root=root,
                previous=(), execution=ExecutionMode.DRY_RUN, initialized=False,
            )
            with self.assertRaisesRegex(ValueError, "dry-run"):
                merge_source_result(new_state(profile="test", output_root=root), course=course, source=source, result=result)

    def test_deleted_unchanged_file_stays_deleted_until_remote_changes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            lock = SyncLock.for_course(
                lock_dir=root / ".locks",
                profile="test",
                output_root=root,
                course_key=course.key,
                course_root=PurePosixPath(course.folder),
            )
            provider = FakeProvider((item(RemoteKind.RESOURCE, "notes", "r1"),))
            with lock:
                first = sync_course_source(
                    course=course,
                    source=source,
                    provider=provider,
                    output_root=root,
                    previous=(),
                    execution=ExecutionMode.APPLY,
                    initialized=False,
                    course_lock=lock,
                    cache_dir=root / ".cache",
                )
            stored = first.state_updates[0].stored_file
            self.assertIsNotNone(stored)
            installed = root / Path(*stored.output_relative_path.parts)
            installed.unlink()

            with lock:
                unchanged = sync_course_source(
                    course=course,
                    source=source,
                    provider=provider,
                    output_root=root,
                    previous=first.state_updates,
                    execution=ExecutionMode.APPLY,
                    initialized=True,
                    course_lock=lock,
                    cache_dir=root / ".cache",
                )
            self.assertEqual("skip", unchanged.items[0].action)
            self.assertFalse(installed.exists())

            provider.items = (item(RemoteKind.RESOURCE, "notes", "r2"),)
            with lock:
                changed = sync_course_source(
                    course=course,
                    source=source,
                    provider=provider,
                    output_root=root,
                    previous=first.state_updates,
                    execution=ExecutionMode.APPLY,
                    initialized=True,
                    course_lock=lock,
                    cache_dir=root / ".cache",
                )
            replacement = changed.state_updates[0].stored_file
            self.assertIsNotNone(replacement)
            self.assertNotEqual(stored.output_relative_path, replacement.output_relative_path)
            self.assertTrue((root / Path(*replacement.output_relative_path.parts)).is_file())

    def test_completed_baseline_downloads_item_first_seen_on_later_run(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.BASELINE_FROM_NOW)
            provider = FakeProvider((item(RemoteKind.RESOURCE, "existing"),))
            baseline = sync_course_source(
                course=course,
                source=source,
                provider=provider,
                output_root=root,
                previous=(),
                execution=ExecutionMode.APPLY,
                initialized=False,
            )
            self.assertTrue(baseline.initialization_complete)
            durable = merge_source_result(
                new_state(profile="test", output_root=root), course=course, source=source, result=baseline
            )
            self.assertTrue(durable.initialized_for(course_key=course.key, provider="ed", instance="ed.invalid", course_id="course-1"))
            provider.items += (item(RemoteKind.RESOURCE, "new-later"),)
            lock = SyncLock.for_course(
                lock_dir=root / ".locks",
                profile="test",
                output_root=root,
                course_key=course.key,
                course_root=PurePosixPath(course.folder),
            )
            with lock:
                second = sync_course_source(
                    course=course,
                    source=source,
                    provider=provider,
                    output_root=root,
                    previous=baseline.state_updates,
                    execution=ExecutionMode.APPLY,
                    initialized=True,
                    course_lock=lock,
                    cache_dir=root / ".cache",
                )
            actions = {entry.action for entry in second.items}
            self.assertEqual({"skip", "install_new"}, actions)
            self.assertEqual(1, provider.binary_calls)

    def test_merge_preserves_other_source_state_and_rejects_misbound_result(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.BASELINE_FROM_NOW)
            result = sync_course_source(
                course=course, source=source, provider=FakeProvider((item(RemoteKind.RESOURCE, "one"),)),
                output_root=root, previous=(), execution=ExecutionMode.APPLY, initialized=False,
            )
            unrelated = PreviousSource(
                SourceKey("ed", "other.invalid", "course-2", RemoteKind.RESOURCE, "other"), "r1"
            )
            initial = replace(new_state(profile="test", output_root=root), sources=(unrelated,))
            merged = merge_source_result(initial, course=course, source=source, result=result)
            self.assertIn(unrelated, merged.sources)
            with self.assertRaisesRegex(ValueError, "configured course source"):
                merge_source_result(
                    initial, course=course, source=source,
                    result=replace(result, course_key="wrong-course"),
                )

    def test_catalog_only_provider_fails_closed_without_discovery(self) -> None:
        class CatalogOnly:
            provider_name = "ed"
            instance = "ed.invalid"

            def list_items(self, *args, **kwargs):
                raise AssertionError("catalog-only provider must not be called")

        with TemporaryDirectory() as directory:
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            result = sync_course_source(
                course=course,
                source=source,
                provider=CatalogOnly(),  # type: ignore[arg-type]
                output_root=Path(directory),
                previous=(),
                execution=ExecutionMode.DRY_RUN,
                initialized=False,
            )
        self.assertEqual(("content_capability_unavailable",), result.issue_codes)

    def test_policy_skip_is_durable_until_a_later_policy_allows_re_evaluation(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            limited = course.model_copy(update={"policy": DownloadPolicy(max_file_bytes=1)})
            oversized = RemoteItem(
                SourceKey("ed", "ed.invalid", "course-1", RemoteKind.RESOURCE, "large"),
                "r1", "large.bin", size_bytes=2,
            )
            provider = FakeProvider((oversized,))
            skipped = sync_course_source(
                course=limited, source=source, provider=provider, output_root=root,
                previous=(), execution=ExecutionMode.DRY_RUN, initialized=True,
            )
            self.assertEqual(1, len(skipped.policy_skips))
            allowed = sync_course_source(
                course=course, source=source, provider=provider, output_root=root,
                previous=(), execution=ExecutionMode.DRY_RUN, initialized=True,
                policy_skips=skipped.policy_skips,
            )
        self.assertEqual((), allowed.policy_skips)
        self.assertEqual("would_fetch", allowed.items[0].action)

    def test_partial_discovery_processes_items_without_initializing_source(self) -> None:
        class PartialProvider(FakeProvider):
            def list_items(self, course_id: str, *, kinds: frozenset[RemoteKind]) -> ContentBatch:
                self.list_calls += 1
                return ContentBatch(
                    provider=self.provider_name, instance=self.instance, course_id=course_id,
                    requested_kinds=kinds,
                    coverage=tuple(
                        CategoryCoverage(kind, CoverageStatus.PARTIAL)
                        for kind in sorted(kinds, key=lambda value: value.value)
                    ),
                    items=self.items,
                    issues=tuple(
                        ContentIssue(
                            self.provider_name, self.instance, course_id, kind,
                            IssueScope.ITEM, "synthetic_partial", "request_failed", True,
                            item_index=0,
                        )
                        for kind in sorted(kinds, key=lambda value: value.value)
                    ),
                )

        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.BASELINE_FROM_NOW)
            result = sync_course_source(
                course=course, source=source,
                provider=PartialProvider((item(RemoteKind.RESOURCE, "notes"),)),
                output_root=root, previous=(), execution=ExecutionMode.APPLY,
                initialized=False,
            )
            self.assertEqual("record_only", result.items[0].action)
            self.assertFalse(result.initialization_complete)

    def test_fetch_failure_is_retryable_and_later_run_succeeds(self) -> None:
        class FailingProvider(FakeProvider):
            @contextmanager
            def open_binary(self, item: RemoteItem, *, chunk_size: int = 64 * 1024):
                self.binary_calls += 1
                raise OSError("synthetic")
                yield iter(())

        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            lock = SyncLock.for_course(
                lock_dir=root / ".locks", profile="test", output_root=root,
                course_key=course.key, course_root=PurePosixPath(course.folder),
            )
            with lock:
                failed = sync_course_source(
                    course=course, source=source,
                    provider=FailingProvider((item(RemoteKind.RESOURCE, "notes"),)),
                    output_root=root, previous=(), execution=ExecutionMode.APPLY,
                    initialized=True, course_lock=lock, cache_dir=root / ".cache",
                )
            self.assertEqual("failed", failed.items[0].action)
            self.assertTrue(failed.state_updates[0].retry_required)
            healthy = FakeProvider((item(RemoteKind.RESOURCE, "notes"),))
            with lock:
                retried = sync_course_source(
                    course=course, source=source, provider=healthy,
                    output_root=root, previous=failed.state_updates,
                    execution=ExecutionMode.APPLY, initialized=True,
                    course_lock=lock, cache_dir=root / ".cache",
                )
            self.assertEqual("install_new", retried.items[0].action)
            self.assertFalse(retried.state_updates[0].retry_required)

    def test_stream_size_overflow_persists_policy_skip_without_final_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            limited = course.model_copy(update={"policy": DownloadPolicy(max_file_bytes=5)})
            unknown = item(RemoteKind.RESOURCE, "large")
            lock = SyncLock.for_course(
                lock_dir=root / ".locks", profile="test", output_root=root,
                course_key=course.key, course_root=PurePosixPath(course.folder),
            )
            first_provider = FakeProvider((unknown,))
            with lock:
                result = sync_course_source(
                    course=limited, source=source, provider=first_provider,
                    output_root=root, previous=(), execution=ExecutionMode.APPLY,
                    initialized=True, course_lock=lock, cache_dir=root / ".cache",
                )
            self.assertEqual("policy_skip", result.items[0].action)
            self.assertTrue(result.state_updates[0].retry_required)
            self.assertEqual("stream_size_limit_exceeded", result.policy_skips[0].reason.value)
            self.assertFalse((root / course.folder).exists())

            durable = merge_source_result(
                new_state(profile="test", output_root=root),
                course=limited, source=source, result=result,
            )
            unchanged_provider = FakeProvider((unknown,))
            with lock:
                unchanged = sync_course_source(
                    course=limited, source=source, provider=unchanged_provider,
                    output_root=root, previous=durable.sources,
                    execution=ExecutionMode.APPLY, initialized=True,
                    policy_skips=durable.policy_skips,
                    course_lock=lock, cache_dir=root / ".cache",
                )
            self.assertEqual("policy_skip", unchanged.items[0].action)
            self.assertEqual(0, unchanged_provider.binary_calls)

            relaxed_provider = FakeProvider((unknown,))
            with lock:
                relaxed = sync_course_source(
                    course=course, source=source, provider=relaxed_provider,
                    output_root=root, previous=durable.sources,
                    execution=ExecutionMode.APPLY, initialized=True,
                    policy_skips=durable.policy_skips,
                    course_lock=lock, cache_dir=root / ".cache",
                )
            self.assertEqual("install_new", relaxed.items[0].action)
            self.assertEqual(1, relaxed_provider.binary_calls)

    def test_state_commit_failure_never_deletes_already_published_content(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            provider = FakeProvider((item(RemoteKind.RESOURCE, "notes"),))
            course_lock = SyncLock.for_course(
                lock_dir=root / ".locks", profile="test", output_root=root,
                course_key=course.key, course_root=PurePosixPath(course.folder),
            )
            with course_lock:
                result = sync_course_source(
                    course=course, source=source, provider=provider, output_root=root,
                    previous=(), execution=ExecutionMode.APPLY, initialized=True,
                    course_lock=course_lock, cache_dir=root / ".cache",
                )
            stored = result.state_updates[0].stored_file
            self.assertIsNotNone(stored)
            state_lock = SyncLock.for_state(
                lock_dir=root / ".locks", profile="test", output_root=root,
            )
            with state_lock, patch(
                "canvas_ed_mcp.sync.engine.save_state", side_effect=OSError("injected failure")
            ):
                committed = commit_source_result(
                    new_state(profile="test", output_root=root), course=course, source=source, result=result,
                    state_path=root / "state.json", backup_path=root / "backup.json", state_lock=state_lock,
                )
            self.assertFalse(committed.committed)
            self.assertEqual("state_commit_failed", committed.issue_code)
            self.assertTrue((root / Path(*stored.output_relative_path.parts)).is_file())

            with course_lock:
                retried = sync_course_source(
                    course=course, source=source, provider=provider, output_root=root,
                    previous=(), execution=ExecutionMode.APPLY, initialized=True,
                    course_lock=course_lock, cache_dir=root / ".cache",
                )
            self.assertEqual("deduplicate", retried.items[0].action)
            self.assertIsNone(retried.state_updates[0].stored_file)
            with state_lock:
                recovered = commit_source_result(
                    new_state(profile="test", output_root=root), course=course, source=source,
                    result=retried, state_path=root / "state.json",
                    backup_path=root / "backup.json", state_lock=state_lock,
                )
            self.assertTrue(recovered.committed)
            self.assertIsNone(recovered.state.sources[0].stored_file)

    def test_apply_transaction_requires_state_then_course_lock_and_commits(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            state = new_state(profile="test", output_root=root)
            state_lock = SyncLock.for_state(lock_dir=root / ".locks", profile="test", output_root=root)
            with self.assertRaisesRegex(ValueError, "state lock"):
                apply_source_transaction(
                    state, course=course, source=source, provider=FakeProvider(()), output_root=root,
                    state_path=root / "state.json", backup_path=root / "backup.json",
                    state_lock=state_lock, cache_dir=root / ".cache",
                )
            with state_lock:
                transaction = apply_source_transaction(
                    state, course=course, source=source,
                    provider=FakeProvider((item(RemoteKind.RESOURCE, "notes"),)), output_root=root,
                    state_path=root / "state.json", backup_path=root / "backup.json",
                    state_lock=state_lock, cache_dir=root / ".cache",
                )
            self.assertTrue(transaction.commit.committed)
            self.assertEqual("install_new", transaction.source_result.items[0].action)
            self.assertEqual(1, len(transaction.commit.state.sources))

    def test_apply_transaction_rejects_state_for_another_output_before_provider_io(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            state_root = root / "state-root"
            output_root = root / "output-root"
            state_root.mkdir()
            output_root.mkdir()
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            provider = FakeProvider((item(RemoteKind.RESOURCE, "notes"),))
            state_lock = SyncLock.for_state(
                lock_dir=root / ".locks", profile="test", output_root=output_root
            )
            with state_lock, self.assertRaisesRegex(ValueError, "state lock"):
                apply_source_transaction(
                    new_state(profile="test", output_root=state_root), course=course,
                    source=source, provider=provider, output_root=output_root,
                    state_path=root / "state.json", backup_path=root / "backup.json",
                    state_lock=state_lock, cache_dir=root / ".cache",
                )
            self.assertEqual(0, provider.list_calls)
            self.assertEqual([], list(output_root.iterdir()))

    def test_apply_configured_source_loads_then_reuses_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            course, source = self.course(InitialSyncMode.IMPORT_ALL)
            kwargs = {
                "profile": "test", "course": course, "source": source,
                "output_root": root, "state_path": root / "state.json",
                "backup_path": root / "backup.json", "lock_dir": root / ".locks",
                "cache_dir": root / ".cache",
            }
            first_provider = FakeProvider((item(RemoteKind.RESOURCE, "notes"),))
            first = apply_configured_source(provider=first_provider, **kwargs)
            self.assertTrue(first.commit.committed)
            self.assertEqual("install_new", first.source_result.items[0].action)
            second_provider = FakeProvider((item(RemoteKind.RESOURCE, "notes"),))
            second = apply_configured_source(provider=second_provider, **kwargs)
            self.assertTrue(second.commit.committed)
            self.assertEqual("skip", second.source_result.items[0].action)
            self.assertEqual(0, second_provider.binary_calls)

    def test_run_transaction_commits_in_order_under_one_state_lock(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first_course, first_source = self.course(InitialSyncMode.BASELINE_FROM_NOW)
            second_source = CourseSource(
                provider="ed", instance="ed.invalid", course_id="course-2",
                initial_mode=InitialSyncMode.BASELINE_FROM_NOW,
            )
            second_course = CourseConfig(
                key="logical-2", folder="COMP0002",
                confirmed_at=datetime.now(timezone.utc), sources=(second_source,),
            )
            result = apply_configured_sources(
                profile="test",
                selections=((first_course, first_source), (second_course, second_source)),
                provider=FakeProvider(()), output_root=root,
                state_path=root / "state.json", backup_path=root / "backup.json",
                lock_dir=root / ".locks", cache_dir=root / ".cache",
            )
            self.assertTrue(result.committed)
            self.assertEqual(2, len(result.transactions))
            self.assertEqual(2, len(result.transactions[-1].commit.state.initialized_sources))

    def test_run_transaction_selects_provider_by_exact_source_identity(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ed_course, ed_source = self.course(InitialSyncMode.BASELINE_FROM_NOW)
            canvas_source = CourseSource(
                provider="canvas", instance="canvas.invalid", course_id="canvas-1",
                initial_mode=InitialSyncMode.BASELINE_FROM_NOW,
            )
            canvas_course = CourseConfig(
                key="canvas-logical", folder="CANVAS0001",
                confirmed_at=datetime.now(timezone.utc), sources=(canvas_source,),
            )
            ed_provider = FakeProvider(())
            canvas_provider = FakeProvider(())
            canvas_provider.provider_name = "canvas"
            canvas_provider.instance = "canvas.invalid"
            canvas_provider.supported_kinds = frozenset({RemoteKind.FILE, RemoteKind.PAGE})

            result = apply_configured_sources(
                profile="test",
                selections=((canvas_course, canvas_source), (ed_course, ed_source)),
                providers={
                    ("canvas", "canvas.invalid"): canvas_provider,
                    ("ed", "ed.invalid"): ed_provider,
                },
                output_root=root, state_path=root / "state.json",
                backup_path=root / "backup.json", lock_dir=root / ".locks",
                cache_dir=root / ".cache",
            )

            self.assertTrue(result.committed)
            self.assertEqual(1, canvas_provider.list_calls)
            self.assertEqual(1, ed_provider.list_calls)

    def test_run_transaction_stops_after_state_commit_failure(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first_course, first_source = self.course(InitialSyncMode.BASELINE_FROM_NOW)
            second_source = CourseSource(
                provider="ed", instance="ed.invalid", course_id="course-2",
                initial_mode=InitialSyncMode.BASELINE_FROM_NOW,
            )
            second_course = CourseConfig(
                key="logical-2", folder="COMP0002",
                confirmed_at=datetime.now(timezone.utc), sources=(second_source,),
            )
            provider = FakeProvider(())
            with patch("canvas_ed_mcp.sync.engine.save_state", side_effect=OSError("injected")):
                result = apply_configured_sources(
                    profile="test",
                    selections=((first_course, first_source), (second_course, second_source)),
                    provider=provider, output_root=root,
                    state_path=root / "state.json", backup_path=root / "backup.json",
                    lock_dir=root / ".locks", cache_dir=root / ".cache",
                )
            self.assertFalse(result.committed)
            self.assertEqual(1, len(result.transactions))
            self.assertEqual(1, provider.list_calls)

    def test_run_transaction_reports_committed_prefix_when_later_source_raises(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first_course, first_source = self.course(InitialSyncMode.BASELINE_FROM_NOW)
            selections = [(first_course, first_source)]
            for index in (2, 3):
                source = CourseSource(
                    provider="ed", instance="ed.invalid", course_id=f"course-{index}",
                    initial_mode=InitialSyncMode.BASELINE_FROM_NOW,
                )
                course = CourseConfig(
                    key=f"logical-{index}", folder=f"COMP000{index}",
                    confirmed_at=datetime.now(timezone.utc), sources=(source,),
                )
                selections.append((course, source))
            provider = FakeProvider(())
            original_apply = apply_source_transaction

            def fail_second(state, *, course, **kwargs):
                if course.key == "logical-2":
                    raise RuntimeError("synthetic failure")
                return original_apply(state, course=course, **kwargs)

            with patch(
                "canvas_ed_mcp.sync.engine.apply_source_transaction",
                side_effect=fail_second,
            ) as apply:
                result = apply_configured_sources(
                    profile="test", selections=tuple(selections), provider=provider,
                    output_root=root, state_path=root / "state.json",
                    backup_path=root / "backup.json", lock_dir=root / ".locks",
                    cache_dir=root / ".cache",
                )
            self.assertFalse(result.committed)
            self.assertEqual("source_transaction_failed", result.issue_code)
            self.assertEqual(1, result.failed_selection_index)
            self.assertEqual(1, len(result.transactions))
            self.assertTrue(result.transactions[0].commit.committed)
            self.assertEqual(2, apply.call_count)
            self.assertEqual(1, provider.list_calls)


if __name__ == "__main__":
    unittest.main()
