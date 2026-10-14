from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from pathlib import PurePosixPath


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.sync.locking import (
    LockScope,
    LockUnavailable,
    SyncLock,
)


class SyncLockTests(unittest.TestCase):
    def test_same_course_scope_contends_and_owner_is_non_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            lock_dir = base / "locks"
            output = base / "Courses"
            first = SyncLock.for_course(
                lock_dir=lock_dir,
                profile="default",
                output_root=output,
                course_key="canvas:canvas.example.edu:101",
                course_root=PurePosixPath("COURSE"),
            )
            second = SyncLock.for_course(
                lock_dir=lock_dir,
                profile="other",
                output_root=output,
                course_key="different-logical-key",
                course_root=PurePosixPath("COURSE"),
            )
            self.assertEqual(first.path, second.path)
            with first:
                with self.assertRaises(LockUnavailable) as caught:
                    second.acquire()
                self.assertEqual(LockScope.COURSE, caught.exception.scope)
                self.assertEqual(os.getpid(), caught.exception.owner.get("pid"))
                self.assertNotIn("token", json.dumps(caught.exception.owner).casefold())

    def test_different_courses_can_be_held_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            common = {
                "lock_dir": base / "locks",
                "profile": "default",
                "output_root": base / "Courses",
            }
            first = SyncLock.for_course(
                course_key="course-101",
                course_root=PurePosixPath("COURSE101"),
                **common,
            )
            second = SyncLock.for_course(
                course_key="course-202",
                course_root=PurePosixPath("COURSE202"),
                **common,
            )
            self.assertNotEqual(first.path, second.path)
            with first, second:
                self.assertTrue(first.held)
                self.assertTrue(second.held)

    def test_state_scope_is_profile_bound_and_separate_from_course_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            lock_dir = base / "locks"
            output = base / "Courses"
            first = SyncLock.for_state(
                lock_dir=lock_dir,
                profile="default",
                output_root=output,
            )
            same = SyncLock.for_state(
                lock_dir=lock_dir,
                profile="default",
                output_root=output,
            )
            other_profile = SyncLock.for_state(
                lock_dir=lock_dir,
                profile="other",
                output_root=output,
            )
            course = SyncLock.for_course(
                lock_dir=lock_dir,
                profile="default",
                output_root=output,
                course_key="course-101",
                course_root=PurePosixPath("COURSE101"),
            )
            self.assertEqual(first.path, same.path)
            self.assertNotEqual(first.path, other_profile.path)
            self.assertNotEqual(first.path, course.path)
            with first, other_profile, course:
                with self.assertRaises(LockUnavailable):
                    same.acquire()

    def test_release_allows_reacquire_and_retains_diagnostic_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            first = SyncLock.for_course(
                lock_dir=base / "locks",
                profile="default",
                output_root=base / "Courses",
                course_key="course-101",
                course_root=PurePosixPath("COURSE101"),
            )
            second = SyncLock.for_course(
                lock_dir=base / "locks",
                profile="default",
                output_root=base / "Courses",
                course_key="course-101",
                course_root=PurePosixPath("COURSE101"),
            )
            first.acquire()
            first.release()
            self.assertFalse(first.held)
            self.assertTrue(first.path.is_file())
            with second:
                self.assertTrue(second.held)

    def test_context_exception_releases_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            lock = SyncLock.for_state(
                lock_dir=base / "locks",
                profile="default",
                output_root=base / "Courses",
            )
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                with lock:
                    raise RuntimeError("synthetic")
            with SyncLock.for_state(
                lock_dir=base / "locks",
                profile="default",
                output_root=base / "Courses",
            ):
                pass

    def test_stale_metadata_and_process_exit_do_not_create_a_false_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            lock_dir = base / "locks"
            output = base / "Courses"
            code = """
                import os
                from pathlib import Path, PurePosixPath
                from canvas_ed_mcp.sync.locking import SyncLock

                lock = SyncLock.for_course(
                    lock_dir=Path(os.environ["LOCK_DIR"]),
                    profile="default",
                    output_root=Path(os.environ["OUTPUT_ROOT"]),
                    course_key="course-101",
                    course_root=PurePosixPath("COURSE101"),
                )
                lock.acquire()
                print(lock.path, flush=True)
                os._exit(0)
            """
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            environment["LOCK_DIR"] = str(lock_dir)
            environment["OUTPUT_ROOT"] = str(output)
            result = subprocess.run(
                [sys.executable, "-B", "-c", textwrap.dedent(code)],
                cwd=base,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            lock_path = Path(result.stdout.strip())
            self.assertTrue(lock_path.is_file())

            with SyncLock.for_course(
                lock_dir=lock_dir,
                profile="default",
                output_root=output,
                course_key="course-101",
                course_root=PurePosixPath("COURSE101"),
            ):
                pass

    def test_timeout_waits_for_bounded_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            common = {
                "lock_dir": base / "locks",
                "profile": "default",
                "output_root": base / "Courses",
            }
            first = SyncLock.for_state(**common)
            second = SyncLock.for_state(**common)
            with first:
                started = time.monotonic()
                with self.assertRaises(LockUnavailable):
                    second.acquire(timeout=0.05, poll_interval=0.01)
                elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.04)
            self.assertLess(elapsed, 1.0)

    def test_invalid_identity_and_timing_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with self.assertRaises(ValueError):
                SyncLock.for_course(
                    lock_dir=base / "locks",
                    profile="default",
                    output_root=Path("relative"),
                    course_key="course-101",
                    course_root=PurePosixPath("COURSE101"),
                )
            with self.assertRaises(ValueError):
                SyncLock.for_course(
                    lock_dir=base / "locks",
                    profile="default",
                    output_root=base / "Courses",
                    course_key="",
                    course_root=PurePosixPath("COURSE101"),
                )
            lock = SyncLock.for_state(
                lock_dir=base / "locks",
                profile="default",
                output_root=base / "Courses",
            )
            for timeout, poll_interval in (
                (-1, 0.01),
                (False, 0.01),
                (float("nan"), 0.01),
                (float("inf"), 0.01),
                (0, 0),
                (0, True),
                (0, float("nan")),
            ):
                with self.subTest(timeout=timeout, poll=poll_interval), self.assertRaises(ValueError):
                    lock.acquire(timeout=timeout, poll_interval=poll_interval)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
