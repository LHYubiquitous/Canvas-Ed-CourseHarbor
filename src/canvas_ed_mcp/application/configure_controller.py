"""Edit-mode controller that reuses the reviewed setup interaction model."""

from __future__ import annotations

from typing import Mapping

from ..config import AppConfig, CourseSource, PlatformsConfig
from ..configure_flow import ConfigurePlan, build_configure_plan
from ..course_mapping import SourceIdentity
from ..initial import InitialSyncMode
from ..providers.base import CourseCandidate
from .configure_safety import SourceRelocationEligibility
from .setup_controller import SetupController, SetupStep
from .setup_session import CourseOverride, SetupReview


def _configured_candidate(source: CourseSource) -> CourseCandidate:
    return CourseCandidate(
        provider=source.provider,
        instance=source.instance,
        course_id=source.course_id,
        code=source.code or "",
        name=source.name or source.code or f"{source.provider} course {source.course_id}",
        term=source.term,
    )


def merge_configured_catalogs(
    current: AppConfig,
    discovered: Mapping[str, tuple[CourseCandidate, ...]],
) -> dict[str, tuple[CourseCandidate, ...]]:
    """Keep configured sources visible even when providers stop listing them."""
    merged: dict[str, dict[SourceIdentity, CourseCandidate]] = {
        "canvas": {},
        "ed": {},
    }
    for provider, candidates in discovered.items():
        if provider not in merged:
            raise ValueError("catalogs support only canvas and ed")
        for candidate in candidates:
            merged[provider][SourceIdentity.from_candidate(candidate)] = candidate
    for course in current.courses:
        for source in course.sources:
            identity = SourceIdentity.from_source(source)
            # Existing config metadata remains authoritative so opening and saving
            # without edits is a true no-op even if a provider renamed a course.
            merged[source.provider][identity] = _configured_candidate(source)
    for retired in current.retired_sources:
        source = retired.source
        merged[source.provider][SourceIdentity.from_source(source)] = _configured_candidate(source)
    return {
        provider: tuple(values.values())
        for provider, values in merged.items()
    }


