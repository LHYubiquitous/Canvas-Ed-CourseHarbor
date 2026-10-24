"""Run the public release tests with native GUI modules process-isolated."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence


def select_release_tests(root: Path) -> tuple[str, ...]:
    manifest = root / "release-manifest.txt"
    tests = tuple(
        line.strip()
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.startswith("tests/test_") and line.endswith(".py")
    )
    if not tests:
        raise RuntimeError("release manifest contains no tests")
    missing = tuple(relative for relative in tests if not (root / relative).is_file())
    if missing:
        raise RuntimeError("release manifest names a missing test")
    return tests


def build_test_batches(tests: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    gui = tuple(test for test in tests if test.startswith("tests/test_ui_"))
    non_gui = tuple(test for test in tests if test not in gui)
    return ((non_gui,) if non_gui else ()) + tuple((test,) for test in gui)


def run_release_tests(
    root: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> int:
    tests = select_release_tests(root)
    for index, batch in enumerate(build_test_batches(tests), start=1):
        print(f"Release test batch {index}: {len(batch)} module(s)", flush=True)
        completed = runner(
            [sys.executable, "-B", "-m", "unittest", *batch, "-v"],
            cwd=root,
            check=False,
        )
        print(
            f"Release test batch {index} exit: {completed.returncode}",
            flush=True,
        )
        if completed.returncode:
            return int(completed.returncode)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    return run_release_tests(args.root.resolve(strict=True))


if __name__ == "__main__":
    raise SystemExit(main())
