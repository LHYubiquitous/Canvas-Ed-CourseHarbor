"""Build and audit the unpublished CourseHarbor Windows onedir spike."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

try:
    from ci.verify_windows_portable import audit_windows_portable
except ModuleNotFoundError:
    from verify_windows_portable import audit_windows_portable


class PortableBuildError(RuntimeError):
    pass


def _pyinstaller_environment() -> dict[str, str]:
    environment = dict(os.environ)
    windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
    path_entries = (
        Path(sys.executable).resolve().parent,
        Path(sys.base_prefix).resolve(),
        windows / "System32",
        windows,
        windows / "System32" / "Wbem",
    )
    environment["PATH"] = os.pathsep.join(str(path) for path in path_entries)
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONPATH", None)
    return environment


def build_windows_portable(root: Path, output: Path) -> Path:
    repository = root.resolve(strict=True)
    destination = output.resolve(strict=False)
    if sys.platform != "win32":
        raise PortableBuildError("portable build failed: windows_required")
    if destination.exists():
        raise PortableBuildError("portable build failed: output_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    spec = repository / "packaging" / "windows" / "courseharbor.spec"
    if not spec.is_file():
        raise PortableBuildError("portable build failed: spec_missing")
    host_roots = (
        repository,
        Path.home(),
        Path(sys.executable).resolve().parent,
        Path(sys.base_prefix).resolve(),
    )

    with tempfile.TemporaryDirectory(
        prefix=".courseharbor-portable-", dir=destination.parent
    ) as temporary:
        work = Path(temporary)
        command = (
            sys.executable,
            "-m",
            "PyInstaller",
            "--clean",
            "--noconfirm",
            "--distpath",
            str(work / "dist"),
            "--workpath",
            str(work / "build"),
            str(spec),
        )
        completed = subprocess.run(
            command,
            cwd=repository,
            env=_pyinstaller_environment(),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise PortableBuildError("portable build failed: pyinstaller_failed")
        candidate = work / "dist" / "CourseHarbor"
        audit_windows_portable(
            candidate,
            forbidden_roots=(*host_roots, work),
        )
        shutil.copytree(candidate, destination)

    result = audit_windows_portable(
        destination,
        forbidden_roots=(*host_roots, work),
    )
    manifest = destination.parent / f"{destination.name}-manifest.json"
    manifest.write_text(
        json.dumps(asdict(result), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_windows_portable(args.root, args.output)
    print(f"portable bundle ready: {args.output}")
    print(f"audit manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
