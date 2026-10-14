"""Cross-platform, profile-isolated application path resolution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from platformdirs import PlatformDirs


APP_NAME = "canvas-ed-mcp"
_PROFILE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class PlatformDirectories(Protocol):
    user_config_path: Path
    user_state_path: Path
    user_log_path: Path
    user_cache_path: Path


@dataclass(frozen=True, slots=True)
class ProfilePaths:
    """Non-content paths for one isolated profile."""

    profile: str
    config_file: Path
    state_file: Path
    state_backup_file: Path
    lock_dir: Path
    log_dir: Path
    cache_dir: Path


def validate_profile_name(profile: str) -> str:
    """Validate a profile as a single portable path component."""
    if not isinstance(profile, str) or not _PROFILE_PATTERN.fullmatch(profile):
        raise ValueError(
            "profile must be 1-64 characters using letters, numbers, '.', '_' or '-', "
            "and must start with a letter or number"
        )
    if profile in {".", ".."}:
        raise ValueError("profile cannot be '.' or '..'")
    return profile


def resolve_profile_paths(
    profile: str = "default", *, dirs: PlatformDirectories | None = None
) -> ProfilePaths:
    """Resolve config/state/log paths without creating directories."""
    profile = validate_profile_name(profile)
    platform_paths = dirs or PlatformDirs(APP_NAME, appauthor=False, roaming=False)
    config_root = Path(platform_paths.user_config_path) / "profiles"
    state_root = Path(platform_paths.user_state_path) / "profiles" / profile
    log_root = Path(platform_paths.user_log_path) / profile
    cache_root = Path(platform_paths.user_cache_path) / profile
    return ProfilePaths(
        profile=profile,
        config_file=config_root / f"{profile}.toml",
        state_file=state_root / "state-v4.json",
        state_backup_file=state_root / "state-v4.backup.json",
        lock_dir=Path(platform_paths.user_state_path) / "locks",
        log_dir=log_root,
        cache_dir=cache_root,
    )
