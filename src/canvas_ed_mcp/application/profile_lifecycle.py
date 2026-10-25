"""Reviewed, recoverable local lifecycle operations for CourseHarbor profiles."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from canvas_ed_mcp.paths import ProfilePaths, resolve_profile_paths, validate_profile_name

from .contracts import ApplicationIssue, IssueSeverity, OperationStatus
from .fingerprints import MISSING_REVISION, file_revision


PathsResolver = Callable[[str], ProfilePaths]
Clock = Callable[[], datetime]
_RETAINED = ("course_files", "credentials", "state", "logs", "cache")


def _issue(code: str) -> ApplicationIssue:
    return ApplicationIssue(code, IssueSeverity.ERROR)


@dataclass(frozen=True, slots=True)
class ProfileRemovalReview:
    profile: str
    config_path: Path
    archive_path: Path
    config_revision: str


@dataclass(frozen=True, slots=True)
class ProfileRemovalReviewResult:
    status: OperationStatus
    review: ProfileRemovalReview | None = None
    issues: tuple[ApplicationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class ProfileRemovalResult:
    status: OperationStatus
    profile: str | None = None
    archive_path: Path | None = None
    retained: tuple[str, ...] = _RETAINED
    issues: tuple[ApplicationIssue, ...] = ()


class ProfileLifecycleService:
    """Archive a profile config without deleting course data or credentials."""

    def __init__(
        self,
        *,
        paths_resolver: PathsResolver = resolve_profile_paths,
        clock: Clock | None = None,
    ) -> None:
        self._paths_resolver = paths_resolver
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def review_removal(
        self,
        profile: str,
        *,
        expected_config_path: Path | None = None,
    ) -> ProfileRemovalReviewResult:
        try:
            selected = validate_profile_name(profile)
            paths = self._paths_resolver(selected)
            config_path = Path(paths.config_file).resolve(strict=False)
            if paths.profile != selected:
                raise ValueError("profile paths mismatch")
            if expected_config_path is not None and Path(
                expected_config_path
            ).resolve(strict=False) != config_path:
                return ProfileRemovalReviewResult(
                    OperationStatus.FAILED,
                    issues=(_issue("profile_config_mismatch"),),
                )
            revision = file_revision(config_path)
        except (OSError, RuntimeError, ValueError):
            return ProfileRemovalReviewResult(
                OperationStatus.FAILED,
                issues=(_issue("profile_removal_unavailable"),),
            )
        if revision == MISSING_REVISION:
            return ProfileRemovalReviewResult(
                OperationStatus.FAILED,
                issues=(_issue("profile_config_missing"),),
            )
        stamp = self._clock().astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive_root = config_path.parent / ".removed"
        base = f"{selected}--{stamp}--{revision[:12]}"
        archive_path = archive_root / f"{base}.toml"
        suffix = 2
        while archive_path.exists():
            archive_path = archive_root / f"{base}--{suffix}.toml"
            suffix += 1
        return ProfileRemovalReviewResult(
            OperationStatus.SUCCEEDED,
            ProfileRemovalReview(selected, config_path, archive_path, revision),
        )

    def commit_removal(
        self, review: ProfileRemovalReview | None
    ) -> ProfileRemovalResult:
        if not isinstance(review, ProfileRemovalReview):
            return ProfileRemovalResult(
                OperationStatus.FAILED,
                issues=(_issue("profile_removal_review_required"),),
            )
        try:
            paths = self._paths_resolver(validate_profile_name(review.profile))
            config_path = Path(paths.config_file).resolve(strict=False)
            archive_path = Path(review.archive_path).resolve(strict=False)
            archive_root = (config_path.parent / ".removed").resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return ProfileRemovalResult(
                OperationStatus.FAILED,
                profile=review.profile,
                issues=(_issue("profile_removal_unavailable"),),
            )
        if (
            paths.profile != review.profile
            or config_path != review.config_path
            or archive_path.parent != archive_root
            or archive_path.suffix.casefold() != ".toml"
        ):
            return ProfileRemovalResult(
                OperationStatus.CONFLICT,
                profile=review.profile,
                issues=(_issue("profile_removal_review_stale"),),
            )
        try:
            revision = file_revision(config_path)
        except (OSError, RuntimeError, ValueError):
            return ProfileRemovalResult(
                OperationStatus.FAILED,
                profile=review.profile,
                issues=(_issue("profile_removal_unavailable"),),
            )
        if revision != review.config_revision or revision == MISSING_REVISION:
            return ProfileRemovalResult(
                OperationStatus.CONFLICT,
                profile=review.profile,
                issues=(_issue("profile_removal_review_stale"),),
            )
        if archive_path.exists():
            return ProfileRemovalResult(
                OperationStatus.CONFLICT,
                profile=review.profile,
                issues=(_issue("profile_archive_exists"),),
            )
        try:
            archive_root.mkdir(parents=True, exist_ok=True)
            config_path.replace(archive_path)
        except OSError:
            return ProfileRemovalResult(
                OperationStatus.FAILED,
                profile=review.profile,
                issues=(_issue("profile_archive_failed"),),
            )
        return ProfileRemovalResult(
            OperationStatus.SUCCEEDED,
            profile=review.profile,
            archive_path=archive_path,
        )


__all__ = [
    "ProfileLifecycleService",
    "ProfileRemovalResult",
    "ProfileRemovalReview",
    "ProfileRemovalReviewResult",
]
