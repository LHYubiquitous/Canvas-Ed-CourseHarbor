"""Typed, fail-closed opening of a configured local course library."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from canvas_ed_mcp.paths import ProfilePaths, resolve_profile_paths

from .contracts import ApplicationIssue, IssueSeverity, OperationStatus
from .profile_status import inspect_profile_overview


PathsResolver = Callable[[str], ProfilePaths]
FolderOpener = Callable[[Path], bool]


def _issue(code: str) -> ApplicationIssue:
    return ApplicationIssue(code, IssueSeverity.ERROR)


@dataclass(frozen=True, slots=True)
class OpenOutputFolderRequest:
    profile: str
    config_path: Path | None = None
    expected_output_root: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.profile, str) or not self.profile.strip():
            raise ValueError("profile must be non-empty")
        if self.config_path is not None and not isinstance(self.config_path, Path):
            raise ValueError("config_path must be a Path or None")
        if self.expected_output_root is not None:
            if (
                not isinstance(self.expected_output_root, Path)
                or not self.expected_output_root.is_absolute()
            ):
                raise ValueError("expected_output_root must be an absolute Path or None")


@dataclass(frozen=True, slots=True)
class OpenOutputFolderResult:
    status: OperationStatus
    path: Path | None = None
    issues: tuple[ApplicationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, OperationStatus):
            raise ValueError("status must be an OperationStatus")
        if self.path is not None and not isinstance(self.path, Path):
            raise ValueError("path must be a Path or None")
        if not isinstance(self.issues, tuple) or not all(
            isinstance(issue, ApplicationIssue) for issue in self.issues
        ):
            raise ValueError("issues must contain ApplicationIssue values")
        if self.status is OperationStatus.SUCCEEDED and self.path is None:
            raise ValueError("successful folder result requires a path")


class OutputFolderService:
    """Revalidate the configured output root immediately before opening it."""

    def __init__(
        self,
        *,
        opener: FolderOpener,
        paths_resolver: PathsResolver = resolve_profile_paths,
    ) -> None:
        if not callable(opener) or not callable(paths_resolver):
            raise TypeError("opener and paths_resolver must be callable")
        self._opener = opener
        self._paths_resolver = paths_resolver

    def open(self, request: OpenOutputFolderRequest) -> OpenOutputFolderResult:
        if not isinstance(request, OpenOutputFolderRequest):
            raise TypeError("request must be an OpenOutputFolderRequest")
        try:
            paths = self._paths_resolver(request.profile)
            overview = inspect_profile_overview(
                request.profile,
                config_path=request.config_path,
                paths=paths,
            )
        except Exception:
            return OpenOutputFolderResult(
                OperationStatus.FAILED,
                issues=(_issue("profile_not_found"),),
            )
        if overview.status in {OperationStatus.FAILED, OperationStatus.CONFLICT}:
            code = overview.issues[0].code if overview.issues else "config_invalid"
            return OpenOutputFolderResult(
                overview.status,
                issues=(_issue(code),),
            )
        output_root = overview.output_root
        if output_root is None:
            return OpenOutputFolderResult(
                OperationStatus.FAILED,
                issues=(_issue("config_invalid"),),
            )
        try:
            output_root = output_root.resolve(strict=False)
            expected = (
                request.expected_output_root.resolve(strict=False)
                if request.expected_output_root is not None
                else None
            )
        except (OSError, RuntimeError, ValueError):
            return OpenOutputFolderResult(
                OperationStatus.FAILED,
                issues=(_issue("config_invalid"),),
            )
        if expected is not None and output_root != expected:
            return OpenOutputFolderResult(
                OperationStatus.CONFLICT,
                issues=(_issue("output_root_changed"),),
            )
        if not output_root.exists():
            return OpenOutputFolderResult(
                OperationStatus.FAILED,
                issues=(_issue("output_root_missing"),),
            )
        if not output_root.is_dir():
            return OpenOutputFolderResult(
                OperationStatus.FAILED,
                issues=(_issue("output_root_not_directory"),),
            )
        try:
            opened = self._opener(output_root)
        except Exception:
            opened = False
        if opened is not True:
            return OpenOutputFolderResult(
                OperationStatus.FAILED,
                issues=(_issue("output_folder_open_failed"),),
            )
        return OpenOutputFolderResult(OperationStatus.SUCCEEDED, output_root)


__all__ = [
    "OpenOutputFolderRequest",
    "OpenOutputFolderResult",
    "OutputFolderService",
]
