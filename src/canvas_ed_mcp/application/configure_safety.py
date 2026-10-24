"""Fail-closed eligibility checks for config-only course remapping."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig, CourseConfig
from ..course_mapping import SourceIdentity
from ..sync.state import StateLoadError, SyncState, load_state


@dataclass(frozen=True, slots=True)
class SourceRelocationEligibility:
    source: SourceIdentity
    allowed: bool
    reason: str


def _source_has_history(state: SyncState, source: SourceIdentity) -> bool:
    identity = (source.provider, source.instance, source.course_id)
    return any(
        (item.key.provider, item.key.instance, item.key.course_id) == identity
        for item in state.sources
    ) or any(
        (item.provider, item.instance, item.course_id) == identity
        for item in state.initialized_sources
    ) or any(
        (item.key.provider, item.key.instance, item.key.course_id) == identity
        for item in state.policy_skips
    )


def _folder_is_absent_or_empty(output_root: Path, course: CourseConfig) -> bool:
    target = output_root / course.folder
    try:
        if not target.exists():
            return True
        if not target.is_dir():
            return False
        return next(target.iterdir(), None) is None
    except OSError:
        return False


def evaluate_relocation_eligibility(
    current: AppConfig,
    state: SyncState | None,
    *,
    state_is_trustworthy: bool = True,
) -> tuple[SourceRelocationEligibility, ...]:
    """Return one explicit remapping decision for every configured source."""
    if not isinstance(current, AppConfig):
        raise ValueError("current must be an AppConfig")
    if state is not None and not isinstance(state, SyncState):
        raise ValueError("state must be SyncState or None")
    decisions: list[SourceRelocationEligibility] = []
    for course in current.courses:
        folder_safe = _folder_is_absent_or_empty(current.output_root, course)
        for configured_source in course.sources:
            source = SourceIdentity.from_source(configured_source)
            if not state_is_trustworthy:
                decisions.append(
                    SourceRelocationEligibility(
                        source,
                        False,
                        "Sync history could not be verified, so this mapping is locked.",
                    )
                )
            elif state is not None and _source_has_history(state, source):
                decisions.append(
                    SourceRelocationEligibility(
                        source,
                        True,
                        "This source has sync history; existing files will stay in their current folder.",
                    )
                )
            elif not folder_safe:
                decisions.append(
                    SourceRelocationEligibility(
                        source,
                        True,
                        "The current folder is not empty; existing files will be preserved in place.",
                    )
                )
            else:
                decisions.append(
                    SourceRelocationEligibility(
                        source,
                        True,
                        "No sync history or local course files were found.",
                    )
                )
    return tuple(decisions)


def load_relocation_eligibility(
    current: AppConfig, state_path: Path
) -> tuple[SourceRelocationEligibility, ...]:
    """Load state if present and fail closed when its evidence is unreadable."""
    if not isinstance(state_path, Path):
        raise ValueError("state_path must be a Path")
    if not state_path.exists():
        return evaluate_relocation_eligibility(current, None)
    try:
        state = load_state(
            state_path,
            profile=current.profile,
            output_root=current.output_root,
        )
    except (OSError, StateLoadError, ValueError):
        return evaluate_relocation_eligibility(
            current, None, state_is_trustworthy=False
        )
    return evaluate_relocation_eligibility(current, state)


__all__ = [
    "SourceRelocationEligibility",
    "evaluate_relocation_eligibility",
    "load_relocation_eligibility",
]
