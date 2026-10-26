"""Fail-closed inventory, metadata, and host-path audit for CourseHarbor.app."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable


class MacOSBundleAuditError(RuntimeError):
    """Raised without reflecting private bytes from the bundle."""


_PRIVATE_KEY_PEM = re.compile(
    rb"-----BEGIN (?P<kind>(?:ENCRYPTED |RSA |DSA |EC |OPENSSH )?PRIVATE KEY)-----"
    rb"\r?\n[A-Za-z0-9+/=\r\n]{80,}"
    rb"-----END (?P=kind)-----"
)
_RC_VERSION = re.compile(r"^(?P<release>\d+\.\d+\.\d+)rc(?P<number>\d+)$")
_UPSTREAM_SBOM_METADATA = re.compile(
    r"^Contents/Resources/[^/]+\.dist-info/sboms/[^/]+\.json$"
)
_BUNDLE_IDENTIFIER = "io.github.lhyubiquitous.courseharbor"


@dataclass(frozen=True, slots=True)
class MacOSBundleAudit:
    file_count: int
    symlink_count: int
    total_bytes: int
    gui_sha256: str
    cli_sha256: str
    bundle_identifier: str
    short_version: str
    bundle_version: str
    architectures: tuple[str, ...]
    qt_platform_plugin: str
    signature_status: str


def macos_bundle_versions(version: str) -> tuple[str, str]:
    match = _RC_VERSION.fullmatch(version)
    if match is None:
        raise MacOSBundleAuditError("macos bundle audit failed: invalid_version")
    release = match.group("release")
    return release, f"{release}fc{match.group('number')}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _encoded_needles(paths: Iterable[Path]) -> tuple[bytes, ...]:
    needles: list[bytes] = []
    for path in paths:
        for text in {str(path), str(path.resolve(strict=False))}:
            needles.extend((text.encode("utf-8"), text.encode("utf-16-le")))
    return tuple(value.lower() for value in needles if value)


def _contains_private_key(payload: bytes) -> bool:
    if _PRIVATE_KEY_PEM.search(payload):
        return True
    utf16_text = payload.decode("utf-16-le", errors="ignore")
    return _PRIVATE_KEY_PEM.search(utf16_text.encode("ascii", errors="ignore")) is not None


def _mach_architectures(path: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        ("/usr/bin/lipo", "-archs", str(path)),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        return ()
    values = tuple(sorted(set(completed.stdout.split())))
    if not values or any(value not in {"arm64", "x86_64"} for value in values):
        raise MacOSBundleAuditError("macos bundle audit failed: unsupported_architecture")
    return values


def _mach_dependencies(path: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        ("/usr/bin/otool", "-L", str(path)),
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise MacOSBundleAuditError("macos bundle audit failed: dependency_read_failed")
    dependencies: list[str] = []
    for line in completed.stdout.splitlines()[1:]:
        value = line.strip().split(" (", 1)[0]
        if value:
            dependencies.append(value)
    return tuple(dependencies)


def _verify_codesign(bundle: Path) -> bool:
    completed = subprocess.run(
        ("/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


def _load_plist(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            value = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise MacOSBundleAuditError("macos bundle audit failed: invalid_plist") from exc
    if not isinstance(value, dict):
        raise MacOSBundleAuditError("macos bundle audit failed: invalid_plist")
    return value


def _validate_symlink(root: Path, path: Path) -> None:
    try:
        raw_target = os.readlink(path)
    except OSError as exc:
        raise MacOSBundleAuditError("macos bundle audit failed: invalid_symlink") from exc
    target = Path(raw_target)
    if target.is_absolute():
        raise MacOSBundleAuditError("macos bundle audit failed: symlink_escape")
    resolved = (path.parent / target).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise MacOSBundleAuditError("macos bundle audit failed: symlink_escape") from exc


def audit_macos_bundle(
    bundle: str | Path,
    *,
    expected_version: str,
    forbidden_roots: Iterable[str | Path] = (),
    build_environment_roots: Iterable[str | Path] = (),
    expected_architecture: str | None = None,
    architecture_reader: Callable[[Path], tuple[str, ...]] | None = None,
    dependency_reader: Callable[[Path], tuple[str, ...]] | None = None,
    signature_verifier: Callable[[Path], bool] | None = None,
) -> MacOSBundleAudit:
    root = Path(bundle).resolve(strict=True)
    if not root.is_dir() or root.suffix != ".app":
        raise MacOSBundleAuditError("macos bundle audit failed: missing_root")
    contents = root / "Contents"
    gui = contents / "MacOS" / "CourseHarbor"
    cli = contents / "MacOS" / "courseharbor-cli"
    plist_path = contents / "Info.plist"
    icon = contents / "Resources" / "courseharbor.icns"
    license_path = contents / "Resources" / "legal" / "LICENSE"
    notices_path = contents / "Resources" / "legal" / "THIRD_PARTY_NOTICES.md"
    if not all(
        path.is_file()
        for path in (gui, cli, plist_path, icon, license_path, notices_path)
    ):
        raise MacOSBundleAuditError("macos bundle audit failed: incomplete_bundle")
    if os.name != "nt" and not all(
        path.stat().st_mode & stat.S_IXUSR for path in (gui, cli)
    ):
        raise MacOSBundleAuditError("macos bundle audit failed: non_executable_entrypoint")

    short_version, bundle_version = macos_bundle_versions(expected_version)
    metadata = _load_plist(plist_path)
    expected_metadata = {
        "CFBundleDisplayName": "CourseHarbor",
        "CFBundleExecutable": "CourseHarbor",
        "CFBundleIconFile": "courseharbor.icns",
        "CFBundleIdentifier": _BUNDLE_IDENTIFIER,
        "CFBundleName": "CourseHarbor",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": short_version,
        "CFBundleVersion": bundle_version,
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
    }
    for key, expected in expected_metadata.items():
        if metadata.get(key) != expected:
            marker = "bundle_identifier" if key == "CFBundleIdentifier" else "bundle_metadata"
            raise MacOSBundleAuditError(f"macos bundle audit failed: {marker}")

    entries = tuple(sorted(root.rglob("*")))
    symlinks = tuple(path for path in entries if path.is_symlink())
    for path in symlinks:
        _validate_symlink(root, path)
    files = tuple(path for path in entries if path.is_file() and not path.is_symlink())
    relative = tuple(path.relative_to(root).as_posix() for path in files)
    qt_plugins = tuple(
        name for name in relative if name.casefold().endswith("/platforms/libqcocoa.dylib")
    )
    if len(qt_plugins) != 1:
        raise MacOSBundleAuditError("macos bundle audit failed: qt_platform_plugin")
    forbidden_names = (".env", "credentials_local", "claude_desktop_config", "api_token")
    if any(marker in name.casefold() for name in relative for marker in forbidden_names):
        raise MacOSBundleAuditError("macos bundle audit failed: private_filename")

    needles = _encoded_needles(Path(value) for value in forbidden_roots)
    build_environment_needles = _encoded_needles(
        Path(value) for value in build_environment_roots
    )
    architecture_cache: dict[Path, tuple[str, ...]] = {}

    def read_architectures(path: Path) -> tuple[str, ...]:
        if path not in architecture_cache:
            architecture_cache[path] = (
                architecture_reader(path) if architecture_reader is not None else ()
            )
        return architecture_cache[path]

    build_root_native_files: set[Path] = set()
    for path in files:
        relative_name = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        lowered = payload.lower()
        if _contains_private_key(payload):
            raise MacOSBundleAuditError(
                f"macos bundle audit failed: private_key:{relative_name}"
            )
        if any(needle in lowered for needle in needles):
            raise MacOSBundleAuditError(
                f"macos bundle audit failed: host_path:{relative_name}"
            )
        if any(needle in lowered for needle in build_environment_needles):
            if read_architectures(path):
                build_root_native_files.add(path)
            elif _UPSTREAM_SBOM_METADATA.fullmatch(relative_name) is None:
                raise MacOSBundleAuditError(
                    f"macos bundle audit failed: host_path:{relative_name}"
                )

    architectures: tuple[str, ...] = ()
    if architecture_reader is not None:
        gui_architectures = read_architectures(gui)
        cli_architectures = read_architectures(cli)
        if not gui_architectures or not cli_architectures:
            raise MacOSBundleAuditError("macos bundle audit failed: invalid_macho")
        if gui_architectures != cli_architectures:
            raise MacOSBundleAuditError("macos bundle audit failed: architecture_mismatch")
        if expected_architecture is not None and expected_architecture not in gui_architectures:
            raise MacOSBundleAuditError("macos bundle audit failed: architecture_mismatch")
        native_candidates = set(build_root_native_files)
        native_candidates.update(
            path
            for path in files
            if path in {gui, cli}
            or path.suffix.casefold() in {".dylib", ".so", ".bundle"}
            or path.stat().st_mode & stat.S_IXUSR
        )
        qt_plugin_path = root.joinpath(*qt_plugins[0].split("/"))
        if qt_plugin_path not in native_candidates:
            native_candidates.add(qt_plugin_path)
        for path in sorted(native_candidates):
            native_architectures = read_architectures(path)
            requires_macho = (
                path in {gui, cli, qt_plugin_path}
                or path in build_root_native_files
                or path.suffix.casefold() in {".dylib", ".so", ".bundle"}
            )
            if requires_macho and not native_architectures:
                raise MacOSBundleAuditError("macos bundle audit failed: invalid_macho")
            if (
                native_architectures
                and expected_architecture is not None
                and expected_architecture not in native_architectures
            ):
                raise MacOSBundleAuditError("macos bundle audit failed: architecture_mismatch")
            if native_architectures and dependency_reader is not None:
                for dependency in dependency_reader(path):
                    if not dependency.startswith(
                        ("@", "/System/Library/", "/usr/lib/")
                    ):
                        relative_name = path.relative_to(root).as_posix()
                        raise MacOSBundleAuditError(
                            "macos bundle audit failed: "
                            f"external_dependency:{relative_name}"
                        )
        architectures = gui_architectures
    signature_status = "NOT_RUN"
    if signature_verifier is not None:
        if not signature_verifier(root):
            raise MacOSBundleAuditError("macos bundle audit failed: invalid_signature")
        signature_status = "adhoc-verified"

    return MacOSBundleAudit(
        file_count=len(files),
        symlink_count=len(symlinks),
        total_bytes=sum(path.stat().st_size for path in files),
        gui_sha256=_sha256(gui),
        cli_sha256=_sha256(cli),
        bundle_identifier=_BUNDLE_IDENTIFIER,
        short_version=short_version,
        bundle_version=bundle_version,
        architectures=architectures,
        qt_platform_plugin=qt_plugins[0],
        signature_status=signature_status,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--forbid-root", action="append", default=[])
    parser.add_argument("--architecture", choices=("arm64", "x86_64"), required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    result = audit_macos_bundle(
        args.bundle,
        expected_version=args.version,
        forbidden_roots=args.forbid_root,
        expected_architecture=args.architecture,
        architecture_reader=_mach_architectures,
        dependency_reader=_mach_dependencies,
        signature_verifier=_verify_codesign,
    )
    rendered = json.dumps(asdict(result), indent=2, sort_keys=True) + "\n"
    if args.manifest is not None:
        args.manifest.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
