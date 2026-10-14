"""Side-effect-free reconfiguration diff and explicit config-only commit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from .config import AppConfig, CourseConfig, RetiredCourseSource, load_config, save_config
from .course_mapping import InitialSyncMode, SourceIdentity


_OPERATIONAL_COURSE_FIELDS = ("folder", "sources", "content", "policy")


class ConfigureChangeKind(str, Enum):
    ADD = "add"
    REMOVE = "remove"
    CHANGE = "change"


class ConfigureConflictError(RuntimeError):
    """The config changed after preview and must be planned again."""


@dataclass(frozen=True, slots=True)
class CourseChange:
    kind: ConfigureChangeKind
    course_key: str
    before: CourseConfig | None
    after: CourseConfig | None
    fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ConfigureChangeKind):
            raise ValueError("kind must be a ConfigureChangeKind")
        if not isinstance(self.course_key, str) or not self.course_key:
            raise ValueError("course_key must be a non-empty string")
        if not isinstance(self.fields, tuple):
            raise ValueError("fields must be an immutable tuple")
        if self.kind is ConfigureChangeKind.ADD:
            if self.before is not None or self.after is None or self.fields:
                raise ValueError("add changes require only an after course")
        elif self.kind is ConfigureChangeKind.REMOVE:
            if self.before is None or self.after is not None or self.fields:
                raise ValueError("remove changes require only a before course")
        elif self.kind is ConfigureChangeKind.CHANGE:
            if self.before is None or self.after is None or not self.fields:
                raise ValueError("course changes require before, after and changed fields")
            if any(field not in _OPERATIONAL_COURSE_FIELDS for field in self.fields):
                raise ValueError("course change contains an unsupported field")
        for item in (self.before, self.after):
            if item is not None:
                if not isinstance(item, CourseConfig):
                    raise ValueError("before and after must be CourseConfig values")
                if item.key != self.course_key:
                    raise ValueError("course change key does not match its course")


@dataclass(frozen=True, slots=True)
class InitialSyncSelection:
    course_key: str
    mode: InitialSyncMode
    source: SourceIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.course_key, str) or not self.course_key:
            raise ValueError("course_key must be a non-empty string")
        if not isinstance(self.mode, InitialSyncMode):
            raise ValueError("mode must be an InitialSyncMode")
        if self.source is not None and not isinstance(self.source, SourceIdentity):
            raise ValueError("source must be a SourceIdentity")


@dataclass(frozen=True, slots=True)
class ConfigurePlan:
    previous_config: AppConfig
    config: AppConfig
    changes: tuple[CourseChange, ...]
    initial_sync: tuple[InitialSyncSelection, ...]
    platforms_changed: bool
    preview: str

    def __post_init__(self) -> None:
        if not isinstance(self.previous_config, AppConfig) or not isinstance(
            self.config, AppConfig
        ):
            raise ValueError("configure plans require validated AppConfig values")
        if not isinstance(self.changes, tuple) or not all(
            isinstance(change, CourseChange) for change in self.changes
        ):
            raise ValueError("changes must be an immutable tuple of CourseChange values")
        if not isinstance(self.initial_sync, tuple) or not all(
            isinstance(selection, InitialSyncSelection)
            for selection in self.initial_sync
        ):
            raise ValueError("initial_sync must be an immutable tuple")
        if not isinstance(self.platforms_changed, bool):
            raise ValueError("platforms_changed must be a boolean")
        if not isinstance(self.preview, str) or not self.preview:
            raise ValueError("preview must be non-empty")

    @property
    def changed_keys(self) -> tuple[str, ...]:
        return tuple(change.course_key for change in self.changes)

    @property
    def has_changes(self) -> bool:
        return self.platforms_changed or bool(self.changes)


def _changed_course_fields(
    before: CourseConfig, after: CourseConfig
) -> tuple[str, ...]:
    return tuple(
        field
        for field in _OPERATIONAL_COURSE_FIELDS
        if getattr(before, field) != getattr(after, field)
    )


def _render_preview(
    *,
    current: AppConfig,
    changes: tuple[CourseChange, ...],
    initial_sync: tuple[InitialSyncSelection, ...],
    platforms_changed: bool,
) -> str:
    modes_by_key: dict[str, list[InitialSyncSelection]] = {}
    for selection in initial_sync:
        modes_by_key.setdefault(selection.course_key, []).append(selection)
    lines = [
        f"Profile: {current.profile}",
        f"Output root: {current.output_root}",
        f"Platform settings: {'changed' if platforms_changed else 'unchanged'}",
    ]
    if not changes and not platforms_changed:
        lines.append("No operational changes.")
        return "\n".join(lines)

    added = tuple(change for change in changes if change.kind is ConfigureChangeKind.ADD)
    removed = tuple(
        change for change in changes if change.kind is ConfigureChangeKind.REMOVE
    )
    changed = tuple(
        change for change in changes if change.kind is ConfigureChangeKind.CHANGE
    )
    if added:
        lines.append("Added courses:")
        for change in added:
            modes = modes_by_key[change.course_key]
            lines.append(
                f"+ {change.course_key} -> {change.after.folder}"  # type: ignore[union-attr]
            )
            for selection in modes:
                if selection.source is not None:
                    lines.append(
                        f"  + {selection.source.label()} [initial={selection.mode.value}]"
                    )
    if removed:
        lines.append("Removed courses:")
        for change in removed:
            lines.append(
                f"- {change.course_key} "
                "(local files and historical state retained)"
            )
    if changed:
        lines.append("Changed courses:")
        for change in changed:
            lines.append(f"~ {change.course_key}: {', '.join(change.fields)}")
            if "sources" in change.fields:
                before = {
                    SourceIdentity.from_source(source) for source in change.before.sources  # type: ignore[union-attr]
                }
                after = {
                    SourceIdentity.from_source(source) for source in change.after.sources  # type: ignore[union-attr]
                }
                for source in sorted(before - after):
                    lines.append(f"  - {source.label()}")
                for source in sorted(after - before):
                    selection = next(
                        item
                        for item in initial_sync
                        if item.course_key == change.course_key and item.source == source
                    )
                    lines.append(
                        f"  + {source.label()} [initial={selection.mode.value}]"
                    )
    return "\n".join(lines)


def build_configure_plan(
    current: AppConfig,
    proposed: AppConfig,
    *,
    new_course_modes: Mapping[str, InitialSyncMode] | None = None,
    new_source_modes: Mapping[SourceIdentity, InitialSyncMode] | None = None,
) -> ConfigurePlan:
    """Build a deterministic config diff without touching config, state or output."""
    if not isinstance(current, AppConfig) or not isinstance(proposed, AppConfig):
        raise ValueError("current and proposed must be AppConfig values")
    if current.profile != proposed.profile:
        raise ValueError("profile changes require a separate profile migration")
    if current.output_root.resolve(strict=False) != proposed.output_root.resolve(
        strict=False
    ):
        raise ValueError("output_root changes require a separate output migration")

    current_by_key = {course.key: course for course in current.courses}
    proposed_by_key = {course.key: course for course in proposed.courses}
    added_keys = tuple(sorted(proposed_by_key.keys() - current_by_key.keys()))
    removed_keys = tuple(sorted(current_by_key.keys() - proposed_by_key.keys()))
    shared_keys = tuple(sorted(current_by_key.keys() & proposed_by_key.keys()))

    if new_course_modes is not None and new_source_modes is not None:
        raise ValueError("supply either new_course_modes or new_source_modes, not both")

    current_source_owner = {
        SourceIdentity.from_source(source): course.key
        for course in current.courses
        for source in course.sources
    }
    proposed_source_owner = {
        SourceIdentity.from_source(source): course.key
        for course in proposed.courses
        for source in course.sources
    }
    for identity in current_source_owner.keys() & proposed_source_owner.keys():
        if current_source_owner[identity] != proposed_source_owner[identity]:
            raise ValueError("moving a remote source requires a separate output/state migration")
    added_sources = set(proposed_source_owner) - set(current_source_owner)
    retired_identities = {
        SourceIdentity.from_source(retired.source): retired.former_course_key
        for retired in current.retired_sources
    }
    blocked_reattachments = added_sources & retired_identities.keys()
    if blocked_reattachments:
        raise ValueError("reattaching a detached remote source requires explicit migration")

    initial_sync: list[InitialSyncSelection] = []
    if new_source_modes is not None:
        if set(new_source_modes) != added_sources:
            raise ValueError("every newly mapped source requires exactly one initial sync mode")
        for identity in sorted(added_sources):
            mode = new_source_modes[identity]
            if not isinstance(mode, InitialSyncMode):
                raise ValueError("new source modes must be InitialSyncMode values")
            initial_sync.append(
                InitialSyncSelection(proposed_source_owner[identity], mode, identity)
            )
    else:
        legacy_modes = {} if new_course_modes is None else new_course_modes
        if set(legacy_modes) != set(added_keys):
            raise ValueError("every added course requires exactly one initial sync mode")
        for key in added_keys:
            course_sources = {
                identity for identity, owner in proposed_source_owner.items() if owner == key
            }
            new_for_course = course_sources & added_sources
            if len(course_sources) != 1 or len(new_for_course) != 1:
                raise ValueError("multi-source courses require per-source initial sync modes")
            mode = legacy_modes[key]
            if not isinstance(mode, InitialSyncMode):
                raise ValueError("new course modes must be InitialSyncMode values")
            identity = next(iter(new_for_course))
            initial_sync.append(InitialSyncSelection(key, mode, identity))

    additions = tuple(
        CourseChange(
            kind=ConfigureChangeKind.ADD,
            course_key=key,
            before=None,
            after=proposed_by_key[key],
        )
        for key in added_keys
    )
    removals = tuple(
        CourseChange(
            kind=ConfigureChangeKind.REMOVE,
            course_key=key,
            before=current_by_key[key],
            after=None,
        )
        for key in removed_keys
    )
    changed_by_key: dict[str, CourseChange] = {}
    for key in shared_keys:
        fields = _changed_course_fields(current_by_key[key], proposed_by_key[key])
        if fields:
            changed_by_key[key] = CourseChange(
                kind=ConfigureChangeKind.CHANGE,
                course_key=key,
                before=current_by_key[key],
                after=proposed_by_key[key],
                fields=fields,
            )
    changes = additions + removals + tuple(changed_by_key.values())

    retained_courses: list[CourseConfig] = []
    for existing in current.courses:
        if existing.key in removed_keys:
            continue
        change = changed_by_key.get(existing.key)
        retained_courses.append(change.after if change is not None else existing)  # type: ignore[arg-type]
    for candidate in proposed.courses:
        if candidate.key in added_keys:
            retained_courses.append(candidate)

    removed_sources = set(current_source_owner) - set(proposed_source_owner)
    retired_by_identity = {
        SourceIdentity.from_source(retired.source): retired
        for retired in current.retired_sources
    }
    current_source_values = {
        SourceIdentity.from_source(source): source
        for course in current.courses
        for source in course.sources
    }
    for identity in sorted(removed_sources):
        retired_by_identity[identity] = RetiredCourseSource(
            source=current_source_values[identity],
            former_course_key=current_source_owner[identity],
        )

    effective = AppConfig(
        profile=current.profile,
        output_root=current.output_root,
        platforms=proposed.platforms,
        courses=tuple(retained_courses),
        retired_sources=tuple(retired_by_identity[key] for key in sorted(retired_by_identity)),
    )
    platforms_changed = current.platforms != proposed.platforms
    initial_tuple = tuple(initial_sync)
    return ConfigurePlan(
        previous_config=current,
        config=effective,
        changes=changes,
        initial_sync=initial_tuple,
        platforms_changed=platforms_changed,
        preview=_render_preview(
            current=current,
            changes=changes,
            initial_sync=initial_tuple,
            platforms_changed=platforms_changed,
        ),
    )


def commit_configure(plan: ConfigurePlan, path: Path, *, confirmed: bool) -> bool:
    """Commit only the config after confirmation and optimistic conflict checking."""
    if not isinstance(plan, ConfigurePlan):
        raise ValueError("plan must be a ConfigurePlan")
    if not isinstance(confirmed, bool):
        raise ValueError("confirmed must be a boolean")
    if not confirmed:
        return False
    current_on_disk = load_config(path)
    if current_on_disk != plan.previous_config:
        raise ConfigureConflictError(
            "configuration changed after preview; rebuild the configure plan"
        )
    if plan.has_changes:
        save_config(plan.config, path)
    return True
