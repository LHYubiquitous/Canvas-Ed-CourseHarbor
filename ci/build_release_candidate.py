"""Build one immutable, deterministic, audited local release-candidate packet."""

from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Sequence

try:
    from ci.release_gate import build_source_archive, read_project_version
    from ci.verify_distribution import AuditResult, audit_archive
    from ci.verify_release_metadata import verify_generated_metadata
except ModuleNotFoundError:  # direct `python ci/build_release_candidate.py`
    from release_gate import build_source_archive, read_project_version
    from verify_distribution import AuditResult, audit_archive
    from verify_release_metadata import verify_generated_metadata


WARNING_BOUNDARIES = (
    "canvas_course_files_authorization_partial",
    "ed_human_verified_with_warnings",
    "linux_owner_device_not_run",
    "macos_0_2_owner_matrix_not_run",
    "macos_real_ed_not_run",
    "publication_not_run",
    "pypi_not_authorized",
    "remote_ci_not_run",
    "windows_python_315_prerelease_dependency_abi_degraded",
)


class ReleaseCandidateError(RuntimeError):
    """Stable failure that never reflects build output or private content."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_extract_source(archive: Path, destination: Path, version: str) -> Path:
    prefix = f"canvas-ed-mcp-{version}"
    destination.mkdir(parents=True, exist_ok=False)
    resolved_destination = destination.resolve()
    try:
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            for member in members:
                path = PurePosixPath(member.name)
                if (
                    "\\" in member.name
                    or any(
                        ord(character) < 32 or ord(character) == 127
                        for character in member.name
                    )
                    or path.is_absolute()
                    or not path.parts
                    or path.parts[0] != prefix
                    or ".." in path.parts
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                    or not (member.isdir() or member.isfile())
                ):
                    raise ReleaseCandidateError("candidate build failed: unsafe_source_archive")
                target = destination.joinpath(*path.parts).resolve()
                try:
                    target.relative_to(resolved_destination)
                except ValueError as exc:
                    raise ReleaseCandidateError(
                        "candidate build failed: unsafe_source_archive"
                    ) from exc
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                payload = source.extractfile(member)
                if payload is None:
                    raise ReleaseCandidateError(
                        "candidate build failed: unsafe_source_archive"
                    )
                with payload, target.open("xb") as output:
                    shutil.copyfileobj(payload, output)
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseCandidateError("candidate build failed: unreadable_source_archive") from exc
    root = destination / prefix
    if not root.is_dir():
        raise ReleaseCandidateError("candidate build failed: missing_source_root")
    return root


def _build_distribution(source: Path, destination: Path, epoch: int) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=False)
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": str(epoch),
        }
    )
    try:
        completed = subprocess.run(
            (
                sys.executable,
                "-m",
                "build",
                "--no-isolation",
                "--wheel",
                "--sdist",
                "--outdir",
                str(destination),
            ),
            cwd=source,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise ReleaseCandidateError("candidate build failed: build_unavailable") from exc
    if completed.returncode != 0:
        raise ReleaseCandidateError("candidate build failed: distribution_build_failed")
    wheels = tuple(destination.glob("*.whl"))
    sdists = tuple(destination.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseCandidateError("candidate build failed: unexpected_distribution_set")
    _canonicalize_sdist(sdists[0], epoch)
    return wheels[0], sdists[0]


def _canonicalize_sdist(archive: Path, epoch: int) -> None:
    """Rewrite backend-created sdist metadata into a reproducible tar.gz."""
    temporary = archive.with_name(f".{archive.name}.canonical")
    try:
        with tarfile.open(archive, "r:gz") as source:
            members = sorted(source.getmembers(), key=lambda item: item.name)
            with temporary.open("wb") as raw:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw, mtime=epoch
                ) as zipped:
                    with tarfile.open(
                        fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT
                    ) as destination:
                        for member in members:
                            item = copy.copy(member)
                            item.mtime = epoch
                            item.uid = 0
                            item.gid = 0
                            item.uname = ""
                            item.gname = ""
                            item.pax_headers = {}
                            payload = source.extractfile(member) if member.isfile() else None
                            destination.addfile(item, payload)
        temporary.replace(archive)
    except (OSError, tarfile.TarError) as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReleaseCandidateError("candidate build failed: sdist_canonicalization_failed") from exc


def _require_identical(first: Path, second: Path, category: str) -> None:
    if first.name != second.name or first.read_bytes() != second.read_bytes():
        raise ReleaseCandidateError(f"candidate build failed: nondeterministic_{category}")


def _artifact_record(
    path: Path,
    *,
    role: str,
    entries: int | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "filename": path.name,
        "role": role,
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }
    if entries is not None:
        record["archive_entry_count"] = entries
    return record


def build_candidate_packet(
    root: str | Path,
    output_dir: str | Path,
    *,
    source_date_epoch: int,
) -> Path:
    repository = Path(root).resolve()
    destination = Path(output_dir).resolve()
    if source_date_epoch < 0:
        raise ReleaseCandidateError("candidate build failed: invalid_source_date_epoch")
    if destination.exists():
        raise ReleaseCandidateError("candidate build failed: output_exists")
    verify_generated_metadata(repository)
    destination.parent.mkdir(parents=True, exist_ok=True)
    version = read_project_version(repository)
    source_name = f"canvas-ed-mcp-{version}-source.tar.gz"

    with tempfile.TemporaryDirectory(
        prefix=f".{destination.name}.building-", dir=destination.parent
    ) as temporary:
        work = Path(temporary)
        source_a = build_source_archive(
            repository,
            work / "source-a" / source_name,
            version=version,
            source_date_epoch=source_date_epoch,
        )
        source_b = build_source_archive(
            repository,
            work / "source-b" / source_name,
            version=version,
            source_date_epoch=source_date_epoch,
        )
        _require_identical(source_a.archive, source_b.archive, "source")

        extracted_a = _safe_extract_source(source_a.archive, work / "extract-a", version)
        extracted_b = _safe_extract_source(source_b.archive, work / "extract-b", version)
        wheel_a, sdist_a = _build_distribution(extracted_a, work / "dist-a", source_date_epoch)
        wheel_b, sdist_b = _build_distribution(extracted_b, work / "dist-b", source_date_epoch)
        _require_identical(wheel_a, wheel_b, "wheel")
        _require_identical(sdist_a, sdist_b, "sdist")
        wheel_audit: AuditResult = audit_archive(wheel_a)
        sdist_audit: AuditResult = audit_archive(sdist_a)

        packet = work / "packet"
        packet.mkdir()
        wheel = packet / wheel_a.name
        sdist = packet / sdist_a.name
        source = packet / source_name
        shutil.copyfile(wheel_a, wheel)
        shutil.copyfile(sdist_a, sdist)
        shutil.copyfile(source_a.archive, source)

        source_manifest = packet / f"{source_name}.manifest.json"
        source_manifest.write_text(
            json.dumps(
                {
                    "archive": source_name,
                    "files": list(source_a.files),
                    "sha256": source_a.sha256,
                    "source_date_epoch": source_date_epoch,
                    "version": version,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (packet / f"{source_name}.sha256").write_text(
            f"{source_a.sha256}  {source_name}\n", encoding="ascii"
        )

        artifacts = (
            _artifact_record(wheel, role="wheel", entries=wheel_audit.entries),
            _artifact_record(sdist, role="sdist", entries=sdist_audit.entries),
            _artifact_record(source, role="clean_source", entries=len(source_a.files)),
            _artifact_record(source_manifest, role="clean_source_manifest"),
        )
        candidate_manifest = {
            "artifacts": sorted(artifacts, key=lambda item: str(item["filename"])),
            "deterministic_verification": {
                "clean_source": "PASS",
                "sdist": "PASS",
                "wheel": "PASS",
            },
            "project_version": version,
            "publication": "NOT_RUN",
            "release_manifest_sha256": _sha256(repository / "release-manifest.txt"),
            "schema_version": 1,
            "source_date_epoch": source_date_epoch,
            "warning_boundaries": list(WARNING_BOUNDARIES),
        }
        (packet / "candidate-manifest.json").write_text(
            json.dumps(candidate_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        checksum_files = (wheel, sdist, source, source_manifest)
        (packet / "SHA256SUMS").write_text(
            "".join(
                f"{_sha256(path)}  {path.name}\n"
                for path in sorted(checksum_files, key=lambda item: item.name)
            ),
            encoding="ascii",
        )
        try:
            packet.rename(destination)
        except OSError as exc:
            raise ReleaseCandidateError("candidate build failed: publish_failed") from exc
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        packet = build_candidate_packet(
            args.root,
            args.output_dir,
            source_date_epoch=args.source_date_epoch,
        )
    except (ReleaseCandidateError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"release candidate packet ready: {packet.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
