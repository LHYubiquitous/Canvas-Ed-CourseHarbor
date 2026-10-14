"""Run a synthetic macOS Keychain round-trip without exposing the canary."""

from __future__ import annotations

import hmac
import json
import secrets
import uuid
from dataclasses import asdict, dataclass
from typing import Any

SERVICE = "canvas-ed-mcp/mac-release-canary"
_UNSAFE_BACKEND_MARKERS = ("fail", "null", "plaintext")


@dataclass(frozen=True, slots=True)
class CanaryOutcome:
    backend_available: bool
    backend_category: str
    round_trip: bool
    cleanup_confirmed: bool
    error_category: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _backend_status(backend: Any, *, injected_for_test: bool = False) -> tuple[bool, str]:
    backend_name = f"{type(backend).__module__}.{type(backend).__qualname__}"
    try:
        priority = float(backend.priority)
    except (TypeError, ValueError):
        priority = 0.0
    if priority <= 0:
        return False, "unsafe_backend"
    if injected_for_test:
        return True, "ready"
    if any(marker in backend_name.casefold() for marker in _UNSAFE_BACKEND_MARKERS):
        return False, "unsafe_backend"
    if not injected_for_test and backend_name != "keyring.backends.macOS.Keyring":
        return False, "not_macos_keychain"
    return True, "ready"


def run_canary(
    *,
    backend: Any | None = None,
    canary: str | None = None,
    account: str | None = None,
) -> CanaryOutcome:
    """Store, compare, and remove one random synthetic Keychain value."""
    injected_for_test = backend is not None
    if backend is None:
        import keyring

        backend = keyring.get_keyring()
    available, category = _backend_status(
        backend, injected_for_test=injected_for_test
    )
    if not available:
        return CanaryOutcome(False, category, False, False, category)

    selected_canary = canary or secrets.token_urlsafe(32)
    selected_account = account or f"synthetic-{uuid.uuid4().hex}"
    round_trip = False
    cleanup_confirmed = False
    error_category: str | None = None
    try:
        backend.set_password(SERVICE, selected_account, selected_canary)
        observed = backend.get_password(SERVICE, selected_account)
        round_trip = observed is not None and hmac.compare_digest(
            observed, selected_canary
        )
        if not round_trip:
            error_category = "round_trip_mismatch"
    except Exception:
        error_category = "backend_operation_failed"
    finally:
        try:
            backend.delete_password(SERVICE, selected_account)
        except Exception:
            pass
        try:
            cleanup_confirmed = backend.get_password(SERVICE, selected_account) is None
        except Exception:
            cleanup_confirmed = False
        if not cleanup_confirmed:
            error_category = "cleanup_failed"

    return CanaryOutcome(
        True,
        category,
        round_trip,
        cleanup_confirmed,
        error_category,
    )


def main() -> int:
    outcome = run_canary()
    print(json.dumps(outcome.as_dict(), sort_keys=True))
    return 0 if outcome.round_trip and outcome.cleanup_confirmed else 1


if __name__ == "__main__":
    raise SystemExit(main())
