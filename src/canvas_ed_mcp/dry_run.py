"""Mutation-free Phase 1 baseline and sync plan previews."""

from __future__ import annotations

from dataclasses import dataclass

from .config import AppConfig


@dataclass(frozen=True, slots=True)
class DryRunAction:
    course_key: str
    operation: str
    target_folder: str


@dataclass(frozen=True, slots=True)
class DryRunPlan:
    actions: tuple[DryRunAction, ...]


def plan_baseline(config: AppConfig) -> DryRunPlan:
    """Preview baseline initialization without state or content writes."""
    return DryRunPlan(
        tuple(
            DryRunAction(course.key, "would_initialize_baseline", course.folder)
            for course in config.courses
        )
    )


def plan_sync(config: AppConfig, *, baseline_initialized: bool) -> DryRunPlan:
    """Preview confirmed-course scanning; refuse unsafe pre-baseline runs."""
    if not baseline_initialized:
        raise RuntimeError("safe baseline is required before sync")
    return DryRunPlan(
        tuple(
            DryRunAction(course.key, "would_scan_confirmed_course", course.folder)
            for course in config.courses
        )
    )