class ConfigureController(SetupController):
    """Seed setup UI from an existing profile and build config-only diffs."""

    def __init__(
        self,
        *,
        current: AppConfig,
        catalogs: Mapping[str, tuple[CourseCandidate, ...]],
        eligibility: tuple[SourceRelocationEligibility, ...],
    ) -> None:
        if not isinstance(current, AppConfig):
            raise TypeError("current must be an AppConfig")
        merged = merge_configured_catalogs(current, catalogs)
        super().__init__(
            profile=current.profile,
            output_root=current.output_root,
            catalogs=merged,
            canvas=current.platforms.canvas,
            ed=current.platforms.ed,
        )
        self.current = current
        self._retired_sources = {
            SourceIdentity.from_source(item.source)
            for item in current.retired_sources
        }
        self._eligibility = {item.source: item for item in eligibility}
        self._explicit_mode_resets: set[SourceIdentity] = set()
        expected = {
            SourceIdentity.from_source(source)
            for course in current.courses
            for source in course.sources
        }
        if set(self._eligibility) != expected:
            raise ValueError("eligibility must cover every configured source")
        self._seed_current_configuration()

    def select_course(self, candidate_key: str) -> bool:
        return super().select_course(candidate_key)

    @property
    def safely_relocatable_sources(self) -> frozenset[SourceIdentity]:
        return frozenset(
            identity
            for identity, decision in self._eligibility.items()
            if decision.allowed
        )

    def relocation_reason(self, candidate_key: str) -> str | None:
        identity = self._require_course_key(candidate_key)
        decision = self._eligibility.get(identity)
        return None if decision is None or decision.allowed else decision.reason

    def match_lock_reason(self, match_id: str) -> str | None:
        match = self._match_by_id.get(match_id)
        if match is None:
            raise ValueError("unknown course match")
        return None

    def _seed_current_configuration(self) -> None:
        current_owner: dict[SourceIdentity, str] = {}
        for course in self.current.courses:
            keys: list[str] = []
            for source in course.sources:
                identity = SourceIdentity.from_source(source)
                current_owner[identity] = course.key
                candidate_key = self._key_by_identity[identity]
                self.select_course(candidate_key)
                self.set_source_initial_mode(candidate_key, source.initial_mode)
                keys.append(candidate_key)
            if len(keys) == 1:
                if keys[0] != course.key:
                    self.rename_logical_group(keys[0], course.key)
            else:
                match = next(
                    (
                        item
                        for item in self._matches
                        if set(item.source_keys) == set(keys)
                    ),
                    None,
                )
                if match is None:
                    canvas_key = next(
                        key for key in keys if self.course(key).provider == "canvas"
                    )
                    ed_key = next(
                        key for key in keys if self.course(key).provider == "ed"
                    )
                    match = self.create_manual_match(canvas_key, ed_key)
                self.link_match(match.match_id, logical_key=course.key)
            self.set_course_override(
                course.key,
                CourseOverride(
                    folder=course.folder,
                    content=course.content,
                    policy=course.policy,
                ),
            )
        for match in self.pending_matches:
            owners = {
                current_owner.get(self._identity_by_key[key])
                for key in match.source_keys
            }
            if None not in owners and len(owners) > 1:
                self.keep_separate(match.match_id)

    def deselect_course(self, candidate_key: str) -> bool:
        identity = self._require_course_key(candidate_key)
        original = next(
            (
                course
                for course in self.current.courses
                if any(
                    SourceIdentity.from_source(source) == identity
                    for source in course.sources
                )
            ),
            None,
        )
        changed = super().deselect_course(candidate_key)
        if changed:
            self._explicit_mode_resets.discard(identity)
        if not changed or original is None:
            return changed
        remaining = tuple(
            SourceIdentity.from_source(source)
            for source in original.sources
            if SourceIdentity.from_source(source)
            in self._session.selected_identities
        )
        if len(remaining) == 1:
            current_group = next(
                group for group in self.logical_groups if group.sources == remaining
            )
            if current_group.logical_key != original.key:
                self.rename_logical_group(current_group.logical_key, original.key)
            super().set_course_override(
                original.key,
                CourseOverride(
                    folder=original.folder,
                    content=original.content,
                    policy=original.policy,
                ),
            )
        return changed

    def _require_relocatable_match(self, match_id: str) -> None:
        self.match_lock_reason(match_id)

    def undo_link(self, match_id: str) -> None:
        self._require_relocatable_match(match_id)
        super().undo_link(match_id)

    def link_match(self, match_id: str, *, logical_key: str) -> None:
        match = self._require_selected_match(match_id)
        super().link_match(match_id, logical_key=logical_key)

    def is_existing_course(self, logical_key: str) -> bool:
        return any(course.key == logical_key for course in self.current.courses)

    def initial_mode_editable(self, logical_key: str) -> bool:
        group = next(
            (item for item in self.logical_groups if item.logical_key == logical_key),
            None,
        )
        return group is not None

    def set_course_initial_mode(
        self, logical_key: str, mode: InitialSyncMode
    ) -> None:
        group = next(
            (item for item in self.logical_groups if item.logical_key == logical_key),
            None,
        )
        if group is None:
            raise ValueError("logical course is not selected")
        current_sources = {
            SourceIdentity.from_source(source): source
            for course in self.current.courses
            for source in course.sources
        }
        current_owners = {
            SourceIdentity.from_source(source): course.key
            for course in self.current.courses
            for source in course.sources
        }
        targets = tuple(
            source for source in group.sources
            if current_owners.get(source) != logical_key
        ) or group.sources
        for source in targets:
            self._session.set_source_initial_mode(source, mode)
            previous = current_sources.get(source)
            if previous is not None and previous.initial_mode != mode:
                self._explicit_mode_resets.add(source)
            else:
                self._explicit_mode_resets.discard(source)

    def open_mapping(self) -> None:
        if self.selected_count == 0:
            self._step = SetupStep.MAP
            return
        super().open_mapping()

    def set_course_content_policy(
        self, logical_key: str, *, content: object, policy: object
    ) -> None:
        from ..config import ContentSelection, DownloadPolicy

        if not isinstance(content, ContentSelection) or not isinstance(
            policy, DownloadPolicy
        ):
            raise ValueError("course settings are invalid")
        existing = next(
            (course for course in self.current.courses if course.key == logical_key),
            None,
        )
        folder = (
            existing.folder
            if existing is not None
            else next(
                row.override
                for row in self.folder_presentations
                if row.logical_key == logical_key
            )
        )
        super().set_course_override(
            logical_key,
            CourseOverride(folder=folder, content=content, policy=policy),
        )

    def set_course_override(
        self, logical_key: str, override: CourseOverride | None
    ) -> None:
        existing = next(
            (course for course in self.current.courses if course.key == logical_key),
            None,
        )
        if existing is not None and (
            override is None or override.folder != existing.folder
        ):
            raise ValueError(
                "Existing folder names are fixed in this version; changing them requires file migration."
            )
        super().set_course_override(logical_key, override)

    def set_folder_overrides(
        self, folders: Mapping[str, str | None]
    ) -> None:
        existing = {course.key: course.folder for course in self.current.courses}
        for logical_key, folder in folders.items():
            if logical_key in existing and folder != existing[logical_key]:
                raise ValueError(
                    "Existing folder names are fixed in this version; changing them requires file migration."
                )
        super().set_folder_overrides(folders)

    def build_review(self) -> SetupReview:
        setup_review = super().build_review()
        setup_config = setup_review.plan.config
        active_identities = {
            SourceIdentity.from_source(source)
            for course in setup_config.courses
            for source in course.sources
        }
        proposed = setup_config.model_copy(
            update={
                "platforms": PlatformsConfig(
                    canvas=setup_config.platforms.canvas,
                    ed=setup_config.platforms.ed,
                    gradescope=self.current.platforms.gradescope,
                ),
                "retired_sources": tuple(
                    item for item in self.current.retired_sources
                    if SourceIdentity.from_source(item.source) not in active_identities
                ),
                "daily_use": self.current.daily_use,
            }
        )
        current_owners = {
            SourceIdentity.from_source(source): course.key
            for course in self.current.courses for source in course.sources
        }
        proposed_owners = {
            SourceIdentity.from_source(source): course.key
            for course in setup_config.courses for source in course.sources
        }
        required = {
            identity for identity, owner in proposed_owners.items()
            if identity not in current_owners or current_owners[identity] != owner
        } | self._explicit_mode_resets
        setup_modes = {
            item.source: item.mode for item in setup_review.plan.initial_sync
            if item.source is not None
        }
        new_modes = {identity: setup_modes[identity] for identity in required}
        plan: ConfigurePlan = build_configure_plan(
            self.current,
            proposed,
            new_source_modes=new_modes,
            safely_relocatable_sources=self.safely_relocatable_sources,
        )
        self._review = SetupReview(
            revision=setup_review.revision,
            course_count=len(plan.config.courses),
            source_count=sum(len(course.sources) for course in plan.config.courses),
            plan=plan,  # type: ignore[arg-type]
        )
        return self._review


__all__ = ["ConfigureController", "merge_configured_catalogs"]
