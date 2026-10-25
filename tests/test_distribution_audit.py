from __future__ import annotations

import sys
import tarfile
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from ci.verify_distribution import (
    AuditError,
    _SDIST_REQUIRED,
    _WHEEL_PACKAGE_DATA,
    _WHEEL_PACKAGE_ENTRIES,
    _WHEEL_PACKAGE_FILES,
    audit_archive,
)


class DistributionAuditTests(unittest.TestCase):
    @staticmethod
    def _write_sdist(path: Path, extra: tuple[tuple[str, bytes], ...] = ()) -> None:
        required = tuple((name, b"synthetic") for name in sorted(_SDIST_REQUIRED))
        with tarfile.open(path, "w:gz") as archive:
            for relative, payload in (*required, *extra):
                item = tarfile.TarInfo(f"canvas_ed_mcp-0.1.0/{relative}")
                item.size = len(payload)
                archive.addfile(item, BytesIO(payload))

    def test_minimal_safe_wheel_manifest_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / "canvas_ed_mcp-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for name in (*sorted(_WHEEL_PACKAGE_ENTRIES),
                    "canvas_ed_mcp-0.1.0.dist-info/METADATA",
                    "canvas_ed_mcp-0.1.0.dist-info/entry_points.txt",
                    "canvas_ed_mcp-0.1.0.dist-info/licenses/LICENSE",
                ):
                    archive.writestr(name, b"synthetic")

            audit_archive(wheel)

    def test_wheel_allowlist_matches_repository_runtime_package(self) -> None:
        package_root = REPOSITORY_ROOT / "src" / "canvas_ed_mcp"
        actual = {
            path.relative_to(package_root.parent).as_posix()
            for path in package_root.rglob("*.py")
        }

        self.assertEqual(actual, _WHEEL_PACKAGE_FILES)

    def test_wheel_data_allowlist_matches_repository_runtime_assets(self) -> None:
        package_root = REPOSITORY_ROOT / "src" / "canvas_ed_mcp"
        actual = {
            path.relative_to(package_root.parent).as_posix()
            for path in package_root.rglob("*")
            if path.is_file()
            and path.suffix != ".py"
            and "__pycache__" not in path.parts
        }
        self.assertEqual(actual, _WHEEL_PACKAGE_DATA)

    def test_wheel_rejects_private_or_non_package_entries(self) -> None:
        for unsafe in (
            "credentials_local.json",
            "Course Material Sync.command",
            "tests/test_private.py",
            "canvas_ed_mcp/__pycache__/server.pyc",
            "../escape.txt",
        ):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as temporary:
                wheel = Path(temporary) / "canvas_ed_mcp-0.1.0-py3-none-any.whl"
                with zipfile.ZipFile(wheel, "w") as archive:
                    for name in _WHEEL_PACKAGE_ENTRIES:
                        archive.writestr(name, b"")
                    archive.writestr(unsafe, b"LEAK_CANARY")
                with self.assertRaises(AuditError):
                    audit_archive(wheel)

    def test_wheel_rejects_nested_credentials_and_env_variants(self) -> None:
        for unsafe in (
            "canvas_ed_mcp/credentials/account.json",
            "canvas_ed_mcp/private/.env.production",
        ):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as temporary:
                wheel = Path(temporary) / "canvas_ed_mcp-0.1.0-py3-none-any.whl"
                with zipfile.ZipFile(wheel, "w") as archive:
                    for name in _WHEEL_PACKAGE_ENTRIES:
                        archive.writestr(name, b"")
                    archive.writestr(unsafe, b"LEAK_CANARY")
                with self.assertRaises(AuditError):
                    audit_archive(wheel)

    def test_wheel_rejects_missing_runtime_module(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            wheel = Path(temporary) / "canvas_ed_mcp-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                for name in _WHEEL_PACKAGE_ENTRIES - {"canvas_ed_mcp/sync/model.py"}:
                    archive.writestr(name, b"")
            with self.assertRaises(AuditError):
                audit_archive(wheel)

    def test_minimal_safe_sdist_manifest_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sdist = Path(temporary) / "canvas_ed_mcp-0.1.0.tar.gz"
            self._write_sdist(sdist)

            audit_archive(sdist)

    def test_sdist_rejects_links_and_path_traversal(self) -> None:
        for unsafe, link in (("../escape.txt", False), ("linked-secret", True)):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as temporary:
                sdist = Path(temporary) / "canvas_ed_mcp-0.1.0.tar.gz"
                with tarfile.open(sdist, "w:gz") as archive:
                    item = tarfile.TarInfo(f"canvas_ed_mcp-0.1.0/{unsafe}")
                    if link:
                        item.type = tarfile.SYMTYPE
                        item.linkname = "credentials_local.json"
                    else:
                        item.size = 0
                    archive.addfile(item)
                with self.assertRaises(AuditError):
                    audit_archive(sdist)

    def test_sdist_rejects_env_prefix_and_unallowlisted_content(self) -> None:
        for unsafe in (".envrc", "lecture-notes.pdf"):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as temporary:
                sdist = Path(temporary) / "canvas_ed_mcp-0.1.0.tar.gz"
                self._write_sdist(sdist, ((unsafe, b"LEAK_CANARY"),))
                with self.assertRaises(AuditError):
                    audit_archive(sdist)


if __name__ == "__main__":
    unittest.main()
