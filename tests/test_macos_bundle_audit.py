from __future__ import annotations

import os
import plistlib
import tempfile
import unittest
from pathlib import Path

from ci.verify_macos_bundle import (
    MacOSBundleAuditError,
    audit_macos_bundle,
    macos_bundle_versions,
)


class MacOSBundleAuditTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
        bundle = root / "CourseHarbor.app"
        macos = bundle / "Contents" / "MacOS"
        resources = bundle / "Contents" / "Resources"
        legal = resources / "legal"
        platforms = (
            bundle
            / "Contents"
            / "Frameworks"
            / "PySide6"
            / "Qt"
            / "plugins"
            / "platforms"
        )
        macos.mkdir(parents=True)
        resources.mkdir(parents=True)
        legal.mkdir(parents=True)
        platforms.mkdir(parents=True)
        gui = macos / "CourseHarbor"
        cli = macos / "courseharbor-cli"
        gui.write_bytes(b"gui")
        cli.write_bytes(b"cli")
        gui.chmod(0o755)
        cli.chmod(0o755)
        (resources / "courseharbor.icns").write_bytes(b"icon")
        (legal / "LICENSE").write_text("license", encoding="utf-8")
        (legal / "THIRD_PARTY_NOTICES.md").write_text("notices", encoding="utf-8")
        (platforms / "libqcocoa.dylib").write_bytes(b"qt")
        with (bundle / "Contents" / "Info.plist").open("wb") as stream:
            plistlib.dump(
                {
                    "CFBundleDisplayName": "CourseHarbor",
                    "CFBundleExecutable": "CourseHarbor",
                    "CFBundleIconFile": "courseharbor.icns",
                    "CFBundleIdentifier": "io.github.lhyubiquitous.courseharbor",
                    "CFBundleName": "CourseHarbor",
                    "CFBundlePackageType": "APPL",
                    "CFBundleShortVersionString": "0.2.0",
                    "CFBundleVersion": "0.2.0fc18",
                    "LSMinimumSystemVersion": "14.0",
                    "NSHighResolutionCapable": True,
                },
                stream,
                sort_keys=True,
            )
        return bundle

    def test_version_mapping_is_explicit_for_rc18(self) -> None:
        self.assertEqual(("0.2.0", "0.2.0fc18"), macos_bundle_versions("0.2.0rc18"))
        with self.assertRaisesRegex(MacOSBundleAuditError, "invalid_version"):
            macos_bundle_versions("0.2.0.dev18")

    def test_inventory_requires_gui_cli_icon_plist_and_qt_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            result = audit_macos_bundle(
                bundle,
                expected_version="0.2.0rc18",
                expected_architecture="arm64",
                architecture_reader=lambda _path: ("arm64",),
                signature_verifier=lambda _path: True,
            )
        self.assertEqual("io.github.lhyubiquitous.courseharbor", result.bundle_identifier)
        self.assertEqual("0.2.0", result.short_version)
        self.assertEqual("0.2.0fc18", result.bundle_version)
        self.assertEqual(("arm64",), result.architectures)
        self.assertTrue(result.qt_platform_plugin.endswith("libqcocoa.dylib"))

    def test_bundle_metadata_must_match_the_product_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            plist_path = bundle / "Contents" / "Info.plist"
            with plist_path.open("rb") as stream:
                metadata = plistlib.load(stream)
            metadata["CFBundleIdentifier"] = "invalid.example"
            with plist_path.open("wb") as stream:
                plistlib.dump(metadata, stream)
            with self.assertRaisesRegex(MacOSBundleAuditError, "bundle_identifier"):
                audit_macos_bundle(bundle, expected_version="0.2.0rc18")

    def test_gui_and_cli_architectures_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))

            def architectures(path: Path) -> tuple[str, ...]:
                return ("arm64",) if path.name == "CourseHarbor" else ("x86_64",)

            with self.assertRaisesRegex(MacOSBundleAuditError, "architecture_mismatch"):
                audit_macos_bundle(
                    bundle,
                    expected_version="0.2.0rc18",
                    architecture_reader=architectures,
                )

    def test_matching_entrypoints_must_include_the_artifact_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            with self.assertRaisesRegex(MacOSBundleAuditError, "architecture_mismatch"):
                audit_macos_bundle(
                    bundle,
                    expected_version="0.2.0rc18",
                    expected_architecture="arm64",
                    architecture_reader=lambda _path: ("x86_64",),
                )

    def test_nested_native_payload_must_include_the_artifact_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            nested = bundle / "Contents" / "Frameworks" / "native.so"
            nested.write_bytes(b"native")

            def architectures(path: Path) -> tuple[str, ...]:
                return ("x86_64",) if path.name == "native.so" else ("arm64",)

            with self.assertRaisesRegex(MacOSBundleAuditError, "architecture_mismatch"):
                audit_macos_bundle(
                    bundle,
                    expected_version="0.2.0rc18",
                    expected_architecture="arm64",
                    architecture_reader=architectures,
                )

    def test_native_payload_may_not_link_to_an_external_toolchain_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            with self.assertRaisesRegex(
                MacOSBundleAuditError, "external_dependency"
            ) as caught:
                audit_macos_bundle(
                    bundle,
                    expected_version="0.2.0rc18",
                    expected_architecture="arm64",
                    architecture_reader=lambda _path: ("arm64",),
                    dependency_reader=lambda _path: (
                        "/opt/toolchain/private/libexternal.dylib",
                    ),
                )
        self.assertNotIn("/opt/toolchain", str(caught.exception))

    def test_build_machine_paths_fail_without_reflecting_the_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root)
            private_root = root / "private-owner"
            (bundle / "Contents" / "Resources" / "payload.bin").write_bytes(
                str(private_root).encode("utf-8")
            )
            with self.assertRaisesRegex(MacOSBundleAuditError, "host_path") as caught:
                audit_macos_bundle(
                    bundle,
                    expected_version="0.2.0rc18",
                    forbidden_roots=(private_root,),
                )
        self.assertNotIn("private-owner", str(caught.exception))

    def test_only_verified_macho_may_contain_a_native_compile_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root)
            private_root = root / "private-owner"
            python_binary = (
                bundle
                / "Contents"
                / "Frameworks"
                / "Python.framework"
                / "Versions"
                / "3.14"
                / "Python"
            )
            python_binary.parent.mkdir(parents=True)
            python_binary.write_bytes(str(private_root).encode("utf-8"))

            def architectures(path: Path) -> tuple[str, ...]:
                if path.name == "build-prefix.txt":
                    return ()
                return ("arm64",)

            audit_macos_bundle(
                bundle,
                expected_version="0.2.0rc18",
                build_environment_roots=(private_root,),
                expected_architecture="arm64",
                architecture_reader=architectures,
            )

            adjacent = python_binary.with_name("build-prefix.txt")
            adjacent.write_bytes(str(private_root).encode("utf-8"))
            with self.assertRaisesRegex(MacOSBundleAuditError, "host_path"):
                audit_macos_bundle(
                    bundle,
                    expected_version="0.2.0rc18",
                    build_environment_roots=(private_root,),
                    expected_architecture="arm64",
                    architecture_reader=architectures,
                )

            adjacent.unlink()
            with self.assertRaisesRegex(MacOSBundleAuditError, "host_path"):
                audit_macos_bundle(
                    bundle,
                    expected_version="0.2.0rc18",
                    forbidden_roots=(private_root,),
                    expected_architecture="arm64",
                    architecture_reader=architectures,
                )

    def test_only_upstream_sbom_metadata_may_contain_build_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle(root)
            private_root = root / "private-owner"
            upstream_sbom = (
                bundle
                / "Contents"
                / "Resources"
                / "cryptography-50.0.2.dist-info"
                / "sboms"
                / "cryptography-rust.cyclonedx.json"
            )
            upstream_sbom.parent.mkdir(parents=True)
            upstream_sbom.write_bytes(str(private_root).encode("utf-8"))

            audit_macos_bundle(
                bundle,
                expected_version="0.2.0rc18",
                build_environment_roots=(private_root,),
            )

            with self.assertRaisesRegex(MacOSBundleAuditError, "host_path"):
                audit_macos_bundle(
                    bundle,
                    expected_version="0.2.0rc18",
                    forbidden_roots=(private_root,),
                )

            ordinary_metadata = upstream_sbom.parents[1] / "METADATA"
            ordinary_metadata.write_bytes(str(private_root).encode("utf-8"))
            with self.assertRaisesRegex(MacOSBundleAuditError, "host_path"):
                audit_macos_bundle(
                    bundle,
                    expected_version="0.2.0rc18",
                    build_environment_roots=(private_root,),
                )

    def test_complete_private_key_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            secret = bundle / "Contents" / "Resources" / "secret.pem"
            begin = b"-----BEGIN " + b"PRIVATE KEY-----\n"
            end = b"\n-----END " + b"PRIVATE KEY-----\n"
            secret.write_bytes(
                begin + (b"A" * 96) + end
            )
            with self.assertRaisesRegex(MacOSBundleAuditError, "private_key"):
                audit_macos_bundle(bundle, expected_version="0.2.0rc18")

    def test_symlink_may_not_escape_the_application_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle = self._bundle(Path(temporary))
            link = bundle / "Contents" / "Resources" / "escape"
            try:
                os.symlink("../../../../outside", link)
            except OSError as exc:  # pragma: no cover - Windows policy dependent
                self.skipTest(f"symlink creation unavailable: {exc}")
            with self.assertRaisesRegex(MacOSBundleAuditError, "symlink_escape"):
                audit_macos_bundle(bundle, expected_version="0.2.0rc18")


if __name__ == "__main__":
    unittest.main()
