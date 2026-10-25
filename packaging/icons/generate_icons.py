"""Generate checked-in CourseHarbor desktop icon derivatives from the master PNG."""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "packaging" / "icons" / "courseharbor-source.png"
RUNTIME_PNG = (
    ROOT / "src" / "canvas_ed_mcp" / "ui" / "assets" / "courseharbor.png"
)
WINDOWS_ICO = ROOT / "packaging" / "windows" / "courseharbor.ico"
MACOS_ICONSET = ROOT / "packaging" / "macos" / "courseharbor.iconset"

ICONSET_SIZES = {
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
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _scaled(source: QImage, size: int) -> QImage:
    return source.scaled(
        size,
        size,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("icon generation failed: buffer")
    if not image.save(buffer, "PNG"):
        raise RuntimeError("icon generation failed: png")
    return bytes(buffer.data())


def _write_ico(source: QImage, destination: Path) -> None:
    payloads = tuple(_png_bytes(_scaled(source, size)) for size in ICO_SIZES)
    offset = 6 + (16 * len(payloads))
    entries: list[bytes] = []
    for size, payload in zip(ICO_SIZES, payloads):
        dimension = 0 if size == 256 else size
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension,
                dimension,
                0,
                0,
                1,
                32,
                len(payload),
                offset,
            )
        )
        offset += len(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(
        struct.pack("<HHH", 0, 1, len(payloads))
        + b"".join(entries)
        + b"".join(payloads)
    )


def generate() -> None:
    source = QImage(str(SOURCE))
    if source.isNull() or source.width() != source.height() or not source.hasAlphaChannel():
        raise RuntimeError("icon generation failed: invalid_source")

    RUNTIME_PNG.parent.mkdir(parents=True, exist_ok=True)
    if not _scaled(source, 512).save(str(RUNTIME_PNG), "PNG"):
        raise RuntimeError("icon generation failed: runtime_png")

    MACOS_ICONSET.mkdir(parents=True, exist_ok=True)
    for filename, size in ICONSET_SIZES.items():
        if not _scaled(source, size).save(str(MACOS_ICONSET / filename), "PNG"):
            raise RuntimeError("icon generation failed: macos_iconset")

    _write_ico(source, WINDOWS_ICO)


if __name__ == "__main__":
    generate()
