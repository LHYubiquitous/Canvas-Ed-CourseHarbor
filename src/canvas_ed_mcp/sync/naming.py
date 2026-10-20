"""Pure, portable destination naming for synchronized course content."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import PurePosixPath

from .model import RemoteItem, RemoteKind, validate_output_relative_path


_GROUPS = {
    RemoteKind.FILE: "Files",
    RemoteKind.PAGE: "Pages",
    RemoteKind.LESSON: "Lessons",
    RemoteKind.RESOURCE: "Resources",
}
_RESERVED = {
    "con", "prn", "aux", "nul", "clock$", "conin$", "conout$",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
    *(f"com{number}" for number in "¹²³"),
    *(f"lpt{number}" for number in "¹²³"),
}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _safe_stem(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", normalized).strip(" .")
    clean = re.sub(r"\s+", " ", clean) or "item"
    if clean.split(".", 1)[0].casefold() in _RESERVED:
        clean = f"_{clean}"
    while len(clean.encode("utf-8")) > 96:
        clean = clean[:-1]
    return clean or "item"


def _safe_extension(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]", "_", value)
    if not clean.startswith(".") or clean in {".", ".."}:
        return ""
    return clean[:20]


def destination_for(
    course_root: PurePosixPath,
    item: RemoteItem,
    *,
    ordinal: int = 0,
) -> PurePosixPath:
    """Return a deterministic revision-specific path within one course root."""
    course_root = validate_output_relative_path(course_root)
    if not isinstance(item, RemoteItem):
        raise ValueError("item must be a RemoteItem")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("ordinal must be a non-negative integer")

    supplied = PurePosixPath(item.display_name).name
    extension = _safe_extension(PurePosixPath(supplied).suffix)
    if item.key.kind in {RemoteKind.PAGE, RemoteKind.LESSON}:
        extension = ".html"
    stem_source = supplied[: -len(PurePosixPath(supplied).suffix)] if PurePosixPath(supplied).suffix else supplied
    stem = _safe_stem(stem_source)
    identity = "\0".join(str(value) for value in item.key.identity_tuple)
    suffix = f"--{_digest(identity)}--rev-{_digest(item.signature)}"
    if ordinal:
        suffix += f"--{ordinal}"
    group_root = course_root / _GROUPS[item.key.kind]
    if item.group is not None:
        group_root /= _safe_stem(item.group)
    destination = group_root / f"{stem}{suffix}{extension}"
    return validate_output_relative_path(destination)
