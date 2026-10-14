"""Provider-aware, read-only MCP server for the portable package."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Protocol, cast
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from .config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseSource,
    EdPlatformConfig,
    GradescopePlatformConfig,
)
from .providers.base import ContentBatch, CourseCatalogProvider, ProviderError
from .providers.canvas import CanvasProvider
from .providers.ed import EdProvider
from .providers.gradescope import (
    GradescopeClient,
    GradescopeError,
    GradescopeProvider,
)
from .secrets import SecretUnavailable, resolve_secret
from .sync.model import RemoteKind


SecretResolver = Callable[[str], str]


class CanvasFactory(Protocol):
    def __call__(self, config: CanvasPlatformConfig, token: str) -> CourseCatalogProvider: ...


class EdFactory(Protocol):
    def __call__(self, config: EdPlatformConfig, token: str) -> CourseCatalogProvider: ...


class GradescopeFactory(Protocol):
    def __call__(
        self, config: GradescopePlatformConfig, email: str, password: str
    ) -> GradescopeClient: ...


def _default_canvas_factory(
    config: CanvasPlatformConfig, token: str
) -> CourseCatalogProvider:
    if config.base_url is None:  # AppConfig already validates enabled providers.
        raise ValueError("enabled Canvas has no base URL")
    return CanvasProvider(config.base_url, token)


def _default_ed_factory(config: EdPlatformConfig, token: str) -> CourseCatalogProvider:
    if config.region != "global":
        raise ValueError("unsupported Ed region")
    return EdProvider(token)


def _default_gradescope_factory(
    config: GradescopePlatformConfig, email: str, password: str
) -> GradescopeClient:
    return GradescopeProvider(email, password)


@dataclass(frozen=True, slots=True)
class ProviderFactories:
    """Injectable provider construction seam used by the server and mock tests."""

    canvas: CanvasFactory = _default_canvas_factory
    ed: EdFactory = _default_ed_factory
    gradescope: GradescopeFactory = _default_gradescope_factory


def _configured_sources(config: AppConfig, provider: str) -> tuple[tuple[str, CourseSource], ...]:
    return tuple(
        (course.key, source)
        for course in config.courses
        for source in course.sources
        if source.provider == provider
    )


def _expected_instance(config: AppConfig, provider: str) -> str:
    if provider == "canvas":
        base_url = config.platforms.canvas.base_url
        if base_url is None:
            raise ValueError("enabled Canvas has no base URL")
        parsed = urlparse(base_url)
        host = cast(str, parsed.hostname).casefold()
        return host if parsed.port in {None, 443} else f"{host}:{parsed.port}"
    if config.platforms.ed.region != "global":
        raise ValueError("only the verified global Ed region is supported")
    return "edstem.org"


@dataclass(slots=True)
class ProviderRuntime:
    """Lazy provider owner; construction never resolves a secret or opens a client."""

    config: AppConfig
    secret_resolver: SecretResolver
    factories: ProviderFactories
    _providers: dict[str, CourseCatalogProvider] = field(default_factory=dict, init=False)
    _locks: dict[str, RLock] = field(
        default_factory=lambda: {"canvas": RLock(), "ed": RLock()}, init=False
    )
    _closed: bool = field(default=False, init=False)
    _gradescope: GradescopeClient | None = field(default=None, init=False)
    _gradescope_lock: RLock = field(default_factory=RLock, init=False)
    _gradescope_course_ids: set[str] = field(default_factory=set, init=False)

    def sources(self, provider: str) -> tuple[tuple[str, CourseSource], ...]:
        return _configured_sources(self.config, provider)

    def source_for(self, logical_course_key: str, provider: str) -> CourseSource:
        for course_key, source in self.sources(provider):
            if course_key == logical_course_key:
                return source
        raise ToolError(f"course_not_configured:{provider}")

    def get_provider(self, provider: str) -> CourseCatalogProvider:
        if provider not in {"canvas", "ed"}:
            raise ToolError("provider_unavailable:invalid_provider")
        platform = getattr(self.config.platforms, provider)
        if not platform.enabled or not self.sources(provider):
            raise ToolError(f"{provider}_unavailable:provider_disabled")
        with self._locks[provider]:
            if self._closed:
                raise ToolError(f"{provider}_unavailable:server_closed")
            existing = self._providers.get(provider)
            if existing is not None:
                return existing
            credential = platform.credential
            if credential is None:
                raise ToolError(f"{provider}_unavailable:missing_reference")
            try:
                token = self.secret_resolver(credential)
            except SecretUnavailable as exc:
                raise ToolError(f"{provider}_unavailable:{exc.category}") from None
            try:
                factory = getattr(self.factories, provider)
                created = factory(platform, token)
            except Exception:
                raise ToolError(f"{provider}_unavailable:provider_unavailable") from None
            if (
                created.provider_name != provider
                or created.instance != _expected_instance(self.config, provider)
            ):
                try:
                    created.close()
                finally:
                    raise ToolError(f"{provider}_unavailable:provider_identity_mismatch")
            self._providers[provider] = created
            return created

    def with_provider(
        self,
        provider: str,
        operation: Callable[[CourseCatalogProvider], Any],
    ) -> Any:
        """Serialize one synchronous provider operation, including lazy startup."""

        with self._locks[provider]:
            return operation(self.get_provider(provider))

    def get_gradescope(self) -> GradescopeClient:
        config = self.config.platforms.gradescope
        if not config.enabled:
            raise ToolError("gradescope_unavailable:provider_disabled")
        with self._gradescope_lock:
            if self._closed:
                raise ToolError("gradescope_unavailable:server_closed")
            if self._gradescope is not None:
                return self._gradescope
            if not config.email_credential or not config.password_credential:
                raise ToolError("gradescope_unavailable:missing_reference")
            try:
                email = self.secret_resolver(config.email_credential)
                password = self.secret_resolver(config.password_credential)
            except SecretUnavailable as exc:
                raise ToolError(f"gradescope_unavailable:{exc.category}") from None
            try:
                created = self.factories.gradescope(config, email, password)
            except GradescopeError as exc:
                raise ToolError(f"gradescope_unavailable:{exc.category}") from None
            except ImportError:
                raise ToolError("gradescope_unavailable:missing_dependency") from None
            except Exception:
                raise ToolError("gradescope_unavailable:provider_unavailable") from None
            self._gradescope = created
            return created

    def with_gradescope(self, operation: Callable[[GradescopeClient], Any]) -> Any:
        """Serialize login and scraper calls on the isolated optional provider."""

        with self._gradescope_lock:
            return operation(self.get_gradescope())

    def record_gradescope_courses(self, course_ids: set[str]) -> None:
        with self._gradescope_lock:
            self._gradescope_course_ids = set(course_ids)

    def require_gradescope_course(self, course_id: str) -> None:
        with self._gradescope_lock:
            if course_id not in self._gradescope_course_ids:
                raise ToolError("gradescope_course_not_discovered")

    def close(self) -> None:
        with self._locks["canvas"], self._locks["ed"], self._gradescope_lock:
            if self._closed:
                return
            self._closed = True
            providers = tuple(self._providers.values())
            self._providers.clear()
            if self._gradescope is not None:
                providers += (self._gradescope,)
                self._gradescope = None
            self._gradescope_course_ids.clear()
        for provider in providers:
            try:
                provider.close()
            except Exception:
                # Cleanup must remain best-effort and must not expose provider details.
                continue


def _safe_provider_call(provider: str, operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except ToolError:
        raise
    except SecretUnavailable as exc:
        raise ToolError(f"{provider}_unavailable:{exc.category}") from None
    except ProviderError as exc:
        raise ToolError(f"{provider}_unavailable:{exc.category}") from None
    except GradescopeError as exc:
        raise ToolError(f"{provider}_unavailable:{exc.category}") from None
    except Exception:
        raise ToolError(f"{provider}_unavailable:provider_unavailable") from None


def _serialize_course(logical_key: str, candidate: Any) -> dict[str, Any]:
    return {
        "logical_course_key": logical_key,
        "provider": candidate.provider,
        "instance": candidate.instance,
        "course_id": candidate.course_id,
        "code": candidate.code,
        "name": candidate.name,
        "term": candidate.term,
    }


def _catalog_result(runtime: ProviderRuntime, provider_name: str) -> dict[str, Any]:
    configured = runtime.sources(provider_name)
    if not configured:
        return {"provider": provider_name, "courses": [], "missing": []}
    candidates = _safe_provider_call(
        provider_name,
        lambda: runtime.with_provider(
            provider_name, lambda provider: provider.list_courses()
        ),
    )
    candidate_index = {
        (item.provider, item.instance, item.course_id): item for item in candidates
    }
    courses: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    for logical_key, source in configured:
        identity = (source.provider, source.instance, source.course_id)
        candidate = candidate_index.get(identity)
        if candidate is None:
            missing.append(
                {
                    "logical_course_key": logical_key,
                    "provider": source.provider,
                    "instance": source.instance,
                    "course_id": source.course_id,
                }
            )
        else:
            courses.append(_serialize_course(logical_key, candidate))
    return {"provider": provider_name, "courses": courses, "missing": missing}


def _serialize_batch(logical_key: str, batch: ContentBatch) -> dict[str, Any]:
    return {
        "logical_course_key": logical_key,
        "provider": batch.provider,
        "instance": batch.instance,
        "course_id": batch.course_id,
        "complete": batch.complete,
        "coverage": [
            {"kind": entry.kind.value, "status": entry.status.value}
            for entry in sorted(batch.coverage, key=lambda item: item.kind.value)
        ],
        "items": [
            {
                "kind": item.key.kind.value,
                "remote_id": item.key.remote_id,
                "display_name": item.display_name,
                "group": item.group,
                "size_bytes": item.size_bytes,
                "media_type": item.media_type,
            }
            for item in batch.items
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
    }


def create_server(
    config: AppConfig,
    *,
    secret_resolver: SecretResolver = resolve_secret,
    factories: ProviderFactories | None = None,
) -> FastMCP:
    """Build a read-only MCP server whose tool surface follows the validated config."""

    selected_factories = factories or ProviderFactories()
    runtime = ProviderRuntime(config, secret_resolver, selected_factories)

    for provider_name in ("canvas", "ed"):
        sources = runtime.sources(provider_name)
        if not sources:
            continue
        expected = _expected_instance(config, provider_name)
        if any(source.instance != expected for _, source in sources):
            raise ValueError(f"configured {provider_name} source does not match platform instance")

    @asynccontextmanager
    async def lifespan(_: FastMCP):
        try:
            yield runtime
        finally:
            runtime.close()

    server = FastMCP(
        "canvas-ed-mcp",
        instructions="Read-only access to explicitly configured Canvas and Ed courses.",
        lifespan=lifespan,
    )
    setattr(server, "_canvas_ed_runtime", runtime)

    @server.tool(name="server_status")
    def server_status() -> dict[str, Any]:
        enabled = [
            provider
            for provider in ("canvas", "ed")
            if getattr(config.platforms, provider).enabled
        ]
        return {
            "profile": config.profile,
            "mode": "read_only",
            "enabled_providers": enabled,
            "configured_course_count": len(config.courses),
            "configured_source_counts": {
                provider: len(runtime.sources(provider)) for provider in enabled
            },
            "enabled_optional_providers": (
                ["gradescope"] if config.platforms.gradescope.enabled else []
            ),
        }

    if config.platforms.canvas.enabled:
        @server.tool(name="canvas_list_configured_courses")
        async def canvas_list_configured_courses() -> dict[str, Any]:
            return await asyncio.to_thread(_catalog_result, runtime, "canvas")

    if config.platforms.ed.enabled:
        @server.tool(name="ed_list_configured_courses")
        async def ed_list_configured_courses() -> dict[str, Any]:
            return await asyncio.to_thread(_catalog_result, runtime, "ed")

        @server.tool(name="ed_list_course_content")
        async def ed_list_course_content(
            logical_course_key: str,
            kinds: list[str],
        ) -> dict[str, Any]:
            source = runtime.source_for(logical_course_key, "ed")
            try:
                selected_kinds = frozenset(RemoteKind(value) for value in kinds)
            except ValueError:
                raise ToolError("invalid_argument:unsupported_content_kind") from None
            allowed = frozenset({RemoteKind.LESSON, RemoteKind.RESOURCE})
            if not selected_kinds or not selected_kinds.issubset(allowed):
                raise ToolError("invalid_argument:unsupported_content_kind")
            def discover_content() -> Any:
                def call(provider: CourseCatalogProvider) -> Any:
                    list_items = getattr(provider, "list_items", None)
                    if list_items is None:
                        raise ToolError("ed_unavailable:capability_unavailable")
                    return list_items(source.course_id, kinds=selected_kinds)

                return runtime.with_provider("ed", call)

            batch = await asyncio.to_thread(
                _safe_provider_call, "ed", discover_content
            )
            if not isinstance(batch, ContentBatch) or (
                batch.provider,
                batch.instance,
                batch.course_id,
            ) != (source.provider, source.instance, source.course_id):
                raise ToolError("ed_unavailable:provider_identity_mismatch")
            return _serialize_batch(logical_course_key, batch)

    if config.platforms.gradescope.enabled:
        read_only_annotations = {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        }

        @server.tool(name="gradescope_list_courses", annotations=read_only_annotations)
        async def gradescope_list_courses() -> dict[str, Any]:
            courses = await asyncio.to_thread(
                _safe_provider_call,
                "gradescope",
                lambda: runtime.with_gradescope(
                    lambda provider: provider.list_courses()
                ),
            )
            runtime.record_gradescope_courses({course.course_id for course in courses})
            return {
                "courses": [
                    {
                        "role": course.role,
                        "course_id": course.course_id,
                        "name": course.name,
                        "semester": course.semester,
                        "year": course.year,
                        "num_assignments": course.num_assignments,
                    }
                    for course in courses
                ]
            }

        @server.tool(name="gradescope_list_assignments", annotations=read_only_annotations)
        async def gradescope_list_assignments(course_id: str) -> dict[str, Any]:
            runtime.require_gradescope_course(course_id)
            assignments = await asyncio.to_thread(
                _safe_provider_call,
                "gradescope",
                lambda: runtime.with_gradescope(
                    lambda provider: provider.list_assignments(course_id)
                ),
            )
            return {
                "course_id": course_id,
                "assignments": [
                    {
                        "course_id": assignment.course_id,
                        "assignment_id": assignment.assignment_id,
                        "name": assignment.name,
                        "due_at": assignment.due_at,
                        "late_due_at": assignment.late_due_at,
                        "submissions_status": assignment.submissions_status,
                        "grade": assignment.grade,
                        "max_grade": assignment.max_grade,
                    }
                    for assignment in assignments
                ],
            }

    return server


def close_server(server: FastMCP) -> None:
    """Idempotently close providers created by :func:`create_server`."""

    runtime = getattr(server, "_canvas_ed_runtime", None)
    if not isinstance(runtime, ProviderRuntime):
        raise ValueError("server was not created by create_server")
    runtime.close()


__all__ = [
    "ProviderFactories",
    "ProviderRuntime",
    "SecretResolver",
    "close_server",
    "create_server",
]
