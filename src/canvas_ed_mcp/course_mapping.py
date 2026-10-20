"""Pure, explicit Canvas/Ed course mapping plans."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

from .config import ContentSelection, CourseConfig, CourseSource, DownloadPolicy
from .providers.base import CourseCandidate
from .initial import InitialSyncMode
from .validation import validate_provider_instance


@dataclass(frozen=True, slots=True, order=True)
class SourceIdentity:
    provider: str
    instance: str
    course_id: str

    def __post_init__(self) -> None:
        if self.provider not in {"canvas", "ed"}:
            raise ValueError("source provider must be canvas or ed")
        for field_name in ("instance", "course_id"):
            value = getattr(self, field_name)
            if (
                not isinstance(value, str)
                or not value
                or value != value.strip()
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"{field_name} must be a safe non-empty string")
        validate_provider_instance(self.instance)

    @classmethod
    def from_candidate(cls, candidate: CourseCandidate) -> "SourceIdentity":
        return cls(candidate.provider, candidate.instance, candidate.course_id)

    @classmethod
    def from_source(cls, source: CourseSource) -> "SourceIdentity":
        return cls(source.provider, source.instance, source.course_id)

    def label(self) -> str:
        return f"{self.provider}@{self.instance} id={self.course_id}"


@dataclass(frozen=True, slots=True)
class MappingSourceChoice:
    candidate_key: str
    confirmed: bool = False
    initial_mode: InitialSyncMode | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_key, str) or not self.candidate_key:
            raise ValueError("candidate_key must be a non-empty string")
        if not isinstance(self.confirmed, bool):
            raise ValueError("confirmed must be a boolean")
        if self.initial_mode is not None and not isinstance(self.initial_mode, InitialSyncMode):
            raise ValueError("initial_mode must be an InitialSyncMode")


@dataclass(frozen=True, slots=True)
class CourseMappingChoice:
    course_key: str
    sources: tuple[MappingSourceChoice, ...]
    confirmed: bool = False
    folder: str | None = None
    content: ContentSelection = field(default_factory=ContentSelection)
    policy: DownloadPolicy = field(default_factory=DownloadPolicy)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.course_key, str)
            or not self.course_key
            or self.course_key != self.course_key.strip()
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.course_key
            )
        ):
            raise ValueError("course_key must be a non-empty string")
        if not isinstance(self.sources, tuple) or not all(
            isinstance(source, MappingSourceChoice) for source in self.sources
        ):
            raise ValueError("sources must be an immutable tuple of MappingSourceChoice values")
        if not isinstance(self.confirmed, bool):
            raise ValueError("confirmed must be a boolean")


@dataclass(frozen=True, slots=True)
class MappingInitialSelection:
    course_key: str
    source: SourceIdentity
    mode: InitialSyncMode


@dataclass(frozen=True, slots=True)
class CourseMappingPlan:
    courses: tuple[CourseConfig, ...]
    initial_sync: tuple[MappingInitialSelection, ...]
    preview: str


def _catalog_index(
    catalogs: Mapping[str, tuple[CourseCandidate, ...]],
) -> dict[str, CourseCandidate]:
    output: dict[str, CourseCandidate] = {}
    for provider, candidates in catalogs.items():
        if provider not in {"canvas", "ed"}:
            raise ValueError("mapping catalogs support only canvas and ed")
        if not isinstance(candidates, tuple):
            raise ValueError("catalog entries must be immutable tuples")
        for candidate in candidates:
            if not isinstance(candidate, CourseCandidate) or candidate.provider != provider:
                raise ValueError("catalog candidate does not match its provider")
            if candidate.key in output:
                raise ValueError("catalog contains a duplicate candidate identity")
            SourceIdentity.from_candidate(candidate)
            output[candidate.key] = candidate
    return output


def build_mapping_plan(
    catalogs: Mapping[str, tuple[CourseCandidate, ...]],
    mappings: tuple[CourseMappingChoice, ...],
    *,
    confirmed_at: datetime | None = None,
) -> CourseMappingPlan:
    """Build logical courses only from separately confirmed discovered sources."""
    if not isinstance(mappings, tuple):
        raise ValueError("mappings must be an immutable tuple")
    catalog = _catalog_index(catalogs)
    timestamp = confirmed_at or datetime.now(timezone.utc)
    used_sources: set[SourceIdentity] = set()
    used_keys: set[str] = set()
    courses: list[CourseConfig] = []
    initial: list[MappingInitialSelection] = []
    lines = ["Explicit Canvas/Ed course mappings:"]

    for mapping in mappings:
        if not isinstance(mapping, CourseMappingChoice) or not mapping.confirmed:
            raise ValueError("every course mapping requires explicit confirmation")
        if mapping.course_key in used_keys:
            raise ValueError("logical course keys must be unique")
        if not mapping.sources:
            raise ValueError("a logical course mapping requires at least one source")
        used_keys.add(mapping.course_key)
        provider_sources: set[str] = set()
        sources: list[CourseSource] = []
        candidates: list[CourseCandidate] = []
        mapping_initial: list[MappingInitialSelection] = []
        for selection in mapping.sources:
            if not selection.confirmed:
                raise ValueError("every mapped source requires explicit confirmation")
            if selection.initial_mode is None:
                raise ValueError("every newly mapped source requires an initial sync mode")
            candidate = catalog.get(selection.candidate_key)
            if candidate is None:
                raise ValueError("mapped source is not in the discovered catalog")
            identity = SourceIdentity.from_candidate(candidate)
            if identity.provider in provider_sources:
                raise ValueError("a logical course can contain at most one source per provider")
            if identity in used_sources:
                raise ValueError("a remote source can belong to only one logical course")
            provider_sources.add(identity.provider)
            used_sources.add(identity)
            candidates.append(candidate)
            sources.append(
                CourseSource(
                    provider=candidate.provider,  # type: ignore[arg-type]
                    instance=candidate.instance,
                    course_id=candidate.course_id,
                    code=candidate.code or None,
                    name=candidate.name or None,
                    term=candidate.term,
                    initial_mode=selection.initial_mode,
                )
            )
            mapping_initial.append(
                MappingInitialSelection(mapping.course_key, identity, selection.initial_mode)
            )
        folder = mapping.folder or candidates[0].code.strip() or mapping.course_key
        course = CourseConfig(
            key=mapping.course_key,
            folder=folder,
            confirmed_at=timestamp,
            sources=tuple(sources),
            content=mapping.content,
            policy=mapping.policy,
        )
        courses.append(course)
        initial.extend(mapping_initial)
        lines.append(f"- {mapping.course_key} -> {folder}")
        for selection in mapping_initial:
            lines.append(
                f"  - {selection.source.label()} [initial={selection.mode.value}]"
            )

    return CourseMappingPlan(tuple(courses), tuple(initial), "\n".join(lines))
