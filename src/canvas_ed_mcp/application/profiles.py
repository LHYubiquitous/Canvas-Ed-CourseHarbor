"""Framework-neutral profile discovery and reviewed setup use cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    CancellationToken,
    IssueSeverity,
    NeverCancelled,
    OperationStatus,
    ProgressEvent,
    ProgressPhase,
    ReviewProof,
)
from canvas_ed_mcp.application.fingerprints import (
    MISSING_REVISION,
    canonical_sha256,
    file_revision,
)
from canvas_ed_mcp.application.providers import (
    CanvasFactory,
    EdFactory,
    ProviderDescriptor,
    ProviderSession,
    SecretResolver,
    _default_canvas_factory,
    _default_ed_factory,
)
from canvas_ed_mcp.config import ConfigLoadError, PlatformsConfig, load_config
from canvas_ed_mcp.course_mapping import SourceIdentity
from canvas_ed_mcp.paths import resolve_profile_paths, validate_profile_name
from canvas_ed_mcp.providers.base import CourseCandidate, ProviderError
from canvas_ed_mcp.secrets import SecretUnavailable
from canvas_ed_mcp.setup_flow import (
    SetupConflictError,
    SetupPlan,
    SetupRequest,
    build_setup_plan,
    commit_setup,
)


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True, slots=True)
class ProviderCatalogResult:
    provider: str
    status: OperationStatus
    courses: tuple[CourseCandidate, ...] = ()
    issues: tuple[ApplicationIssue, ...] = ()

    def __post_init__(self) -> None:
        if self.provider not in {"canvas", "ed"}:
            raise ValueError("catalog provider must be canvas or ed")
        if not isinstance(self.status, OperationStatus):
            raise ValueError("catalog status must be an OperationStatus")
        if not isinstance(self.courses, tuple) or not all(
            isinstance(course, CourseCandidate) for course in self.courses
        ):
            raise ValueError("courses must be an immutable tuple of CourseCandidate values")
        if not isinstance(self.issues, tuple) or not all(
            isinstance(issue, ApplicationIssue) for issue in self.issues
        ):
            raise ValueError("issues must be an immutable tuple of ApplicationIssue values")


@dataclass(frozen=True, slots=True)
class CatalogDiscoveryResult:
    status: OperationStatus
    providers: tuple[ProviderCatalogResult, ...]


@dataclass(frozen=True, slots=True)
class ProfileInspection:
    profile: str
    config_path: Path
    exists: bool
    status: OperationStatus
    issues: tuple[ApplicationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class SetupReview:
    config_path: Path
    proof: ReviewProof
    plan: SetupPlan = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class SetupReviewResult:
    status: OperationStatus
    review: SetupReview | None = None
    issues: tuple[ApplicationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class SetupCommitResult:
    status: OperationStatus
    config_path: Path
    issues: tuple[ApplicationIssue, ...] = ()


def _issue(
    code: str,
    *,
    severity: IssueSeverity = IssueSeverity.ERROR,
    retryable: bool = False,
    provider: str | None = None,
) -> ApplicationIssue:
    return ApplicationIssue(
        code=code,
        severity=severity,
        retryable=retryable,
        provider=provider,
    )


def _canonical_config_target(config_path: Path) -> Path:
    """Bind operations to one absolute target independent of later cwd changes."""

    return Path(config_path).resolve(strict=False)


def inspect_profile(profile: str, *, config_path: Path | None = None) -> ProfileInspection:
    """Inspect one profile without resolving secrets, constructing providers or writing."""
    profile = validate_profile_name(profile)
    selected = Path(config_path or resolve_profile_paths(profile).config_file)
    try:
        target = _canonical_config_target(selected)
    except (OSError, RuntimeError, ValueError):
        return ProfileInspection(
            profile,
            selected,
            False,
            OperationStatus.FAILED,
            (_issue("invalid_config_target"),),
        )
    if not target.exists():
        return ProfileInspection(profile, target, False, OperationStatus.SUCCEEDED)
    try:
        config = load_config(target)
    except ConfigLoadError as exc:
        return ProfileInspection(
            profile,
            target,
            True,
            OperationStatus.FAILED,
            (_issue(exc.category),),
        )
    if config.profile != profile:
        return ProfileInspection(
            profile,
            target,
            True,
            OperationStatus.CONFLICT,
            (_issue("profile_mismatch"),),
        )
    return ProfileInspection(profile, target, True, OperationStatus.SUCCEEDED)


def _descriptors(
    platforms: PlatformsConfig,
    requested_providers: tuple[str, ...],
) -> tuple[dict[str, ProviderDescriptor], dict[str, ApplicationIssue]]:
    descriptors: dict[str, ProviderDescriptor] = {}
    unavailable: dict[str, ApplicationIssue] = {}
    for provider in requested_providers:
        if provider not in {"canvas", "ed"}:
            raise ValueError("requested providers must be canvas or ed")
        if provider in descriptors or provider in unavailable:
            raise ValueError("requested providers must be unique")
        if provider == "canvas":
            config = platforms.canvas
            if not config.enabled or not config.base_url or not config.credential:
                unavailable[provider] = _issue("canvas_not_configured", provider=provider)
                continue
            descriptors[provider] = ProviderDescriptor(
                provider="canvas",
                credential=config.credential,
                base_url=config.base_url,
            )
        else:
            config = platforms.ed
            if not config.enabled or not config.credential:
                unavailable[provider] = _issue("ed_not_configured", provider=provider)
                continue
            if config.region != "global":
                unavailable[provider] = _issue("ed_region_unsupported", provider=provider)
                continue
            descriptors[provider] = ProviderDescriptor(
                provider="ed",
                credential=config.credential,
                region=config.region,
            )
    return descriptors, unavailable


def _validate_candidate(candidate: CourseCandidate, provider: str) -> None:
    if not isinstance(candidate, CourseCandidate) or candidate.provider != provider:
        raise ValueError("catalog candidate does not match its provider")
    SourceIdentity.from_candidate(candidate)
    for field_name in ("code", "name"):
        value = getattr(candidate, field_name)
        if not isinstance(value, str) or any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("catalog contains unsafe display text")
    if candidate.term is not None and (
        not isinstance(candidate.term, str)
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate.term)
    ):
        raise ValueError("catalog contains unsafe term text")


def discover_course_catalogs(
    platforms: PlatformsConfig,
    *,
    requested_providers: tuple[str, ...],
    secret_resolver: SecretResolver,
    canvas_factory: CanvasFactory = _default_canvas_factory,
    ed_factory: EdFactory = _default_ed_factory,
    cancellation: CancellationToken = NeverCancelled(),
    progress: ProgressCallback | None = None,
    operation_id: str = "course-discovery",
) -> CatalogDiscoveryResult:
    """Discover requested catalogs independently with no cross-provider rollback."""
    if not isinstance(platforms, PlatformsConfig):
        raise ValueError("platforms must be a PlatformsConfig")
    if not isinstance(requested_providers, tuple):
        raise ValueError("requested_providers must be an immutable tuple")
    if cancellation.cancellation_requested:
        if progress is not None:
            progress(
                ProgressEvent(
                    operation_id,
                    ProgressPhase.DISCOVERING,
                    0,
                    len(requested_providers),
                    code=OperationStatus.CANCELLED.value,
                )
            )
        return CatalogDiscoveryResult(OperationStatus.CANCELLED, ())
    descriptors, unavailable = _descriptors(platforms, requested_providers)
    results: list[ProviderCatalogResult] = []
    session = ProviderSession(
        descriptors,
        secret_resolver=secret_resolver,
        canvas_factory=canvas_factory,
        ed_factory=ed_factory,
    )
    cancelled = False
    close_failures: tuple[str, ...] = ()
    try:
        for index, provider in enumerate(requested_providers):
            if cancellation.cancellation_requested:
                cancelled = True
                break
            if progress is not None:
                progress(
                    ProgressEvent(
                        operation_id,
                        ProgressPhase.DISCOVERING,
                        index,
                        len(requested_providers),
                        provider=provider,
                    )
                )
            if provider in unavailable:
                results.append(
                    ProviderCatalogResult(
                        provider,
                        OperationStatus.FAILED,
                        issues=(unavailable[provider],),
                    )
                )
                continue
            try:
                courses = session.list_courses(provider)
                for candidate in courses:
                    _validate_candidate(candidate, provider)
            except SecretUnavailable as exc:
                results.append(
                    ProviderCatalogResult(
                        provider,
                        OperationStatus.FAILED,
                        issues=(_issue(exc.category, provider=provider),),
                    )
                )
            except ProviderError as exc:
                results.append(
                    ProviderCatalogResult(
                        provider,
                        OperationStatus.FAILED,
                        issues=(
                            _issue(
                                exc.category,
                                provider=provider,
                                retryable=exc.category == "request_failed",
                            ),
                        ),
                    )
                )
            except ValueError:
                results.append(
                    ProviderCatalogResult(
                        provider,
                        OperationStatus.FAILED,
                        issues=(_issue("invalid_catalog", provider=provider),),
                    )
                )
            except Exception:
                results.append(
                    ProviderCatalogResult(
                        provider,
                        OperationStatus.FAILED,
                        issues=(_issue(f"{provider}_provider_unavailable", provider=provider),),
                    )
                )
            else:
                results.append(
                    ProviderCatalogResult(provider, OperationStatus.SUCCEEDED, courses=courses)
                )
    finally:
        close_failures = session.close()

    if close_failures:
        revised: list[ProviderCatalogResult] = []
        for result in results:
            if result.provider not in close_failures:
                revised.append(result)
                continue
            status = (
                OperationStatus.PARTIAL
                if result.status is OperationStatus.SUCCEEDED
                else result.status
            )
            revised.append(
                ProviderCatalogResult(
                    result.provider,
                    status,
                    courses=result.courses,
                    issues=result.issues
                    + (
                        _issue(
                            "provider_close_failed",
                            severity=IssueSeverity.WARNING,
                            retryable=True,
                            provider=result.provider,
                        ),
                    ),
                )
            )
        results = revised

    if cancelled:
        status = OperationStatus.CANCELLED
    elif all(item.status is OperationStatus.SUCCEEDED for item in results):
        status = OperationStatus.SUCCEEDED
    elif any(
        item.status in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}
        for item in results
    ):
        status = OperationStatus.PARTIAL
    else:
        status = OperationStatus.FAILED
    if progress is not None:
        progress(
            ProgressEvent(
                operation_id,
                ProgressPhase.DISCOVERING,
                len(results),
                len(requested_providers),
                code=status.value,
            )
        )
    return CatalogDiscoveryResult(status, tuple(results))


def _plan_digest(plan: SetupPlan) -> str:
    return canonical_sha256(
        {
            "config": plan.config.model_dump(mode="json"),
            "preview": plan.preview,
            "initial_sync": [
                {
                    "course_key": selection.course_key,
                    "source": {
                        "provider": selection.source.provider,
                        "instance": selection.source.instance,
                        "course_id": selection.source.course_id,
                    },
                    "mode": selection.mode.value,
                }
                for selection in plan.initial_sync
            ],
        }
    )


def seal_setup_plan(
    plan: SetupPlan,
    *,
    config_path: Path,
) -> SetupReviewResult:
    """Bind one already-rendered exact plan to a still-missing config target."""

    if not isinstance(plan, SetupPlan):
        return SetupReviewResult(
            OperationStatus.FAILED,
            issues=(_issue("invalid_setup_plan"),),
        )
    try:
        target = _canonical_config_target(config_path)
    except (OSError, RuntimeError, ValueError):
        return SetupReviewResult(
            OperationStatus.FAILED,
            issues=(_issue("invalid_config_target"),),
        )
    try:
        revision = file_revision(target)
    except (OSError, ValueError):
        return SetupReviewResult(
            OperationStatus.FAILED,
            issues=(_issue("config_revision_failed", retryable=True),),
        )
    if revision != MISSING_REVISION:
        return SetupReviewResult(
            OperationStatus.CONFLICT,
            issues=(_issue("config_exists"),),
        )
    proof = ReviewProof(
        operation="setup",
        plan_digest=_plan_digest(plan),
        config_revision=revision,
    )
    return SetupReviewResult(
        OperationStatus.SUCCEEDED,
        review=SetupReview(target, proof, plan),
    )


def build_setup_review(
    request: SetupRequest,
    catalogs: Mapping[str, tuple[CourseCandidate, ...]],
    *,
    config_path: Path,
) -> SetupReviewResult:
    """Build a structured review without writing config, state, output or launchers."""
    try:
        plan = build_setup_plan(request, catalogs)
    except ValueError:
        return SetupReviewResult(
            OperationStatus.FAILED,
            issues=(_issue("invalid_setup_plan"),),
        )
    return seal_setup_plan(plan, config_path=config_path)


def commit_setup_review(
    review: SetupReview | None,
    *,
    config_path: Path,
) -> SetupCommitResult:
    """Commit only the exact reviewed setup plan when its target is unchanged."""
    supplied_target = Path(config_path)
    try:
        target = _canonical_config_target(supplied_target)
    except (OSError, RuntimeError, ValueError):
        return SetupCommitResult(
            OperationStatus.FAILED,
            supplied_target,
            (_issue("invalid_config_target"),),
        )
    if not isinstance(review, SetupReview):
        return SetupCommitResult(
            OperationStatus.FAILED,
            target,
            (_issue("invalid_setup_review"),),
        )
    if target != review.config_path or review.proof.operation != "setup":
        return SetupCommitResult(
            OperationStatus.CONFLICT,
            target,
            (_issue("review_required"),),
        )
    try:
        revision = file_revision(target)
    except (OSError, ValueError):
        return SetupCommitResult(
            OperationStatus.FAILED,
            target,
            (_issue("config_revision_failed", retryable=True),),
        )
    if revision != review.proof.config_revision or _plan_digest(
        review.plan
    ) != review.proof.plan_digest:
        return SetupCommitResult(
            OperationStatus.CONFLICT,
            target,
            (_issue("review_required"),),
        )
    cleanup_warnings: list[ApplicationIssue] = []

    def capture_warning(_message: str) -> None:
        cleanup_warnings.append(
            _issue(
                "config_cleanup_pending",
                severity=IssueSeverity.WARNING,
                retryable=True,
            )
        )

    try:
        committed = commit_setup(
            review.plan,
            target,
            confirmed=True,
            warning_fn=capture_warning,
        )
    except SetupConflictError:
        return SetupCommitResult(
            OperationStatus.CONFLICT,
            target,
            (_issue("config_exists"),),
        )
    except OSError:
        return SetupCommitResult(
            OperationStatus.FAILED,
            target,
            (_issue("config_write_failed", retryable=True),),
        )
    if not committed:
        return SetupCommitResult(
            OperationStatus.FAILED,
            target,
            (_issue("config_not_committed"),),
        )
    if cleanup_warnings:
        return SetupCommitResult(
            OperationStatus.PARTIAL,
            target,
            tuple(cleanup_warnings),
        )
    return SetupCommitResult(OperationStatus.SUCCEEDED, target)
