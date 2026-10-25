"""Typed credential lifecycle for desktop and other application adapters.

Raw values cross this boundary only as :class:`SecretValue`.  Results contain
stable status metadata and never retain provider payloads or secret values.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    CancellationToken,
    IssueSeverity,
    NeverCancelled,
    OperationStatus,
    ProgressEvent,
    SecretValue,
)
from canvas_ed_mcp.application.profiles import discover_course_catalogs
from canvas_ed_mcp.application.providers import (
    CanvasFactory,
    EdFactory,
    _default_canvas_factory,
    _default_ed_factory,
)
from canvas_ed_mcp.config import (
    CanvasPlatformConfig,
    EdPlatformConfig,
    PlatformsConfig,
)
from canvas_ed_mcp.paths import validate_profile_name
from canvas_ed_mcp.secrets import (
    KeyringBackend,
    SecretUnavailable,
    keyring_status,
    resolve_secret,
    store_secret,
)


_ENV_REFERENCE = re.compile(r"env:[A-Z_][A-Z0-9_]*\Z")
_PROVIDERS = frozenset({"canvas", "ed"})
ProgressCallback = Callable[[ProgressEvent], None]


def _issue(
    code: str,
    *,
    provider: str,
    severity: IssueSeverity = IssueSeverity.ERROR,
    retryable: bool = False,
) -> ApplicationIssue:
    return ApplicationIssue(
        code,
        severity,
        retryable=retryable,
        provider=provider,
    )


def _valid_reference(reference: str) -> bool:
    if _ENV_REFERENCE.fullmatch(reference):
        return True
    if not reference.startswith("keyring:"):
        return False
    body = reference.removeprefix("keyring:")
    service, separator, account = body.rpartition("/")
    return bool(
        separator
        and service
        and account
        and reference == reference.strip()
        and not any(
            character.isspace()
            or ord(character) < 32
            or ord(character) == 127
            for character in reference
        )
    )


def credential_reference(profile: str, provider: str) -> str:
    """Return the deterministic native-keyring reference for one profile/provider."""

    selected_profile = validate_profile_name(profile)
    if provider not in _PROVIDERS:
        raise ValueError("provider must be canvas or ed")
    return f"keyring:canvas-ed-mcp/{selected_profile}/{provider}"


class CredentialMode(str, Enum):
    KEYRING = "keyring"
    EXTERNALLY_MANAGED = "externally_managed"


class CredentialPresence(str, Enum):
    PRESENT = "present"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CredentialTarget:
    profile: str
    provider: str
    reference: str
    canvas_base_url: str | None = None

    def __post_init__(self) -> None:
        validate_profile_name(self.profile)
        if self.provider not in _PROVIDERS:
            raise ValueError("provider must be canvas or ed")
        if not _valid_reference(self.reference):
            raise ValueError("invalid credential reference")
        if self.reference.startswith("keyring:") and self.reference != credential_reference(
            self.profile, self.provider
        ):
            raise ValueError("keyring reference must match profile and provider")
        if self.provider == "canvas":
            CanvasPlatformConfig(
                enabled=True,
                base_url=self.canvas_base_url,
                credential=self.reference,
            )
        elif self.canvas_base_url is not None:
            raise ValueError("Ed target cannot include a Canvas base URL")


@dataclass(frozen=True, slots=True)
class CredentialInspection:
    status: OperationStatus
    provider: str
    mode: CredentialMode
    presence: CredentialPresence
    writable: bool
    issues: tuple[ApplicationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class CredentialOperationResult:
    status: OperationStatus
    provider: str
    verified: bool = False
    persisted: bool = False
    course_count: int | None = None
    issues: tuple[ApplicationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class _StoreInspection:
    mode: CredentialMode
    presence: CredentialPresence
    writable: bool
    category: str | None = None


class NativeCredentialStore:
    """Small adapter around the low-level env/keyring primitives."""

    def __init__(
        self,
        *,
        backend: KeyringBackend | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._backend = backend
        self._environ = environ

    def inspect(self, reference: str) -> _StoreInspection:
        if reference.startswith("env:"):
            try:
                resolve_secret(
                    reference,
                    environ=self._environ,
                    backend=self._backend,
                )
            except SecretUnavailable as exc:
                if exc.category == "missing_env":
                    return _StoreInspection(
                        CredentialMode.EXTERNALLY_MANAGED,
                        CredentialPresence.MISSING,
                        False,
                    )
                return _StoreInspection(
                    CredentialMode.EXTERNALLY_MANAGED,
                    CredentialPresence.UNKNOWN,
                    False,
                    exc.category,
                )
            return _StoreInspection(
                CredentialMode.EXTERNALLY_MANAGED,
                CredentialPresence.PRESENT,
                False,
            )

        if not reference.startswith("keyring:"):
            return _StoreInspection(
                CredentialMode.KEYRING,
                CredentialPresence.UNKNOWN,
                False,
                "invalid_reference",
            )
        status = keyring_status(backend=self._backend)
        if not status.available:
            return _StoreInspection(
                CredentialMode.KEYRING,
                CredentialPresence.UNKNOWN,
                False,
                status.category,
            )
        try:
            resolve_secret(
                reference,
                environ=self._environ,
                backend=self._backend,
            )
        except SecretUnavailable as exc:
            if exc.category == "missing_keyring":
                return _StoreInspection(
                    CredentialMode.KEYRING,
                    CredentialPresence.MISSING,
                    True,
                )
            return _StoreInspection(
                CredentialMode.KEYRING,
                CredentialPresence.UNKNOWN,
                True,
                exc.category,
            )
        return _StoreInspection(
            CredentialMode.KEYRING,
            CredentialPresence.PRESENT,
            True,
        )

    def resolve(self, reference: str) -> str:
        return resolve_secret(
            reference,
            environ=self._environ,
            backend=self._backend,
        )

    def store(self, reference: str, value: SecretValue) -> None:
        store_secret(reference, value.value, backend=self._backend)


class CredentialController:
    """Orchestrate readiness, read-only validation, replacement, and persistence."""

    def __init__(
        self,
        *,
        store: NativeCredentialStore | None = None,
        canvas_factory: CanvasFactory = _default_canvas_factory,
        ed_factory: EdFactory = _default_ed_factory,
    ) -> None:
        self._store = store or NativeCredentialStore()
        self._canvas_factory = canvas_factory
        self._ed_factory = ed_factory

    def target_for(
        self,
        profile: str,
        provider: str,
        *,
        canvas_base_url: str | None = None,
        reference: str | None = None,
    ) -> CredentialTarget:
        return CredentialTarget(
            profile=profile,
            provider=provider,
            reference=reference or credential_reference(profile, provider),
            canvas_base_url=canvas_base_url,
        )

    def inspect(self, target: CredentialTarget) -> CredentialInspection:
        if not isinstance(target, CredentialTarget):
            raise ValueError("target must be CredentialTarget")
        inspected = self._store.inspect(target.reference)
        issues = (
            (_issue(inspected.category, provider=target.provider, retryable=True),)
            if inspected.category is not None
            else ()
        )
        return CredentialInspection(
            OperationStatus.FAILED if issues else OperationStatus.SUCCEEDED,
            target.provider,
            inspected.mode,
            inspected.presence,
            inspected.writable,
            issues,
        )

    def test_existing(
        self,
        target: CredentialTarget,
        *,
        cancellation: CancellationToken = NeverCancelled(),
        progress: ProgressCallback | None = None,
    ) -> CredentialOperationResult:
        inspected = self.inspect(target)
        if inspected.status is not OperationStatus.SUCCEEDED:
            return CredentialOperationResult(
                inspected.status,
                target.provider,
                issues=inspected.issues,
            )
        if inspected.presence is not CredentialPresence.PRESENT:
            code = (
                "missing_env"
                if inspected.mode is CredentialMode.EXTERNALLY_MANAGED
                else "missing_keyring"
            )
            return CredentialOperationResult(
                OperationStatus.FAILED,
                target.provider,
                issues=(_issue(code, provider=target.provider),),
            )
        return self._test(
            target,
            resolver=self._store.resolve,
            cancellation=cancellation,
            progress=progress,
        )

    def save_and_test(
        self,
        target: CredentialTarget,
        value: SecretValue,
        *,
        replace_confirmed: bool = False,
        cancellation: CancellationToken = NeverCancelled(),
        progress: ProgressCallback | None = None,
    ) -> CredentialOperationResult:
        if not isinstance(value, SecretValue):
            raise ValueError("value must be SecretValue")
        inspected = self.inspect(target)
        if inspected.mode is CredentialMode.EXTERNALLY_MANAGED:
            return CredentialOperationResult(
                OperationStatus.FAILED,
                target.provider,
                issues=(_issue("read_only_reference", provider=target.provider),),
            )
        if inspected.status is not OperationStatus.SUCCEEDED:
            return CredentialOperationResult(
                inspected.status,
                target.provider,
                issues=inspected.issues,
            )
        if (
            inspected.presence is CredentialPresence.PRESENT
            and not replace_confirmed
        ):
            return CredentialOperationResult(
                OperationStatus.CONFLICT,
                target.provider,
                issues=(
                    _issue(
                        "replacement_confirmation_required",
                        provider=target.provider,
                    ),
                ),
            )
        if cancellation.cancellation_requested:
            return CredentialOperationResult(OperationStatus.CANCELLED, target.provider)

        def transient_resolver(reference: str) -> str:
            if reference != target.reference:
                raise SecretUnavailable(
                    "invalid_reference", "credential test reference changed"
                )
            return value.value

        tested = self._test(
            target,
            resolver=transient_resolver,
            cancellation=cancellation,
            progress=progress,
        )
        if (
            tested.status is not OperationStatus.SUCCEEDED
            or not tested.verified
            or cancellation.cancellation_requested
        ):
            if cancellation.cancellation_requested and tested.status is OperationStatus.SUCCEEDED:
                return CredentialOperationResult(
                    OperationStatus.CANCELLED,
                    target.provider,
                    verified=True,
                    course_count=tested.course_count,
                )
            return tested
        try:
            self._store.store(target.reference, value)
        except (SecretUnavailable, ValueError):
            return CredentialOperationResult(
                OperationStatus.FAILED,
                target.provider,
                verified=True,
                course_count=tested.course_count,
                issues=(
                    _issue(
                        "credential_state_unknown",
                        provider=target.provider,
                        retryable=True,
                    ),
                ),
            )
        return CredentialOperationResult(
            OperationStatus.SUCCEEDED,
            target.provider,
            verified=True,
            persisted=True,
            course_count=tested.course_count,
        )

    def _test(
        self,
        target: CredentialTarget,
        *,
        resolver: Callable[[str], str],
        cancellation: CancellationToken,
        progress: ProgressCallback | None,
    ) -> CredentialOperationResult:
        if cancellation.cancellation_requested:
            return CredentialOperationResult(OperationStatus.CANCELLED, target.provider)
        if target.provider == "canvas":
            platforms = PlatformsConfig(
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url=target.canvas_base_url,
                    credential=target.reference,
                )
            )
        else:
            platforms = PlatformsConfig(
                ed=EdPlatformConfig(
                    enabled=True,
                    region="global",
                    credential=target.reference,
                )
            )
        discovered = discover_course_catalogs(
            platforms,
            requested_providers=(target.provider,),
            secret_resolver=resolver,
            canvas_factory=self._canvas_factory,
            ed_factory=self._ed_factory,
            cancellation=cancellation,
            progress=progress,
            operation_id=f"credential-{target.provider}",
        )
        if discovered.status is OperationStatus.CANCELLED:
            return CredentialOperationResult(OperationStatus.CANCELLED, target.provider)
        selected = next(
            (item for item in discovered.providers if item.provider == target.provider),
            None,
        )
        if selected is None:
            return CredentialOperationResult(
                OperationStatus.FAILED,
                target.provider,
                issues=(_issue("provider_unavailable", provider=target.provider),),
            )
        verified = selected.status in {
            OperationStatus.SUCCEEDED,
            OperationStatus.PARTIAL,
        }
        return CredentialOperationResult(
            selected.status,
            target.provider,
            verified=verified,
            course_count=len(selected.courses) if verified else None,
            issues=selected.issues,
        )


__all__ = [
    "CredentialController",
    "CredentialInspection",
    "CredentialMode",
    "CredentialOperationResult",
    "CredentialPresence",
    "CredentialTarget",
    "NativeCredentialStore",
    "credential_reference",
]
