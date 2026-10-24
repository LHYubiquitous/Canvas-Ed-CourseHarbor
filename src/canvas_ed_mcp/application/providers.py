"""Lazy, redacted provider lifecycle for application adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from canvas_ed_mcp.providers.base import CourseCandidate, CourseCatalogProvider


CanvasFactory = Callable[[str, str], CourseCatalogProvider]
EdFactory = Callable[[str], CourseCatalogProvider]


class SecretResolver(Protocol):
    def __call__(self, reference: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    provider: str
    credential: str = field(repr=False)
    base_url: str | None = None
    region: str | None = None

    def __post_init__(self) -> None:
        if self.provider not in {"canvas", "ed"}:
            raise ValueError("provider must be canvas or ed")
        if not isinstance(self.credential, str) or not self.credential:
            raise ValueError("credential must be a non-empty reference")
        if self.provider == "canvas" and not self.base_url:
            raise ValueError("Canvas requires a base URL")
        if self.provider == "ed" and self.region != "global":
            raise ValueError("Ed supports only the global region")


def _default_canvas_factory(base_url: str, token: str) -> CourseCatalogProvider:
    from canvas_ed_mcp.providers.canvas import CanvasProvider

    return CanvasProvider(base_url, token)


def _default_ed_factory(token: str) -> CourseCatalogProvider:
    from canvas_ed_mcp.providers.ed import EdProvider

    return EdProvider(token)


class ProviderSession:
    """Construct only requested providers and close every constructed instance once."""

    def __init__(
        self,
        descriptors: Mapping[str, ProviderDescriptor],
        *,
        secret_resolver: SecretResolver,
        canvas_factory: CanvasFactory = _default_canvas_factory,
        ed_factory: EdFactory = _default_ed_factory,
    ) -> None:
        if not isinstance(descriptors, Mapping):
            raise ValueError("descriptors must be a mapping")
        self._descriptors = dict(descriptors)
        for key, descriptor in self._descriptors.items():
            if key != descriptor.provider:
                raise ValueError("descriptor key must match provider")
        self._secret_resolver = secret_resolver
        self._canvas_factory = canvas_factory
        self._ed_factory = ed_factory
        self._providers: dict[str, CourseCatalogProvider] = {}
        self._closed = False

    def get(self, provider: str) -> CourseCatalogProvider:
        if self._closed:
            raise RuntimeError("provider_session_closed")
        if provider in self._providers:
            return self._providers[provider]
        descriptor = self._descriptors.get(provider)
        if descriptor is None:
            raise ValueError("provider_not_requested")
        token = self._secret_resolver(descriptor.credential)
        if provider == "canvas":
            assert descriptor.base_url is not None
            candidate = self._canvas_factory(descriptor.base_url, token)
        else:
            candidate = self._ed_factory(token)
        if candidate.provider_name != provider:
            try:
                candidate.close()
            finally:
                raise ValueError("provider_identity_mismatch")
        self._providers[provider] = candidate
        return candidate

    def list_courses(self, provider: str) -> tuple[CourseCandidate, ...]:
        selected = self.get(provider)
        courses = selected.list_courses()
        if not isinstance(courses, tuple):
            raise ValueError("provider catalog must be an immutable tuple")
        for candidate in courses:
            if (
                not isinstance(candidate, CourseCandidate)
                or candidate.provider != selected.provider_name
                or candidate.instance != selected.instance
            ):
                raise ValueError(
                    "provider catalog candidate does not match its provider instance"
                )
        return courses

    def close(self) -> tuple[str, ...]:
        if self._closed:
            return ()
        self._closed = True
        failed: list[str] = []
        for provider_name, provider in self._providers.items():
            try:
                provider.close()
            except Exception:
                failed.append(provider_name)
        return tuple(failed)

    def __enter__(self) -> "ProviderSession":
        if self._closed:
            raise RuntimeError("provider_session_closed")
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
