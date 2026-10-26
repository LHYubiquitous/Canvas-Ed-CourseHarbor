"""Build, smoke-test, and audit an unsigned CourseHarbor macOS DMG."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

try:
    from ci.release_gate import read_project_version
    from ci.verify_macos_bundle import (
        MacOSBundleAudit,
        _mach_architectures,
        _mach_dependencies,
        _verify_codesign,
        audit_macos_bundle,
        macos_bundle_versions,
    )
except ModuleNotFoundError:
    from release_gate import read_project_version
    from verify_macos_bundle import (
        MacOSBundleAudit,
        _mach_architectures,
        _mach_dependencies,
        _verify_codesign,
        audit_macos_bundle,
        macos_bundle_versions,
    )


class MacOSDMGBuildError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MacOSDMGAudit:
    artifact_name: str
    architecture: str
    dmg_bytes: int
    dmg_sha256: str
    app: MacOSBundleAudit
    signing: str = "ad-hoc"
    notarization: str = "NOT_RUN"
    gatekeeper: str = "DEGRADED"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_architecture(value: str) -> str:
    normalized = value.strip().casefold()
    aliases = {"aarch64": "arm64", "amd64": "x86_64"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"arm64", "x86_64"}:
        raise MacOSDMGBuildError("dmg build failed: unsupported_architecture")
    return normalized


def dmg_artifact_name(version: str, architecture: str) -> str:
    return f"CourseHarbor-v{version}-macos-{_normalized_architecture(architecture)}.dmg"


def _pyinstaller_environment() -> dict[str, str]:
    environment = dict(os.environ)
    path_entries = (
        Path(sys.executable).resolve().parent,
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/sbin"),
        Path("/sbin"),
    )
    environment["PATH"] = os.pathsep.join(str(path) for path in path_entries)
    environment["PYTHONNOUSERSITE"] = "1"
    environment.pop("PYTHONPATH", None)
    return environment


def _build_host_roots(repository: Path) -> tuple[Path, ...]:
    return (
        repository,
        Path.home().resolve(strict=False),
        Path(sys.executable).resolve().parent,
        Path(sys.base_prefix).resolve(),
    )


def _run(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    environment: dict[str, str],
    stage: str,
) -> str:
    completed = subprocess.run(
        tuple(str(value) for value in command),
        cwd=cwd,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise MacOSDMGBuildError(f"dmg build failed: {stage}")
    return completed.stdout


def _attachment_records(payload: bytes) -> tuple[tuple[str, str | None], ...]:
    try:
        document = plistlib.loads(payload)
    except (plistlib.InvalidFileException, ValueError):
        return ()
    if not isinstance(document, dict):
        return ()
    entities = document.get("system-entities")
    if not isinstance(entities, list):
        return ()
    records: list[tuple[str, str | None]] = []
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        device = entity.get("dev-entry")
        mount = entity.get("mount-point")
        if isinstance(device, str):
            records.append((device, mount if isinstance(mount, str) else None))
    return tuple(records)


def _best_effort_detach(
    targets: Sequence[str | Path], *, cwd: Path, environment: dict[str, str]
) -> None:
    for target in reversed(tuple(dict.fromkeys(str(value) for value in targets))):
        subprocess.run(
            ("/usr/bin/hdiutil", "detach", target, "-force"),
            cwd=cwd,
            env=environment,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _attach_readonly(
    image: Path, mountpoint: Path, *, cwd: Path, environment: dict[str, str]
) -> str:
    completed = subprocess.run(
        (
            "/usr/bin/hdiutil",
            "attach",
            "-plist",
            "-readonly",
            "-nobrowse",
            "-mountpoint",
            str(mountpoint),
            str(image),
        ),
        cwd=cwd,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    records = _attachment_records(completed.stdout)
    cleanup_targets = tuple(device for device, _mount in records) or (mountpoint,)
    if completed.returncode != 0:
        _best_effort_detach(cleanup_targets, cwd=cwd, environment=environment)
        raise MacOSDMGBuildError("dmg build failed: hdiutil_attach_failed")
    expected_mount = str(mountpoint)
    device = next(
        (candidate for candidate, mount in records if mount == expected_mount),
        None,
    )
    if device is None:
        _best_effort_detach(cleanup_targets, cwd=cwd, environment=environment)
        raise MacOSDMGBuildError("dmg build failed: hdiutil_attach_contract")
    return device


def _publish_create_only(
    candidate_dmg: Path,
    candidate_manifest: Path,
    destination: Path,
    manifest_destination: Path,
) -> None:
    dmg_published = False
    try:
        os.link(candidate_dmg, destination)
        dmg_published = True
        os.link(candidate_manifest, manifest_destination)
    except FileExistsError as exc:
        if dmg_published:
            destination.unlink(missing_ok=True)
        raise MacOSDMGBuildError("dmg build failed: output_exists") from exc
    except OSError as exc:
        if dmg_published:
            destination.unlink(missing_ok=True)
        raise MacOSDMGBuildError("dmg build failed: publish_failed") from exc


def _install_cli_companion(app: Path, standalone_cli: Path) -> Path:
    if (
        not standalone_cli.is_file()
        or standalone_cli.is_symlink()
        or not app.is_dir()
    ):
        raise MacOSDMGBuildError("dmg build failed: cli_companion_missing")
    destination = app / "Contents" / "MacOS" / "courseharbor-cli"
    if destination.exists() or destination.is_symlink():
        raise MacOSDMGBuildError(
            "dmg build failed: cli_companion_destination_exists"
        )
    try:
        with standalone_cli.open("rb") as source, destination.open("xb") as target:
            shutil.copyfileobj(source, target)
        destination.chmod(0o755)
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise MacOSDMGBuildError(
            "dmg build failed: cli_companion_install_failed"
        ) from exc
    return destination


def _smoke_bundle(app: Path, *, cwd: Path, environment: dict[str, str]) -> None:
    smoke_environment = dict(environment)
    smoke_environment["QT_QPA_PLATFORM"] = "offscreen"
    _run(
        (app / "Contents" / "MacOS" / "CourseHarbor", "--bundle-smoke"),
        cwd=cwd,
        environment=smoke_environment,
        stage="gui_smoke_failed",
    )
    cli = app / "Contents" / "MacOS" / "courseharbor-cli"
    _run(
        (cli, "--version"),
        cwd=cwd,
        environment=smoke_environment,
        stage="cli_startup_failed",
    )
    completed = subprocess.run(
        (
            str(cli),
            "--bundle-smoke",
        ),
        cwd=cwd,
        env=smoke_environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    category = "invalid_output"
    for line in reversed(completed.stdout.splitlines()):
        try:
            document = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        candidate = document.get("keyring") if isinstance(document, dict) else None
        if isinstance(candidate, str) and candidate in {
            "backend_error",
            "missing_dependency",
            "ready",
            "unsafe_backend",
        }:
            category = candidate
            break
    if completed.returncode != 0 or category != "ready":
        raise MacOSDMGBuildError(f"dmg build failed: cli_smoke_failed:{category}")


def build_macos_dmg(root: Path, output: Path) -> Path:
    if sys.platform != "darwin":
        raise MacOSDMGBuildError("dmg build failed: macos_required")
    repository = root.resolve(strict=True)
    destination = output.resolve(strict=False)
    manifest_destination = destination.with_suffix(destination.suffix + ".manifest.json")
    if destination.exists() or manifest_destination.exists():
        raise MacOSDMGBuildError("dmg build failed: output_exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    spec = repository / "packaging" / "macos" / "courseharbor.spec"
    iconset = repository / "packaging" / "macos" / "courseharbor.iconset"
    if not spec.is_file() or not iconset.is_dir():
        raise MacOSDMGBuildError("dmg build failed: packaging_inputs_missing")

    version = read_project_version(repository)
    short_version, bundle_version = macos_bundle_versions(version)
    architecture = _normalized_architecture(platform.machine())
    if destination.name != dmg_artifact_name(version, architecture):
        raise MacOSDMGBuildError("dmg build failed: artifact_name_mismatch")
    environment = _pyinstaller_environment()
    host_roots = _build_host_roots(repository)

    with tempfile.TemporaryDirectory(
        prefix=".courseharbor-macos-", dir=destination.parent
    ) as temporary:
        work = Path(temporary)
        icon = work / "courseharbor.icns"
        _run(
            ("/usr/bin/iconutil", "-c", "icns", "-o", icon, iconset),
            cwd=repository,
            environment=environment,
            stage="iconutil_failed",
        )
        build_environment = dict(environment)
        build_environment.update(
            {
                "COURSEHARBOR_MACOS_ICON": str(icon),
                "COURSEHARBOR_SHORT_VERSION": short_version,
                "COURSEHARBOR_BUNDLE_VERSION": bundle_version,
                "MACOSX_DEPLOYMENT_TARGET": "14.0",
            }
        )
        _run(
            (
                sys.executable,
                "-m",
                "PyInstaller",
                "--clean",
                "--noconfirm",
                "--distpath",
                work / "dist",
                "--workpath",
                work / "build",
                spec,
            ),
            cwd=repository,
            environment=build_environment,
            stage="pyinstaller_failed",
        )
        app = work / "dist" / "CourseHarbor.app"
        _install_cli_companion(app, work / "dist" / "courseharbor-cli")
        _run(
            (
                "/usr/bin/codesign",
                "--force",
                "--deep",
                "--sign",
                "-",
                "--timestamp=none",
                app,
            ),
            cwd=repository,
            environment=environment,
            stage="adhoc_codesign_failed",
        )
        audit_macos_bundle(
            app,
            expected_version=version,
            forbidden_roots=(repository, work),
            build_environment_roots=host_roots[1:],
            expected_architecture=architecture,
            architecture_reader=_mach_architectures,
            dependency_reader=_mach_dependencies,
            signature_verifier=_verify_codesign,
        )
        _smoke_bundle(app, cwd=work, environment=environment)

        staging = work / "dmg-root"
        shutil.copytree(app, staging / "CourseHarbor.app", symlinks=True)
        os.symlink("/Applications", staging / "Applications")
        candidate_dmg = work / destination.name
        _run(
            (
                "/usr/bin/hdiutil",
                "create",
                "-volname",
                "CourseHarbor",
                "-srcfolder",
                staging,
                "-ov",
                "-format",
                "UDZO",
                candidate_dmg,
            ),
            cwd=repository,
            environment=environment,
            stage="hdiutil_create_failed",
        )
        _run(
            ("/usr/bin/hdiutil", "verify", candidate_dmg),
            cwd=repository,
            environment=environment,
            stage="hdiutil_verify_failed",
        )

        mountpoint = work / "mounted"
        mountpoint.mkdir()
        attached_device: str | None = None
        try:
            attached_device = _attach_readonly(
                candidate_dmg,
                mountpoint,
                cwd=repository,
                environment=environment,
            )
            mounted_app = mountpoint / "CourseHarbor.app"
            app_audit = audit_macos_bundle(
                mounted_app,
                expected_version=version,
                forbidden_roots=(repository, work),
                build_environment_roots=host_roots[1:],
                expected_architecture=architecture,
                architecture_reader=_mach_architectures,
                dependency_reader=_mach_dependencies,
                signature_verifier=_verify_codesign,
            )
            applications_link = mountpoint / "Applications"
            if not applications_link.is_symlink() or os.readlink(applications_link) != "/Applications":
                raise MacOSDMGBuildError("dmg build failed: applications_link_invalid")
            _smoke_bundle(mounted_app, cwd=mountpoint, environment=environment)
        finally:
            if attached_device is not None:
                _run(
                    ("/usr/bin/hdiutil", "detach", attached_device, "-force"),
                    cwd=repository,
                    environment=environment,
                    stage="hdiutil_detach_failed",
                )

        result = MacOSDMGAudit(
            artifact_name=destination.name,
            architecture=architecture,
            dmg_bytes=candidate_dmg.stat().st_size,
            dmg_sha256=_sha256(candidate_dmg),
            app=app_audit,
        )
        candidate_manifest = work / manifest_destination.name
        candidate_manifest.write_text(
            json.dumps(asdict(result), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _publish_create_only(
            candidate_dmg,
            candidate_manifest,
            destination,
            manifest_destination,
        )
    return manifest_destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_macos_dmg(args.root, args.output)
    print(f"macOS DMG ready: {args.output}")
    print(f"audit manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
