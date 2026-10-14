from __future__ import annotations

import errno
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.sync.decisions import decide_install, decide_source
from canvas_ed_mcp.sync.locking import SyncLock
from canvas_ed_mcp.sync.model import (
    LocalStatus,
    PreviousSource,
    RemoteItem,
    RemoteKind,
    SourceKey,
    StoredFile,
    SyncAction,
    SyncMode,
)
from canvas_ed_mcp.sync.storage import (
    AtomicPublishResult,
    AtomicPublishUnsupported,
    CourseFileIndex,
    IndexedFile,
    install_new_atomic as install_new_atomic_locked,
    resolve_contained_path,
    sha256_file,
)
from canvas_ed_mcp.sync.storage import _is_link


COURSE_KEY = "canvas:canvas.example.edu:course-101"


def install_new_atomic(
    source: Path,
    *,
    output_root: Path,
    course_root: PurePosixPath,
    destination: PurePosixPath,
) -> AtomicPublishResult:
    lock = SyncLock.for_course(
        lock_dir=Path(output_root) / ".test-locks",
        profile="default",
        output_root=Path(output_root),
        course_key=COURSE_KEY,
        course_root=course_root,
    )
    with lock:
        return install_new_atomic_locked(
            source,
            output_root=output_root,
            course_root=course_root,
            destination=destination,
            course_key=COURSE_KEY,
            lock=lock,
        )


class CourseFileIndexTests(unittest.TestCase):
    def test_windows_reparse_flag_is_rejected_without_pathlib_is_junction(self) -> None:
        metadata = SimpleNamespace(
            st_mode=0,
            st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400),
        )
        with patch("canvas_ed_mcp.sync.storage.os.lstat", return_value=metadata):
            self.assertTrue(_is_link(Path("synthetic-junction")))

    def test_public_constructor_rejects_cross_course_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                CourseFileIndex(
                    output_root=Path(temporary),
                    course_root=PurePosixPath("COURSE1"),
                    files=(IndexedFile(PurePosixPath("COURSE2/file.pdf"), 1),),
                )

    def test_hash_and_duplicate_lookup_are_course_scoped_and_content_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            first = output / "COURSE1"
            second = output / "COURSE2"
            first.mkdir()
            second.mkdir()
            (first / "renamed.pdf").write_bytes(b"same content")
            (first / "same-size.pdf").write_bytes(b"different!!!")
            (second / "outside.pdf").write_bytes(b"outside only")
            incoming = output / "incoming.tmp"
            incoming.write_bytes(b"same content")

            index = CourseFileIndex.scan(
                output_root=output,
                course_root=PurePosixPath("COURSE1"),
            )
            match = index.find_identical(incoming)

            self.assertIsNotNone(match)
            self.assertEqual(PurePosixPath("COURSE1/renamed.pdf"), match.output_relative_path)
            self.assertNotIn(
                PurePosixPath("COURSE2/outside.pdf"),
                {item.output_relative_path for item in index.files},
            )

            incoming.write_bytes(b"other content")
            self.assertIsNone(index.find_identical(incoming))

    def test_crash_leftover_staging_never_participates_in_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            course = output / "COURSE"
            course.mkdir()
            staging = course / ".canvas-ed-mcp-crash.staging"
            staging.write_bytes(b"remote")
            incoming = output / "incoming.tmp"
            incoming.write_bytes(b"remote")

            index = CourseFileIndex.scan(
                output_root=output,
                course_root=PurePosixPath("COURSE"),
            )

            self.assertEqual((), index.files)
            self.assertIsNone(index.find_same_name(staging.name))
            self.assertIsNone(index.find_identical(incoming))

    def test_hash_reads_more_than_one_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "large.bin"
            path.write_bytes((b"abc123" * 400_000) + b"tail")
            self.assertEqual(sha256_file(path), sha256_file(path, chunk_size=257))

    def test_observe_reports_missing_unmodified_modified_and_unsafe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            course = output / "COURSE"
            course.mkdir()
            target = course / "notes.txt"
            target.write_bytes(b"original")
            stored = StoredFile(
                PurePosixPath("COURSE/notes.txt"),
                sha256_file(target),
                True,
            )
            index = CourseFileIndex.scan(
                output_root=output,
                course_root=PurePosixPath("COURSE"),
            )

            self.assertEqual(LocalStatus.UNMODIFIED, index.observe(stored).status)
            target.write_bytes(b"my notes")
            self.assertEqual(LocalStatus.MODIFIED, index.observe(stored).status)
            target.unlink()
            self.assertEqual(LocalStatus.MISSING, index.observe(stored).status)

            outside = StoredFile(
                PurePosixPath("OTHER/notes.txt"),
                "0" * 64,
                True,
            )
            self.assertEqual(LocalStatus.UNSAFE, index.observe(outside).status)

    def test_symlink_escape_is_not_indexed_or_observed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            course = output / "COURSE"
            outside = Path(temporary) / "outside"
            course.mkdir(parents=True)
            outside.mkdir()
            secret = outside / "secret.txt"
            secret.write_bytes(b"outside")
            linked = course / "linked.txt"
            try:
                linked.symlink_to(secret)
            except OSError as exc:
                self.skipTest(f"symlink creation is unavailable: {exc}")

            index = CourseFileIndex.scan(
                output_root=output,
                course_root=PurePosixPath("COURSE"),
            )
            self.assertEqual((), index.files)
            stored = StoredFile(
                PurePosixPath("COURSE/linked.txt"),
                sha256_file(secret),
                True,
            )
            self.assertEqual(LocalStatus.UNSAFE, index.observe(stored).status)
            with self.assertRaises(ValueError):
                resolve_contained_path(
                    output,
                    PurePosixPath("COURSE/linked.txt"),
                    must_exist=True,
                )


