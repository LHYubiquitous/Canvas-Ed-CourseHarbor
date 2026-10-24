"""Reviewed, config-only save boundary for graphical reconfiguration."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from ..config import ConfigLoadError
from ..configure_flow import (
    ConfigureConflictError,
    ConfigurePlan,
    build_configure_plan,
    commit_configure,
)
from .configure_controller import ConfigureController
from .configure_safety import load_relocation_eligibility
from .contracts import ApplicationIssue, IssueSeverity, OperationStatus
from .profiles import SetupCommitResult
from .setup_controller import SetupStep
from .setup_session import ReviewState, SetupReview


def _result(
    status: OperationStatus, path: Path, code: str | None = None
) -> SetupCommitResult:
    issues = () if code is None else (ApplicationIssue(code, IssueSeverity.ERROR),)
    return SetupCommitResult(status, path, issues)


class ConfigOnlyConfigureSaver:
    """Save only the displayed config diff after rechecking disk and sync evidence."""

    def __init__(
        self,
        controller: ConfigureController,
        config_path: Path,
        state_path: Path,
    ) -> None:
        if not isinstance(controller, ConfigureController):
            raise TypeError("controller must be a ConfigureController")
        self._controller = controller
        self._config_path = config_path.resolve(strict=False)
        self._state_path = state_path.resolve(strict=False)
        self._lock = Lock()
        self._running = False
        self._completed = False

    @property
    def config_path(self) -> Path:
        return self._config_path

    def _is_current(self, review: SetupReview) -> bool:
        return (
            isinstance(review, SetupReview)
            and self._controller.step is SetupStep.REVIEW
            and self._controller.review is review
            and self._controller.review_state is ReviewState.CURRENT
            and isinstance(review.plan, ConfigurePlan)
        )

    def save(self, review: SetupReview) -> SetupCommitResult:
        with self._lock:
            if self._completed:
                return _result(
                    OperationStatus.CONFLICT,
                    self._config_path,
                    "save_already_completed",
                )
            if self._running:
                return _result(
                    OperationStatus.CONFLICT, self._config_path, "save_in_progress"
                )
            self._running = True
        try:
            if not self._is_current(review):
                return _result(
                    OperationStatus.CONFLICT, self._config_path, "review_required"
                )
            plan = review.plan
            assert isinstance(plan, ConfigurePlan)
            fresh = load_relocation_eligibility(plan.previous_config, self._state_path)
            safe = frozenset(item.source for item in fresh if item.allowed)
            modes = {
                item.source: item.mode
                for item in plan.initial_sync
                if item.source is not None
            }
            try:
                rebuilt = build_configure_plan(
                    plan.previous_config,
                    plan.config,
                    new_source_modes=modes,
                    safely_relocatable_sources=safe,
                )
            except ValueError:
                return _result(
                    OperationStatus.CONFLICT,
                    self._config_path,
                    "mapping_became_locked",
                )
            if rebuilt.config != plan.config or rebuilt.changes != plan.changes:
                return _result(
                    OperationStatus.CONFLICT, self._config_path, "review_required"
                )
            if not self._is_current(review):
                return _result(
                    OperationStatus.CONFLICT, self._config_path, "review_required"
                )
            try:
                commit_configure(rebuilt, self._config_path, confirmed=True)
            except ConfigureConflictError:
                return _result(
                    OperationStatus.CONFLICT, self._config_path, "config_changed"
                )
            except (ConfigLoadError, OSError, ValueError):
                return _result(
                    OperationStatus.FAILED, self._config_path, "config_write_failed"
                )
            with self._lock:
                self._completed = True
            return _result(OperationStatus.SUCCEEDED, self._config_path)
        finally:
            with self._lock:
                self._running = False


__all__ = ["ConfigOnlyConfigureSaver"]
