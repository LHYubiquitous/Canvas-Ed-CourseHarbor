from __future__ import annotations

import sys
import tarfile
import tempfile
import unittest
import json
import re
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[no-redef]
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from ci.release_gate import (
    ReleaseAuditError,
    audit_public_files,
    build_source_archive,
    read_project_version,
    select_public_files,
)
from ci.verify_release_metadata import MetadataAuditError, compare_generated_metadata


class ReleaseGateTests(unittest.TestCase):
    def test_project_version_reader_rejects_runtime_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "src/canvas_ed_mcp"
            package.mkdir(parents=True)
            (root / "pyproject.toml").write_text(
                '[project]\nname = "canvas-ed-mcp"\nversion = "0.2.0rc4"\n',
                encoding="utf-8",
            )
            (package / "__init__.py").write_text(
                '__version__ = "0.2.0rc11"\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ReleaseAuditError, "version_mismatch"):
                read_project_version(root)

    def test_repository_release_metadata_is_complete_and_consistent(self) -> None:
        project = tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        package_init = (REPOSITORY_ROOT / "src/canvas_ed_mcp/__init__.py").read_text(
            encoding="utf-8"
        )
        version_match = re.search(r'^__version__ = "([^"]+)"$', package_init, re.MULTILINE)
        self.assertIsNotNone(version_match)
        version = project["project"]["version"]
        self.assertEqual("0.2.0rc11", version)
        self.assertEqual(version, version_match.group(1))
        development_dependencies = project["dependency-groups"]["dev"]
        runtime_dependencies = project["project"]["dependencies"]
        self.assertIn("tzdata>=2024.1; sys_platform == 'win32'", runtime_dependencies)
        self.assertTrue(any(item.startswith("build") for item in development_dependencies))
        self.assertTrue(any(item.startswith("setuptools") for item in development_dependencies))
        self.assertTrue(any(item.startswith("wheel") for item in development_dependencies))

        pylock = tomllib.loads((REPOSITORY_ROOT / "pylock.toml").read_text("utf-8"))
        uvlock = tomllib.loads((REPOSITORY_ROOT / "uv.lock").read_text("utf-8"))
        sbom = json.loads((REPOSITORY_ROOT / "sbom.cdx.json").read_text("utf-8"))
        self.assertEqual("CycloneDX", sbom["bomFormat"])
        self.assertEqual("1.5", sbom["specVersion"])
        self.assertEqual(version, sbom["metadata"]["component"]["version"])
        self.assertEqual(
            [{"vendor": "Astral Software Inc.", "name": "uv", "version": "0.11.28"}],
            sbom["metadata"]["tools"],
        )
        locked = {(item["name"].casefold(), item["version"]) for item in pylock["packages"]}
        components = {
            (item["name"].casefold(), item["version"]) for item in sbom["components"]
        }
        uv_packages = {
            (item["name"].casefold(), item["version"])
            for item in uvlock["package"]
            if item["name"] != project["project"]["name"]
        }
        development_only = {
            ("build", "1.6.1"),
            ("pyproject-hooks", "1.3.3"),
            ("setuptools", "84.0.0"),
            ("wheel", "0.48.0"),
        }
        self.assertEqual(uv_packages, locked | development_only)
        self.assertTrue(locked.isdisjoint(development_only))
        self.assertEqual(locked, components)

        notices = (REPOSITORY_ROOT / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        ).casefold()
        normalized_notices = re.sub(r"[-_.]+", "-", notices)
        for name, _ in components:
            normalized = re.sub(r"[-_.]+", "-", name)
            self.assertTrue(
                normalized in normalized_notices,
                f"third-party notice is missing {name}",
            )

        selected = set(select_public_files(REPOSITORY_ROOT))
        for private in (
            "AGENTS.md",
            "HANDOFF.md",
            "PROGRESS.md",
            "TASKS.md",
            "canvas_ed_mcp.py",
            "sync_courses.py",
            "docs/CANVAS_SYNC_MANUAL_TEST.md",
            "docs/ED_SYNC_MANUAL_TEST.md",
            "tests/test_mcp_config.py",
            "tests/test_sync_output.py",
        ):
            self.assertNotIn(private, selected)

    def test_public_file_selection_is_allowlist_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in (
                "README.md",
                "LICENSE",
                "pyproject.toml",
                "CHANGELOG.md",
                "CONTRIBUTING.md",
                "SECURITY.md",
                "THIRD_PARTY_NOTICES.md",
                "pylock.toml",
                "sbom.cdx.json",
                "uv.lock",
                "release-manifest.txt",
                "src/canvas_ed_mcp/__init__.py",
                "tests/test_safe.py",
                "tests/test_mcp_config.py",
                "tests/test_sync_output.py",
                "ci/release_gate.py",
                "docs/INSTALLATION.md",
                "canvas_ed_mcp.py",
                "sync_courses.py",
                "HANDOFF.md",
                "credentials_local.json",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("synthetic\n", encoding="utf-8")

            (root / "release-manifest.txt").write_text(
                "ci/release_gate.py\n"
                "docs/INSTALLATION.md\n"
                "release-manifest.txt\n"
                "src/canvas_ed_mcp/__init__.py\n"
                "tests/test_safe.py\n",
                encoding="utf-8",
            )

            selected = set(select_public_files(root, require_complete=False))

            self.assertEqual(
                selected,
                {
                    "release-manifest.txt",
                    "ci/release_gate.py",
                    "docs/INSTALLATION.md",
                    "src/canvas_ed_mcp/__init__.py",
                    "tests/test_safe.py",
                },
            )
            self.assertNotIn("tests/test_mcp_config.py", selected)
            self.assertNotIn("tests/test_sync_output.py", selected)
            self.assertNotIn("canvas_ed_mcp.py", selected)
            self.assertNotIn("sync_courses.py", selected)
            self.assertNotIn("HANDOFF.md", selected)
            self.assertNotIn("credentials_local.json", selected)

    def test_public_file_selection_rejects_unreviewed_eligible_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "release-manifest.txt").write_text(
                "release-manifest.txt\nsrc/canvas_ed_mcp/__init__.py\n",
                encoding="utf-8",
            )
            package = root / "src/canvas_ed_mcp"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("synthetic\n", encoding="utf-8")
            (package / "unreviewed.py").write_text("synthetic\n", encoding="utf-8")

            with self.assertRaisesRegex(ReleaseAuditError, "unreviewed_file"):
                select_public_files(root, require_complete=False)

    def test_content_audit_rejects_private_material_without_echoing_it(self) -> None:
        cases = {
            "private key": b"-----BEGIN " + b"PRIVATE KEY-----\nsynthetic-canary\n",
            "absolute home": b"D:" + b"\\Users\\private-person\\Desktop\\file.txt\n",
            "literal token": b"API_TOKEN=\"" + b"production-sensitive-value" + b"\"\n",
            "signed URL": (
                b"https://files.example.com/a?" + b"signature=" + b"real-secret-value\n"
            ),
            "provider-prefixed token": b"CANVAS_API_TOKEN=production-sensitive-value\n",
            "encrypted private key": b"-----BEGIN " + b"ENCRYPTED PRIVATE KEY-----\n",
            "AWS signed URL": (
                b"https://bucket.example.com/a?X-Amz-"
                + b"Signature=real-secret-value\n"
            ),
            "private-host signed URL": (
                b"http://127.0.0.1/a?" + b"signature=real-secret-value\n"
            ),
            "HTML escaped signed URL": (
                b"https://files.example.com/a?x=1&amp;"
                + b"signature=real-secret-value\n"
            ),
            "Windows slash home": b"C:/" + b"Users/private-person/file.txt\n",
            "Linux home": b"/home" + b"/private-person/file.txt\n",
            "known token shape": b"AKIA" + b"ABCDEFGHIJKLMNOP\n",
            "safe-word token bypass": (
                b"CANVAS_API_TOKEN=real-example-secret-material-123456\n"
            ),
            "safe-word signed URL bypass": (
                b"https://files.real.edu/x?X-Amz-"
                + b"Signature=actual-example-secret-123456\n"
            ),
        }
        for label, payload in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(ReleaseAuditError) as captured:
                    audit_public_files({"README.md": payload})
                self.assertNotIn("private-person", str(captured.exception))
                self.assertNotIn("real-secret-value", str(captured.exception))

    def test_content_audit_allows_placeholders_and_synthetic_test_urls(self) -> None:
        audit_public_files(
            {
                "README.md": b"C:\\Users\\<name>\\AppData and token=[REDACTED]\n",
                "tests/test_safe.py": (
                    b"https://files.example.test/a?signature=synthetic-canary\n"
                ),
            }
        )

    def test_release_metadata_comparison_rejects_semantic_mutations(self) -> None:
        pylock = {
            "packages": [
                {
                    "name": "dependency",
                    "version": "1.0",
                    "marker": "python_version >= '3.10'",
                    "wheels": [{"hashes": {"sha256": "abc"}}],
                }
            ]
        }
        sbom = {
            "serialNumber": "urn:uuid:first",
            "metadata": {"timestamp": "first", "component": {"name": "project"}},
            "components": [{"name": "dependency", "purl": "pkg:pypi/dependency@1.0"}],
            "dependencies": [{"ref": "dependency", "dependsOn": []}],
        }
        regenerated_sbom = json.loads(json.dumps(sbom))
        regenerated_sbom["serialNumber"] = "urn:uuid:second"
        regenerated_sbom["metadata"]["timestamp"] = "second"
        compare_generated_metadata(pylock, pylock, sbom, regenerated_sbom)

        mutations = []
        changed_hash = json.loads(json.dumps(pylock))
        changed_hash["packages"][0]["wheels"][0]["hashes"]["sha256"] = "changed"
        mutations.append((changed_hash, sbom))
        changed_marker = json.loads(json.dumps(pylock))
        changed_marker["packages"][0]["marker"] = "python_version >= '3.11'"
        mutations.append((changed_marker, sbom))
        changed_sbom = json.loads(json.dumps(sbom))
        changed_sbom["components"][0]["purl"] = "pkg:pypi/dependency@2.0"
        mutations.append((pylock, changed_sbom))
        changed_edge = json.loads(json.dumps(sbom))
        changed_edge["dependencies"][0]["dependsOn"] = ["other"]
        mutations.append((pylock, changed_edge))

        for generated_pylock, generated_sbom in mutations:
            with self.subTest(generated_pylock=generated_pylock, generated_sbom=generated_sbom):
                with self.assertRaises(MetadataAuditError):
                    compare_generated_metadata(
                        pylock,
                        generated_pylock,
                        sbom,
                        generated_sbom,
                    )

    def test_source_archive_is_deterministic_and_contains_only_selected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            output = Path(temporary) / "dist"
            root.mkdir()
            for relative in ("LICENSE", "release-manifest.txt", "src/canvas_ed_mcp/__init__.py"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative + "\n", encoding="utf-8")

            (root / "release-manifest.txt").write_text(
                "LICENSE\nrelease-manifest.txt\nsrc/canvas_ed_mcp/__init__.py\n",
                encoding="utf-8",
            )
            first = build_source_archive(
                root,
                output / "first.tar.gz",
                version="0.1.0rc1",
                source_date_epoch=1_700_000_000,
                require_complete=False,
            )
            second = build_source_archive(
                root,
                output / "second.tar.gz",
                version="0.1.0rc1",
                source_date_epoch=1_700_000_000,
                require_complete=False,
            )

            self.assertEqual(first.sha256, second.sha256)
            with tarfile.open(first.archive, "r:gz") as archive:
                names = tuple(member.name for member in archive.getmembers())
            self.assertEqual(
                names,
                (
                    "canvas-ed-mcp-0.1.0rc1/LICENSE",
                    "canvas-ed-mcp-0.1.0rc1/release-manifest.txt",
                    "canvas-ed-mcp-0.1.0rc1/src/canvas_ed_mcp/__init__.py",
                ),
            )

    def test_source_archive_rejects_traversal_and_unsafe_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            (root / "release-manifest.txt").write_text(
                "release-manifest.txt\n", encoding="utf-8"
            )
            (root.parent / "outside.txt").write_text("private\n", encoding="utf-8")

            with self.assertRaises(ReleaseAuditError):
                build_source_archive(
                    root,
                    root.parent / "bad.tar.gz",
                    version="0.1.0rc1",
                    files=("../outside.txt",),
                    source_date_epoch=1_700_000_000,
                )
            with self.assertRaises(ReleaseAuditError):
                build_source_archive(
                    root,
                    root.parent / "bad-version.tar.gz",
                    version="../0.1",
                    source_date_epoch=1_700_000_000,
                )


if __name__ == "__main__":
    unittest.main()
