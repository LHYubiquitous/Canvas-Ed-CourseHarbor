"""Regenerate locked release metadata and compare all stable semantics."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[no-redef]
from pathlib import Path
from typing import Any, Mapping


class MetadataAuditError(RuntimeError):
    """Raised without reflecting dependency URLs or metadata content."""


def _stable_sbom(document: Mapping[str, Any]) -> dict[str, Any]:
    stable = copy.deepcopy(dict(document))
    stable.pop("serialNumber", None)
    metadata = stable.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("timestamp", None)
    return stable


def compare_generated_metadata(
    tracked_pylock: Mapping[str, Any],
    generated_pylock: Mapping[str, Any],
    tracked_sbom: Mapping[str, Any],
    generated_sbom: Mapping[str, Any],
) -> None:
    """Compare complete TOML semantics and SBOM semantics except volatile identity/time."""
    if tracked_pylock != generated_pylock:
        raise MetadataAuditError("release metadata audit failed: stale_pylock")
    if _stable_sbom(tracked_sbom) != _stable_sbom(generated_sbom):
        raise MetadataAuditError("release metadata audit failed: stale_sbom")


def _export(root: Path, output: Path, format_name: str) -> None:
    command = [
        "uv",
        "export",
        "--locked",
        "--no-sources",
        "--no-build",
        "--no-dev",
        "--all-extras",
        "--no-emit-project",
        "--format",
        format_name,
        "--output-file",
        str(output),
    ]
    try:
        subprocess.run(
            command,
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MetadataAuditError("release metadata audit failed: export_failed") from exc


def verify_generated_metadata(root: str | Path) -> None:
    repository = Path(root)
    tracked_pylock = tomllib.loads(
        (repository / "pylock.toml").read_text(encoding="utf-8")
    )
    tracked_sbom = json.loads(
        (repository / "sbom.cdx.json").read_text(encoding="utf-8")
    )
    with tempfile.TemporaryDirectory() as temporary:
        generated_root = Path(temporary)
        generated_pylock_path = generated_root / "pylock.toml"
        generated_sbom_path = generated_root / "sbom.cdx.json"
        _export(repository, generated_pylock_path, "pylock.toml")
        _export(repository, generated_sbom_path, "cyclonedx1.5")
        generated_pylock = tomllib.loads(
            generated_pylock_path.read_text(encoding="utf-8")
        )
        generated_sbom = json.loads(
            generated_sbom_path.read_text(encoding="utf-8")
        )
    compare_generated_metadata(
        tracked_pylock,
        generated_pylock,
        tracked_sbom,
        generated_sbom,
    )


def main() -> int:
    verify_generated_metadata(Path.cwd())
    print("release metadata audit passed: pylock.toml and sbom.cdx.json are current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
