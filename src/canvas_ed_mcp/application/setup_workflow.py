"""Reviewed config-only save boundary for the desktop setup adapter."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Protocol, runtime_checkable

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
)
from canvas_ed_mcp.application.profiles import (
    SetupCommitResult,
    commit_setup_review,
    seal_setup_plan,
)
from canvas_ed_mcp.application.setup_controller import SetupController, SetupStep
from canvas_ed_mcp.application.setup_session import (
    ReviewState,
    SetupReview as SessionSetupReview,
)


@runtime_checkable
class SetupSavePort(Protocol):
    """Capability injected only into setup windows that may create a config."""

    @property
    def config_path(self) -> Path: ...

    def save(self, review: SessionSetupReview) -> SetupCommitResult: ...


def _conflict(path: Path, code: str) -> SetupCommitResult:
    return SetupCommitResult(
        OperationStatus.CONFLICT,
        path,
        (ApplicationIssue(code, IssueSeverity.ERROR),),
    )


class ConfigOnlySetupSaver:
    """Seal and commit only the exact, current review shown by a controller."""

    def __init__(self, controller: SetupController, config_path: Path) -> None:
        if not isinstance(controller, SetupController):
            raise TypeError("controller must be a SetupController")
        if not isinstance(config_path, Path):
            raise TypeError("config_path must be a Path")
        self._controller = controller
        # Bind ordinary relative paths once so the confirmation dialog and worker
        # refer to the same target even if another component changes the CWD. Full
        # validation still belongs to seal/commit; unusual paths remain stable issues
        # instead of crashing window construction.
        try:
            self._config_path = config_path.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            self._config_path = config_path
        self._lock = Lock()
        self._running = False
        self._completed = False

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def completed(self) -> bool:
        with self._lock:
            return self._completed

    def _is_current(self, review: SessionSetupReview) -> bool:
        return (
            isinstance(review, SessionSetupReview)
            and self._controller.step is SetupStep.REVIEW
            and self._controller.review is review
            and self._controller.review_state is ReviewState.CURRENT
        )

    def save(self, review: SessionSetupReview) -> SetupCommitResult:
        with self._lock:
            if self._completed:
                return _conflict(self._config_path, "save_already_completed")
            if self._running:
                return _conflict(self._config_path, "save_in_progress")
            self._running = True
        try:
            if not self._is_current(review):
                return _conflict(self._config_path, "review_required")
            sealed = seal_setup_plan(review.plan, config_path=self._config_path)
            if sealed.status is not OperationStatus.SUCCEEDED or sealed.review is None:
                return SetupCommitResult(
                    sealed.status,
                    self._config_path,
                    sealed.issues,
                )
            if not self._is_current(review):
                return _conflict(self._config_path, "review_required")
            result = commit_setup_review(
                sealed.review,
                config_path=self._config_path,
            )
            if result.status in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}:
                with self._lock:
                    self._completed = True
            return result
        finally:
            with self._lock:
                self._running = False


__all__ = ["ConfigOnlySetupSaver", "SetupSavePort"]
