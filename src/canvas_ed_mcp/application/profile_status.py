"""Local, non-secret profile status for daily-use presentation adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig, ConfigLoadError, load_config_bytes
from ..paths import ProfilePaths, resolve_profile_paths, validate_profile_name
from ..sync.state import StateLoadError, load_state_bytes
from .contracts import ApplicationIssue, IssueSeverity, OperationStatus
from .fingerprints import MISSING_REVISION, bytes_revision


@dataclass(frozen=True, slots=True)
class ProviderOverview:
    """Non-secret configuration status for one supported provider."""

    provider: str
    enabled: bool
    credential_configured: bool

    def __post_init__(self) -> None:
        if self.provider not in {"canvas", "ed"}:
            raise ValueError("provider overview supports only canvas and ed")
        if not isinstance(self.enabled, bool) or not isinstance(
            self.credential_configured, bool
        ):
            raise ValueError("provider flags must be booleans")


@dataclass(frozen=True, slots=True)
class ProfileOverview:
    """Framework-neutral dashboard data with no credential reference values."""

    profile: str
    status: OperationStatus
    config_path: Path
    state_path: Path
    output_root: Path | None = None
    output_exists: bool = False
    state_exists: bool = False
    course_count: int = 0
    source_count: int = 0
    providers: tuple[ProviderOverview, ...] = ()
    config_revision: str = MISSING_REVISION
    state_revision: str = MISSING_REVISION
    issues: tuple[ApplicationIssue, ...] = ()


def _issue(
    code: str, *, severity: IssueSeverity = IssueSeverity.ERROR
) -> ApplicationIssue:
    return ApplicationIssue(code=code, severity=severity)


def _provider_overviews(config: AppConfig) -> tuple[ProviderOverview, ...]:
    return tuple(
        ProviderOverview(
            provider,
            platform.enabled,
            bool(platform.credential),
        )
        for provider, platform in (
            ("canvas", config.platforms.canvas),
            ("ed", config.platforms.ed),
        )
    )


def _failed_overview(
    *,
    profile: str,
    status: OperationStatus,
    config_path: Path,
    state_path: Path,
    issue_code: str,
) -> ProfileOverview:
    return ProfileOverview(
        profile=profile,
        status=status,
        config_path=config_path,
        state_path=state_path,
        issues=(_issue(issue_code),),
    )


def inspect_profile_overview(
    profile: str,
    *,
    config_path: Path | None = None,
    paths: ProfilePaths | None = None,
) -> ProfileOverview:
    """Inspect config, output, and state without resolving a secret or doing I/O remotely."""

    profile = validate_profile_name(profile)
    selected_paths = paths or resolve_profile_paths(profile)
    if selected_paths.profile != profile:
        raise ValueError("profile paths do not match the requested profile")
    try:
        selected_config = Path(config_path or selected_paths.config_file).resolve(
            strict=False
        )
        selected_state = Path(selected_paths.state_file).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return _failed_overview(
            profile=profile,
            status=OperationStatus.FAILED,
            config_path=Path(config_path or selected_paths.config_file),
            state_path=selected_paths.state_file,
            issue_code="invalid_config_target",
        )
    if not selected_config.is_file():
        return _failed_overview(
            profile=profile,
            status=OperationStatus.FAILED,
            config_path=selected_config,
            state_path=selected_state,
            issue_code="config_missing",
        )
    try:
        config_snapshot = selected_config.read_bytes()
        config = load_config_bytes(config_snapshot)
        config_revision = bytes_revision(config_snapshot)
    except ConfigLoadError as exc:
        return _failed_overview(
            profile=profile,
            status=OperationStatus.FAILED,
            config_path=selected_config,
            state_path=selected_state,
            issue_code=exc.category,
        )
    except (OSError, RuntimeError, ValueError):
        return _failed_overview(
            profile=profile,
            status=OperationStatus.FAILED,
            config_path=selected_config,
            state_path=selected_state,
            issue_code="config_unreadable",
        )
    if config.profile != profile:
        return _failed_overview(
            profile=profile,
            status=OperationStatus.CONFLICT,
            config_path=selected_config,
            state_path=selected_state,
            issue_code="profile_mismatch",
        )

    issues: list[ApplicationIssue] = []
    output_root = config.output_root.resolve(strict=False)
    output_exists = output_root.is_dir()
    if output_root.exists() and not output_exists:
        issues.append(_issue("invalid_output_root", severity=IssueSeverity.WARNING))

    state_exists = selected_state.is_file()
    state_revision = MISSING_REVISION
    if state_exists:
        try:
            state_snapshot = selected_state.read_bytes()
            load_state_bytes(
                state_snapshot, profile=profile, output_root=output_root
            )
            state_revision = bytes_revision(state_snapshot)
        except (OSError, RuntimeError, StateLoadError, ValueError):
            issues.append(_issue("invalid_sync_state", severity=IssueSeverity.WARNING))

    return ProfileOverview(
        profile=profile,
        status=OperationStatus.PARTIAL if issues else OperationStatus.SUCCEEDED,
        config_path=selected_config,
        state_path=selected_state,
        output_root=output_root,
        output_exists=output_exists,
        state_exists=state_exists,
        course_count=len(config.courses),
        source_count=sum(len(course.sources) for course in config.courses),
        providers=_provider_overviews(config),
        config_revision=config_revision,
        state_revision=state_revision,
        issues=tuple(issues),
    )


__all__ = [
    "ProfileOverview",
    "ProviderOverview",
    "inspect_profile_overview",
]
