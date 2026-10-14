"""Secret references, safe backend diagnostics, and shared redaction."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping, Protocol


_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
_BEARER = re.compile(r"(?i)(\bBearer\s+)[^\s,;]+")
_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:access_token|token|signature|sig|verifier|api_key|key|credential|"
    r"x-amz-[^=&#\s]+|x-goog-[^=&#\s]+)=)[^&#\s]*"
)
_URL_FRAGMENT = re.compile(r"(?i)(https?://[^\s#]+)#[^\s'\"<>]*")
_UNSAFE_BACKEND_MARKERS = ("fail", "null", "plaintext")


class KeyringBackend(Protocol):
    priority: object

    def get_password(self, service: str, username: str) -> str | None: ...

    def set_password(self, service: str, username: str, password: str) -> None: ...


class SecretUnavailable(RuntimeError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class KeyringStatus:
    available: bool
    category: str
    backend: str


def redact_text(value: object) -> str:
    """Redact bearer credentials and common signed-query values."""
    text = str(value)
    text = _BEARER.sub(r"\1[REDACTED]", text)
    text = _SENSITIVE_QUERY.sub(r"\1[REDACTED]", text)
    return _URL_FRAGMENT.sub(r"\1#[REDACTED]", text)


def _default_backend() -> KeyringBackend:
    try:
        import keyring
    except ImportError as exc:
        raise SecretUnavailable(
            "missing_dependency", "keyring support is not installed"
        ) from exc
    return keyring.get_keyring()


def keyring_status(*, backend: KeyringBackend | None = None) -> KeyringStatus:
    """Describe backend readiness without reading or printing any secret."""
    if backend is None:
        try:
            backend = _default_backend()
        except SecretUnavailable as exc:
            return KeyringStatus(False, exc.category, "unavailable")
    backend_name = f"{type(backend).__module__}.{type(backend).__qualname__}"
    try:
        priority = float(backend.priority)
    except (TypeError, ValueError):
        priority = 0.0
    lowered = backend_name.casefold()
    if priority <= 0 or any(marker in lowered for marker in _UNSAFE_BACKEND_MARKERS):
        return KeyringStatus(False, "unsafe_backend", backend_name)
    return KeyringStatus(True, "ready", backend_name)


def _parse_keyring_reference(reference: str) -> tuple[str, str]:
    body = reference.removeprefix("keyring:")
    service, separator, account = body.rpartition("/")
    if not separator or not service or not account:
        raise SecretUnavailable(
            "invalid_reference", "keyring reference must contain service/account"
        )
    return service, account


def resolve_secret(
    reference: str,
    *,
    environ: Mapping[str, str] | None = None,
    backend: KeyringBackend | None = None,
) -> str:
    """Resolve an env/keyring reference without placing the value in logs or argv."""
    if reference.startswith("env:"):
        name = reference.removeprefix("env:")
        if not _ENV_NAME.fullmatch(name):
            raise SecretUnavailable("invalid_reference", "invalid environment reference")
        value = (environ if environ is not None else os.environ).get(name)
        if not value:
            raise SecretUnavailable("missing_env", f"environment variable {name} is not set")
        return value
    if reference.startswith("keyring:"):
        service, account = _parse_keyring_reference(reference)
        selected = backend or _default_backend()
        status = keyring_status(backend=selected)
        if not status.available:
            raise SecretUnavailable(status.category, "secure keyring backend is unavailable")
        try:
            value = selected.get_password(service, account)
        except Exception as exc:
            raise SecretUnavailable("backend_error", "keyring lookup failed") from exc
        if not value:
            raise SecretUnavailable("missing_keyring", "keyring entry is not set")
        return value
    raise SecretUnavailable("invalid_reference", "secret reference must use env: or keyring:")


def store_secret(
    reference: str, value: str, *, backend: KeyringBackend | None = None
) -> None:
    """Store a value only in a verified keyring backend; env references are read-only."""
    if not value:
        raise ValueError("secret value cannot be empty")
    if not reference.startswith("keyring:"):
        raise SecretUnavailable("read_only_reference", "only keyring references can be stored")
    service, account = _parse_keyring_reference(reference)
    selected = backend or _default_backend()
    status = keyring_status(backend=selected)
    if not status.available:
        raise SecretUnavailable(status.category, "secure keyring backend is unavailable")
    try:
        selected.set_password(service, account, value)
    except Exception as exc:
        raise SecretUnavailable("backend_error", "keyring write failed") from exc
