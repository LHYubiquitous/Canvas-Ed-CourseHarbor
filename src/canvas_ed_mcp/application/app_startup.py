"""Local-only startup routing for the CourseHarbor desktop application."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from canvas_ed_mcp.paths import ProfilePaths, resolve_profile_paths, validate_profile_name

from .contracts import OperationStatus
from .profile_status import ProfileOverview, inspect_profile_overview


PathsResolver = Callable[[str], ProfilePaths]


class StartupDestination(str, Enum):
    FIRST_RUN = "first_run"
    APP_SHELL = "app_shell"
    PROFILE_CHOOSER = "profile_chooser"


@dataclass(frozen=True, slots=True)
class StartupProfile:
    """Non-secret profile summary suitable for a chooser UI."""

    overview: ProfileOverview

    @property
    def profile(self) -> str:
        return self.overview.profile

    @property
    def ready(self) -> bool:
        return self.overview.status in {
            OperationStatus.SUCCEEDED,
            OperationStatus.PARTIAL,
        }

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.overview.issues)


@dataclass(frozen=True, slots=True)
class StartupPlan:
    """One deterministic, local-only desktop startup decision."""

    destination: StartupDestination
    requested_profile: str | None = None
    selected: ProfileOverview | None = None
    profiles: tuple[StartupProfile, ...] = ()


def _candidate_profiles(paths_resolver: PathsResolver) -> tuple[str, ...]:
    config_root = paths_resolver("default").config_file.parent
    try:
        entries = tuple(config_root.glob("*.toml")) if config_root.is_dir() else ()
    except OSError:
        return ()
    names: list[str] = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            names.append(validate_profile_name(entry.stem))
        except (OSError, ValueError):
            continue
    return tuple(sorted(set(names), key=lambda value: (value.casefold(), value)))


def _inspect(profile: str, paths_resolver: PathsResolver) -> StartupProfile:
    paths = paths_resolver(profile)
    return StartupProfile(
        inspect_profile_overview(
            profile,
            config_path=paths.config_file,
            paths=paths,
        )
    )


def plan_app_startup(
    *,
    profile: str | None = None,
    config_path: Path | None = None,
    paths_resolver: PathsResolver = resolve_profile_paths,
) -> StartupPlan:
    """Choose first run, one ready profile, or the local profile chooser.

    This function reads only local configuration/state metadata through
    ``inspect_profile_overview``. It never resolves credentials or contacts a provider.
    """

    if profile is not None or config_path is not None:
        selected_profile = validate_profile_name(profile or "default")
        paths = paths_resolver(selected_profile)
        selected_config = Path(config_path or paths.config_file).resolve(strict=False)
        if not selected_config.is_file():
            return StartupPlan(
                StartupDestination.FIRST_RUN,
                requested_profile=selected_profile,
            )
        overview = inspect_profile_overview(
            selected_profile,
            config_path=selected_config,
            paths=paths,
        )
        candidate = StartupProfile(overview)
        if candidate.ready:
            return StartupPlan(
                StartupDestination.APP_SHELL,
                requested_profile=selected_profile,
                selected=overview,
            )
        return StartupPlan(
            StartupDestination.PROFILE_CHOOSER,
            requested_profile=selected_profile,
            profiles=(candidate,),
        )

    names = _candidate_profiles(paths_resolver)
    if not names:
        return StartupPlan(
            StartupDestination.FIRST_RUN,
            requested_profile="default",
        )
    profiles = tuple(_inspect(name, paths_resolver) for name in names)
    if len(profiles) == 1 and profiles[0].ready:
        return StartupPlan(
            StartupDestination.APP_SHELL,
            requested_profile=profiles[0].profile,
            selected=profiles[0].overview,
        )
    return StartupPlan(
        StartupDestination.PROFILE_CHOOSER,
        profiles=profiles,
    )


__all__ = [
    "StartupDestination",
    "StartupPlan",
    "StartupProfile",
    "plan_app_startup",
]
