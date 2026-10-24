"""Provider-neutral orchestration slice for safe course-content synchronization."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from dataclasses import replace
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from canvas_ed_mcp.config import CourseConfig, CourseSource
from canvas_ed_mcp.providers.base import ContentBatch, CourseContentProvider

from .decisions import decide_install, decide_source
from .locking import LockScope, SyncLock
from .model import (
    InitialSyncMode,
    LocalStatus,
    PreviousSource,
    RemoteItem,
    RemoteKind,
    SourceKey,
    SyncAction,
    SyncMode,
)
from .naming import destination_for
from .policy import PolicySkipReason, evaluate_item_policy, evaluate_stream_size
from .storage import CourseFileIndex, install_new_atomic, resolve_contained_path
from .state import (
    ArchivedSourceState,
    InitializedSource,
    PolicySkipState,
    StateLoadError,
    SyncState,
    load_state,
    new_state,
    save_state,
)


class ExecutionMode(str, Enum):
    DRY_RUN = "dry_run"
    APPLY = "apply"


class CheckpointStatus(str, Enum):
    CURRENT = "current"
    MIGRATED_COMPATIBLE = "migrated_compatible"
    RECONCILIATION_REQUIRED = "reconciliation_required"


def classify_source_checkpoint(
    state: SyncState, *, course: CourseConfig, source: CourseSource
) -> CheckpointStatus:
    """Classify active state without treating old-folder history as current."""
    checkpoint = state.checkpoint_for(
        provider=source.provider, instance=source.instance, course_id=source.course_id
    )
    if checkpoint is None:
        return CheckpointStatus.RECONCILIATION_REQUIRED
    if (
        checkpoint.course_key == course.key
        and checkpoint.course_folder == course.folder
        and checkpoint.reconcile_generation == source.reconcile_generation
    ):
        return CheckpointStatus.CURRENT
    if (
        checkpoint.course_key == course.key
        and checkpoint.course_folder is None
        and checkpoint.reconcile_generation == 0
        and source.reconcile_generation == 0
    ):
        return CheckpointStatus.MIGRATED_COMPATIBLE
    return CheckpointStatus.RECONCILIATION_REQUIRED


_PROVIDER_OWNED_KINDS = {
    "canvas": frozenset({RemoteKind.FILE, RemoteKind.PAGE}),
    "ed": frozenset({RemoteKind.LESSON, RemoteKind.RESOURCE}),
}


@dataclass(frozen=True, slots=True)
class ItemSyncResult:
    item_token: str
    action: str
    reason: str
    destination: PurePosixPath | None = None
    retry_required: bool = False
    issue_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceSyncResult:
    course_key: str
    provider: str
    items: tuple[ItemSyncResult, ...]
    state_updates: tuple[PreviousSource, ...]
    issue_codes: tuple[str, ...] = ()
    initialization_complete: bool = False
    policy_skips: tuple[PolicySkipState, ...] = ()
    execution: ExecutionMode = ExecutionMode.DRY_RUN


@dataclass(frozen=True, slots=True)
class StateCommitResult:
    state: SyncState
    committed: bool
    issue_code: str | None = None


@dataclass(frozen=True, slots=True)
class SourceTransactionResult:
    """One APPLY source run performed under state-then-course locking."""

    source_result: SourceSyncResult
    commit: StateCommitResult


@dataclass(frozen=True, slots=True)
class RunTransactionResult:
    """Ordered APPLY results; a failed commit stops all later sources."""

    transactions: tuple[SourceTransactionResult, ...]
    issue_code: str | None = None
    failed_selection_index: int | None = None

    def __post_init__(self) -> None:
        if (self.issue_code is None) != (self.failed_selection_index is None):
            raise ValueError("issue_code and failed_selection_index must appear together")
        if self.failed_selection_index is not None and self.failed_selection_index < 0:
            raise ValueError("failed_selection_index must be non-negative")

    @property
    def committed(self) -> bool:
        return self.issue_code is None and bool(self.transactions) and all(
            transaction.commit.committed for transaction in self.transactions
        )


def _item_token(key: SourceKey) -> str:
    payload = "\0".join(str(value) for value in key.identity_tuple)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _policy_fingerprint(course: CourseConfig) -> str:
    """Bind durable skips to every policy input that can change eligibility."""
    payload = json.dumps(
        {
            "content": course.content.model_dump(mode="json"),
            "policy": course.policy.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _unlink_temporary(path: Path) -> bool:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


def _requested_kinds(course: CourseConfig) -> frozenset[RemoteKind]:
    selected = {
        RemoteKind.FILE: course.content.files,
        RemoteKind.PAGE: course.content.pages,
        RemoteKind.LESSON: course.content.lessons,
        RemoteKind.RESOURCE: course.content.resources,
    }
    return frozenset(kind for kind, enabled in selected.items() if enabled)


def _upsert(
    previous: tuple[PreviousSource, ...], updates: list[PreviousSource]
) -> tuple[PreviousSource, ...]:
    values = {entry.key: entry for entry in previous}
    values.update({entry.key: entry for entry in updates})
    return tuple(sorted(values.values(), key=lambda value: value.key.identity_tuple))


def _upsert_skips(
    existing: tuple[PolicySkipState, ...],
    additions: list[PolicySkipState],
    removals: set[SourceKey],
) -> tuple[PolicySkipState, ...]:
    values = {
        (entry.course_key, entry.key): entry
        for entry in existing
        if entry.key not in removals
    }
    values.update({(entry.course_key, entry.key): entry for entry in additions})
    return tuple(sorted(values.values(), key=lambda value: (value.course_key, value.key.identity_tuple)))


def _materialize(
    provider: CourseContentProvider,
    item: RemoteItem,
    *,
    course: CourseConfig,
    cache_dir: Path,
) -> tuple[Path | None, bool, tuple[str, ...], str | None]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=cache_dir, delete=False) as handle:
            temporary = Path(handle.name)
            if item.key.kind in {RemoteKind.FILE, RemoteKind.RESOURCE}:
                received = 0
                with provider.open_binary(item) as chunks:
                    for chunk in chunks:
                        if not isinstance(chunk, bytes):
                            raise ValueError("provider binary streams must yield bytes")
                        allowed = evaluate_stream_size(
                            received_bytes=received,
                            next_chunk_bytes=len(chunk),
                            max_file_bytes=course.policy.max_file_bytes,
                        )
                        if not allowed.allowed:
                            handle.close()
                            cleanup_issue = () if _unlink_temporary(temporary) else ("cache_cleanup_pending",)
                            return None, True, cleanup_issue, allowed.reason.value
                        handle.write(chunk)
                        received += len(chunk)
                retry = False
                issues: tuple[str, ...] = ()
            else:
                rendered = provider.render_html(item)
                handle.write(rendered.document.encode("utf-8"))
                retry = rendered.retry_required
                issues = rendered.issue_codes
            handle.flush()
        return temporary, retry, issues, None
    except BaseException:
        if temporary is not None:
            _unlink_temporary(temporary)
        raise


def _available_destination(
    *, output_root: Path, course_root: PurePosixPath, item: RemoteItem
) -> PurePosixPath:
    for ordinal in range(0, 101):
        candidate = destination_for(course_root, item, ordinal=ordinal)
        path = resolve_contained_path(output_root, candidate, must_exist=False)
        if not path.exists():
            return candidate
    raise RuntimeError("destination_collision_limit")


def merge_source_result(
    state: SyncState, *, course: CourseConfig, source: CourseSource, result: SourceSyncResult
) -> SyncState:
    """Return the durable state candidate; caller still owns locked save_state()."""
    if not isinstance(state, SyncState) or not isinstance(result, SourceSyncResult):
        raise ValueError("state and result must be valid sync values")
    if result.execution is not ExecutionMode.APPLY:
        raise ValueError("dry-run results must not be merged into durable state")
    if source not in course.sources or result.course_key != course.key or result.provider != source.provider:
        raise ValueError("result is not bound to the configured course source")
    def belongs_to_source(key: SourceKey) -> bool:
        return (key.provider, key.instance, key.course_id) == (
            source.provider, source.instance, source.course_id
        )
    if not all(belongs_to_source(entry.key) for entry in result.state_updates):
        raise ValueError("result contains state for another source")
    if not all(
        skip.course_key == course.key and belongs_to_source(skip.key)
        for skip in result.policy_skips
    ):
        raise ValueError("result contains policy skips for another course source")
    checkpoint = InitializedSource(
        result.course_key, source.provider, source.instance, source.course_id,
        source.reconcile_generation, course.folder,
    )
    prior_checkpoint = state.checkpoint_for(
        provider=source.provider, instance=source.instance, course_id=source.course_id
    )
    status = classify_source_checkpoint(state, course=course, source=source)
    if status is CheckpointStatus.RECONCILIATION_REQUIRED and not result.initialization_complete:
        return state
    initialized = {
        item for item in state.initialized_sources
        if (item.provider, item.instance, item.course_id)
        != (source.provider, source.instance, source.course_id)
    }
    if result.initialization_complete:
        initialized.add(checkpoint)
    elif prior_checkpoint is not None:
        initialized.add(prior_checkpoint)
    retained_sources = tuple(entry for entry in state.sources if not belongs_to_source(entry.key))
    retained_skips = tuple(
        entry for entry in state.policy_skips
        if not belongs_to_source(entry.key)
    )
    archived = list(state.archived_sources)
    old_sources = tuple(entry for entry in state.sources if belongs_to_source(entry.key))
    old_skips = tuple(
        entry for entry in state.policy_skips
        if prior_checkpoint is not None
        and entry.course_key == prior_checkpoint.course_key
        and belongs_to_source(entry.key)
    )
    if (
        result.initialization_complete
        and status is CheckpointStatus.RECONCILIATION_REQUIRED
        and prior_checkpoint is not None
        and (old_sources or old_skips)
    ):
        archived.append(ArchivedSourceState(
            prior_checkpoint.course_key,
            prior_checkpoint.provider,
            prior_checkpoint.instance,
            prior_checkpoint.course_id,
            prior_checkpoint.reconcile_generation,
            prior_checkpoint.course_folder,
            old_sources,
            old_skips,
        ))
    return replace(
        state,
        sources=tuple(sorted((*retained_sources, *result.state_updates), key=lambda entry: entry.key.identity_tuple)),
        initialized_sources=tuple(sorted(initialized)),
        policy_skips=tuple(sorted((*retained_skips, *result.policy_skips), key=lambda entry: (entry.course_key, entry.key.identity_tuple))),
        archived_sources=tuple(archived),
    )


def commit_source_result(
    state: SyncState,
    *,
    course: CourseConfig,
    source: CourseSource,
    result: SourceSyncResult,
    state_path: Path,
    backup_path: Path,
    state_lock: SyncLock,
) -> StateCommitResult:
    """Save one already-published source delta without deleting any output on failure."""
    proposed = merge_source_result(state, course=course, source=source, result=result)
    try:
        save_state(proposed, path=state_path, backup_path=backup_path, lock=state_lock)
    except Exception:
        try:
            observed = load_state(
                state_path, profile=proposed.profile, output_root=proposed.output_root
            )
        except Exception:
            return StateCommitResult(state, False, "state_commit_failed")
        if observed == proposed:
            return StateCommitResult(proposed, True, "state_commit_ack_uncertain")
        return StateCommitResult(state, False, "state_commit_failed")
    return StateCommitResult(proposed, True)


def apply_source_transaction(
    state: SyncState,
    *,
    course: CourseConfig,
    source: CourseSource,
    provider: CourseContentProvider,
    output_root: Path,
    state_path: Path,
    backup_path: Path,
    state_lock: SyncLock,
    cache_dir: Path,
    discovered_batch: ContentBatch | None = None,
) -> SourceTransactionResult:
    """Apply one source while preserving the required state -> course lock order.

    The caller loads ``state`` while holding its matching state lock.  This
    helper deliberately acquires only one course lock and does not continue
    after a failed state commit.
    """
    output_root = Path(output_root).resolve(strict=True)
    identity = state_lock.identity
    if (
        not state_lock.held
        or identity.scope is not LockScope.STATE
        or identity.profile != state.profile
        or identity.output_root != output_root
        or state.output_root != output_root
    ):
        raise ValueError("apply transaction requires a held matching state lock")
    course_root = PurePosixPath(course.folder)
    course_lock = SyncLock.for_course(
        lock_dir=state_lock.path.parent,
        profile=state.profile,
        output_root=output_root,
        course_key=course.key,
        course_root=course_root,
    )
    all_previous = tuple(
        entry for entry in state.sources
        if (entry.key.provider, entry.key.instance, entry.key.course_id)
        == (source.provider, source.instance, source.course_id)
    )
    status = classify_source_checkpoint(state, course=course, source=source)
    prior_checkpoint = state.checkpoint_for(
        provider=source.provider, instance=source.instance, course_id=source.course_id
    )
    binding_compatible = (
        status in {CheckpointStatus.CURRENT, CheckpointStatus.MIGRATED_COMPATIBLE}
        or (
            prior_checkpoint is not None
            and prior_checkpoint.course_key == course.key
            and prior_checkpoint.course_folder == course.folder
        )
    )
    previous = all_previous if binding_compatible else ()
    if status is CheckpointStatus.RECONCILIATION_REQUIRED and source.initial_mode is InitialSyncMode.IMPORT_ALL:
        previous = tuple(entry for entry in previous if entry.stored_file is not None)
    skips = tuple(
        entry for entry in state.policy_skips
        if status in {CheckpointStatus.CURRENT, CheckpointStatus.MIGRATED_COMPATIBLE}
        and entry.course_key == course.key
        and (entry.key.provider, entry.key.instance, entry.key.course_id)
        == (source.provider, source.instance, source.course_id)
    )
    initialized = status in {CheckpointStatus.CURRENT, CheckpointStatus.MIGRATED_COMPATIBLE}
    with course_lock:
        result = sync_course_source(
            course=course,
            source=source,
            provider=provider,
            output_root=output_root,
            previous=previous,
            execution=ExecutionMode.APPLY,
            initialized=initialized,
            policy_skips=skips,
            reconciliation_required=status is CheckpointStatus.RECONCILIATION_REQUIRED,
            course_lock=course_lock,
            cache_dir=cache_dir,
            discovered_batch=discovered_batch,
        )
        committed = commit_source_result(
            state,
            course=course,
            source=source,
            result=result,
            state_path=state_path,
            backup_path=backup_path,
            state_lock=state_lock,
        )
    return SourceTransactionResult(result, committed)


def apply_configured_source(
    *,
    profile: str,
    course: CourseConfig,
    source: CourseSource,
    provider: CourseContentProvider,
    output_root: Path,
    state_path: Path,
    backup_path: Path,
    lock_dir: Path,
    cache_dir: Path,
) -> SourceTransactionResult:
    """Load/create one state namespace then run a single APPLY transaction.

    This is deliberately internal orchestration: it has no CLI parsing or
    provider construction, so callers must still make provider capability and
    credential decisions before invoking it.
    """
    output_root = Path(output_root).resolve(strict=True)
    state_lock = SyncLock.for_state(
        lock_dir=lock_dir, profile=profile, output_root=output_root
    )
    with state_lock:
        try:
            state = load_state(state_path, profile=profile, output_root=output_root)
        except StateLoadError as exc:
            if exc.category != "missing":
                raise
            state = new_state(profile=profile, output_root=output_root)
        return apply_source_transaction(
            state,
            course=course,
            source=source,
            provider=provider,
            output_root=output_root,
            state_path=state_path,
            backup_path=backup_path,
            state_lock=state_lock,
            cache_dir=cache_dir,
        )


def apply_configured_sources(
    *,
    profile: str,
    selections: tuple[tuple[CourseConfig, CourseSource], ...],
    provider: CourseContentProvider | None = None,
    providers: Mapping[tuple[str, str], CourseContentProvider] | None = None,
    output_root: Path,
    state_path: Path,
    backup_path: Path,
    lock_dir: Path,
    cache_dir: Path,
) -> RunTransactionResult:
    """Apply ordered configured sources under one state lock.

    State is advanced only after each successful durable commit. A failed
    commit ends the run before any later provider discovery or publication.
    """
    if not isinstance(selections, tuple) or not selections:
        raise ValueError("selections must be a non-empty tuple")
    if (provider is None) == (providers is None):
        raise ValueError("provide exactly one of provider or providers")
    if providers is not None:
        required = {(source.provider, source.instance) for _, source in selections}
        if not required.issubset(providers):
            raise ValueError("providers must cover every selected source identity")
    output_root = Path(output_root).resolve(strict=True)
    state_lock = SyncLock.for_state(
        lock_dir=lock_dir, profile=profile, output_root=output_root
    )
    completed: list[SourceTransactionResult] = []
    with state_lock:
        try:
            state = load_state(state_path, profile=profile, output_root=output_root)
        except StateLoadError as exc:
            if exc.category != "missing":
                raise
            state = new_state(profile=profile, output_root=output_root)
        for index, (course, source) in enumerate(selections):
            selected_provider = (
                provider if providers is None else providers[(source.provider, source.instance)]
            )
            try:
                transaction = apply_source_transaction(
                    state,
                    course=course,
                    source=source,
                    provider=selected_provider,
                    output_root=output_root,
                    state_path=state_path,
                    backup_path=backup_path,
                    state_lock=state_lock,
                    cache_dir=cache_dir,
                )
            except Exception:
                return RunTransactionResult(
                    tuple(completed), "source_transaction_failed", index
                )
            completed.append(transaction)
            if not transaction.commit.committed:
                break
            state = transaction.commit.state
    return RunTransactionResult(tuple(completed))


def preview_configured_source(
    *,
    profile: str,
    course: CourseConfig,
    source: CourseSource,
    provider: CourseContentProvider,
    output_root: Path,
    state_path: Path,
) -> SourceSyncResult:
    """Read one state snapshot and plan a source sync without filesystem writes."""
    output_root = Path(output_root).resolve(strict=True)
    try:
        state = load_state(state_path, profile=profile, output_root=output_root)
    except StateLoadError as exc:
        if exc.category != "missing":
            raise
        state = new_state(profile=profile, output_root=output_root)
    source_identity = (source.provider, source.instance, source.course_id)
    status = classify_source_checkpoint(state, course=course, source=source)
    prior_checkpoint = state.checkpoint_for(
        provider=source.provider, instance=source.instance, course_id=source.course_id
    )
    binding_compatible = (
        status in {CheckpointStatus.CURRENT, CheckpointStatus.MIGRATED_COMPATIBLE}
        or (
            prior_checkpoint is not None
            and prior_checkpoint.course_key == course.key
            and prior_checkpoint.course_folder == course.folder
        )
    )
    previous = tuple(
        entry for entry in state.sources
        if (entry.key.provider, entry.key.instance, entry.key.course_id) == source_identity
    ) if binding_compatible else ()
    if status is CheckpointStatus.RECONCILIATION_REQUIRED and source.initial_mode is InitialSyncMode.IMPORT_ALL:
        previous = tuple(entry for entry in previous if entry.stored_file is not None)
    return sync_course_source(
        course=course,
        source=source,
        provider=provider,
        output_root=output_root,
        previous=previous,
        execution=ExecutionMode.DRY_RUN,
        initialized=status in {CheckpointStatus.CURRENT, CheckpointStatus.MIGRATED_COMPATIBLE},
        policy_skips=tuple(
            entry for entry in state.policy_skips
            if status in {CheckpointStatus.CURRENT, CheckpointStatus.MIGRATED_COMPATIBLE}
            and entry.course_key == course.key
            and (entry.key.provider, entry.key.instance, entry.key.course_id) == source_identity
        ),
        reconciliation_required=status is CheckpointStatus.RECONCILIATION_REQUIRED,
    )


def sync_course_source(
    *,
    course: CourseConfig,
    source: CourseSource,
    provider: CourseContentProvider,
    output_root: Path,
    previous: tuple[PreviousSource, ...],
    execution: ExecutionMode,
    initialized: bool,
    reconciliation_required: bool = False,
    policy_skips: tuple[PolicySkipState, ...] = (),
    course_lock: SyncLock | None = None,
    cache_dir: Path | None = None,
    discovered_batch: ContentBatch | None = None,
) -> SourceSyncResult:
    """Synchronize one confirmed course source without saving global state.

    The caller owns state locking/persistence. APPLY publication requires the
    existing matching course lock; DRY_RUN performs discovery only.
    """
    if source not in course.sources:
        raise ValueError("source is not configured for this course")
    if not isinstance(initialized, bool):
        raise ValueError("initialized must be a boolean")
    if not isinstance(reconciliation_required, bool):
        raise ValueError("reconciliation_required must be a boolean")
    if not isinstance(execution, ExecutionMode):
        raise ValueError("execution must be an ExecutionMode")
    if provider.provider_name != source.provider or provider.instance != source.instance:
        raise ValueError("provider does not match configured source")
    output_root = Path(output_root).resolve(strict=True)
    course_root = PurePosixPath(course.folder)
    supported = getattr(provider, "supported_kinds", None)
    if not isinstance(supported, frozenset) or not all(
        isinstance(kind, RemoteKind) for kind in supported
    ):
        return SourceSyncResult(
            course.key, source.provider, (), previous,
            ("content_capability_unavailable",), initialized, execution=execution,
        )
    selected = _requested_kinds(course)
    owned = _PROVIDER_OWNED_KINDS.get(source.provider)
    if owned is None:
        return SourceSyncResult(
            course.key,
            source.provider,
            (),
            previous,
            ("content_capability_unavailable",),
            initialized, execution=execution,
        )
    course_owned = frozenset().union(
        *(
            _PROVIDER_OWNED_KINDS.get(configured.provider, frozenset())
            for configured in course.sources
        )
    )
    missing_course_capability = selected.difference(course_owned)
    selected_owned = selected.intersection(owned)
    if not selected_owned:
        issues = (
            ("content_capability_unavailable",)
            if missing_course_capability and source == course.sources[0]
            else ()
        )
        return SourceSyncResult(
            course.key, source.provider, (), previous,
            issue_codes=issues, initialization_complete=initialized,
            execution=execution,
        )
    requested = selected_owned.intersection(supported)
    unsupported = selected_owned.difference(supported)
    if not requested:
        return SourceSyncResult(
            course.key,
            source.provider,
            (),
            previous,
            ("content_capability_unavailable",),
            initialized, execution=execution,
        )

    if discovered_batch is None:
        try:
            batch = provider.list_items(source.course_id, kinds=requested)
        except Exception:
            return SourceSyncResult(
                course.key, source.provider, (), previous,
                ("provider_discovery_failed",), initialized, execution=execution,
            )
    else:
        if not isinstance(discovered_batch, ContentBatch):
            raise ValueError("discovered_batch must be a ContentBatch or None")
        batch = discovered_batch
    if (
        batch.provider != source.provider
        or batch.instance != source.instance
        or batch.course_id != source.course_id
        or batch.requested_kinds != requested
        or any(
            item.key.provider != source.provider
            or item.key.instance != source.instance
            or item.key.course_id != source.course_id
            for item in batch.items
        )
    ):
        return SourceSyncResult(
            course.key, source.provider, (), previous,
            ("provider_identity_mismatch",), initialized, execution=execution,
        )
    previous_by_key = {entry.key: entry for entry in previous}
    updates: list[PreviousSource] = []
    skip_updates: list[PolicySkipState] = []
    skip_removals: set[SourceKey] = set()
    results: list[ItemSyncResult] = []
    source_issue_codes = [issue.code for issue in batch.issues]
    policy_fingerprint = _policy_fingerprint(course)
    prior_skips = {entry.key: entry for entry in policy_skips}
    if unsupported:
        source_issue_codes.append("content_capability_partial")
    if (
        missing_course_capability
        and source == course.sources[0]
        and "content_capability_partial" not in source_issue_codes
    ):
        source_issue_codes.append("content_capability_partial")

    for item in sorted(batch.items, key=lambda value: value.key.identity_tuple):
        prior_skip = prior_skips.get(item.key)
        if (
            prior_skip is not None
            and prior_skip.signature == item.signature
            and prior_skip.policy_fingerprint == policy_fingerprint
        ):
            results.append(
                ItemSyncResult(
                    _item_token(item.key), "policy_skip", prior_skip.reason.value
                )
            )
            continue
        policy = evaluate_item_policy(item, content=course.content, policy=course.policy)
        if not policy.allowed:
            skip_updates.append(
                PolicySkipState(
                    course.key, item.key, item.signature, policy.reason,
                    policy_fingerprint,
                )
            )
            results.append(ItemSyncResult(_item_token(item.key), "policy_skip", policy.reason.value))
            continue
        skip_removals.add(item.key)
        old = previous_by_key.get(item.key)
        mode = (
            SyncMode.BASELINE
            if not initialized and source.initial_mode is InitialSyncMode.BASELINE_FROM_NOW
            else SyncMode.SYNC
        )
        decision = decide_source(item, old, mode=mode)
        if decision.action is SyncAction.RECORD_ONLY:
            retained_file = (
                old.stored_file
                if old is not None and old.signature == item.signature
                else None
            )
            update = PreviousSource(item.key, item.signature, retained_file)
            updates.append(update)
            results.append(ItemSyncResult(_item_token(item.key), decision.action.value, decision.reason.value))
            continue
        if decision.action is SyncAction.SKIP:
            results.append(ItemSyncResult(_item_token(item.key), decision.action.value, decision.reason.value))
            continue
        if execution is ExecutionMode.DRY_RUN:
            results.append(ItemSyncResult(_item_token(item.key), "would_fetch", decision.reason.value))
            continue
        if course_lock is None or cache_dir is None:
            raise ValueError("apply fetch requires a course lock and cache_dir")

        temporary: Path | None = None
        try:
            temporary, retry, rendered_issues, materialize_issue = _materialize(
                provider, item, course=course, cache_dir=Path(cache_dir)
            )
            source_issue_codes.extend(rendered_issues)
            if materialize_issue is not None:
                skip_updates.append(
                    PolicySkipState(
                        course.key,
                        item.key,
                        item.signature,
                        PolicySkipReason(materialize_issue),
                        policy_fingerprint,
                    )
                )
                updates.append(
                    PreviousSource(item.key, item.signature, old.stored_file if old else None, True)
                )
                results.append(
                    ItemSyncResult(
                        _item_token(item.key), "policy_skip", materialize_issue,
                        retry_required=True, issue_codes=rendered_issues,
                    )
                )
                continue
            assert temporary is not None
            index = CourseFileIndex.scan(output_root=output_root, course_root=course_root)
            observation = (
                index.observe(old.stored_file)
                if old is not None and old.stored_file is not None
                else None
            )
            identical = index.find_identical(temporary)
            install = decide_install(
                decision,
                local_status=observation.status if observation is not None else LocalStatus.NONE,
                identical_content_exists=identical is not None,
                allow_in_place_replace=False,
                retry_required_after_install=retry,
            )
            if install.action is SyncAction.BLOCK:
                updates.append(
                    PreviousSource(item.key, item.signature, old.stored_file if old else None, True)
                )
                results.append(ItemSyncResult(_item_token(item.key), install.action.value, install.reason.value, retry_required=True))
                continue
            if install.action is SyncAction.DEDUPLICATE:
                stored = (
                    old.stored_file
                    if old is not None and observation is not None and observation.status is LocalStatus.UNMODIFIED
                    else None
                )
                updates.append(PreviousSource(item.key, item.signature, stored, retry))
                results.append(ItemSyncResult(_item_token(item.key), install.action.value, install.reason.value, retry_required=retry, issue_codes=rendered_issues))
                continue
            destination = _available_destination(
                output_root=output_root, course_root=course_root, item=item
            )
            published = install_new_atomic(
                temporary,
                output_root=output_root,
                course_root=course_root,
                destination=destination,
                course_key=course.key,
                lock=course_lock,
            )
            updates.append(
                PreviousSource(item.key, item.signature, published.stored_file, retry)
            )
            extra = rendered_issues + (("staging_cleanup_pending",) if published.staging_cleanup_pending else ())
            results.append(ItemSyncResult(_item_token(item.key), install.action.value, install.reason.value, destination, retry, extra))
        except Exception:
            updates.append(
                PreviousSource(item.key, item.signature, old.stored_file if old else None, True)
            )
            results.append(ItemSyncResult(_item_token(item.key), "failed", "content_processing_failed", retry_required=True))
        finally:
            if temporary is not None and not _unlink_temporary(temporary):
                source_issue_codes.append("cache_cleanup_pending")

    if reconciliation_required:
        source_issue_codes.append(
            "reconcile_completed" if batch.complete else (
                "reconcile_baseline_pending"
                if source.initial_mode is InitialSyncMode.BASELINE_FROM_NOW
                else "reconcile_import_all_pending"
            )
        )
    return SourceSyncResult(
        course.key,
        source.provider,
        tuple(results),
        _upsert(previous, updates),
        tuple(dict.fromkeys(source_issue_codes)),
        batch.complete,
        _upsert_skips(policy_skips, skip_updates, skip_removals),
        execution,
    )
