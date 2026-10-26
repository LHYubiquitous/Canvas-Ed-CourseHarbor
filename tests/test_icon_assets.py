from __future__ import annotations

import struct
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_metadata(path: Path) -> tuple[int, int, int]:
    payload = path.read_bytes()
    if len(payload) < 33 or payload[:8] != PNG_SIGNATURE:
        raise AssertionError(f"invalid PNG: {path.name}")
    chunk_length = struct.unpack_from(">I", payload, 8)[0]
    if chunk_length != 13 or payload[12:16] != b"IHDR":
        raise AssertionError(f"missing PNG IHDR: {path.name}")
    width, height, _depth, color_type = struct.unpack_from(">IIBB", payload, 16)
    return width, height, color_type


class IconAssetTests(unittest.TestCase):
    def test_runtime_icon_is_square_transparent_and_readable(self) -> None:
        runtime_icon = (
            ROOT / "src" / "canvas_ed_mcp" / "ui" / "assets" / "courseharbor.png"
        )
        width, height, color_type = _png_metadata(runtime_icon)
        self.assertEqual((512, 512), (width, height))
        self.assertIn(color_type, {4, 6})

    def test_windows_ico_contains_reviewed_sizes(self) -> None:
        payload = (ROOT / "packaging" / "windows" / "courseharbor.ico").read_bytes()
        reserved, image_type, count = struct.unpack_from("<HHH", payload)
        self.assertEqual((0, 1, 7), (reserved, image_type, count))
        dimensions: set[int] = set()
        for index in range(count):
            width, height = struct.unpack_from("<BB", payload, 6 + (index * 16))
            dimensions.add(256 if width == 0 else width)
            self.assertEqual(width, height)
        self.assertEqual({16, 24, 32, 48, 64, 128, 256}, dimensions)

    def test_macos_iconset_has_exact_native_dimensions(self) -> None:
        iconset = ROOT / "packaging" / "macos" / "courseharbor.iconset"
        expected = {
            "icon_16x16.png": 16,
            "icon_16x16@2x.png": 32,
            "icon_32x32.png": 32,
            "icon_32x32@2x.png": 64,
            "icon_128x128.png": 128,
            "icon_128x128@2x.png": 256,
            "icon_256x256.png": 256,
            "icon_256x256@2x.png": 512,
            "icon_512x512.png": 512,
            "icon_512x512@2x.png": 1024,
        }
        self.assertEqual(set(expected), {path.name for path in iconset.iterdir()})
        for filename, size in expected.items():
            width, height, _color_type = _png_metadata(iconset / filename)
            self.assertEqual((size, size), (width, height))

    def test_macos_spec_uses_native_icon_and_stable_bundle_identity(self) -> None:
        text = (ROOT / "packaging" / "macos" / "courseharbor.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn("COURSEHARBOR_MACOS_ICON", text)
        self.assertIn("io.github.lhyubiquitous.courseharbor", text)
        self.assertIn('name="CourseHarbor.app"', text)
        self.assertIn('name="courseharbor-cli"', text)

    def test_macos_cli_is_a_standalone_companion_not_a_merged_executable(self) -> None:
        text = (ROOT / "packaging" / "macos" / "courseharbor.spec").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("MERGE(", text)
        self.assertIn("cli_analysis.binaries", text)
        self.assertIn("cli_analysis.datas", text)
        self.assertIn("exclude_binaries=False", text)


if __name__ == "__main__":
    unittest.main()
