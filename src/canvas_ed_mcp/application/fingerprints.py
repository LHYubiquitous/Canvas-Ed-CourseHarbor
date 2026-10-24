"""Deterministic SHA-256 fingerprints for reviewed application plans."""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import json
import math
from collections.abc import Mapping, Sequence, Set
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePath


MISSING_REVISION = "missing"


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _normalize(value: object) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical fingerprints require finite numbers")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("canonical fingerprints require finite decimals")
        return {"$decimal": str(value.normalize())}
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Enum):
        return _normalize(value.value)
    if isinstance(value, PurePath):
        return {"$path": value.as_posix()}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("canonical datetimes must include a timezone")
        return {"$datetime": value.isoformat()}
    if isinstance(value, date):
        return {"$date": value.isoformat()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _normalize(getattr(value, item.name))
            for item in dataclasses.fields(value)
        }
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _normalize(model_dump(mode="python"))
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical mapping keys must be strings")
            normalized[key] = _normalize(item)
        return normalized
    if isinstance(value, Set):
        items = [_normalize(item) for item in value]
        items.sort(key=_json_bytes)
        return {"$set": items}
    if isinstance(value, Sequence):
        return [_normalize(item) for item in value]
    raise TypeError(f"unsupported canonical fingerprint type: {type(value).__name__}")


def canonical_sha256(value: object) -> str:
    """Hash a canonical, type-safe representation of application data."""

    return hashlib.sha256(_json_bytes(_normalize(value))).hexdigest()


def bytes_revision(payload: bytes) -> str:
    """Return the exact-byte SHA-256 revision of an immutable payload."""

    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    return hashlib.sha256(payload).hexdigest()


def file_revision(path: Path) -> str:
    """Return an exact-byte file revision, or an explicit missing marker."""

    selected = Path(path)
    if not selected.exists():
        return MISSING_REVISION
    if not selected.is_file():
        raise ValueError("revision target must be a regular file")
    try:
        payload = selected.read_bytes()
    except FileNotFoundError:
        return MISSING_REVISION
    return bytes_revision(payload)


__all__ = [
    "MISSING_REVISION",
    "bytes_revision",
    "canonical_sha256",
    "file_revision",
]
