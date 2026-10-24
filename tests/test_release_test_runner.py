from __future__ import annotations

import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock

from ci.run_release_tests import build_test_batches, run_release_tests


class ReleaseTestRunnerTests(unittest.TestCase):
    def test_native_ui_modules_are_isolated_from_the_main_batch(self) -> None:
        tests = (
            "tests/test_core.py",
            "tests/test_ui_first.py",
            "tests/test_more.py",
            "tests/test_ui_second.py",
        )

        self.assertEqual(
            (
                ("tests/test_core.py", "tests/test_more.py"),
                ("tests/test_ui_first.py",),
                ("tests/test_ui_second.py",),
            ),
            build_test_batches(tests),
        )

    def test_first_failed_batch_stops_the_release_suite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            for name in ("test_core.py", "test_ui_window.py"):
                (root / "tests" / name).write_text("", encoding="utf-8")
            (root / "release-manifest.txt").write_text(
                "tests/test_core.py\ntests/test_ui_window.py\n",
                encoding="utf-8",
            )
            runner = Mock(
                side_effect=(
                    subprocess.CompletedProcess([], 0),
                    subprocess.CompletedProcess([], 7),
                )
            )

            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(7, run_release_tests(root, runner=runner))
            self.assertEqual(2, runner.call_count)
            self.assertIn("Release test batch 1 exit: 0", output.getvalue())
            self.assertIn("Release test batch 2 exit: 7", output.getvalue())


if __name__ == "__main__":
    unittest.main()
