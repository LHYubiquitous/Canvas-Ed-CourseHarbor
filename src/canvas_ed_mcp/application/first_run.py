"""Typed, fail-closed preparation for a real first-run desktop host.

This module validates only non-secret setup metadata and converts an already
sanitized catalogue discovery result into the framework-neutral setup
controller.  It never resolves credentials, constructs providers, or writes a
configuration; those capabilities remain separate application boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
)
from canvas_ed_mcp.application.fingerprints import canonical_sha256
from canvas_ed_mcp.application.profiles import CatalogDiscoveryResult
from canvas_ed_mcp.application.setup_controller import SetupController
from canvas_ed_mcp.config import (
    CanvasPlatformConfig,
    EdPlatformConfig,
    PlatformsConfig,
)
from canvas_ed_mcp.paths import resolve_profile_paths, validate_profile_name
from canvas_ed_mcp.providers.base import CourseCandidate


_ENV_REFERENCE = re.compile(r"env:[A-Z_][A-Z0-9_]*\Z")
_PROVIDER_ORDER = ("canvas", "ed")


def _issue(
    code: str,
    *,
    status: IssueSeverity = IssueSeverity.ERROR,
    provider: str | None = None,
) -> ApplicationIssue:
    return ApplicationIssue(code, status, provider=provider)


def _valid_secret_reference(reference: str | None) -> bool:
    """Validate reference syntax without reading the referenced value."""

    if not isinstance(reference, str) or not reference:
        return False
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


@dataclass(frozen=True, slots=True)
class FirstRunDraft:
    """One immutable generation of validated first-run metadata."""

    generation: str
    profile: str
    output_root: Path
    config_path: Path
    platforms: PlatformsConfig = field(repr=False)
    requested_providers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.generation):
            raise ValueError("generation must be a lowercase SHA-256 digest")
        if not isinstance(self.platforms, PlatformsConfig):
            raise ValueError("platforms must be PlatformsConfig")
        if not self.output_root.is_absolute() or not self.config_path.is_absolute():
            raise ValueError("first-run paths must be absolute")
        if not self.requested_providers or any(
            provider not in _PROVIDER_ORDER for provider in self.requested_providers
        ):
            raise ValueError("requested providers must contain canvas or ed")


@dataclass(frozen=True, slots=True)
class FirstRunPreparationResult:
    """Stable result of local first-run validation and profile inspection."""

    status: OperationStatus
    draft: FirstRunDraft | None = field(default=None, repr=False)
    issues: tuple[ApplicationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class FirstRunControllerResult:
    """Generation-bound controller result suitable for a UI host."""

    status: OperationStatus
    generation: str
    controller: SetupController | None = field(default=None, repr=False, compare=False)
    platforms: PlatformsConfig | None = field(default=None, repr=False)
    catalogs: tuple[tuple[str, tuple[CourseCandidate, ...]], ...] = ()
    issues: tuple[ApplicationIssue, ...] = ()

    @property
    def ready(self) -> bool:
        return self.controller is not None


def _build_platforms(
    *,
    canvas_enabled: bool,
    canvas_base_url: str | None,
    canvas_credential: str | None,
    ed_enabled: bool,
    ed_credential: str | None,
) -> tuple[PlatformsConfig | None, tuple[ApplicationIssue, ...]]:
    requested = tuple(
        provider
        for provider, enabled in (
            ("canvas", canvas_enabled),
            ("ed", ed_enabled),
        )
        if enabled
    )
    if not requested:
        return None, (_issue("no_provider_selected"),)

    reference_issues: list[ApplicationIssue] = []
    if canvas_enabled and not _valid_secret_reference(canvas_credential):
        reference_issues.append(
            _issue("invalid_credential_reference", provider="canvas")
        )
    if ed_enabled and not _valid_secret_reference(ed_credential):
        reference_issues.append(
            _issue("invalid_credential_reference", provider="ed")
        )
    if reference_issues:
        return None, tuple(reference_issues)

    try:
        canvas = (
            CanvasPlatformConfig(
                enabled=True,
                base_url=canvas_base_url,
                credential=canvas_credential,
            )
            if canvas_enabled
            else CanvasPlatformConfig()
        )
    except ValueError:
        return None, (_issue("invalid_canvas_settings", provider="canvas"),)
    try:
        ed = (
            EdPlatformConfig(
                enabled=True,
                region="global",
                credential=ed_credential,
            )
            if ed_enabled
            else EdPlatformConfig()
        )
    except ValueError:
        return None, (_issue("invalid_ed_settings", provider="ed"),)
    return PlatformsConfig(canvas=canvas, ed=ed), ()


def prepare_first_run(
    *,
    profile: str,
    output_root: Path,
    config_path: Path | None = None,
    canvas_enabled: bool = False,
    canvas_base_url: str | None = None,
    canvas_credential: str | None = None,
    ed_enabled: bool = False,
    ed_credential: str | None = None,
) -> FirstRunPreparationResult:
    """Validate a first-run generation before any secret or network access."""

    try:
        selected_profile = validate_profile_name(profile)
    except ValueError:
        return FirstRunPreparationResult(
            OperationStatus.FAILED,
            issues=(_issue("invalid_profile"),),
        )
    if not isinstance(output_root, Path) or not output_root.is_absolute():
        return FirstRunPreparationResult(
            OperationStatus.FAILED,
            issues=(_issue("invalid_output_root"),),
        )
    try:
        selected_output = output_root.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return FirstRunPreparationResult(
            OperationStatus.FAILED,
            issues=(_issue("invalid_output_root"),),
        )

    if config_path is not None and not isinstance(config_path, Path):
        return FirstRunPreparationResult(
            OperationStatus.FAILED,
            issues=(_issue("invalid_config_target"),),
        )
    try:
        selected_config = Path(
            config_path or resolve_profile_paths(selected_profile).config_file
        ).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return FirstRunPreparationResult(
            OperationStatus.FAILED,
            issues=(_issue("invalid_config_target"),),
        )

    # The existence check intentionally precedes connection parsing.  A first-run
    # host must never resolve a secret or reach a provider for an occupied target.
    if selected_config.exists():
        return FirstRunPreparationResult(
            OperationStatus.CONFLICT,
            issues=(_issue("config_exists"),),
        )

    platforms, issues = _build_platforms(
        canvas_enabled=canvas_enabled,
        canvas_base_url=canvas_base_url,
        canvas_credential=canvas_credential,
        ed_enabled=ed_enabled,
        ed_credential=ed_credential,
    )
    if platforms is None:
        return FirstRunPreparationResult(OperationStatus.FAILED, issues=issues)
    requested = tuple(
        provider
        for provider in _PROVIDER_ORDER
        if getattr(platforms, provider).enabled
    )
    generation = canonical_sha256(
        {
            "profile": selected_profile,
            "output_root": selected_output,
            "config_path": selected_config,
            "platforms": platforms,
            "requested_providers": requested,
        }
    )
    return FirstRunPreparationResult(
        OperationStatus.SUCCEEDED,
        FirstRunDraft(
            generation,
            selected_profile,
            selected_output,
            selected_config,
            platforms,
            requested,
        ),
    )


def _discovery_issues(discovery: CatalogDiscoveryResult) -> tuple[ApplicationIssue, ...]:
    return tuple(issue for result in discovery.providers for issue in result.issues)


def _disabled_unavailable_platforms(
    draft: FirstRunDraft, retained: set[str]
) -> PlatformsConfig:
    return PlatformsConfig(
        canvas=(
            draft.platforms.canvas
            if "canvas" in retained
            else CanvasPlatformConfig()
        ),
        ed=draft.platforms.ed if "ed" in retained else EdPlatformConfig(),
    )


def build_first_run_controller(
    draft: FirstRunDraft,
    discovery: CatalogDiscoveryResult,
    *,
    generation: str,
    accept_available_only: bool = False,
) -> FirstRunControllerResult:
    """Build a controller only from discovery bound to the current draft."""

    if not isinstance(draft, FirstRunDraft) or not isinstance(
        discovery, CatalogDiscoveryResult
    ):
        return FirstRunControllerResult(
            OperationStatus.FAILED,
            generation if isinstance(generation, str) else "",
            issues=(_issue("invalid_discovery_result"),),
        )
    if generation != draft.generation:
        return FirstRunControllerResult(
            OperationStatus.CONFLICT,
            draft.generation,
            issues=(_issue("draft_generation_mismatch"),),
        )

    provider_names = tuple(result.provider for result in discovery.providers)
    if (
        len(provider_names) != len(set(provider_names))
        or any(provider not in draft.requested_providers for provider in provider_names)
    ):
        return FirstRunControllerResult(
            OperationStatus.FAILED,
            draft.generation,
            issues=(_issue("invalid_discovery_result"),),
        )
    issues = _discovery_issues(discovery)

    if discovery.status is OperationStatus.PARTIAL and not accept_available_only:
        return FirstRunControllerResult(
            OperationStatus.PARTIAL,
            draft.generation,
            issues=issues + (_issue("partial_discovery_requires_confirmation"),),
        )
    if discovery.status in {OperationStatus.FAILED, OperationStatus.CANCELLED}:
        code = (
            "course_discovery_cancelled"
            if discovery.status is OperationStatus.CANCELLED
            else "course_discovery_failed"
        )
        return FirstRunControllerResult(
            discovery.status,
            draft.generation,
            issues=issues + (_issue(code),),
        )
    if discovery.status not in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}:
        return FirstRunControllerResult(
            OperationStatus.FAILED,
            draft.generation,
            issues=issues + (_issue("invalid_discovery_result"),),
        )

    if discovery.status is OperationStatus.SUCCEEDED:
        if set(provider_names) != set(draft.requested_providers) or any(
            result.status is not OperationStatus.SUCCEEDED
            for result in discovery.providers
        ):
            return FirstRunControllerResult(
                OperationStatus.FAILED,
                draft.generation,
                issues=issues + (_issue("invalid_discovery_result"),),
            )
        selected_results = discovery.providers
        platforms = draft.platforms
        status = OperationStatus.SUCCEEDED
    else:
        retained_results = tuple(
            result
            for result in discovery.providers
            if result.status in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}
        )
        retained = {result.provider for result in retained_results}
        platforms = _disabled_unavailable_platforms(draft, retained)
        selected_results = retained_results
        status = OperationStatus.PARTIAL
        issues += (
            _issue(
                "available_courses_only",
                status=IssueSeverity.WARNING,
            ),
        )

    if not any(result.courses for result in selected_results):
        return FirstRunControllerResult(
            OperationStatus.FAILED,
            draft.generation,
            issues=issues + (_issue("no_courses_available"),),
        )
    catalogs = tuple(
        (provider, next(result.courses for result in selected_results if result.provider == provider))
        for provider in _PROVIDER_ORDER
        if any(result.provider == provider for result in selected_results)
    )
    try:
        controller = SetupController(
            profile=draft.profile,
            output_root=draft.output_root,
            catalogs=dict(catalogs),
            canvas=platforms.canvas,
            ed=platforms.ed,
        )
    except (TypeError, ValueError):
        return FirstRunControllerResult(
            OperationStatus.FAILED,
            draft.generation,
            platforms=platforms,
            catalogs=catalogs,
            issues=issues + (_issue("invalid_discovered_catalog"),),
        )
    return FirstRunControllerResult(
        status,
        draft.generation,
        controller,
        platforms,
        catalogs,
        issues,
    )


__all__ = [
    "FirstRunControllerResult",
    "FirstRunDraft",
    "FirstRunPreparationResult",
    "build_first_run_controller",
    "prepare_first_run",
]