class AtomicInstallTests(unittest.TestCase):
    def test_install_requires_a_held_matching_course_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.mkdir()
            source = Path(temporary) / "download.tmp"
            source.write_bytes(b"complete")
            arguments = {
                "output_root": output,
                "course_root": PurePosixPath("COURSE"),
                "destination": PurePosixPath("COURSE/notes.pdf"),
                "course_key": COURSE_KEY,
            }
            with self.assertRaises(ValueError):
                install_new_atomic_locked(source, **arguments)

            wrong = SyncLock.for_course(
                lock_dir=output / ".test-locks",
                profile="default",
                output_root=output,
                course_key="other-course",
                course_root=PurePosixPath("COURSE"),
            )
            with wrong, self.assertRaises(ValueError):
                install_new_atomic_locked(source, **arguments, lock=wrong)
            self.assertFalse((output / "COURSE" / "notes.pdf").exists())

    def test_cleanup_marker_is_bound_to_destination_staging_scope(self) -> None:
        stored = StoredFile(
            PurePosixPath("COURSE/Week 1/notes.pdf"),
            "0" * 64,
            True,
        )
        for pending in (
            PurePosixPath("OTHER/.canvas-ed-mcp-random.staging"),
            PurePosixPath("COURSE/Week 1/user-notes.pdf"),
        ):
            with self.subTest(pending=pending), self.assertRaises(ValueError):
                AtomicPublishResult(stored, pending)

    def test_install_is_same_directory_atomic_and_returns_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.mkdir()
            source = Path(temporary) / "download.tmp"
            source.write_bytes(b"complete")
            destination = PurePosixPath("COURSE/Week 1/notes.pdf")
            calls: list[tuple[Path, Path]] = []
            real_link = os.link

            def record_link(
                source_path: str | os.PathLike[str],
                target_path: str | os.PathLike[str],
                **kwargs: object,
            ) -> None:
                calls.append((Path(source_path), Path(target_path)))
                real_link(source_path, target_path, **kwargs)

            with patch("canvas_ed_mcp.sync.storage.os.link", side_effect=record_link):
                result = install_new_atomic(
                    source,
                    output_root=output,
                    course_root=PurePosixPath("COURSE"),
                    destination=destination,
                )

            final = output / "COURSE" / "Week 1" / "notes.pdf"
            self.assertEqual(b"complete", final.read_bytes())
            self.assertEqual(destination, result.stored_file.output_relative_path)
            self.assertEqual(sha256_file(final), result.stored_file.written_sha256)
            self.assertTrue(result.stored_file.managed_by_sync)
            self.assertIsNone(result.staging_cleanup_pending)
            self.assertEqual(final.parent.resolve(), calls[0][0].parent.resolve())
            self.assertEqual(final.resolve(), calls[0][1].resolve())
            self.assertEqual([final], [path for path in final.parent.iterdir() if path.is_file()])

    def test_install_never_overwrites_file_created_during_publish_race(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.mkdir()
            source = Path(temporary) / "download.tmp"
            source.write_bytes(b"remote")
            destination = PurePosixPath("COURSE/notes.pdf")
            target = output / "COURSE" / "notes.pdf"
            real_link = os.link

            def create_user_file_then_link(
                source_path: str | os.PathLike[str],
                target_path: str | os.PathLike[str],
                **kwargs: object,
            ) -> None:
                Path(target_path).write_bytes(b"user-created-during-race")
                real_link(source_path, target_path, **kwargs)

            with patch(
                "canvas_ed_mcp.sync.storage.os.link",
                side_effect=create_user_file_then_link,
            ):
                with self.assertRaises(FileExistsError):
                    install_new_atomic(
                        source,
                        output_root=output,
                        course_root=PurePosixPath("COURSE"),
                        destination=destination,
                    )

            self.assertEqual(b"user-created-during-race", target.read_bytes())
            self.assertEqual([target], [path for path in target.parent.iterdir() if path.is_file()])

    def test_unsupported_hard_link_fails_closed_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.mkdir()
            source = Path(temporary) / "download.tmp"
            source.write_bytes(b"remote")
            destination = PurePosixPath("COURSE/notes.pdf")

            with patch(
                "canvas_ed_mcp.sync.storage.os.link",
                side_effect=OSError(errno.EOPNOTSUPP, "synthetic unsupported link"),
            ):
                with self.assertRaises(AtomicPublishUnsupported):
                    install_new_atomic(
                        source,
                        output_root=output,
                        course_root=PurePosixPath("COURSE"),
                        destination=destination,
                    )

            course = output / "COURSE"
            self.assertFalse((course / "notes.pdf").exists())
            self.assertEqual([], [path for path in course.iterdir() if path.is_file()])

    def test_cleanup_failure_after_commit_reports_pending_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.mkdir()
            source = Path(temporary) / "download.tmp"
            source.write_bytes(b"remote")
            destination = PurePosixPath("COURSE/notes.pdf")
            real_unlink = Path.unlink

            def fail_staging_cleanup(path: Path, *args: object, **kwargs: object) -> None:
                if path.name.endswith(".staging"):
                    raise OSError("synthetic cleanup failure")
                real_unlink(path, *args, **kwargs)

            with patch("pathlib.Path.unlink", new=fail_staging_cleanup):
                result = install_new_atomic(
                    source,
                    output_root=output,
                    course_root=PurePosixPath("COURSE"),
                    destination=destination,
                )

            final = output / "COURSE" / "notes.pdf"
            self.assertEqual(b"remote", final.read_bytes())
            self.assertIsNotNone(result.staging_cleanup_pending)
            pending = output / Path(*result.staging_cleanup_pending.parts)  # type: ignore[union-attr]
            self.assertTrue(pending.is_file())
            real_unlink(pending)

    def test_install_refuses_existing_or_out_of_course_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            target = output / "COURSE" / "notes.pdf"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"user file")
            source = Path(temporary) / "download.tmp"
            source.write_bytes(b"new")

            with self.assertRaises(FileExistsError):
                install_new_atomic(
                    source,
                    output_root=output,
                    course_root=PurePosixPath("COURSE"),
                    destination=PurePosixPath("COURSE/notes.pdf"),
                )
            with self.assertRaises(ValueError):
                install_new_atomic(
                    source,
                    output_root=output,
                    course_root=PurePosixPath("COURSE"),
                    destination=PurePosixPath("OTHER/notes.pdf"),
                )
            self.assertEqual(b"user file", target.read_bytes())

    def test_precommit_failure_leaves_no_final_or_staging_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.mkdir()
            source = Path(temporary) / "download.tmp"
            source.write_bytes(b"complete")
            destination = PurePosixPath("COURSE/notes.pdf")

            lock = SyncLock.for_course(
                lock_dir=output / ".test-locks",
                profile="default",
                output_root=output,
                course_key=COURSE_KEY,
                course_root=PurePosixPath("COURSE"),
            )
            with lock:
                with patch(
                    "canvas_ed_mcp.sync.storage.os.fsync",
                    side_effect=OSError("synthetic disk failure"),
                ):
                    with self.assertRaises(OSError):
                        install_new_atomic_locked(
                            source,
                            output_root=output,
                            course_root=PurePosixPath("COURSE"),
                            destination=destination,
                            course_key=COURSE_KEY,
                            lock=lock,
                        )

            course = output / "COURSE"
            self.assertFalse((course / "notes.pdf").exists())
            self.assertEqual([], [path for path in course.iterdir() if path.is_file()])


