"""Framework-neutral coordinator for the first-run setup UI.

The controller owns presentation workflow state and reversible mapping decisions.
It delegates catalogue, selection, mapping, defaults, overrides, and exact review
construction to :class:`SetupSession`; it performs no network or filesystem writes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping

from canvas_ed_mcp.application.setup_session import (
    CatalogFilter,
    CatalogRow,
    CourseOverride,
    FolderPresentation,
    LogicalCourseGroup,
    ReviewState,
    SetupDefaults,
    SetupReview,
    SetupSession,
)
from canvas_ed_mcp.config import CanvasPlatformConfig, EdPlatformConfig
from canvas_ed_mcp.course_mapping import SourceIdentity
from canvas_ed_mcp.course_mapping import CourseMappingChoice
from canvas_ed_mcp.initial import InitialSyncMode
from canvas_ed_mcp.providers.base import CourseCandidate


class SetupStep(str, Enum):
    """User-visible steps in the zero-write setup review journey."""

    SELECT = "select"
    MAP = "map"
    FOLDERS = "folders"
    REVIEW = "review"


class MappingDisposition(str, Enum):
    """Explicit and reversible state for one possible Canvas/Ed match."""

    PENDING = "pending"
    LINKED = "linked"
    SEPARATE = "separate"


@dataclass(frozen=True, slots=True)
class CourseMatch:
    """One unambiguous cross-provider candidate; never an automatic mapping."""

    match_id: str
    canvas_key: str
    ed_key: str
    basis: str
    suggested_logical_key: str

    @property
    def source_keys(self) -> tuple[str, str]:
        return (self.canvas_key, self.ed_key)


@dataclass(frozen=True, slots=True)
class MappingDecision:
    """Immutable mapping state rendered by a UI adapter."""

    match: CourseMatch
    disposition: MappingDisposition
    logical_key: str | None = None


def _safe_logical_key(course: CourseCandidate, match_id: str) -> str:
    raw = "-".join(part for part in (course.code, course.term or "") if part)
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._") or match_id


def suggest_course_matches(
    catalogs: Mapping[str, tuple[CourseCandidate, ...]],
) -> tuple[CourseMatch, ...]:
    """Suggest unique non-empty code/term pairs without linking them.

    Duplicate Canvas or Ed entries for the same normalized code and term are
    deliberately omitted. Those ambiguous cases require a future manual pairing
    control rather than a guessed relationship.
    """

    if not isinstance(catalogs, Mapping):
        raise ValueError("catalogs must be a mapping")
    grouped: dict[tuple[str, str], dict[str, list[CourseCandidate]]] = {}
    for provider, courses in catalogs.items():
        if provider not in {"canvas", "ed"} or not isinstance(courses, tuple):
            raise ValueError("catalogs support immutable canvas and ed entries")
        for course in courses:
            if not isinstance(course, CourseCandidate) or course.provider != provider:
                raise ValueError("catalog candidate does not match its provider")
            code = course.code.strip().casefold()
            term = (course.term or "").strip().casefold()
            if not code or not term:
                continue
            grouped.setdefault((code, term), {"canvas": [], "ed": []})[
                provider
            ].append(course)

    matches: list[CourseMatch] = []
    for providers in grouped.values():
        if len(providers["canvas"]) != 1 or len(providers["ed"]) != 1:
            continue
        canvas = providers["canvas"][0]
        ed = providers["ed"][0]
        digest = hashlib.sha256(
            f"{canvas.key}\0{ed.key}".encode("utf-8")
        ).hexdigest()[:16]
        match_id = f"code_term_{digest}"
        matches.append(
            CourseMatch(
                match_id=match_id,
                canvas_key=canvas.key,
                ed_key=ed.key,
                basis="same_unique_code_and_term",
                suggested_logical_key=_safe_logical_key(canvas, match_id),
            )
        )
    return tuple(sorted(matches, key=lambda item: item.source_keys))


class SetupController:
    """Pure-Python workflow state for a future PySide6 setup window."""

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
        frozen_catalogs = {provider: tuple(courses) for provider, courses in catalogs.items()}
        self._session = SetupSession(
            profile=profile,
            output_root=output_root,
            catalogs=frozen_catalogs,
            defaults=defaults,
            canvas=canvas,
            ed=ed,
        )
        self._candidate_by_key = {
            candidate.key: candidate
            for courses in frozen_catalogs.values()
            for candidate in courses
        }
        self._identity_by_key = {
            key: SourceIdentity.from_candidate(candidate)
            for key, candidate in self._candidate_by_key.items()
        }
        self._key_by_identity = {
            identity: key for key, identity in self._identity_by_key.items()
        }
        self._matches = suggest_course_matches(frozen_catalogs)
        self._match_by_id = {match.match_id: match for match in self._matches}
        self._disposition = {
            match.match_id: MappingDisposition.PENDING for match in self._matches
        }
        self._linked_logical_key: dict[str, str] = {}
        self._filters = CatalogFilter()
        self._step = SetupStep.SELECT
        self._review: SetupReview | None = None

    @property
    def step(self) -> SetupStep:
        return self._step

    @property
    def filters(self) -> CatalogFilter:
        return self._filters

    @property
    def defaults(self) -> SetupDefaults:
        return self._session.defaults

    @property
    def visible_rows(self) -> tuple[CatalogRow, ...]:
        rows = list(self._session.visible_rows(self._filters))
        rows.sort(
            key=lambda row: (
                row.code.casefold(),
                row.name.casefold(),
                0 if row.provider == "canvas" else 1,
                row.course_id,
            )
        )
        rows.sort(key=self._term_sort_key, reverse=True)
        return tuple(rows)

    @staticmethod
    def _term_sort_key(row: CatalogRow) -> tuple[bool, tuple[int, ...], str]:
        term = row.term or ""
        numbers = tuple(int(value) for value in re.findall(r"\d+", term))
        return (bool(term), numbers, term.casefold())

    @property
    def selected_count(self) -> int:
        return self._session.selected_count

    @property
    def has_draft_changes(self) -> bool:
        """Whether closing the preview would discard an in-memory setup draft."""

        return self.selected_count > 0

    @property
    def selected_course_keys(self) -> tuple[str, ...]:
        return tuple(
            self._key_by_identity[identity]
            for identity in self._session.selected_identities
        )

    @property
    def logical_groups(self) -> tuple[LogicalCourseGroup, ...]:
        return self._session.logical_groups

    @property
    def mapping_choices(self) -> tuple[CourseMappingChoice, ...]:
        return self._session.mapping_choices()

    @property
    def folder_presentations(self) -> tuple[FolderPresentation, ...]:
        return self._session.folder_presentations()

    @property
    def review_state(self) -> ReviewState:
        return self._session.review_state

    @property
    def course_keys(self) -> tuple[str, ...]:
        return tuple(self._candidate_by_key)

    @property
    def ordered_terms(self) -> tuple[str, ...]:
        """Discovered terms ordered newest-first using their numeric components."""

        terms = {
            course.term
            for course in self._candidate_by_key.values()
            if course.term
        }
        return tuple(
            sorted(
                terms,
                key=lambda term: (
                    tuple(int(value) for value in re.findall(r"\d+", term)),
                    term.casefold(),
                ),
                reverse=True,
            )
        )

    def course(self, candidate_key: str) -> CourseCandidate:
        try:
            return self._candidate_by_key[candidate_key]
        except KeyError as exc:
            raise ValueError("course key is not in the discovered catalog") from exc

    def _selected_match(self, match: CourseMatch) -> bool:
        selected = set(self.selected_course_keys)
        return match.canvas_key in selected and match.ed_key in selected

    @property
    def decisions(self) -> tuple[MappingDecision, ...]:
        manual_source_keys = {
            key
            for match in self._matches
            if match.basis == "user_selected_manual_pair"
            and self._selected_match(match)
            for key in match.source_keys
        }
        return tuple(
            MappingDecision(
                match,
                self._disposition[match.match_id],
                self._linked_logical_key.get(match.match_id),
            )
            for match in self._matches
            if self._selected_match(match)
            and (
                match.basis == "user_selected_manual_pair"
                or manual_source_keys.isdisjoint(match.source_keys)
            )
        )

    @property
    def pending_matches(self) -> tuple[CourseMatch, ...]:
        return tuple(
            decision.match
            for decision in self.decisions
            if decision.disposition is MappingDisposition.PENDING
        )

    @property
    def linked_matches(self) -> tuple[CourseMatch, ...]:
        return tuple(
            decision.match
            for decision in self.decisions
            if decision.disposition is MappingDisposition.LINKED
        )

    @property
    def separate_matches(self) -> tuple[CourseMatch, ...]:
        return tuple(
            decision.match
            for decision in self.decisions
            if decision.disposition is MappingDisposition.SEPARATE
        )

    @property
    def unmatched_selected_keys(self) -> tuple[str, ...]:
        represented = {
            key for decision in self.decisions for key in decision.match.source_keys
        }
        return tuple(
            key for key in self.selected_course_keys if key not in represented
        )

    def manual_pair_options(self, provider: str) -> tuple[CourseCandidate, ...]:
        if provider not in {"canvas", "ed"}:
            raise ValueError("manual pair provider must be canvas or ed")
        unavailable = {
            key
            for decision in self.decisions
            if decision.match.basis == "user_selected_manual_pair"
            or decision.disposition is not MappingDisposition.PENDING
            for key in decision.match.source_keys
        }
        return tuple(
            self._candidate_by_key[key]
            for key in self.selected_course_keys
            if key not in unavailable
            and self._candidate_by_key[key].provider == provider
        )

    @property
    def review(self) -> SetupReview | None:
        return self._review

    def set_filters(self, filters: CatalogFilter) -> None:
        if not isinstance(filters, CatalogFilter):
            raise ValueError("filters must be CatalogFilter")
        self._filters = filters

    def _require_course_key(self, candidate_key: str) -> SourceIdentity:
        try:
            return self._identity_by_key[candidate_key]
        except KeyError as exc:
            raise ValueError("course key is not in the discovered catalog") from exc

    def select_course(self, candidate_key: str) -> bool:
        return self._session.select(self._require_course_key(candidate_key))

    def deselect_course(self, candidate_key: str) -> bool:
        identity = self._require_course_key(candidate_key)
        remove_manual_ids: list[str] = []
        for match in self._matches:
            if candidate_key not in match.source_keys:
                continue
            if self._disposition[match.match_id] is MappingDisposition.LINKED:
                logical_key = self._linked_logical_key[match.match_id]
                self._session.unlink_sources(logical_key)
                self._linked_logical_key.pop(match.match_id)
            if match.basis == "user_selected_manual_pair":
                remove_manual_ids.append(match.match_id)
            else:
                self._disposition[match.match_id] = MappingDisposition.PENDING
        if remove_manual_ids:
            removed = set(remove_manual_ids)
            self._matches = tuple(
                match for match in self._matches if match.match_id not in removed
            )
            for match_id in removed:
                self._match_by_id.pop(match_id, None)
                self._disposition.pop(match_id, None)
                self._linked_logical_key.pop(match_id, None)
        return self._session.deselect(identity)

    def select_visible(self) -> int:
        return self._session.select_visible(self._filters)

    def clear_visible(self) -> int:
        keys = tuple(row.candidate_key for row in self.visible_rows if row.selected)
        return sum(int(self.deselect_course(key)) for key in keys)

    def _require_selected_match(self, match_id: str) -> CourseMatch:
        try:
            match = self._match_by_id[match_id]
        except KeyError as exc:
            raise ValueError("unknown course match") from exc
        if not self._selected_match(match):
            raise ValueError("both match sources must be selected")
        return match

    def create_manual_match(self, canvas_key: str, ed_key: str) -> CourseMatch:
        canvas = self.course(canvas_key)
        ed = self.course(ed_key)
        if canvas.provider != "canvas" or ed.provider != "ed":
            raise ValueError("manual match requires one Canvas and one Ed course")
        available = {
            course.key
            for provider in ("canvas", "ed")
            for course in self.manual_pair_options(provider)
        }
        if canvas_key not in available or ed_key not in available:
            raise ValueError("manual match sources must be selected and available")
        if any(
            canvas_key in match.source_keys or ed_key in match.source_keys
            for match in self._matches
            if match.basis == "user_selected_manual_pair"
        ):
            raise ValueError("manual match sources already belong to another pair")
        digest = hashlib.sha256(
            f"{canvas_key}\0{ed_key}".encode("utf-8")
        ).hexdigest()[:16]
        match_id = f"manual_{digest}"
        if match_id in self._match_by_id:
            raise ValueError("manual match already exists")
        match = CourseMatch(
            match_id=match_id,
            canvas_key=canvas_key,
            ed_key=ed_key,
            basis="user_selected_manual_pair",
            suggested_logical_key=_safe_logical_key(canvas, match_id),
        )
        self._matches += (match,)
        self._match_by_id[match_id] = match
        self._disposition[match_id] = MappingDisposition.PENDING
        return match

    def remove_manual_match(self, match_id: str) -> None:
        match = self._require_selected_match(match_id)
        if match.basis != "user_selected_manual_pair":
            raise ValueError("only a manually added pair can be removed")
        if self._disposition[match_id] is not MappingDisposition.PENDING:
            raise ValueError("undo the current pair decision before removing it")
        self._matches = tuple(
            value for value in self._matches if value.match_id != match_id
        )
        self._match_by_id.pop(match_id)
        self._disposition.pop(match_id)
        self._linked_logical_key.pop(match_id, None)

    def link_match(self, match_id: str, *, logical_key: str) -> None:
        match = self._require_selected_match(match_id)
        if self._disposition[match_id] is not MappingDisposition.PENDING:
            raise ValueError("course match already has a decision")
        self._session.link_sources(
            tuple(self._identity_by_key[key] for key in match.source_keys),
            logical_key=logical_key,
        )
        self._disposition[match_id] = MappingDisposition.LINKED
        self._linked_logical_key[match_id] = logical_key

    def undo_link(self, match_id: str) -> None:
        self._require_selected_match(match_id)
        if self._disposition[match_id] is not MappingDisposition.LINKED:
            raise ValueError("course match is not linked")
        logical_key = self._linked_logical_key[match_id]
        self._session.unlink_sources(logical_key)
        self._linked_logical_key.pop(match_id)
        self._disposition[match_id] = MappingDisposition.PENDING

    def keep_separate(self, match_id: str) -> None:
        self._require_selected_match(match_id)
        if self._disposition[match_id] is not MappingDisposition.PENDING:
            raise ValueError("course match already has a decision")
        self._disposition[match_id] = MappingDisposition.SEPARATE

    def undo_separate(self, match_id: str) -> None:
        self._require_selected_match(match_id)
        if self._disposition[match_id] is not MappingDisposition.SEPARATE:
            raise ValueError("course match is not marked separate")
        self._disposition[match_id] = MappingDisposition.PENDING

    def set_defaults(self, defaults: SetupDefaults) -> None:
        self._session.set_defaults(defaults)

    def set_course_initial_mode(
        self, logical_key: str, mode: InitialSyncMode
    ) -> None:
        self._session.set_course_initial_mode(logical_key, mode)

    def set_source_initial_mode(
        self, candidate_key: str, mode: InitialSyncMode
    ) -> None:
        self._session.set_source_initial_mode(
            self._require_course_key(candidate_key), mode
        )

    def rename_logical_group(self, current_key: str, new_key: str) -> None:
        self._session.rename_logical_group(current_key, new_key)

    def set_course_override(
        self, logical_key: str, override: CourseOverride | None
    ) -> None:
        self._session.set_course_override(logical_key, override)

    def set_folder_overrides(
        self, folders: Mapping[str, str | None]
    ) -> None:
        self._session.set_folder_overrides(folders)

    def open_mapping(self) -> None:
        if self.selected_count < 1:
            raise ValueError("select at least one course before mapping")
        self._step = SetupStep.MAP

    def open_folders(self) -> None:
        if self._step is not SetupStep.MAP:
            raise ValueError("folder settings are available after mapping")
        if self.pending_matches:
            raise ValueError(
                "review every pending suggestion before opening folder settings"
            )
        self._step = SetupStep.FOLDERS

    def build_review(self) -> SetupReview:
        if self._step is not SetupStep.FOLDERS:
            raise ValueError("review is available after folder settings")
        self._review = self._session.build_review()
        self._step = SetupStep.REVIEW
        return self._review

    def back(self) -> None:
        previous = {
            SetupStep.MAP: SetupStep.SELECT,
            SetupStep.FOLDERS: SetupStep.MAP,
            SetupStep.REVIEW: SetupStep.FOLDERS,
        }
        try:
            self._step = previous[self._step]
        except KeyError as exc:
            raise ValueError("already at the first setup step") from exc


__all__ = [
    "CourseMatch",
    "MappingDecision",
    "MappingDisposition",
    "SetupController",
    "SetupStep",
    "suggest_course_matches",
]
