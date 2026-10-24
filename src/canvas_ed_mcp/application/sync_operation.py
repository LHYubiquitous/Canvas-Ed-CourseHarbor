"""Production reviewed-sync adapter with one-shot provider-session leases."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from threading import Lock
from typing import Callable, cast

from canvas_ed_mcp.config import ConfigLoadError, load_config_bytes
from canvas_ed_mcp.paths import ProfilePaths, resolve_profile_paths
from canvas_ed_mcp.providers.base import CourseContentProvider
from canvas_ed_mcp.secrets import SecretUnavailable, resolve_secret
from canvas_ed_mcp.sync.reviewed import (
    ReviewedSyncCancelled,
    ReviewedSyncPlan,
    apply_reviewed_sync_plan,
    build_reviewed_sync_plan,
)

from .contracts import (
    ApplicationIssue,
    CancellationToken,
    IssueSeverity,
    OperationStatus,
    ProgressEvent,
    ProgressPhase,
    ReviewProof,
)
from .daily_sync import (
    ProgressCallback,
    SyncApplyResult,
    SyncPreviewRequest,
    SyncPreviewResult,
    SyncReview,
    SyncSourceSummary,
)
from .providers import (
    CanvasFactory,
    EdFactory,
    ProviderDescriptor,
    ProviderSession,
    SecretResolver,
    _default_canvas_factory,
    _default_ed_factory,
)


_CODE = re.compile(r"[a-z][a-z0-9_]*\Z")
_INFO_CODES = frozenset({"reconcile_completed"})
PathsResolver = Callable[[str], ProfilePaths]
_PREVIEW_OPERATION = "material-sync-preview"
_APPLY_OPERATION = "material-sync-apply"


@dataclass(slots=True)
class _PendingSync:
    review: SyncReview
    plan: ReviewedSyncPlan
    paths: _RuntimePaths
    session: ProviderSession
    providers: dict[tuple[str, str], CourseContentProvider]


@dataclass(frozen=True, slots=True)
class _RuntimePaths:
    profile: str
    state_file: Path
    state_backup_file: Path
    lock_dir: Path
    cache_dir: Path


def _issue(
    code: str,
    *,
    severity: IssueSeverity = IssueSeverity.ERROR,
    provider: str | None = None,
    course_key: str | None = None,
) -> ApplicationIssue:
    stable = code if isinstance(code, str) and _CODE.fullmatch(code) else "sync_failed"
    return ApplicationIssue(
        stable,
        severity,
        provider=provider,
        course_key=course_key,
    )


def _summary(source_plan, *, actions: tuple[str, ...] | None = None) -> SyncSourceSummary:
    return SyncSourceSummary(
        course_key=source_plan.course.key,
        provider=source_plan.source.provider,
        instance=source_plan.source.instance,
        course_id=source_plan.source.course_id,
        actions=(
            actions
            if actions is not None
            else tuple(item.action for item in source_plan.preview.items)
        ),
        issue_codes=source_plan.preview.issue_codes,
        course_folder=source_plan.course.folder,
        course_code=source_plan.source.code,
        course_name=source_plan.source.name,
        course_term=source_plan.source.term,
    )


def _canonical_runtime_paths(paths: ProfilePaths) -> _RuntimePaths:
    return _RuntimePaths(
        paths.profile,
        Path(paths.state_file).resolve(strict=False),
        Path(paths.state_backup_file).resolve(strict=False),
        Path(paths.lock_dir).resolve(strict=False),
        Path(paths.cache_dir).resolve(strict=False),
    )


def _progress(
    callback: ProgressCallback | None,
    operation_id: str,
    phase: ProgressPhase,
    completed: int,
    total: int | None,
    *,
    code: str | None = None,
) -> None:
    if callback is not None:
        try:
            callback(ProgressEvent(operation_id, phase, completed, total, code=code))
        except Exception:
            # Progress is observational. A disposed UI receiver must not alter the
            # reviewed operation or bypass provider-session cleanup.
            pass


class ReviewedSyncOperation:
    """Create, consume, and discard one reviewed sync/provider lease at a time."""

    def __init__(
        self,
        *,
        paths_resolver: PathsResolver = resolve_profile_paths,
        secret_resolver: SecretResolver = resolve_secret,
        canvas_factory: CanvasFactory = _default_canvas_factory,
        ed_factory: EdFactory = _default_ed_factory,
    ) -> None:
        self._paths_resolver = paths_resolver
        self._secret_resolver = secret_resolver
        self._canvas_factory = canvas_factory
        self._ed_factory = ed_factory
        self._lock = Lock()
        self._pending: _PendingSync | None = None

    def _take_pending(self, review: SyncReview | None = None) -> _PendingSync | None:
        with self._lock:
            pending = self._pending
            if pending is None or (review is not None and pending.review is not review):
                return None
            self._pending = None
            return pending

    def close(self) -> None:
        pending = self._take_pending()
        if pending is not None:
            pending.session.close()

    def discard(self, review: SyncReview) -> None:
        pending = self._take_pending(review)
        if pending is not None:
            pending.session.close()

    def _descriptors(self, config, provider_names: set[str]) -> dict[str, ProviderDescriptor]:
        descriptors: dict[str, ProviderDescriptor] = {}
        if "canvas" in provider_names:
            platform = config.platforms.canvas
            if not platform.enabled or not platform.base_url or not platform.credential:
                raise ValueError("canvas_not_configured")
            descriptors["canvas"] = ProviderDescriptor(
                "canvas", platform.credential, base_url=platform.base_url
            )
        if "ed" in provider_names:
            platform = config.platforms.ed
            if not platform.enabled or not platform.credential:
                raise ValueError("ed_not_configured")
            descriptors["ed"] = ProviderDescriptor(
                "ed", platform.credential, region=platform.region
            )
        return descriptors

    def preview(
        self,
        request: SyncPreviewRequest,
        *,
        progress: ProgressCallback | None,
        cancellation: CancellationToken,
    ) -> SyncPreviewResult:
        self.close()
        if cancellation.cancellation_requested:
            return SyncPreviewResult(OperationStatus.CANCELLED)
        session: ProviderSession | None = None
        try:
            _progress(
                progress,
                _PREVIEW_OPERATION,
                ProgressPhase.VALIDATING,
                0,
                1,
            )
            paths = self._paths_resolver(request.profile)
            if paths.profile != request.profile:
                raise ValueError("profile_mismatch")
            runtime_paths = _canonical_runtime_paths(paths)
            config_path = Path(request.config_path or paths.config_file).resolve(
                strict=True
            )
            config_snapshot = config_path.read_bytes()
            config = load_config_bytes(config_snapshot)
            if config.profile != request.profile:
                return SyncPreviewResult(
                    OperationStatus.CONFLICT,
                    issues=(_issue("profile_mismatch"),),
                )
            try:
                output_root = Path(config.output_root).resolve(strict=True)
            except FileNotFoundError:
                return SyncPreviewResult(
                    OperationStatus.FAILED,
                    issues=(_issue("output_root_missing"),),
                )
            except (OSError, RuntimeError, ValueError):
                return SyncPreviewResult(
                    OperationStatus.FAILED,
                    issues=(_issue("invalid_output_root"),),
                )
            if not output_root.is_dir():
                return SyncPreviewResult(
                    OperationStatus.FAILED,
                    issues=(_issue("invalid_output_root"),),
                )
            selected = tuple(
                course
                for course in config.courses
                if not request.course_keys or course.key in request.course_keys
            )
            if request.course_keys and {course.key for course in selected} != set(
                request.course_keys
            ):
                return SyncPreviewResult(
                    OperationStatus.FAILED,
                    issues=(_issue("unknown_course"),),
                )
            selections = tuple(
                (course, source)
                for course in selected
                for source in course.sources
                if source.provider in {"canvas", "ed"}
            )
            if not selections:
                return SyncPreviewResult(
                    OperationStatus.FAILED,
                    issues=(_issue("content_not_configured"),),
                )
            _progress(
                progress,
                _PREVIEW_OPERATION,
                ProgressPhase.VALIDATING,
                1,
                1,
            )
            provider_names = {source.provider for _, source in selections}
            session = ProviderSession(
                self._descriptors(config, provider_names),
                secret_resolver=self._secret_resolver,
                canvas_factory=self._canvas_factory,
                ed_factory=self._ed_factory,
            )
            providers: dict[tuple[str, str], CourseContentProvider] = {}
            _progress(
                progress,
                _PREVIEW_OPERATION,
                ProgressPhase.CONNECTING,
                0,
                len(provider_names),
            )
            for course, source in selections:
                if cancellation.cancellation_requested:
                    session.close()
                    return SyncPreviewResult(OperationStatus.CANCELLED)
                identity = (source.provider, source.instance)
                if identity in providers:
                    continue
                candidate = session.get(source.provider)
                if candidate.instance != source.instance or not all(
                    callable(getattr(candidate, name, None))
                    for name in ("list_items", "open_binary", "render_html")
                ):
                    raise ValueError("provider_instance_mismatch")
                providers[identity] = cast(CourseContentProvider, candidate)
                _progress(
                    progress,
                    _PREVIEW_OPERATION,
                    ProgressPhase.CONNECTING,
                    len(providers),
                    len(provider_names),
                )
            _progress(
                progress,
                _PREVIEW_OPERATION,
                ProgressPhase.DISCOVERING,
                0,
                len(selections),
            )
            plan = build_reviewed_sync_plan(
                profile=config.profile,
                config_path=config_path,
                state_path=runtime_paths.state_file,
                output_root=output_root,
                selections=selections,
                providers=providers,
                config_snapshot=config_snapshot,
                cancellation_requested=lambda: cancellation.cancellation_requested,
            )
            _progress(
                progress,
                _PREVIEW_OPERATION,
                ProgressPhase.DISCOVERING,
                len(plan.sources),
                len(selections),
            )
            if cancellation.cancellation_requested:
                session.close()
                return SyncPreviewResult(OperationStatus.CANCELLED)
            summaries = tuple(_summary(source) for source in plan.sources)
            issues = tuple(
                _issue(
                    code,
                    severity=(
                        IssueSeverity.INFO
                        if code in _INFO_CODES
                        else IssueSeverity.WARNING
                    ),
                    provider=source.source.provider,
                    course_key=source.course.key,
                )
                for source in plan.sources
                for code in source.preview.issue_codes
            )
            review = SyncReview(
                proof=ReviewProof(
                    operation="sync",
                    plan_digest=plan.digest,
                    config_revision=plan.config_revision,
                    state_revision=plan.state_revision,
                ),
                profile=config.profile,
                config_path=config_path,
                state_path=plan.state_path,
                output_root=plan.output_root,
                sources=summaries,
            )
            if plan.state_path != runtime_paths.state_file:
                raise ValueError("profile_paths_changed")
            pending = _PendingSync(review, plan, runtime_paths, session, providers)
            with self._lock:
                if self._pending is not None:
                    raise RuntimeError("sync_preview_in_progress")
                self._pending = pending
            status = (
                OperationStatus.PARTIAL
                if any(issue.severity is not IssueSeverity.INFO for issue in issues)
                else OperationStatus.SUCCEEDED
            )
            _progress(
                progress,
                _PREVIEW_OPERATION,
                ProgressPhase.PLANNING,
                1,
                1,
                code=status.value,
            )
            return SyncPreviewResult(
                status,
                review,
                issues,
            )
        except ReviewedSyncCancelled:
            if session is not None:
                session.close()
            return SyncPreviewResult(OperationStatus.CANCELLED)
        except ConfigLoadError as exc:
            if session is not None:
                session.close()
            return SyncPreviewResult(
                OperationStatus.FAILED, issues=(_issue(exc.category),)
            )
        except SecretUnavailable as exc:
            if session is not None:
                session.close()
            return SyncPreviewResult(
                OperationStatus.FAILED, issues=(_issue(exc.category),)
            )
        except Exception:
            if session is not None:
                session.close()
            return SyncPreviewResult(
                OperationStatus.FAILED, issues=(_issue("sync_preview_failed"),)
            )

    def apply(
        self,
        review: SyncReview,
        *,
        progress: ProgressCallback | None,
        cancellation: CancellationToken,
    ) -> SyncApplyResult:
        pending = self._take_pending(review)
        if pending is None:
            return SyncApplyResult(
                OperationStatus.CONFLICT,
                issues=(_issue("review_required"),),
            )
        close_failures: tuple[str, ...] = ()
        try:
            if cancellation.cancellation_requested:
                return SyncApplyResult(OperationStatus.CANCELLED)
            _progress(
                progress,
                _APPLY_OPERATION,
                ProgressPhase.VALIDATING,
                0,
                1,
            )
            try:
                current_paths = _canonical_runtime_paths(
                    self._paths_resolver(review.profile)
                )
            except Exception:
                return SyncApplyResult(
                    OperationStatus.CONFLICT,
                    issues=(_issue("profile_paths_changed"),),
                )
            if pending.paths != current_paths:
                return SyncApplyResult(
                    OperationStatus.CONFLICT,
                    issues=(_issue("profile_paths_changed"),),
                )
            _progress(
                progress,
                _APPLY_OPERATION,
                ProgressPhase.VALIDATING,
                1,
                1,
            )
            _progress(
                progress,
                _APPLY_OPERATION,
                ProgressPhase.APPLYING,
                0,
                len(pending.plan.sources),
            )
            try:
                run = apply_reviewed_sync_plan(
                    pending.plan,
                    expected_plan_digest=review.proof.plan_digest,
                    providers=pending.providers,
                    config_path=review.config_path,
                    state_path=review.state_path,
                    backup_path=pending.paths.state_backup_file,
                    lock_dir=pending.paths.lock_dir,
                    cache_dir=pending.paths.cache_dir,
                    cancellation_requested=(
                        lambda: cancellation.cancellation_requested
                    ),
                )
            except Exception:
                return SyncApplyResult(
                    OperationStatus.FAILED,
                    issues=(_issue("sync_apply_failed"),),
                )
        finally:
            close_failures = pending.session.close()

        conflict_codes = {
            "plan_changed",
            "config_changed",
            "state_changed",
            "plan_provider_mismatch",
        }
        issues: list[ApplicationIssue] = []
        if run.issue_code is not None:
            issues.append(
                _issue(
                    run.issue_code,
                    severity=(
                        IssueSeverity.WARNING
                        if run.issue_code == "operation_cancelled"
                        else IssueSeverity.ERROR
                    ),
                )
            )
        summaries: list[SyncSourceSummary] = []
        for source_plan, transaction in zip(pending.plan.sources, run.transactions):
            result = transaction.source_result
            source_codes = list(result.issue_codes)
            if transaction.commit.issue_code is not None:
                source_codes.append(transaction.commit.issue_code)
            summaries.append(
                SyncSourceSummary(
                    course_key=source_plan.course.key,
                    provider=source_plan.source.provider,
                    instance=source_plan.source.instance,
                    course_id=source_plan.source.course_id,
                    actions=tuple(item.action for item in result.items),
                    issue_codes=tuple(dict.fromkeys(source_codes)),
                    course_folder=source_plan.course.folder,
                    course_code=source_plan.source.code,
                    course_name=source_plan.source.name,
                    course_term=source_plan.source.term,
                )
            )
            issues.extend(
                _issue(
                    code,
                    severity=(
                        IssueSeverity.INFO
                        if code in _INFO_CODES
                        else IssueSeverity.WARNING
                    ),
                    provider=source_plan.source.provider,
                    course_key=source_plan.course.key,
                )
                for code in source_codes
            )
        issues.extend(
            _issue(
                "provider_close_failed",
                severity=IssueSeverity.WARNING,
                provider=provider,
            )
            for provider in close_failures
        )
        if run.issue_code == "operation_cancelled" and not run.transactions:
            status = OperationStatus.CANCELLED
        elif run.issue_code in conflict_codes and not run.transactions:
            status = OperationStatus.CONFLICT
        elif run.committed and not any(
            issue.severity is not IssueSeverity.INFO for issue in issues
        ):
            status = OperationStatus.SUCCEEDED
        elif run.transactions:
            status = OperationStatus.PARTIAL
        else:
            status = OperationStatus.FAILED
        _progress(
            progress,
            _APPLY_OPERATION,
            ProgressPhase.CLEANING_UP,
            1,
            1,
            code=status.value,
        )
        return SyncApplyResult(status, tuple(summaries), tuple(issues))


__all__ = ["ReviewedSyncOperation"]
