"""Dependency-neutral validation shared by config and sync domain models."""

from __future__ import annotations

import re


_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "conin$",
    "conout$",
    "nul",
    "prn",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
    *(f"com{number}" for number in "¹²³"),
    *(f"lpt{number}" for number in "¹²³"),
}
_WINDOWS_INVALID_CHARS = frozenset('<>:"/\\|?*')


def validate_provider_instance(value: str) -> str:
    """Validate canonical provider authority form: lowercase host[:port]."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("instance must be a non-empty canonical string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("instance cannot contain control characters")
    if value != value.casefold():
        raise ValueError("instance must already be canonical lowercase")
    if any(character in value for character in "/\\?#@[]"):
        raise ValueError("instance must be a canonical host with optional port")
    if value.count(":") > 1:
        raise ValueError("instance cannot contain an ambiguous port")
    if ":" in value:
        host, port_text = value.rsplit(":", 1)
        if not port_text.isascii() or not port_text.isdigit():
            raise ValueError("instance port must be numeric")
        port = int(port_text)
        if port < 1 or port > 65535 or port == 443:
            raise ValueError("instance must omit the default HTTPS port")
    else:
        host = value
    if len(host) > 253:
        raise ValueError("instance host is too long")
    labels = host.split(".")
    if not labels or any(not _HOST_LABEL_RE.fullmatch(label) for label in labels):
        raise ValueError("instance host is not canonical")
    return value


def validate_portable_component(value: str, *, field: str) -> str:
    """Validate one cross-platform path component without changing it."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty path component")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field} cannot contain control characters")
    device_name = value.split(".", 1)[0].casefold()
    if (
        value in {".", ".."}
        or any(character in _WINDOWS_INVALID_CHARS for character in value)
        or value.endswith((" ", "."))
        or device_name in _WINDOWS_RESERVED_NAMES
    ):
        raise ValueError(f"{field} is not a portable path component")
    return value