class DecisionStorageIntegrationTests(unittest.TestCase):
    def test_new_then_changed_source_composes_with_observation_and_atomic_install(self) -> None:
        key = SourceKey(
            provider="canvas",
            instance="canvas.example.edu",
            course_id="course:001",
            kind=RemoteKind.FILE,
            remote_id="file:001",
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            output.mkdir()
            course_root = PurePosixPath("COURSE")
            destination = PurePosixPath("COURSE/Week 1/notes.pdf")
            source = Path(temporary) / "download.tmp"
            source.write_bytes(b"first")

            first_remote = RemoteItem(key, "revision-1", "notes.pdf", "Week 1")
            source_decision = decide_source(first_remote, None, mode=SyncMode.SYNC)
            install_decision = decide_install(
                source_decision,
                local_status=LocalStatus.NONE,
                identical_content_exists=False,
                allow_in_place_replace=False,
            )
            self.assertEqual(SyncAction.INSTALL_NEW, install_decision.action)
            stored = install_new_atomic(
                source,
                output_root=output,
                course_root=course_root,
                destination=destination,
            ).stored_file

            index = CourseFileIndex.scan(
                output_root=output,
                course_root=course_root,
            )
            observation = index.observe(stored)
            second_remote = RemoteItem(key, "revision-2", "notes.pdf", "Week 1")
            source_decision = decide_source(
                second_remote,
                PreviousSource(key, "revision-1", stored_file=stored),
                mode=SyncMode.SYNC,
            )
            install_decision = decide_install(
                source_decision,
                local_status=observation.status,
                identical_content_exists=False,
                allow_in_place_replace=False,
            )
            self.assertEqual(SyncAction.INSTALL_SEPARATELY, install_decision.action)
            self.assertEqual(
                "safe_replace_unavailable", install_decision.reason.value
            )

            source.write_bytes(b"second")
            separate_destination = PurePosixPath("COURSE/Week 1/notes (updated).pdf")
            updated = install_new_atomic(
                source,
                output_root=output,
                course_root=course_root,
                destination=separate_destination,
            ).stored_file
            self.assertEqual(separate_destination, updated.output_relative_path)
            self.assertEqual(b"first", (output / Path(*destination.parts)).read_bytes())
            self.assertEqual(
                b"second", (output / Path(*separate_destination.parts)).read_bytes()
            )


if __name__ == "__main__":
    unittest.main()
