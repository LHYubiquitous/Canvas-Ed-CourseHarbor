"""In-memory reviewed sync plans bound to exact config and state snapshots."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re

from canvas_ed_mcp.config import CourseConfig, CourseSource, load_config_bytes
from canvas_ed_mcp.providers.base import ContentBatch, CourseContentProvider

from .engine import (
    CheckpointStatus,
    ExecutionMode,
    RunTransactionResult,
    SourceSyncResult,
    apply_source_transaction,
    classify_source_checkpoint,
    sync_course_source,
)
from .locking import SyncLock
from .model import InitialSyncMode, RemoteKind
from .state import (
    PolicySkipState,
    StateLoadError,
    SyncState,
    load_state_bytes,
    new_state,
)


_REVISION = re.compile(r"[0-9a-f]{64}\Z")
_MISSING_REVISION = "missing"


class ReviewedSyncCancelled(RuntimeError):
    """Raised when reviewed discovery reaches a cooperative cancellation point."""


def _cancelled(callback: Callable[[], bool] | None) -> bool:
    if callback is None:
        return False
    try:
        return bool(callback())
    except Exception:
        return True


def _bytes_revision(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_revision(value: str) -> bool:
    return value == _MISSING_REVISION or bool(_REVISION.fullmatch(value))


@dataclass(frozen=True, slots=True, repr=False)
class ReviewedSourcePlan:
    """One source discovery snapshot and its reviewed dry-run result."""

    course: CourseConfig = field(repr=False)
    source: CourseSource = field(repr=False)
    batch: ContentBatch = field(repr=False, compare=False)
    preview: SourceSyncResult = field(repr=False)

    def __post_init__(self) -> None:
        if self.source not in self.course.sources:
            raise ValueError("reviewed source is not configured for its course")
        if not isinstance(self.batch, ContentBatch):
            raise ValueError("reviewed source requires a ContentBatch")
        if not isinstance(self.preview, SourceSyncResult):
            raise ValueError("reviewed source requires a SourceSyncResult")
        if self.preview.execution is not ExecutionMode.DRY_RUN:
            raise ValueError("reviewed source preview must be a dry-run result")
        if (
            self.batch.provider != self.source.provider
            or self.batch.instance != self.source.instance
            or self.batch.course_id != self.source.course_id
            or self.preview.course_key != self.course.key
            or self.preview.provider != self.source.provider
        ):
            raise ValueError("reviewed source identities do not match")

    def __repr__(self) -> str:
        return (
            "ReviewedSourcePlan("
            f"course_key={self.course.key!r}, provider={self.source.provider!r}, "
            f"item_count={len(self.preview.items)})"
        )


def _plan_document(plan: "ReviewedSyncPlan") -> dict[str, object]:
    sources: list[dict[str, object]] = []
    for value in plan.sources:
        batch = value.batch
        sources.append(
            {
                "course": value.course.model_dump(mode="json"),
                "source": value.source.model_dump(mode="json"),
                "batch": {
                    "provider": batch.provider,
                    "instance": batch.instance,
                    "course_id": batch.course_id,
                    "requested_kinds": sorted(kind.value for kind in batch.requested_kinds),
                    "coverage": sorted(
                        (
                            {"kind": entry.kind.value, "status": entry.status.value}
                            for entry in batch.coverage
                        ),
                        key=lambda entry: entry["kind"],
                    ),
                    "items": [
                        {
                            "identity": [
                                item.key.provider,
                                item.key.instance,
                                item.key.course_id,
                                item.key.kind.value,
                                item.key.remote_id,
                            ],
                            "signature": item.signature,
                            "display_name": item.display_name,
                            "group": item.group,
                            "size_bytes": item.size_bytes,
                            "media_type": item.media_type,
                        }
                        for item in sorted(
                            batch.items, key=lambda item: item.key.identity_tuple
                        )
                    ],
                    "issues": [
                        {
                            "kind": issue.kind.value,
                            "scope": issue.scope.value,
                            "code": issue.code,
                            "category": issue.category,
                            "retry_required": issue.retry_required,
                            "remote_id": issue.remote_id,
                            "item_index": issue.item_index,
                        }
                        for issue in batch.issues
                    ],
                },
                "preview": {
                    "items": [
                        {
                            "item_token": item.item_token,
                            "action": item.action,
                            "reason": item.reason,
                            "retry_required": item.retry_required,
                            "issue_codes": list(item.issue_codes),
                        }
                        for item in value.preview.items
                    ],
                    "issue_codes": list(value.preview.issue_codes),
                    "initialization_complete": value.preview.initialization_complete,
                },
            }
        )
    return {
        "schema_version": 1,
        "profile": plan.profile,
        "output_root": str(plan.output_root),
        "config_revision": plan.config_revision,
        "state_revision": plan.state_revision,
        "sources": sources,
    }


@dataclass(frozen=True, slots=True, repr=False)
class ReviewedSyncPlan:
    """A one-shot in-memory plan; provider sessions remain owned by its caller."""

    profile: str
    config_path: Path = field(repr=False)
    state_path: Path = field(repr=False)
    output_root: Path = field(repr=False)
    config_revision: str
    state_revision: str
    sources: tuple[ReviewedSourcePlan, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.profile, str) or not self.profile:
            raise ValueError("profile must be non-empty")
        if not _valid_revision(self.config_revision) or self.config_revision == _MISSING_REVISION:
            raise ValueError("config_revision must be a SHA-256 digest")
        if not _valid_revision(self.state_revision):
            raise ValueError("state_revision must be missing or a SHA-256 digest")
        if not isinstance(self.sources, tuple) or not self.sources:
            raise ValueError("reviewed sync plan requires at least one source")
        identities = tuple(
            (item.source.provider, item.source.instance, item.source.course_id)
            for item in self.sources
        )
        if len(identities) != len(set(identities)):
            raise ValueError("reviewed sync source identities must be unique")

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            _plan_document(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def __repr__(self) -> str:
        return (
            "ReviewedSyncPlan("
            f"profile={self.profile!r}, source_count={len(self.sources)}, "
            f"digest={self.digest!r})"
        )


class _CapturingProvider:
    def __init__(self, provider: CourseContentProvider) -> None:
        self._provider = provider
        self.provider_name = provider.provider_name
        self.instance = provider.instance
        self.supported_kinds = provider.supported_kinds
        self.batch: ContentBatch | None = None

    def list_items(
        self, course_id: str, *, kinds: frozenset[RemoteKind]
    ) -> ContentBatch:
        self.batch = self._provider.list_items(course_id, kinds=kinds)
        return self.batch

    def open_binary(self, *args, **kwargs):
        return self._provider.open_binary(*args, **kwargs)

    def render_html(self, *args, **kwargs):
        return self._provider.render_html(*args, **kwargs)


def _source_state_context(
    state: SyncState, *, course: CourseConfig, source: CourseSource
) -> tuple[tuple, bool, tuple[PolicySkipState, ...], bool]:
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
        entry
        for entry in state.sources
        if (entry.key.provider, entry.key.instance, entry.key.course_id)
        == source_identity
    ) if binding_compatible else ()
    if (
        status is CheckpointStatus.RECONCILIATION_REQUIRED
        and source.initial_mode is InitialSyncMode.IMPORT_ALL
    ):
        previous = tuple(entry for entry in previous if entry.stored_file is not None)
    initialized = status in {
        CheckpointStatus.CURRENT,
        CheckpointStatus.MIGRATED_COMPATIBLE,
    }
    skips = tuple(
        entry
        for entry in state.policy_skips
        if initialized
        and entry.course_key == course.key
        and (entry.key.provider, entry.key.instance, entry.key.course_id)
        == source_identity
    )
    return previous, initialized, skips, status is CheckpointStatus.RECONCILIATION_REQUIRED


def _load_state_snapshot(
    path: Path, *, profile: str, output_root: Path
) -> tuple[SyncState, str]:
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        return new_state(profile=profile, output_root=output_root), _MISSING_REVISION
    state = load_state_bytes(payload, profile=profile, output_root=output_root)
    return state, _bytes_revision(payload)


def _validate_config_bindings(
    config, *, profile: str, output_root: Path, selections
) -> bool:
    if config.profile != profile or config.output_root.resolve(strict=False) != output_root:
        return False
    courses = {course.key: course for course in config.courses}
    for course, source in selections:
        configured = courses.get(course.key)
        if configured != course or source not in configured.sources:
            return False
    return True


def build_reviewed_sync_plan(
    *,
    profile: str,
    config_path: Path,
    state_path: Path,
    output_root: Path,
    selections: tuple[tuple[CourseConfig, CourseSource], ...],
    providers: Mapping[tuple[str, str], CourseContentProvider],
    config_snapshot: bytes | None = None,
    cancellation_requested: Callable[[], bool] | None = None,
) -> ReviewedSyncPlan:
    """Discover once and seal safe metadata plus dry-run decisions."""

    if not isinstance(selections, tuple) or not selections:
        raise ValueError("selections must be a non-empty tuple")
    output_root = Path(output_root).resolve(strict=True)
    config_path = Path(config_path).resolve(strict=True)
    state_path = Path(state_path).resolve(strict=False)
    if config_snapshot is None:
        config_snapshot = config_path.read_bytes()
    elif not isinstance(config_snapshot, bytes):
        raise ValueError("config_snapshot must be bytes or None")
    config = load_config_bytes(config_snapshot)
    if not _validate_config_bindings(
        config,
        profile=profile,
        output_root=output_root,
        selections=selections,
    ):
        raise ValueError("selections do not match the config snapshot")
    state, state_revision = _load_state_snapshot(
        state_path, profile=profile, output_root=output_root
    )
    plans: list[ReviewedSourcePlan] = []
    for course, source in selections:
        if _cancelled(cancellation_requested):
            raise ReviewedSyncCancelled("reviewed sync discovery cancelled")
        identity = (source.provider, source.instance)
        provider = providers.get(identity)
        if (
            provider is None
            or provider.provider_name != source.provider
            or provider.instance != source.instance
        ):
            raise ValueError("providers must cover each exact source identity")
        previous, initialized, skips, reconciliation_required = _source_state_context(
            state, course=course, source=source
        )
        capturing = _CapturingProvider(provider)
        preview = sync_course_source(
            course=course,
            source=source,
            provider=capturing,
            output_root=output_root,
            previous=previous,
            execution=ExecutionMode.DRY_RUN,
            initialized=initialized,
            reconciliation_required=reconciliation_required,
            policy_skips=skips,
        )
        if "provider_discovery_failed" in preview.issue_codes:
            raise RuntimeError("provider discovery failed")
        batch = capturing.batch or ContentBatch(
            source.provider,
            source.instance,
            source.course_id,
            frozenset(),
            (),
            (),
            (),
        )
        plans.append(ReviewedSourcePlan(course, source, batch, preview))
    return ReviewedSyncPlan(
        profile,
        config_path,
        state_path,
        output_root,
        _bytes_revision(config_snapshot),
        state_revision,
        tuple(plans),
    )


def _conflict(code: str) -> RunTransactionResult:
    return RunTransactionResult((), code, 0)


def apply_reviewed_sync_plan(
    plan: ReviewedSyncPlan,
    *,
    expected_plan_digest: str,
    providers: Mapping[tuple[str, str], CourseContentProvider],
    config_path: Path,
    state_path: Path,
    backup_path: Path,
    lock_dir: Path,
    cache_dir: Path,
    cancellation_requested: Callable[[], bool] | None = None,
) -> RunTransactionResult:
    """Apply the sealed batches after in-lock revision and binding checks."""

    if not isinstance(plan, ReviewedSyncPlan) or plan.digest != expected_plan_digest:
        return _conflict("plan_changed")
    try:
        config_path = Path(config_path).resolve(strict=True)
        state_path = Path(state_path).resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return _conflict("plan_changed")
    if config_path != plan.config_path or state_path != plan.state_path:
        return _conflict("plan_changed")
    if _cancelled(cancellation_requested):
        return _conflict("operation_cancelled")
    state_lock = SyncLock.for_state(
        lock_dir=lock_dir, profile=plan.profile, output_root=plan.output_root
    )
    completed = []
    with state_lock:
        try:
            config_snapshot = config_path.read_bytes()
            config = load_config_bytes(config_snapshot)
        except Exception:
            return _conflict("config_changed")
        if _bytes_revision(config_snapshot) != plan.config_revision:
            return _conflict("config_changed")
        selections = tuple((entry.course, entry.source) for entry in plan.sources)
        if not _validate_config_bindings(
            config,
            profile=plan.profile,
            output_root=plan.output_root,
            selections=selections,
        ):
            return _conflict("config_changed")
        try:
            state, state_revision = _load_state_snapshot(
                state_path, profile=plan.profile, output_root=plan.output_root
            )
        except (OSError, StateLoadError, ValueError):
            return _conflict("state_changed")
        if state_revision != plan.state_revision:
            return _conflict("state_changed")
        for index, entry in enumerate(plan.sources):
            if _cancelled(cancellation_requested):
                return RunTransactionResult(
                    tuple(completed), "operation_cancelled", index
                )
            identity = (entry.source.provider, entry.source.instance)
            provider = providers.get(identity)
            if (
                provider is None
                or provider.provider_name != entry.source.provider
                or provider.instance != entry.source.instance
            ):
                return RunTransactionResult(
                    tuple(completed), "plan_provider_mismatch", index
                )
            try:
                transaction = apply_source_transaction(
                    state,
                    course=entry.course,
                    source=entry.source,
                    provider=provider,
                    output_root=plan.output_root,
                    state_path=state_path,
                    backup_path=backup_path,
                    state_lock=state_lock,
                    cache_dir=cache_dir,
                    discovered_batch=entry.batch,
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


__all__ = [
    "ReviewedSourcePlan",
    "ReviewedSyncCancelled",
    "ReviewedSyncPlan",
    "apply_reviewed_sync_plan",
    "build_reviewed_sync_plan",
]
