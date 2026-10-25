"""Typed due-report workflow for desktop and other application adapters.

Refreshing may update the existing last-good due-state cache, but never publishes
reports.  Publishing remains an explicit, separately validated operation.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from pathlib import Path

from canvas_ed_mcp.config import AppConfig, ConfigLoadError, load_config_bytes
from canvas_ed_mcp.due.model import DueItem
from canvas_ed_mcp.due.render import (
    SYDNEY,
    latest_versioned_report,
    publish_versioned_report,
    render_semester_markdown,
    render_week_text,
    select_week_items,
)
from canvas_ed_mcp.due.service import DueStateError, collect_configured_due_items
from canvas_ed_mcp.paths import ProfilePaths, resolve_profile_paths
from canvas_ed_mcp.secrets import SecretUnavailable, resolve_secret

from .contracts import (
    ApplicationIssue,
    CancellationToken,
    IssueSeverity,
    NeverCancelled,
    OperationStatus,
    ProgressEvent,
    ProgressPhase,
)
from .daily_sync import ProgressCallback
from .fingerprints import bytes_revision
from .providers import (
    CanvasFactory,
    EdFactory,
    ProviderDescriptor,
    ProviderSession,
    SecretResolver,
    _default_canvas_factory,
    _default_ed_factory,
)


PathsResolver = Callable[[str], ProfilePaths]
Clock = Callable[[], datetime]
GradescopeFactory = Callable[[str, str], object]
_OPERATION = "due-reports"


class DueReportScope(str, Enum):
    """Select which user-facing report is prepared and published."""

    THIS_WEEK = "this_week"
    SEMESTER = "semester"
    BOTH = "both"


def _default_gradescope_factory(email: str, password: str) -> object:
    from canvas_ed_mcp.providers.gradescope import GradescopeProvider

    return GradescopeProvider(email, password)


def _issue(
    code: str,
    *,
    severity: IssueSeverity = IssueSeverity.ERROR,
    provider: str | None = None,
    retryable: bool = False,
) -> ApplicationIssue:
    return ApplicationIssue(
        code,
        severity,
        provider=provider,
        retryable=retryable,
    )


def _progress(
    callback: ProgressCallback | None,
    phase: ProgressPhase,
    completed: int,
    total: int | None,
    *,
    code: str | None = None,
) -> None:
    if callback is None:
        return
    try:
        callback(ProgressEvent(_OPERATION, phase, completed, total, code=code))
    except Exception:
        pass


def _safe_export_directory(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ValueError("export directory must be an absolute Path")
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        raise ValueError("export directory is invalid") from None


@dataclass(frozen=True, slots=True)
class DueRefreshRequest:
    profile: str
    config_path: Path | None = None
    export_directory: Path | None = None
    scope: DueReportScope = DueReportScope.BOTH

    def __post_init__(self) -> None:
        if not isinstance(self.profile, str) or not self.profile.strip():
            raise ValueError("profile must be non-empty")
        if self.config_path is not None and not isinstance(self.config_path, Path):
            raise ValueError("config_path must be a Path or None")
        if self.export_directory is not None:
            _safe_export_directory(self.export_directory)
        if not isinstance(self.scope, DueReportScope):
            raise ValueError("scope must be a DueReportScope")


@dataclass(frozen=True, slots=True)
class DueReportSnapshot:
    profile: str
    config_path: Path
    config_revision: str
    state_path: Path
    export_directory: Path
    weekly_text: str
    semester_markdown: str
    item_count: int
    week_item_count: int
    semester_label: str
    publishable: bool
    used_last_good: bool
    scope: DueReportScope = DueReportScope.BOTH
    issues: tuple[ApplicationIssue, ...] = ()
    items: tuple[DueItem, ...] = field(default=(), repr=False, compare=False)
    report_now: datetime = field(repr=False, compare=False, default_factory=lambda: datetime.now(SYDNEY))
    teaching_start: date | None = field(default=None, repr=False, compare=False)
    teaching_periods: tuple[tuple[str, date, date], ...] = field(
        default=(), repr=False, compare=False
    )


@dataclass(frozen=True, slots=True)
class DueRefreshResult:
    status: OperationStatus
    snapshot: DueReportSnapshot | None = field(default=None, repr=False)
    issues: tuple[ApplicationIssue, ...] = ()


@dataclass(frozen=True, slots=True)
class DueExportResult:
    status: OperationStatus
    weekly_path: Path | None = None
    semester_path: Path | None = None
    issues: tuple[ApplicationIssue, ...] = ()


class _CancellableDueProvider:
    """Stop before the next source request without changing provider semantics."""

    def __init__(self, provider, cancellation: CancellationToken) -> None:
        self._provider = provider
        self._cancellation = cancellation

    def list_due_items(self, *args, **kwargs):
        if self._cancellation.cancellation_requested:
            raise _DueCancelled
        return self._provider.list_due_items(*args, **kwargs)


class _DueCancelled(Exception):
    pass


class DueReportService:
    """Collect, render, retarget, and explicitly publish both due reports."""

    def __init__(
        self,
        *,
        paths_resolver: PathsResolver = resolve_profile_paths,
        secret_resolver: SecretResolver = resolve_secret,
        canvas_factory: CanvasFactory = _default_canvas_factory,
        ed_factory: EdFactory = _default_ed_factory,
        gradescope_factory: GradescopeFactory = _default_gradescope_factory,
        clock: Clock = lambda: datetime.now(SYDNEY),
    ) -> None:
        self._paths_resolver = paths_resolver
        self._secret_resolver = secret_resolver
        self._canvas_factory = canvas_factory
        self._ed_factory = ed_factory
        self._gradescope_factory = gradescope_factory
        self._clock = clock

    @staticmethod
    def _semester_label(config: AppConfig, now: datetime) -> str:
        terms = sorted(
            {
                source.term
                for course in config.courses
                for source in course.sources
                if source.term
            }
        )
        return config.daily_use.due.semester_label or " / ".join(terms) or f"{now.year} Semester"

    @staticmethod
    def _descriptors(config: AppConfig, names: set[str]) -> dict[str, ProviderDescriptor]:
        descriptors: dict[str, ProviderDescriptor] = {}
        if "canvas" in names:
            canvas = config.platforms.canvas
            if canvas.enabled and canvas.base_url and canvas.credential:
                descriptors["canvas"] = ProviderDescriptor(
                    "canvas", canvas.credential, base_url=canvas.base_url
                )
        if "ed" in names:
            ed = config.platforms.ed
            if ed.enabled and ed.credential:
                descriptors["ed"] = ProviderDescriptor(
                    "ed", ed.credential, region=ed.region
                )
        return descriptors

    def _create_gradescope(
        self,
        config: AppConfig,
        cancellation: CancellationToken,
    ) -> object:
        """Resolve Gradescope secrets only inside the shortest construction scope."""

        if cancellation.cancellation_requested:
            raise _DueCancelled
        platform = config.platforms.gradescope
        if (
            not platform.enabled
            or not platform.email_credential
            or not platform.password_credential
        ):
            raise SecretUnavailable(
                "missing_credential",
                "Gradescope credentials are not configured",
            )
        email: str | None = None
        password: str | None = None
        try:
            email = self._secret_resolver(platform.email_credential)
            if cancellation.cancellation_requested:
                raise _DueCancelled
            password = self._secret_resolver(platform.password_credential)
            if cancellation.cancellation_requested:
                raise _DueCancelled
            candidate = self._gradescope_factory(email, password)
        finally:
            email = None
            password = None
        if cancellation.cancellation_requested:
            try:
                candidate.close()
            finally:
                raise _DueCancelled
        candidate.list_courses()
        return candidate

    @staticmethod
    def _render_snapshot(
        snapshot: DueReportSnapshot,
        export_directory: Path,
    ) -> DueReportSnapshot:
        selected = _safe_export_directory(export_directory)
        weekly = (
            render_week_text(snapshot.items, now=snapshot.report_now)
            if snapshot.scope in {DueReportScope.THIS_WEEK, DueReportScope.BOTH}
            else ""
        )
        semester = ""
        if snapshot.scope in {DueReportScope.SEMESTER, DueReportScope.BOTH}:
            latest = latest_versioned_report(selected, "this-semester-due.md")
            existing = latest.read_text(encoding="utf-8") if latest else ""
            semester = render_semester_markdown(
                snapshot.items,
                semester_label=snapshot.semester_label,
                existing_markdown=existing,
                teaching_start=snapshot.teaching_start,
                teaching_periods=snapshot.teaching_periods,
            )
        return replace(
            snapshot,
            export_directory=selected,
            weekly_text=weekly,
            semester_markdown=semester,
        )

    def refresh(
        self,
        request: DueRefreshRequest,
        *,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken = NeverCancelled(),
    ) -> DueRefreshResult:
        session: ProviderSession | None = None
        gradescope = None
        try:
            _progress(progress, ProgressPhase.VALIDATING, 0, 1)
            paths = self._paths_resolver(request.profile)
            config_path = Path(request.config_path or paths.config_file).resolve(strict=True)
            config_bytes = config_path.read_bytes()
            config = load_config_bytes(config_bytes)
            if config.profile != request.profile:
                return DueRefreshResult(
                    OperationStatus.CONFLICT,
                    issues=(_issue("profile_mismatch"),),
                )
            if not config.daily_use.due.enabled:
                return DueRefreshResult(
                    OperationStatus.FAILED,
                    issues=(_issue("due_disabled"),),
                )
            if cancellation.cancellation_requested:
                return DueRefreshResult(OperationStatus.CANCELLED)
            _progress(progress, ProgressPhase.VALIDATING, 1, 1)

            names = {
                source.provider
                for course in config.courses
                for source in course.sources
                if source.provider in {"canvas", "ed"}
            }
            descriptors = self._descriptors(config, names)
            session = ProviderSession(
                descriptors,
                secret_resolver=self._secret_resolver,
                canvas_factory=self._canvas_factory,
                ed_factory=self._ed_factory,
            )
            providers = {}
            startup_issues: list[ApplicationIssue] = []
            _progress(progress, ProgressPhase.CONNECTING, 0, len(names))
            for index, name in enumerate(sorted(names), start=1):
                if cancellation.cancellation_requested:
                    return DueRefreshResult(OperationStatus.CANCELLED)
                try:
                    candidate = session.get(name)
                    providers[(name, candidate.instance)] = _CancellableDueProvider(
                        candidate, cancellation
                    )
                except SecretUnavailable as exc:
                    startup_issues.append(
                        _issue(exc.category, provider=name, retryable=True)
                    )
                except Exception:
                    startup_issues.append(
                        _issue("provider_unavailable", provider=name, retryable=True)
                    )
                _progress(progress, ProgressPhase.CONNECTING, index, len(names))

            if config.daily_use.due.gradescope_courses:
                try:
                    gradescope = self._create_gradescope(config, cancellation)
                    providers[("gradescope", "gradescope.com")] = _CancellableDueProvider(
                        gradescope, cancellation
                    )
                except _DueCancelled:
                    return DueRefreshResult(OperationStatus.CANCELLED)
                except SecretUnavailable as exc:
                    startup_issues.append(
                        _issue(exc.category, provider="gradescope", retryable=True)
                    )
                except Exception:
                    startup_issues.append(
                        _issue(
                            "provider_unavailable",
                            provider="gradescope",
                            retryable=True,
                        )
                    )

            _progress(progress, ProgressPhase.DISCOVERING, 0, None)
            state_path = paths.state_file.parent / "due-state-v1.json"
            try:
                collection = collect_configured_due_items(config, providers, state_path)
            except _DueCancelled:
                return DueRefreshResult(OperationStatus.CANCELLED)
            if cancellation.cancellation_requested:
                return DueRefreshResult(OperationStatus.CANCELLED)
            _progress(progress, ProgressPhase.DISCOVERING, 1, 1)

            close_failures = list(session.close())
            session = None
            if gradescope is not None:
                try:
                    gradescope.close()
                except Exception:
                    close_failures.append("gradescope")
                gradescope = None
            startup_issues.extend(
                _issue(
                    "provider_close_failed",
                    severity=IssueSeverity.WARNING,
                    provider=provider,
                    retryable=True,
                )
                for provider in close_failures
            )

            now = self._clock().astimezone(SYDNEY)
            semester_label = self._semester_label(config, now)
            application_issues = tuple(startup_issues) + tuple(
                _issue(
                    item.code,
                    severity=IssueSeverity.WARNING,
                    retryable=item.retry_required,
                )
                for item in collection.issues
            )
            export_directory = _safe_export_directory(
                request.export_directory or (config.output_root / "Due Dates")
            )
            week_items = select_week_items(collection.items, now=now)
            teaching_periods = tuple(
                (period.label, period.start_date, period.end_date)
                for period in config.daily_use.due.teaching_periods
            )
            snapshot = DueReportSnapshot(
                profile=config.profile,
                config_path=config_path,
                config_revision=bytes_revision(config_bytes),
                state_path=state_path.resolve(strict=False),
                export_directory=export_directory,
                weekly_text="",
                semester_markdown="",
                item_count=len(collection.items),
                week_item_count=len(week_items),
                semester_label=semester_label,
                publishable=collection.publishable,
                used_last_good=collection.used_last_good,
                issues=application_issues,
                items=collection.items,
                report_now=now,
                teaching_start=config.daily_use.due.teaching_start,
                teaching_periods=teaching_periods,
                scope=request.scope,
            )
            snapshot = self._render_snapshot(snapshot, export_directory)
            status = (
                OperationStatus.PARTIAL
                if application_issues or collection.used_last_good or not collection.publishable
                else OperationStatus.SUCCEEDED
            )
            _progress(
                progress,
                ProgressPhase.PLANNING,
                1,
                1,
                code=status.value,
            )
            return DueRefreshResult(status, snapshot, application_issues)
        except ConfigLoadError as exc:
            return DueRefreshResult(
                OperationStatus.FAILED, issues=(_issue(exc.category),)
            )
        except FileNotFoundError:
            return DueRefreshResult(
                OperationStatus.FAILED, issues=(_issue("config_missing"),)
            )
        except DueStateError:
            return DueRefreshResult(
                OperationStatus.FAILED, issues=(_issue("invalid_due_state"),)
            )
        except (OSError, RuntimeError, ValueError):
            return DueRefreshResult(
                OperationStatus.FAILED, issues=(_issue("due_refresh_failed"),)
            )
        finally:
            if session is not None:
                session.close()
            if gradescope is not None:
                try:
                    gradescope.close()
                except Exception:
                    pass

    def retarget(
        self,
        snapshot: DueReportSnapshot,
        export_directory: Path,
        *,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken = NeverCancelled(),
    ) -> DueRefreshResult:
        if not isinstance(snapshot, DueReportSnapshot):
            raise TypeError("snapshot must be DueReportSnapshot")
        if cancellation.cancellation_requested:
            return DueRefreshResult(OperationStatus.CANCELLED)
        try:
            _progress(progress, ProgressPhase.PLANNING, 0, 1)
            updated = self._render_snapshot(snapshot, export_directory)
        except (OSError, RuntimeError, UnicodeError, ValueError):
            return DueRefreshResult(
                OperationStatus.FAILED,
                issues=(_issue("export_directory_invalid"),),
            )
        if cancellation.cancellation_requested:
            return DueRefreshResult(OperationStatus.CANCELLED)
        status = (
            OperationStatus.PARTIAL
            if updated.issues or updated.used_last_good or not updated.publishable
            else OperationStatus.SUCCEEDED
        )
        _progress(progress, ProgressPhase.PLANNING, 1, 1, code=status.value)
        return DueRefreshResult(status, updated, updated.issues)

    def export(
        self,
        snapshot: DueReportSnapshot,
        *,
        progress: ProgressCallback | None = None,
        cancellation: CancellationToken = NeverCancelled(),
    ) -> DueExportResult:
        if not isinstance(snapshot, DueReportSnapshot):
            raise TypeError("snapshot must be DueReportSnapshot")
        if not snapshot.publishable:
            return DueExportResult(
                OperationStatus.FAILED,
                issues=(_issue("due_not_publishable"),),
            )
        if cancellation.cancellation_requested:
            return DueExportResult(OperationStatus.CANCELLED)
        try:
            current = snapshot.config_path.read_bytes()
            if bytes_revision(current) != snapshot.config_revision:
                return DueExportResult(
                    OperationStatus.CONFLICT,
                    issues=(_issue("config_changed"),),
                )
            export_weekly = snapshot.scope in {
                DueReportScope.THIS_WEEK,
                DueReportScope.BOTH,
            }
            export_semester = snapshot.scope in {
                DueReportScope.SEMESTER,
                DueReportScope.BOTH,
            }
            total = int(export_weekly) + int(export_semester)
            completed = 0
            _progress(progress, ProgressPhase.COMMITTING, completed, total)
            weekly = None
            if export_weekly:
                weekly = publish_versioned_report(
                    snapshot.export_directory,
                    "this-week-due.txt",
                    snapshot.weekly_text,
                )
                completed += 1
                _progress(progress, ProgressPhase.COMMITTING, completed, total)
            if cancellation.cancellation_requested and export_semester:
                return DueExportResult(
                    OperationStatus.PARTIAL if weekly is not None else OperationStatus.CANCELLED,
                    weekly_path=weekly,
                    issues=(
                        (_issue("export_cancelled_after_weekly"),)
                        if weekly is not None and export_semester
                        else ()
                    ),
                )
            semester = None
            if export_semester:
                try:
                    semester = publish_versioned_report(
                        snapshot.export_directory,
                        "this-semester-due.md",
                        snapshot.semester_markdown,
                    )
                except OSError:
                    return DueExportResult(
                        OperationStatus.PARTIAL if weekly is not None else OperationStatus.FAILED,
                        weekly_path=weekly,
                        issues=(_issue("semester_export_failed"),),
                    )
                completed += 1
            _progress(
                progress,
                ProgressPhase.COMMITTING,
                completed,
                total,
                code=OperationStatus.SUCCEEDED.value,
            )
            return DueExportResult(OperationStatus.SUCCEEDED, weekly, semester)
        except (OSError, RuntimeError, ValueError):
            return DueExportResult(
                OperationStatus.FAILED,
                issues=(_issue("due_export_failed"),),
            )


__all__ = [
    "DueExportResult",
    "DueRefreshRequest",
    "DueRefreshResult",
    "DueReportScope",
    "DueReportService",
    "DueReportSnapshot",
]
