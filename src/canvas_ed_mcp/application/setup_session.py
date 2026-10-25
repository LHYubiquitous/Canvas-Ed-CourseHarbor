"""Framework-neutral state and presentation models for first-run setup.

The session keeps discovery, selection, mapping and review state in memory.  It is
deliberately independent of terminal or GUI widgets; adapters render the immutable
rows/groups and send explicit mutations back to the session.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from ..config import (
    CanvasPlatformConfig,
    ContentSelection,
    DownloadPolicy,
    EdPlatformConfig,
)
from ..course_mapping import (
    CourseMappingChoice,
    MappingSourceChoice,
    SourceIdentity,
)
from ..initial import InitialSyncMode
from ..providers.base import CourseCandidate
from ..setup_flow import SetupPlan, SetupRequest, build_setup_plan
from ..validation import validate_portable_component


_PROVIDER_ORDER = {"canvas": 0, "ed": 1}
_UNKNOWN_TERM = "Term unavailable"


@dataclass(frozen=True, slots=True)
class CatalogFilter:
    """Composable filters for the course catalog presentation."""

    query: str = ""
    providers: frozenset[str] = frozenset()
    terms: frozenset[str] = frozenset()
    selected: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query, str):
            raise ValueError("query must be a string")
        if not isinstance(self.providers, frozenset) or not self.providers.issubset(
            _PROVIDER_ORDER
        ):
            raise ValueError("providers must contain only canvas or ed")
        if not isinstance(self.terms, frozenset) or not all(
            isinstance(term, str) for term in self.terms
        ):
            raise ValueError("terms must be strings")
        if self.selected is not None and not isinstance(self.selected, bool):
            raise ValueError("selected must be true, false, or none")


@dataclass(frozen=True, slots=True)
class CatalogRow:
    identity: SourceIdentity
    candidate_key: str
    name: str
    code: str
    term: str | None
    provider: str
    instance: str
    course_id: str
    selected: bool


@dataclass(frozen=True, slots=True)
class CatalogGroup:
    provider: str
    term: str
    rows: tuple[CatalogRow, ...]


@dataclass(frozen=True, slots=True)
class LogicalCourseGroup:
    logical_key: str
    sources: tuple[SourceIdentity, ...]


@dataclass(frozen=True, slots=True)
class FolderPresentation:
    """Read-only folder values for one logical course in a setup UI."""

    logical_key: str
    computed_default: str
    override: str | None
    effective: str


@dataclass(frozen=True, slots=True)
class SetupDefaults:
    initial_mode: InitialSyncMode = InitialSyncMode.BASELINE_FROM_NOW
    content: ContentSelection = field(default_factory=ContentSelection)
    policy: DownloadPolicy = field(default_factory=DownloadPolicy)

    def __post_init__(self) -> None:
        if not isinstance(self.initial_mode, InitialSyncMode):
            raise ValueError("initial_mode must be an InitialSyncMode")
        if not isinstance(self.content, ContentSelection):
            raise ValueError("content must be a ContentSelection")
        if not isinstance(self.policy, DownloadPolicy):
            raise ValueError("policy must be a DownloadPolicy")


@dataclass(frozen=True, slots=True)
class CourseOverride:
    folder: str | None = None
    content: ContentSelection | None = None
    policy: DownloadPolicy | None = None

    def __post_init__(self) -> None:
        if self.folder is not None:
            validate_portable_component(self.folder, field="folder override")
        if self.content is not None and not isinstance(self.content, ContentSelection):
            raise ValueError("content override must be a ContentSelection")
        if self.policy is not None and not isinstance(self.policy, DownloadPolicy):
            raise ValueError("policy override must be a DownloadPolicy")


class ReviewState(str, Enum):
    NOT_REVIEWED = "not_reviewed"
    CURRENT = "current"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class SetupReview:
    revision: int
    course_count: int
    source_count: int
    plan: SetupPlan


class SetupSession:
    """In-memory setup state with explicit selection, mapping and review gates."""

    def __init__(
        self,
        *,
        profile: str,
        output_root: Path,
        catalogs: Mapping[str, tuple[CourseCandidate, ...]],
        defaults: SetupDefaults | None = None,
        canvas: CanvasPlatformConfig | None = None,
        ed: EdPlatformConfig | None = None,
    ) -> None:
        if not isinstance(profile, str) or not profile:
            raise ValueError("profile must be a non-empty string")
        if not isinstance(output_root, Path) or not output_root.is_absolute():
            raise ValueError("output_root must be an absolute Path")
        self.profile = profile
        self.output_root = output_root
        self._defaults = defaults or SetupDefaults()
        self._candidates: dict[SourceIdentity, CourseCandidate] = {}
        self._ordered_identities: list[SourceIdentity] = []
        self._load_catalogs(catalogs)
        self._selected: set[SourceIdentity] = set()
        self._groups: dict[str, tuple[SourceIdentity, ...]] = {}
        self._source_owner: dict[SourceIdentity, str] = {}
        self._source_modes: dict[SourceIdentity, InitialSyncMode] = {}
        self._course_overrides: dict[str, CourseOverride] = {}
        self._canvas_platform = canvas or CanvasPlatformConfig()
        self._ed_platform = ed or EdPlatformConfig()
        if not isinstance(self._canvas_platform, CanvasPlatformConfig) or not isinstance(
            self._ed_platform, EdPlatformConfig
        ):
            raise ValueError("platform settings must use CanvasPlatformConfig and EdPlatformConfig")
        self._revision = 0
        self._review_revision: int | None = None

    def _load_catalogs(
        self, catalogs: Mapping[str, tuple[CourseCandidate, ...]]
    ) -> None:
        if not isinstance(catalogs, Mapping):
            raise ValueError("catalogs must be a mapping")
        for provider, candidates in catalogs.items():
            if provider not in _PROVIDER_ORDER or not isinstance(candidates, tuple):
                raise ValueError("catalogs support immutable canvas and ed entries")
            for item in candidates:
                if not isinstance(item, CourseCandidate) or item.provider != provider:
                    raise ValueError("catalog candidate does not match its provider")
                for value in (item.code, item.name, item.term):
                    if value is not None and any(
                        ord(character) < 32 or ord(character) == 127
                        for character in value
                    ):
                        raise ValueError("catalog display text contains control characters")
                source = SourceIdentity.from_candidate(item)
                if source in self._candidates:
                    raise ValueError("catalog contains a duplicate source identity")
                self._candidates[source] = item
                self._ordered_identities.append(source)
        self._ordered_identities.sort(key=self._identity_sort_key)

    def _identity_sort_key(self, source: SourceIdentity) -> tuple[object, ...]:
        item = self._candidates[source]
        return (
            _PROVIDER_ORDER[source.provider],
            item.term or _UNKNOWN_TERM,
            item.name.casefold(),
            item.code.casefold(),
            source.course_id,
        )

    @property
    def defaults(self) -> SetupDefaults:
        return self._defaults

    def set_output_root(self, output_root: Path) -> bool:
        """Replace the reviewed library root without touching the filesystem."""

        if not isinstance(output_root, Path) or not output_root.is_absolute():
            raise ValueError("library root must be an absolute Path")
        try:
            selected = output_root.resolve(strict=False)
            current = self.output_root.resolve(strict=False)
        except (OSError, RuntimeError):
            raise ValueError("library root could not be resolved safely") from None
        if selected == current:
            return False
        self.output_root = selected
        self._mark_changed()
        return True

    @property
    def canvas_platform(self) -> CanvasPlatformConfig:
        return self._canvas_platform

    @property
    def ed_platform(self) -> EdPlatformConfig:
        return self._ed_platform

    @property
    def selected_count(self) -> int:
        return len(self._selected)

    @property
    def selected_identities(self) -> tuple[SourceIdentity, ...]:
        return tuple(
            source for source in self._ordered_identities if source in self._selected
        )

    @property
    def logical_groups(self) -> tuple[LogicalCourseGroup, ...]:
        return tuple(
            LogicalCourseGroup(key, sources) for key, sources in self._groups.items()
        )

    @property
    def review_state(self) -> ReviewState:
        if self._review_revision is None:
            return ReviewState.NOT_REVIEWED
        if self._review_revision == self._revision:
            return ReviewState.CURRENT
        return ReviewState.STALE

    def _mark_changed(self) -> None:
        self._revision += 1

    def _row(self, source: SourceIdentity) -> CatalogRow:
        item = self._candidates[source]
        return CatalogRow(
            identity=source,
            candidate_key=item.key,
            name=item.name,
            code=item.code,
            term=item.term,
            provider=item.provider,
            instance=item.instance,
            course_id=item.course_id,
            selected=source in self._selected,
        )

    def visible_rows(
        self, filters: CatalogFilter | None = None
    ) -> tuple[CatalogRow, ...]:
        selected_filter = filters or CatalogFilter()
        query = selected_filter.query.strip().casefold()
        output: list[CatalogRow] = []
        for source in self._ordered_identities:
            item = self._candidates[source]
            is_selected = source in self._selected
            if selected_filter.providers and item.provider not in selected_filter.providers:
                continue
            term_label = item.term or _UNKNOWN_TERM
            if selected_filter.terms and term_label not in selected_filter.terms:
                continue
            if selected_filter.selected is not None and is_selected != selected_filter.selected:
                continue
            if query and not any(
                query in value.casefold()
                for value in (
                    item.name,
                    item.code,
                    item.term or "",
                    item.provider,
                    item.instance,
                    item.course_id,
                )
            ):
                continue
            output.append(self._row(source))
        return tuple(output)

    def grouped_rows(
        self, filters: CatalogFilter | None = None
    ) -> tuple[CatalogGroup, ...]:
        grouped: dict[tuple[str, str], list[CatalogRow]] = {}
        for row in self.visible_rows(filters):
            grouped.setdefault((row.provider, row.term or _UNKNOWN_TERM), []).append(row)
        return tuple(
            CatalogGroup(provider, term, tuple(rows))
            for (provider, term), rows in grouped.items()
        )

    def _require_source(self, source: SourceIdentity) -> CourseCandidate:
        if not isinstance(source, SourceIdentity) or source not in self._candidates:
            raise ValueError("source is not in the discovered catalog")
        return self._candidates[source]

    def select(self, source: SourceIdentity) -> bool:
        item = self._require_source(source)
        if source in self._selected:
            return False
        if item.key in self._groups:
            raise ValueError("candidate logical course key collides with an existing group")
        self._selected.add(source)
        self._groups[item.key] = (source,)
        self._source_owner[source] = item.key
        self._source_modes[source] = self._defaults.initial_mode
        self._mark_changed()
        return True

    def deselect(self, source: SourceIdentity) -> bool:
        self._require_source(source)
        if source not in self._selected:
            return False
        self._selected.remove(source)
        self._source_modes.pop(source, None)
        owner = self._source_owner.pop(source)
        remaining = tuple(item for item in self._groups[owner] if item != source)
        if remaining:
            self._groups[owner] = remaining
        else:
            del self._groups[owner]
            self._course_overrides.pop(owner, None)
        self._mark_changed()
        return True

    def select_visible(self, filters: CatalogFilter | None = None) -> int:
        changed = 0
        for row in self.visible_rows(filters):
            changed += int(self.select(row.identity))
        return changed

    def clear_visible(self, filters: CatalogFilter | None = None) -> int:
        changed = 0
        for row in self.visible_rows(filters):
            changed += int(self.deselect(row.identity))
        return changed

    def link_sources(
        self, sources: tuple[SourceIdentity, ...], *, logical_key: str
    ) -> LogicalCourseGroup:
        if not isinstance(sources, tuple) or len(sources) < 2:
            raise ValueError("linking requires at least two selected sources")
        if (
            not isinstance(logical_key, str)
            or not logical_key
            or logical_key != logical_key.strip()
        ):
            raise ValueError("logical_key must be a safe non-empty string")
        if len(sources) != len(set(sources)):
            raise ValueError("linked sources must be unique")
        for source in sources:
            self._require_source(source)
            if source not in self._selected:
                raise ValueError("only selected sources can be linked")
        ordered_sources = tuple(sorted(sources, key=self._identity_sort_key))
        providers = tuple(source.provider for source in ordered_sources)
        if len(providers) != len(set(providers)):
            raise ValueError("a logical course can contain at most one source per provider")

        if any(
            item.key == logical_key and source not in ordered_sources
            for source, item in self._candidates.items()
        ):
            raise ValueError("logical course key is reserved for another catalog source")

        owner_keys = {self._source_owner[source] for source in ordered_sources}
        if any(
            any(source not in ordered_sources for source in self._groups[key])
            for key in owner_keys
        ):
            raise ValueError("unlink an existing multi-source group before relinking")
        if logical_key in self._groups and logical_key not in owner_keys:
            raise ValueError("logical course key is already in use")

        ordered_owner_keys = tuple(key for key in self._groups if key in owner_keys)
        inherited_values = tuple(
            self._course_overrides[key]
            for key in ordered_owner_keys
            if key in self._course_overrides
        )
        if inherited_values and any(
            value != inherited_values[0] for value in inherited_values[1:]
        ):
            raise ValueError("conflicting course overrides must be resolved before linking")
        inherited_override = inherited_values[0] if inherited_values else None

        if (
            len(owner_keys) == 1
            and logical_key in owner_keys
            and self._groups[logical_key] == ordered_sources
        ):
            return LogicalCourseGroup(logical_key, ordered_sources)

        replacement: dict[str, tuple[SourceIdentity, ...]] = {}
        inserted = False
        for key, owned in self._groups.items():
            if key in owner_keys:
                if not inserted:
                    replacement[logical_key] = ordered_sources
                    inserted = True
                self._course_overrides.pop(key, None)
            else:
                replacement[key] = owned
        self._groups = replacement
        for source in ordered_sources:
            self._source_owner[source] = logical_key
        if inherited_override is not None:
            self._course_overrides[logical_key] = inherited_override
        self._mark_changed()
        return LogicalCourseGroup(logical_key, ordered_sources)

    def unlink_sources(self, logical_key: str) -> tuple[LogicalCourseGroup, ...]:
        """Split one linked course; its override follows the canonical primary source."""
        if not isinstance(logical_key, str) or logical_key not in self._groups:
            raise ValueError("logical course is not selected")
        sources = self._groups[logical_key]
        if len(sources) == 1:
            return (LogicalCourseGroup(logical_key, sources),)

        ordered_sources = tuple(sorted(sources, key=self._identity_sort_key))
        singleton_keys = tuple(self._candidates[source].key for source in ordered_sources)
        outside_keys = set(self._groups).difference({logical_key})
        if any(key in outside_keys for key in singleton_keys):
            raise ValueError("catalog source key is already used by another logical course")

        inherited_override = self._course_overrides.pop(logical_key, None)
        replacement: dict[str, tuple[SourceIdentity, ...]] = {}
        for key, owned in self._groups.items():
            if key != logical_key:
                replacement[key] = owned
                continue
            for source, singleton_key in zip(ordered_sources, singleton_keys):
                replacement[singleton_key] = (source,)
        self._groups = replacement
        for source, singleton_key in zip(ordered_sources, singleton_keys):
            self._source_owner[source] = singleton_key
        if inherited_override is not None:
            self._course_overrides[singleton_keys[0]] = inherited_override
        self._mark_changed()
        return tuple(
            LogicalCourseGroup(singleton_key, (source,))
            for source, singleton_key in zip(ordered_sources, singleton_keys)
        )

    def rename_logical_group(self, current_key: str, new_key: str) -> None:
        """Rename one selected logical group without changing source ownership."""
        if current_key not in self._groups:
            raise ValueError("logical course is not selected")
        if (
            not isinstance(new_key, str)
            or not new_key
            or new_key != new_key.strip()
        ):
            raise ValueError("logical_key must be a safe non-empty string")
        if current_key == new_key:
            return
        if new_key in self._groups:
            raise ValueError("logical course key is already in use")
        sources = self._groups[current_key]
        if any(
            item.key == new_key and source not in sources
            for source, item in self._candidates.items()
        ):
            raise ValueError("logical course key is reserved for another catalog source")
        replacement: dict[str, tuple[SourceIdentity, ...]] = {}
        for key, owned in self._groups.items():
            replacement[new_key if key == current_key else key] = owned
        self._groups = replacement
        for source in sources:
            self._source_owner[source] = new_key
        override = self._course_overrides.pop(current_key, None)
        if override is not None:
            self._course_overrides[new_key] = override
        self._mark_changed()

    def set_defaults(self, defaults: SetupDefaults) -> None:
        if not isinstance(defaults, SetupDefaults):
            raise ValueError("defaults must be SetupDefaults")
        if defaults == self._defaults:
            return
        previous_mode = self._defaults.initial_mode
        self._defaults = defaults
        for source, mode in tuple(self._source_modes.items()):
            if mode is previous_mode:
                self._source_modes[source] = defaults.initial_mode
        self._mark_changed()

    def set_source_initial_mode(
        self, source: SourceIdentity, mode: InitialSyncMode
    ) -> None:
        self._require_source(source)
        if source not in self._selected:
            raise ValueError("initial mode can only be set for a selected source")
        if not isinstance(mode, InitialSyncMode):
            raise ValueError("mode must be an InitialSyncMode")
        if self._source_modes[source] is mode:
            return
        self._source_modes[source] = mode
        self._mark_changed()

    def set_course_initial_mode(
        self, logical_key: str, mode: InitialSyncMode
    ) -> None:
        """Apply one initial-download decision to every source in a logical course."""

        try:
            sources = self._groups[logical_key]
        except KeyError as exc:
            raise ValueError("logical course is not selected") from exc
        if not isinstance(mode, InitialSyncMode):
            raise ValueError("mode must be an InitialSyncMode")
        changed = False
        for source in sources:
            if self._source_modes[source] is mode:
                continue
            self._source_modes[source] = mode
            changed = True
        if changed:
            self._mark_changed()

    @staticmethod
    def _canvas_instance(config: CanvasPlatformConfig) -> str | None:
        if not config.enabled or config.base_url is None:
            return None
        parsed = urlparse(config.base_url)
        assert parsed.hostname is not None
        host = parsed.hostname.casefold()
        return host if parsed.port in {None, 443} else f"{host}:{parsed.port}"

    def _validate_selected_platforms(
        self, canvas: CanvasPlatformConfig, ed: EdPlatformConfig
    ) -> None:
        canvas_sources = tuple(
            source for source in self._selected if source.provider == "canvas"
        )
        if canvas_sources:
            expected = self._canvas_instance(canvas)
            if expected is None:
                raise ValueError("selected Canvas sources require an enabled Canvas platform")
            if any(source.instance != expected for source in canvas_sources):
                raise ValueError("selected Canvas instance does not match the Canvas base URL")

        if ed.enabled and ed.region != "global":
            raise ValueError("only the global Ed region is supported")
        ed_sources = tuple(source for source in self._selected if source.provider == "ed")
        if ed_sources:
            if not ed.enabled:
                raise ValueError("selected Ed sources require an enabled Ed platform")
            if any(source.instance != "edstem.org" for source in ed_sources):
                raise ValueError("selected Ed instance must be edstem.org")

    def set_platforms(
        self, *, canvas: CanvasPlatformConfig, ed: EdPlatformConfig
    ) -> bool:
        """Set reviewed connection metadata and invalidate reviews only on change."""
        if not isinstance(canvas, CanvasPlatformConfig) or not isinstance(
            ed, EdPlatformConfig
        ):
            raise ValueError("platform settings must use CanvasPlatformConfig and EdPlatformConfig")
        self._validate_selected_platforms(canvas, ed)
        if canvas == self._canvas_platform and ed == self._ed_platform:
            return False
        self._canvas_platform = canvas
        self._ed_platform = ed
        self._mark_changed()
        return True

    def set_course_override(
        self, logical_key: str, override: CourseOverride | None
    ) -> None:
        if logical_key not in self._groups:
            raise ValueError("logical course is not selected")
        if override is not None and not isinstance(override, CourseOverride):
            raise ValueError("override must be a CourseOverride or none")
        current = self._course_overrides.get(logical_key)
        if override == CourseOverride():
            override = None
        if override is None and current is None:
            return
        if current == override:
            return
        proposed = dict(self._course_overrides)
        if override is None:
            proposed.pop(logical_key, None)
        else:
            proposed[logical_key] = override
        # Validate the complete effective folder set before changing session state.
        self._mapping_choices(proposed)
        self._course_overrides = proposed
        self._mark_changed()

    def set_folder_overrides(
        self, folders: Mapping[str, str | None]
    ) -> None:
        """Atomically replace folder overrides while preserving other overrides."""

        if not isinstance(folders, Mapping):
            raise ValueError("folder overrides must be a mapping")
        proposed = dict(self._course_overrides)
        for logical_key, folder in folders.items():
            if logical_key not in self._groups:
                raise ValueError("logical course is not selected")
            if folder is not None and not isinstance(folder, str):
                raise ValueError("folder override must be a string or none")
            current = proposed.get(logical_key, CourseOverride())
            replacement = CourseOverride(
                folder=folder,
                content=current.content,
                policy=current.policy,
            )
            if replacement == CourseOverride():
                proposed.pop(logical_key, None)
            else:
                proposed[logical_key] = replacement
        # Validate the complete proposed set before mutating any session state.
        self._mapping_choices(proposed)
        if proposed == self._course_overrides:
            return
        self._course_overrides = proposed
        self._mark_changed()

    @staticmethod
    def _provider_aware_content(
        content: ContentSelection, sources: tuple[SourceIdentity, ...]
    ) -> ContentSelection:
        providers = {source.provider for source in sources}
        return content.model_copy(
            update={
                "files": content.files if "canvas" in providers else False,
                "pages": content.pages if "canvas" in providers else False,
                "lessons": content.lessons if "ed" in providers else False,
                "resources": content.resources if "ed" in providers else False,
            }
        )

    @staticmethod
    def _readable_folder_stem(candidate: CourseCandidate) -> str:
        raw = candidate.code.strip() or candidate.name.strip() or (
            f"{candidate.provider}-{candidate.course_id}"
        )
        normalized = unicodedata.normalize("NFC", raw)
        clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", normalized).strip(" .")
        clean = re.sub(r"\s+", " ", clean) or "course"
        while len(clean.encode("utf-8")) > 80:
            clean = clean[:-1]
        try:
            return validate_portable_component(clean, field="course folder")
        except ValueError:
            return validate_portable_component(f"_{clean}", field="course folder")

    @staticmethod
    def _folder_suffix(sources: tuple[SourceIdentity, ...]) -> str:
        payload = "\0".join(
            "\0".join((source.provider, source.instance, source.course_id))
            for source in sources
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def _mapping_choices(
        self, overrides: Mapping[str, CourseOverride]
    ) -> tuple[CourseMappingChoice, ...]:
        prepared: list[
            tuple[
                str,
                tuple[SourceIdentity, ...],
                CourseOverride,
                CourseCandidate,
                str,
                bool,
            ]
        ] = []
        default_counts: dict[str, int] = {}
        reserved_folders = {
            override.folder.casefold()
            for override in overrides.values()
            if override.folder is not None
        }
        for logical_key, sources in self._groups.items():
            override = overrides.get(logical_key, CourseOverride())
            first = self._candidates[sources[0]]
            stem = self._readable_folder_stem(first)
            try:
                validate_portable_component(first.code.strip(), field="course folder")
            except ValueError:
                code_is_portable = False
            else:
                code_is_portable = bool(first.code.strip())
            prepared.append(
                (logical_key, sources, override, first, stem, code_is_portable)
            )
            if override.folder is None:
                key = stem.casefold()
                default_counts[key] = default_counts.get(key, 0) + 1

        output: list[CourseMappingChoice] = []
        used_folders: set[str] = set()
        for logical_key, sources, override, first, stem, code_is_portable in prepared:
            content = override.content or self._defaults.content
            folder = override.folder
            if folder is None:
                needs_suffix = (
                    not code_is_portable
                    or default_counts[stem.casefold()] > 1
                    or stem.casefold() in reserved_folders
                )
                base = (
                    f"{stem}--{self._folder_suffix(sources)}"
                    if needs_suffix
                    else stem
                )
                folder = base
                ordinal = 2
                while folder.casefold() in reserved_folders or folder.casefold() in used_folders:
                    folder = f"{base}--{ordinal}"
                    ordinal += 1
            if folder.casefold() in used_folders:
                raise ValueError("course folder overrides must be unique")
            used_folders.add(folder.casefold())
            output.append(
                CourseMappingChoice(
                    course_key=logical_key,
                    sources=tuple(
                        MappingSourceChoice(
                            candidate_key=self._candidates[source].key,
                            confirmed=True,
                            initial_mode=self._source_modes[source],
                        )
                        for source in sources
                    ),
                    confirmed=True,
                    folder=folder,
                    content=self._provider_aware_content(content, sources),
                    policy=override.policy or self._defaults.policy,
                )
            )
        return tuple(output)

    def mapping_choices(self) -> tuple[CourseMappingChoice, ...]:
        return self._mapping_choices(self._course_overrides)

    def folder_presentations(self) -> tuple[FolderPresentation, ...]:
        """Return exact default, override, and effective folders without mutation."""

        choices = self.mapping_choices()
        effective = {choice.course_key: choice.folder for choice in choices}
        rows: list[FolderPresentation] = []
        for logical_key in self._groups:
            current = self._course_overrides.get(logical_key)
            # With no folder override, the effective name is already the exact
            # computed default. Rebuilding every mapping once per row made the
            # initial 300-course folder view quadratic. Only rows that actually
            # override their folder need the counterfactual calculation.
            if current is None or current.folder is None:
                rows.append(
                    FolderPresentation(
                        logical_key=logical_key,
                        computed_default=effective[logical_key],
                        override=None,
                        effective=effective[logical_key],
                    )
                )
                continue
            without_folder = dict(self._course_overrides)
            replacement = CourseOverride(
                content=current.content,
                policy=current.policy,
            )
            if replacement == CourseOverride():
                without_folder.pop(logical_key, None)
            else:
                without_folder[logical_key] = replacement
            default = next(
                choice.folder
                for choice in self._mapping_choices(without_folder)
                if choice.course_key == logical_key
            )
            rows.append(
                FolderPresentation(
                    logical_key=logical_key,
                    computed_default=default,
                    override=current.folder,
                    effective=effective[logical_key],
                )
            )
        return tuple(rows)

    def build_review(
        self,
        *,
        canvas: CanvasPlatformConfig | None = None,
        ed: EdPlatformConfig | None = None,
    ) -> SetupReview:
        selected_canvas = self._canvas_platform if canvas is None else canvas
        selected_ed = self._ed_platform if ed is None else ed
        self.set_platforms(canvas=selected_canvas, ed=selected_ed)
        request = SetupRequest(
            profile=self.profile,
            output_root=self.output_root,
            canvas=self._canvas_platform,
            ed=self._ed_platform,
            mappings=self.mapping_choices(),
        )
        catalogs: dict[str, tuple[CourseCandidate, ...]] = {"canvas": (), "ed": ()}
        for provider in catalogs:
            catalogs[provider] = tuple(
                self._candidates[source]
                for source in self._ordered_identities
                if source.provider == provider
            )
        plan = build_setup_plan(request, catalogs)
        self._review_revision = self._revision
        return SetupReview(
            revision=self._revision,
            course_count=len(plan.config.courses),
            source_count=sum(len(course.sources) for course in plan.config.courses),
            plan=plan,
        )

    def is_review_stale(self, review: SetupReview) -> bool:
        if not isinstance(review, SetupReview):
            raise ValueError("review must be a SetupReview")
        return review.revision != self._revision
