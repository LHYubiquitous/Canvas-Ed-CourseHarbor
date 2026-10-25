"""Fail-closed inventory and host-path audit for a Windows onedir bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable


class PortableBundleAuditError(RuntimeError):
    """Raised without reflecting private bytes from the bundle."""


_PRIVATE_KEY_PEM = re.compile(
    rb"-----BEGIN (?P<kind>(?:ENCRYPTED |RSA |DSA |EC |OPENSSH )?PRIVATE KEY)-----"
    rb"\r?\n[A-Za-z0-9+/=\r\n]{80,}"
    rb"-----END (?P=kind)-----"
)


@dataclass(frozen=True, slots=True)
class PortableBundleAudit:
    file_count: int
    total_bytes: int
    gui_sha256: str
    cli_sha256: str
    qt_platform_plugin: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pe_subsystem(path: Path) -> int:
    image = None
    try:
        import pefile

        image = pefile.PE(str(path), fast_load=True)
        return int(image.OPTIONAL_HEADER.Subsystem)
    except Exception as exc:
        raise PortableBundleAuditError("portable bundle audit failed: invalid_pe") from exc
    finally:
        if image is not None:
            image.close()


def _pe_has_icon(path: Path) -> bool:
    image = None
    try:
        import pefile

        image = pefile.PE(str(path), fast_load=False)
        resources = getattr(image, "DIRECTORY_ENTRY_RESOURCE", None)
        return bool(
            resources
            and any(entry.id == 14 for entry in resources.entries)
        )
    except Exception as exc:
        raise PortableBundleAuditError("portable bundle audit failed: invalid_pe") from exc
    finally:
        if image is not None:
            image.close()


def _encoded_needles(paths: Iterable[Path]) -> tuple[bytes, ...]:
    needles: list[bytes] = []
    for path in paths:
        raw = str(path)
        resolved = str(path.resolve(strict=False))
        for text in {raw, resolved}:
            for variant in {text, text.replace("\\", "/")}:
                needles.extend((variant.encode("utf-8"), variant.encode("utf-16-le")))
    return tuple(value.lower() for value in needles if value)


def _contains_private_key(payload: bytes) -> bool:
    if _PRIVATE_KEY_PEM.search(payload):
        return True
    utf16_text = payload.decode("utf-16-le", errors="ignore")
    return _PRIVATE_KEY_PEM.search(utf16_text.encode("ascii", errors="ignore")) is not None


def audit_windows_portable(
    bundle: str | Path,
    *,
    forbidden_roots: Iterable[str | Path] = (),
    subsystem_reader: Callable[[Path], int] = _pe_subsystem,
    icon_reader: Callable[[Path], bool] = _pe_has_icon,
) -> PortableBundleAudit:
    root = Path(bundle).resolve(strict=True)
    if not root.is_dir():
        raise PortableBundleAuditError("portable bundle audit failed: missing_root")
    gui = root / "CourseHarbor.exe"
    cli = root / "courseharbor-cli.exe"
    internal = root / "_internal"
    if not gui.is_file() or not cli.is_file() or not internal.is_dir():
        raise PortableBundleAuditError("portable bundle audit failed: incomplete_bundle")
    if subsystem_reader(gui) != 2:
        raise PortableBundleAuditError("portable bundle audit failed: gui_has_console")
    if subsystem_reader(cli) != 3:
        raise PortableBundleAuditError("portable bundle audit failed: cli_has_no_console")
    if not icon_reader(gui) or not icon_reader(cli):
        raise PortableBundleAuditError("portable bundle audit failed: missing_icon_resource")

    entries = tuple(sorted(root.rglob("*")))
    if any(
        path.is_symlink()
        or (hasattr(path, "is_junction") and path.is_junction())
        for path in entries
    ):
        raise PortableBundleAuditError("portable bundle audit failed: symlink")
    files = tuple(path for path in entries if path.is_file())
    relative = tuple(path.relative_to(root).as_posix() for path in files)
    qt_plugins = tuple(
        name for name in relative if name.casefold().endswith("/platforms/qwindows.dll")
    )
    if len(qt_plugins) != 1:
        raise PortableBundleAuditError("portable bundle audit failed: qt_platform_plugin")
    forbidden_names = (".env", "credentials_local", "claude_desktop_config", "api_token")
    if any(marker in name.casefold() for name in relative for marker in forbidden_names):
        raise PortableBundleAuditError("portable bundle audit failed: private_filename")

    needles = _encoded_needles(Path(value) for value in forbidden_roots)
    for path in files:
        relative_name = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        lowered = payload.lower()
        if _contains_private_key(payload):
            raise PortableBundleAuditError(
                f"portable bundle audit failed: private_key:{relative_name}"
            )
        if any(needle in lowered for needle in needles):
            raise PortableBundleAuditError(
                f"portable bundle audit failed: host_path:{relative_name}"
            )

    return PortableBundleAudit(
        file_count=len(files),
        total_bytes=sum(path.stat().st_size for path in files),
        gui_sha256=_sha256(gui),
        cli_sha256=_sha256(cli),
        qt_platform_plugin=qt_plugins[0],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--forbid-root", action="append", default=[])
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    result = audit_windows_portable(args.bundle, forbidden_roots=args.forbid_root)
    rendered = json.dumps(asdict(result), indent=2, sort_keys=True) + "\n"
    if args.manifest is not None:
        args.manifest.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
