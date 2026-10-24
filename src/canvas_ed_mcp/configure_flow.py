"""Side-effect-free reconfiguration diff and explicit config-only commit."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import AbstractSet, Callable, Mapping

from .config import (
    AppConfig,
    CanvasPlatformConfig,
    ContentSelection,
    CourseConfig,
    CourseSource,
    DownloadPolicy,
    EdPlatformConfig,
    RetiredCourseSource,
    load_config,
    save_config,
)
from .course_mapping import InitialSyncMode, SourceIdentity
from .providers.base import CourseCandidate


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
            modes = modes_by_key.get(change.course_key, ())
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
    reset_only = tuple(
        selection
        for selection in initial_sync
        if not any(
            change.after is not None
            and selection.source is not None
            and selection.source in {
                SourceIdentity.from_source(source) for source in change.after.sources
            }
            and (
                change.before is None
                or selection.source not in {
                    SourceIdentity.from_source(source) for source in change.before.sources
                }
            )
            for change in changes
        )
    )
    if reset_only:
        lines.append("Next-sync resets:")
        for selection in reset_only:
            if selection.source is not None:
                lines.append(
                    f"~ {selection.course_key}: {selection.source.label()} "
                    f"[{selection.mode.value}; one-time, then incremental]"
                )
    lines.append("No existing course file will be deleted or moved.")
    return "\n".join(lines)


def build_configure_plan(
    current: AppConfig,
    proposed: AppConfig,
    *,
    new_course_modes: Mapping[str, InitialSyncMode] | None = None,
    new_source_modes: Mapping[SourceIdentity, InitialSyncMode] | None = None,
    safely_relocatable_sources: AbstractSet[SourceIdentity] = frozenset(),
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
    current_source_values = {
        SourceIdentity.from_source(source): source
        for course in current.courses
        for source in course.sources
    }
    proposed_source_owner = {
        SourceIdentity.from_source(source): course.key
        for course in proposed.courses
        for source in course.sources
    }
    if not isinstance(safely_relocatable_sources, AbstractSet) or not all(
        isinstance(identity, SourceIdentity)
        for identity in safely_relocatable_sources
    ):
        raise ValueError("safely_relocatable_sources must contain SourceIdentity values")
    moved_sources = {
        identity
        for identity in current_source_owner.keys() & proposed_source_owner.keys()
        if current_source_owner[identity] != proposed_source_owner[identity]
    }
    added_sources = set(proposed_source_owner) - set(current_source_owner)
    retired_values = {
        SourceIdentity.from_source(retired.source): retired
        for retired in current.retired_sources
    }
    historical_source_values = {
        **{identity: retired.source for identity, retired in retired_values.items()},
        **current_source_values,
    }

    initial_sync: list[InitialSyncSelection] = []
    if new_source_modes is not None:
        selected_identities = set(new_source_modes)
        if not (added_sources | moved_sources).issubset(selected_identities):
            raise ValueError(
                "every newly mapped, reactivated or moved source requires exactly one next-sync mode"
            )
        if not selected_identities.issubset(proposed_source_owner):
            raise ValueError("next-sync modes must refer to proposed active sources")
        for identity in sorted(selected_identities):
            mode = new_source_modes[identity]
            if not isinstance(mode, InitialSyncMode):
                raise ValueError("next-sync modes must be InitialSyncMode values")
            initial_sync.append(
                InitialSyncSelection(proposed_source_owner[identity], mode, identity)
            )
    else:
        legacy_modes = {} if new_course_modes is None else new_course_modes
        if moved_sources:
            raise ValueError("moved sources require per-source next-sync modes")
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

    selected_modes = {
        selection.source: selection.mode for selection in initial_sync
        if selection.source is not None
    }
    normalized_proposed: dict[str, CourseConfig] = {}
    for key, course in proposed_by_key.items():
        normalized_sources = []
        for source in course.sources:
            identity = SourceIdentity.from_source(source)
            if identity in selected_modes:
                previous = historical_source_values.get(identity)
                generation = 0 if previous is None else previous.reconcile_generation + 1
                source = source.model_copy(
                    update={
                        "initial_mode": selected_modes[identity],
                        "reconcile_generation": generation,
                    }
                )
            else:
                previous = current_source_values.get(identity)
                if previous is None:
                    raise ValueError("new active sources require an explicit next-sync mode")
                source = source.model_copy(
                    update={
                        "initial_mode": previous.initial_mode,
                        "reconcile_generation": previous.reconcile_generation,
                    }
                )
            normalized_sources.append(source)
        normalized_proposed[key] = course.model_copy(
            update={"sources": tuple(normalized_sources)}
        )
    proposed_by_key = normalized_proposed

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
            retained_courses.append(proposed_by_key[candidate.key])

    removed_sources = set(current_source_owner) - set(proposed_source_owner)
    retired_by_identity = {
        SourceIdentity.from_source(retired.source): retired
        for retired in current.retired_sources
    }
    for identity in sorted(removed_sources):
        retired_by_identity[identity] = RetiredCourseSource(
            source=current_source_values[identity],
            former_course_key=current_source_owner[identity],
        )
    for identity in proposed_source_owner:
        retired_by_identity.pop(identity, None)

    effective = AppConfig(
        profile=current.profile,
        output_root=current.output_root,
        platforms=proposed.platforms,
        courses=tuple(retained_courses),
        retired_sources=tuple(retired_by_identity[key] for key in sorted(retired_by_identity)),
        daily_use=proposed.daily_use,
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


def _source_from_candidate(
    candidate: CourseCandidate, mode: InitialSyncMode
) -> CourseSource:
    return CourseSource(
        provider=candidate.provider,  # type: ignore[arg-type]
        instance=candidate.instance,
        course_id=candidate.course_id,
        code=candidate.code or None,
        name=candidate.name or None,
        term=candidate.term,
        initial_mode=mode,
    )


def _content_text(content: ContentSelection, allowed: set[str]) -> str:
    return ",".join(
        name
        for name in ("files", "pages", "lessons", "resources")
        if name in allowed and getattr(content, name)
    )


def _merge_provider_content(
    current: ContentSelection,
    selected: ContentSelection,
    provider: str,
) -> ContentSelection:
    updates = (
        {"files": selected.files, "pages": selected.pages}
        if provider == "canvas"
        else {"lessons": selected.lessons, "resources": selected.resources}
    )
    return current.model_copy(update=updates)


def _clear_provider_content(
    current: ContentSelection, provider: str
) -> ContentSelection:
    updates = (
        {"files": False, "pages": False}
        if provider == "canvas"
        else {"lessons": False, "resources": False}
    )
    return current.model_copy(update=updates)


def run_configure_wizard(
    *,
    profile: str,
    config_path: Path,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    secret_resolver: Callable[[str], str],
    canvas_catalog_loader: Callable[[str, str], tuple[CourseCandidate, ...]],
    ed_catalog_loader: Callable[[str], tuple[CourseCandidate, ...]] | None = None,
    dry_run: bool = False,
) -> bool:
    """Interactively plan and optionally commit an existing profile update."""
    from .setup_flow import (
        _content_selection,
        _default_folder,
        _initial_mode,
        _validate_catalog_candidate,
        _yes,
    )

    current = load_config(config_path)
    if current.profile != profile:
        raise ValueError("profile does not match the configuration")
    side_text = input_fn(
        "Provider side(s) to configure [canvas/ed/both; blank cancels]: "
    ).strip().casefold()
    if not side_text:
        output_fn("Configure cancelled before provider selection; no changes made.")
        return False
    if side_text not in {"canvas", "ed", "both"}:
        raise ValueError("provider selection must be canvas, ed, or both")
    selected_providers = (
        ("canvas", "ed") if side_text == "both" else (side_text,)
    )

    courses = {course.key: course for course in current.courses}
    course_order = [course.key for course in current.courses]
    platforms = current.platforms
    new_source_modes: dict[SourceIdentity, InitialSyncMode] = {}
    newly_created_keys: set[str] = set()

    for provider_name in selected_providers:
        # Retention is the default; detaching never deletes local output or state.
        for key in tuple(course_order):
            course = courses[key]
            matching = tuple(
                source for source in course.sources if source.provider == provider_name
            )
            if not matching:
                continue
            source = matching[0]
            label = f"{key} ({provider_name}@{source.instance} id={source.course_id})"
            if not _yes(
                input_fn(
                    f"Remove {label}? Local files and historical state will be retained. [y/N]: "
                )
            ):
                continue
            remaining = tuple(item for item in course.sources if item is not source)
            if remaining:
                courses[key] = course.model_copy(
                    update={
                        "sources": remaining,
                        "content": _clear_provider_content(
                            course.content, provider_name
                        ),
                    }
                )
            else:
                del courses[key]
                course_order.remove(key)

        platform = getattr(platforms, provider_name)
        if platform.enabled:
            discover = _yes(
                input_fn(f"Discover additional {provider_name} courses? [y/N]: ")
            )
        else:
            discover = _yes(
                input_fn(f"Enable {provider_name} and discover courses? [y/N]: ")
            )
            if discover and provider_name == "canvas":
                base_url = input_fn(
                    "Canvas base URL [https://canvas.sydney.edu.au/api/v1]: "
                ).strip() or "https://canvas.sydney.edu.au/api/v1"
                credential = input_fn(
                    f"Canvas credential reference [keyring:canvas-ed-mcp/{profile}/canvas]: "
                ).strip() or f"keyring:canvas-ed-mcp/{profile}/canvas"
                platform = CanvasPlatformConfig(
                    enabled=True, base_url=base_url, credential=credential
                )
                platforms = platforms.model_copy(update={"canvas": platform})
            elif discover:
                region = input_fn("Ed region [global]: ").strip().casefold() or "global"
                if region != "global":
                    raise ValueError("only the global Ed region is currently supported")
                credential = input_fn(
                    f"Ed credential reference [keyring:canvas-ed-mcp/{profile}/ed]: "
                ).strip() or f"keyring:canvas-ed-mcp/{profile}/ed"
                platform = EdPlatformConfig(
                    enabled=True, region=region, credential=credential
                )
                platforms = platforms.model_copy(update={"ed": platform})

        if discover:
            if provider_name == "canvas":
                assert isinstance(platform, CanvasPlatformConfig)
                assert platform.base_url is not None and platform.credential is not None
                catalog = canvas_catalog_loader(
                    platform.base_url, secret_resolver(platform.credential)
                )
            else:
                assert isinstance(platform, EdPlatformConfig)
                assert platform.credential is not None
                if platform.region != "global":
                    raise ValueError("only the global Ed region is currently supported")
                if ed_catalog_loader is None:
                    raise ValueError("Ed catalog loader is unavailable")
                catalog = ed_catalog_loader(secret_resolver(platform.credential))

            active_identities = {
                SourceIdentity.from_source(source)
                for course in courses.values()
                for source in course.sources
            }
            retired_identities = {
                SourceIdentity.from_source(retired.source)
                for retired in current.retired_sources
            }
            for candidate in catalog:
                _validate_catalog_candidate(candidate, provider_name)
                identity = SourceIdentity.from_candidate(candidate)
                if identity in active_identities or identity in retired_identities:
                    continue
                label = (
                    f"{candidate.name} | provider={candidate.provider} | "
                    f"instance={candidate.instance} | code={candidate.code or '-'} | "
                    f"term={candidate.term or '-'} | id={candidate.course_id}"
                )
                if not _yes(input_fn(f"Add {label}? [y/N]: ")):
                    continue
                mode = _initial_mode(
                    input_fn(
                        "Initial mode [baseline_from_now/import_all; "
                        "default baseline_from_now]: "
                    )
                )
                logical_key = input_fn(
                    f"Logical course key [{candidate.key}]: "
                ).strip() or candidate.key
                if not _yes(
                    input_fn(
                        f"Confirm source {identity.label()} -> {logical_key}? [y/N]: "
                    )
                ):
                    continue
                source = _source_from_candidate(candidate, mode)
                if logical_key in courses:
                    existing = courses[logical_key]
                    if any(item.provider == provider_name for item in existing.sources):
                        raise ValueError(
                            "logical course already contains a source from this provider"
                        )
                    allowed = {"files", "pages"} if provider_name == "canvas" else {
                        "lessons", "resources"
                    }
                    default_content = ",".join(sorted(allowed))
                    content_text = input_fn(
                        f"Added {provider_name} content [{default_content}]: "
                    ).strip() or default_content
                    selected_content = _content_selection(content_text, allowed)
                    courses[logical_key] = existing.model_copy(
                        update={
                            "sources": existing.sources + (source,),
                            "content": _merge_provider_content(
                                existing.content, selected_content, provider_name
                            ),
                        }
                    )
                else:
                    folder_default = _default_folder(candidate)
                    folder = input_fn(f"Folder [{folder_default}]: ").strip() or folder_default
                    allowed = {"files", "pages"} if provider_name == "canvas" else {
                        "lessons", "resources"
                    }
                    default_content = ",".join(sorted(allowed))
                    content_text = input_fn(
                        f"Content [{default_content}]: "
                    ).strip() or default_content
                    max_mib_text = input_fn("Maximum single-file MiB [100]: ").strip()
                    max_mib = int(max_mib_text) if max_mib_text else 100
                    if max_mib <= 0:
                        raise ValueError("maximum file size must be positive")
                    courses[logical_key] = CourseConfig(
                        key=logical_key,
                        folder=folder,
                        confirmed_at=datetime.now(timezone.utc),
                        sources=(source,),
                        content=_content_selection(content_text, allowed),
                        policy=DownloadPolicy(max_file_bytes=max_mib * 1024 * 1024),
                    )
                    course_order.append(logical_key)
                    newly_created_keys.add(logical_key)
                active_identities.add(identity)
                new_source_modes[identity] = mode

        provider_still_used = any(
            source.provider == provider_name
            for course in courses.values()
            for source in course.sources
        )
        platform = getattr(platforms, provider_name)
        if platform.enabled and not provider_still_used and _yes(
            input_fn(f"Disable unused {provider_name} platform? [y/N]: ")
        ):
            disabled = (
                CanvasPlatformConfig()
                if provider_name == "canvas"
                else EdPlatformConfig()
            )
            platforms = platforms.model_copy(update={provider_name: disabled})

    for key in tuple(course_order):
        if key in newly_created_keys:
            continue
        course = courses[key]
        if not any(source.provider in selected_providers for source in course.sources):
            continue
        if not _yes(input_fn(f"Edit settings for {key}? [y/N]: ")):
            continue
        folder = input_fn(f"Folder [{course.folder}]: ").strip() or course.folder
        editable_providers = {
            source.provider
            for source in course.sources
            if source.provider in selected_providers
        }
        allowed: set[str] = set()
        if "canvas" in editable_providers:
            allowed.update(("files", "pages"))
        if "ed" in editable_providers:
            allowed.update(("lessons", "resources"))
        current_content = _content_text(course.content, allowed)
        content_text = input_fn(f"Content [{current_content}]: ").strip() or current_content
        selected_content = _content_selection(content_text, allowed)
        updated_content = course.content
        for provider_name in sorted(editable_providers):
            updated_content = _merge_provider_content(
                updated_content, selected_content, provider_name
            )
        current_limit = course.policy.max_file_bytes
        current_mib = (
            f"{current_limit / (1024 * 1024):g}"
            if current_limit is not None
            else "unlimited"
        )
        max_mib_text = input_fn(
            f"Maximum single-file MiB [{current_mib}; blank keeps current]: "
        ).strip()
        policy = course.policy
        if max_mib_text:
            max_mib = int(max_mib_text)
            if max_mib <= 0:
                raise ValueError("maximum file size must be positive")
            policy = course.policy.model_copy(
                update={"max_file_bytes": max_mib * 1024 * 1024}
            )
        courses[key] = course.model_copy(
            update={
                "folder": folder,
                "content": updated_content,
                "policy": policy,
            }
        )

    proposed = AppConfig(
        profile=current.profile,
        output_root=current.output_root,
        platforms=platforms,
        courses=tuple(courses[key] for key in course_order),
        retired_sources=current.retired_sources,
    )
    plan = build_configure_plan(
        current, proposed, new_source_modes=new_source_modes
    )
    output_fn(plan.preview)
    if dry_run:
        output_fn("Dry-run only; no config, state, or course content was written.")
        return False
    if not plan.has_changes:
        output_fn("No changes; configuration remains unchanged.")
        return True
    confirmed = _yes(input_fn("Commit these configuration changes? [y/N]: "))
    committed = commit_configure(plan, config_path, confirmed=confirmed)
    output_fn(
        f"Configuration saved to {config_path}"
        if committed
        else "Configure cancelled at final confirmation; no changes made."
    )
    return committed
