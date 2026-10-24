from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch


_ABSOLUTE_OUTPUT_ROOT = (Path.cwd() / "synthetic-courses").resolve()

from canvas_ed_mcp.application.setup_session import (
    CatalogFilter,
    CourseOverride,
    ReviewState,
    SetupDefaults,
    SetupSession,
)
from canvas_ed_mcp.config import (
    CanvasPlatformConfig,
    ContentSelection,
    DownloadPolicy,
    EdPlatformConfig,
)
from canvas_ed_mcp.course_mapping import SourceIdentity
from canvas_ed_mcp.initial import InitialSyncMode
from canvas_ed_mcp.providers.base import CourseCandidate
from canvas_ed_mcp.validation import validate_portable_component


def candidate(
    provider: str,
    course_id: str,
    *,
    code: str,
    name: str,
    term: str | None,
) -> CourseCandidate:
    instance = "canvas.example.edu" if provider == "canvas" else "edstem.org"
    return CourseCandidate(provider, instance, course_id, code, name, term)


def identity(item: CourseCandidate) -> SourceIdentity:
    return SourceIdentity.from_candidate(item)


class SetupSessionSelectionTests(unittest.TestCase):
    def test_search_filter_and_grouping_preserve_selection(self) -> None:
        canvas_old = candidate(
            "canvas", "c-old", code="INFO1110", name="Introduction to Programming", term="2025 S2"
        )
        canvas_new = candidate(
            "canvas", "c-new", code="COMP2017", name="Systems Programming", term="2026 S1"
        )
        ed_new = candidate(
            "ed", "e-new", code="COMP2017", name="Systems Discussion", term="2026 S1"
        )
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (canvas_old, canvas_new), "ed": (ed_new,)},
        )

        self.assertEqual(0, session.selected_count)
        session.select(identity(canvas_old))
        systems = CatalogFilter(query="systems", terms=frozenset({"2026 S1"}))
        visible = session.visible_rows(systems)
        self.assertEqual(
            (identity(canvas_new), identity(ed_new)),
            tuple(row.identity for row in visible),
        )

        session.select_visible(systems)
        self.assertEqual(3, session.selected_count)
        old_row = next(
            row
            for row in session.visible_rows()
            if row.identity == identity(canvas_old)
        )
        self.assertTrue(old_row.selected)

        grouped = session.grouped_rows(CatalogFilter(providers=frozenset({"canvas"})))
        self.assertEqual(
            (("canvas", "2025 S2", 1), ("canvas", "2026 S1", 1)),
            tuple((group.provider, group.term, len(group.rows)) for group in grouped),
        )

        session.clear_visible(systems)
        self.assertEqual((identity(canvas_old),), session.selected_identities)

    def test_select_visible_is_scoped_and_selected_filter_is_composable(self) -> None:
        courses = tuple(
            candidate(
                "canvas",
                str(index),
                code=f"COMP{index:04d}",
                name=f"Synthetic Course {index}",
                term="2026 S2" if index % 2 else "2026 S1",
            )
            for index in range(20)
        )
        session = SetupSession(
            profile="default", output_root=_ABSOLUTE_OUTPUT_ROOT, catalogs={"canvas": courses}
        )
        s2 = CatalogFilter(terms=frozenset({"2026 S2"}))

        self.assertEqual(10, session.select_visible(s2))
        self.assertEqual(10, len(session.visible_rows(CatalogFilter(selected=True))))
        self.assertEqual(10, len(session.visible_rows(CatalogFilter(selected=False))))
        self.assertEqual(10, session.clear_visible(s2))
        self.assertEqual(0, session.selected_count)

    def test_six_hundred_course_catalog_has_stable_identity_and_selection(self) -> None:
        canvas = tuple(
            candidate(
                "canvas",
                f"c-{index}",
                code=f"C{index:03d}",
                name=f"Canvas Course {index:03d}",
                term=f"2026 S{1 + index % 2}",
            )
            for index in range(300)
        )
        ed = tuple(
            candidate(
                "ed",
                f"e-{index}",
                code=f"E{index:03d}",
                name=f"Ed Course {index:03d}",
                term=f"2026 S{1 + index % 2}",
            )
            for index in range(300)
        )
        session = SetupSession(
            profile="large", output_root=_ABSOLUTE_OUTPUT_ROOT, catalogs={"canvas": canvas, "ed": ed}
        )

        self.assertEqual(600, len(session.visible_rows()))
        result = session.visible_rows(CatalogFilter(query="Course 299"))
        self.assertEqual(2, len(result))
        self.assertEqual(2, session.select_visible(CatalogFilter(query="Course 299")))
        self.assertEqual(2, session.selected_count)
        self.assertEqual(600, len(session.visible_rows()))

    def test_six_hundred_course_select_all_and_clear_all_maintain_owner_index(self) -> None:
        courses = tuple(
            candidate(
                "canvas",
                str(index),
                code=f"COMP{index:04d}",
                name=f"Synthetic Course {index}",
                term="2026 S1",
            )
            for index in range(600)
        )
        session = SetupSession(
            profile="large", output_root=_ABSOLUTE_OUTPUT_ROOT, catalogs={"canvas": courses}
        )

        self.assertEqual(600, session.select_visible())
        self.assertEqual(600, len(session._source_owner))
        self.assertEqual(600, session.clear_visible())
        self.assertEqual(0, len(session._source_owner))
        self.assertEqual(0, session.selected_count)


class SetupSessionMappingTests(unittest.TestCase):
    def test_identical_display_names_stay_separate_until_explicit_link(self) -> None:
        canvas = candidate(
            "canvas", "canvas-1", code="SAME1000", name="Identical Display Name", term="2099 T1"
        )
        ed = candidate(
            "ed", "ed-1", code="SAME1000", name="Identical Display Name", term="2099 T1"
        )
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (canvas,), "ed": (ed,)},
        )
        session.select(identity(canvas))
        session.select(identity(ed))

        groups = session.logical_groups
        self.assertEqual(2, len(groups))
        self.assertTrue(all(len(group.sources) == 1 for group in groups))

        linked = session.link_sources(
            (identity(canvas), identity(ed)), logical_key="SAME1000-combined"
        )
        self.assertEqual("SAME1000-combined", linked.logical_key)
        self.assertEqual(1, len(session.logical_groups))
        self.assertEqual((identity(canvas), identity(ed)), session.logical_groups[0].sources)

    def test_link_rejects_two_sources_from_the_same_provider(self) -> None:
        first = candidate("canvas", "1", code="ONE", name="One", term="2026 S1")
        second = candidate("canvas", "2", code="TWO", name="Two", term="2026 S1")
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (first, second)},
        )
        session.select_visible()

        with self.assertRaisesRegex(ValueError, "at most one source per provider"):
            session.link_sources((identity(first), identity(second)), logical_key="invalid")
        self.assertEqual(2, len(session.logical_groups))

    def test_logical_key_reserved_for_unselected_candidate_cannot_orphan_sources(self) -> None:
        canvas = candidate("canvas", "1", code="ONE", name="One", term="2026 S1")
        ed = candidate("ed", "2", code="TWO", name="Two", term="2026 S1")
        future = candidate("canvas", "3", code="THREE", name="Three", term="2026 S1")
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (canvas, future), "ed": (ed,)},
        )
        session.select(identity(canvas))
        session.select(identity(ed))

        with self.assertRaisesRegex(ValueError, "reserved"):
            session.link_sources(
                (identity(canvas), identity(ed)), logical_key=future.key
            )
        self.assertEqual(2, len(session.logical_groups))

        session.select(identity(future))
        self.assertEqual(3, session.selected_count)
        self.assertEqual(3, len(session.logical_groups))
        self.assertEqual(3, session.clear_visible())
        self.assertEqual(0, session.selected_count)

    def test_link_rejects_unequal_overrides_and_preserves_equal_override(self) -> None:
        canvas = candidate("canvas", "1", code="ONE", name="One", term="2026 S1")
        ed = candidate("ed", "2", code="TWO", name="Two", term="2026 S1")
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (canvas,), "ed": (ed,)},
        )
        session.select_visible()
        session.set_course_override(canvas.key, CourseOverride(folder="canvas-folder"))
        session.set_course_override(ed.key, CourseOverride(folder="ed-folder"))

        with self.assertRaisesRegex(ValueError, "conflicting course overrides"):
            session.link_sources(
                (identity(canvas), identity(ed)), logical_key="combined"
            )
        self.assertEqual(2, len(session.logical_groups))

        shared = CourseOverride(
            content=ContentSelection(
                files=True, pages=False, lessons=True, resources=False
            )
        )
        session.set_course_override(canvas.key, shared)
        session.set_course_override(ed.key, shared)
        session.link_sources((identity(ed), identity(canvas)), logical_key="combined")
        mapping = session.mapping_choices()[0]
        self.assertEqual("ONE", mapping.folder)
        self.assertFalse(mapping.content.pages)
        self.assertFalse(mapping.content.resources)

    def test_unlink_splits_group_and_keeps_override_on_canonical_primary(self) -> None:
        canvas = candidate("canvas", "1", code="ONE", name="One", term="2026 S1")
        ed = candidate("ed", "2", code="TWO", name="Two", term="2026 S1")
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (canvas,), "ed": (ed,)},
        )
        session.select_visible()
        session.link_sources((identity(ed), identity(canvas)), logical_key="combined")
        session.set_course_override("combined", CourseOverride(folder="combined-folder"))

        groups = session.unlink_sources("combined")

        self.assertEqual((canvas.key, ed.key), tuple(group.logical_key for group in groups))
        mappings = {item.course_key: item for item in session.mapping_choices()}
        self.assertEqual("combined-folder", mappings[canvas.key].folder)
        self.assertEqual("TWO", mappings[ed.key].folder)
        self.assertEqual(2, session.clear_visible())

    def test_equivalent_link_and_empty_override_are_noops(self) -> None:
        canvas = candidate("canvas", "1", code="ONE", name="One", term="2026 S1")
        ed = candidate("ed", "2", code="TWO", name="Two", term="2026 S1")
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (canvas,), "ed": (ed,)},
        )
        session.select_visible()
        session.link_sources((identity(canvas), identity(ed)), logical_key="combined")
        review = session.build_review(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_CANVAS_TOKEN",
            ),
            ed=EdPlatformConfig(enabled=True, credential="env:SYNTHETIC_ED_TOKEN"),
        )

        session.set_course_override("combined", CourseOverride())
        session.link_sources((identity(ed), identity(canvas)), logical_key="combined")

        self.assertFalse(session.is_review_stale(review))
        self.assertEqual(ReviewState.CURRENT, session.review_state)


class SetupSessionDefaultsAndReviewTests(unittest.TestCase):
    def test_folder_overrides_are_applied_as_one_atomic_batch(self) -> None:
        first = candidate("canvas", "1", code="ONE", name="One", term="2026 S1")
        second = candidate("canvas", "2", code="TWO", name="Two", term="2026 S1")
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (first, second)},
        )
        session.select_visible()

        with self.assertRaisesRegex(ValueError, "must be unique"):
            session.set_folder_overrides(
                {first.key: "shared", second.key: "shared"}
            )

        failed = {row.logical_key: row for row in session.folder_presentations()}
        self.assertIsNone(failed[first.key].override)
        self.assertIsNone(failed[second.key].override)

        custom_content = ContentSelection(
            files=True, pages=False, lessons=True, resources=True
        )
        session.set_course_override(
            first.key, CourseOverride(content=custom_content)
        )
        session.set_folder_overrides(
            {first.key: "ONE-local", second.key: None}
        )
        applied = {row.logical_key: row for row in session.folder_presentations()}
        self.assertEqual("ONE-local", applied[first.key].effective)
        self.assertIsNone(applied[second.key].override)
        choices = {choice.course_key: choice for choice in session.mapping_choices()}
        self.assertFalse(choices[first.key].content.pages)

    def test_folder_presentations_only_recompute_rows_with_folder_overrides(self) -> None:
        courses = tuple(
            candidate(
                "canvas",
                str(index),
                code=f"SYN{index:04d}",
                name=f"Synthetic Course {index:04d}",
                term="2026 S1",
            )
            for index in range(300)
        )
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": courses},
        )
        session.select_visible()

        with patch.object(
            session, "_mapping_choices", wraps=session._mapping_choices
        ) as mapping_choices:
            rows = session.folder_presentations()
        self.assertEqual(300, len(rows))
        self.assertEqual(1, mapping_choices.call_count)

        session.set_course_override(courses[0].key, CourseOverride(folder="custom"))
        with patch.object(
            session, "_mapping_choices", wraps=session._mapping_choices
        ) as mapping_choices:
            rows = session.folder_presentations()
        self.assertEqual("SYN0000", rows[0].computed_default)
        self.assertEqual("custom", rows[0].effective)
        self.assertEqual(2, mapping_choices.call_count)

    def test_duplicate_folder_override_is_rejected_without_mutating_session(self) -> None:
        first = candidate("canvas", "1", code="ONE", name="One", term="2026 S1")
        second = candidate("canvas", "2", code="TWO", name="Two", term="2026 S1")
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (first, second)},
        )
        session.select_visible()
        session.set_course_override(first.key, CourseOverride(folder="shared"))

        with self.assertRaisesRegex(ValueError, "must be unique"):
            session.set_course_override(second.key, CourseOverride(folder="shared"))

        choices = {item.course_key: item.folder for item in session.mapping_choices()}
        self.assertEqual("shared", choices[first.key])
        self.assertNotEqual("shared", choices[second.key])
        folders = {row.logical_key: row for row in session.folder_presentations()}
        self.assertEqual("shared", folders[first.key].override)
        self.assertIsNone(folders[second.key].override)

    def test_defaults_and_overrides_produce_provider_aware_mapping_choices(self) -> None:
        canvas = candidate("canvas", "1", code="COMP1000", name="Course", term="2026 S1")
        ed = candidate("ed", "2", code="COMP1000", name="Course", term="2026 S1")
        defaults = SetupDefaults(
            initial_mode=InitialSyncMode.BASELINE_FROM_NOW,
            content=ContentSelection(files=True, pages=True, lessons=True, resources=True),
            policy=DownloadPolicy(max_file_bytes=100 * 1024 * 1024),
        )
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (canvas,), "ed": (ed,)},
            defaults=defaults,
        )
        session.select_visible()
        session.link_sources((identity(canvas), identity(ed)), logical_key="COMP1000")
        session.set_source_initial_mode(identity(ed), InitialSyncMode.IMPORT_ALL)
        session.set_course_override(
            "COMP1000",
            CourseOverride(
                folder="COMP1000-2026S1",
                content=ContentSelection(files=True, pages=False, lessons=True, resources=False),
                policy=DownloadPolicy(max_file_bytes=7 * 1024 * 1024),
            ),
        )

        mapping = session.mapping_choices()[0]
        self.assertEqual("COMP1000-2026S1", mapping.folder)
        self.assertEqual(7 * 1024 * 1024, mapping.policy.max_file_bytes)
        self.assertFalse(mapping.content.pages)
        self.assertEqual(
            (InitialSyncMode.BASELINE_FROM_NOW, InitialSyncMode.IMPORT_ALL),
            tuple(source.initial_mode for source in mapping.sources),
        )

    def test_review_becomes_stale_only_after_effective_mutation(self) -> None:
        course = candidate("canvas", "1", code="COMP1000", name="Course", term="2026 S1")
        session = SetupSession(
            profile="default",
            output_root=(Path.cwd() / "synthetic-courses").resolve(),
            catalogs={"canvas": (course,)},
        )
        session.select(identity(course))
        review = session.build_review(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_CANVAS_TOKEN",
            ),
            ed=EdPlatformConfig(),
        )

        self.assertEqual(ReviewState.CURRENT, session.review_state)
        self.assertEqual(1, review.course_count)
        self.assertEqual(1, review.source_count)
        self.assertIn("canvas@canvas.example.edu id=1", review.plan.preview)

        session.select(identity(course))
        self.assertEqual(ReviewState.CURRENT, session.review_state)
        session.set_source_initial_mode(identity(course), InitialSyncMode.IMPORT_ALL)
        self.assertEqual(ReviewState.STALE, session.review_state)
        self.assertTrue(session.is_review_stale(review))

        refreshed = session.build_review(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_CANVAS_TOKEN",
            ),
            ed=EdPlatformConfig(),
        )
        self.assertEqual(ReviewState.CURRENT, session.review_state)
        self.assertFalse(session.is_review_stale(refreshed))

    def test_platform_settings_are_session_state_and_make_old_review_stale(self) -> None:
        course = candidate("canvas", "1", code="COMP1000", name="Course", term="2026 S1")
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (course,)},
        )
        session.select(identity(course))
        first_canvas = CanvasPlatformConfig(
            enabled=True,
            base_url="https://canvas.example.edu/api/v1",
            credential="env:FIRST_TOKEN",
        )
        session.set_platforms(canvas=first_canvas, ed=EdPlatformConfig())
        first = session.build_review()

        second_canvas = first_canvas.model_copy(update={"credential": "env:SECOND_TOKEN"})
        second = session.build_review(canvas=second_canvas, ed=EdPlatformConfig())

        self.assertTrue(session.is_review_stale(first))
        self.assertFalse(session.is_review_stale(second))
        self.assertEqual(second_canvas, session.canvas_platform)

    def test_platform_source_identity_mismatch_is_rejected_before_review(self) -> None:
        canvas = candidate("canvas", "1", code="COMP1000", name="Course", term="2026 S1")
        wrong_ed = CourseCandidate(
            "ed", "regional.edstem.org", "2", "COMP1000", "Course", "2026 S1"
        )
        session = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (canvas,), "ed": (wrong_ed,)},
        )
        session.select(identity(canvas))

        with self.assertRaisesRegex(ValueError, "Canvas instance"):
            session.set_platforms(
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url="https://other.example.edu/api/v1",
                    credential="env:TOKEN",
                ),
                ed=EdPlatformConfig(),
            )

        session.deselect(identity(canvas))
        session.select(identity(wrong_ed))
        with self.assertRaisesRegex(ValueError, "Ed instance"):
            session.set_platforms(
                canvas=CanvasPlatformConfig(),
                ed=EdPlatformConfig(enabled=True, credential="env:ED_TOKEN"),
            )
        with self.assertRaisesRegex(ValueError, "global"):
            session.set_platforms(
                canvas=CanvasPlatformConfig(),
                ed=EdPlatformConfig(
                    enabled=True, region="regional", credential="env:ED_TOKEN"
                ),
            )

    def test_duplicate_or_invalid_course_codes_get_stable_portable_folders(self) -> None:
        old = candidate(
            "canvas", "old", code="COMP2017", name="Systems 2025", term="2025 S2"
        )
        new = candidate(
            "canvas", "new", code="COMP2017", name="Systems 2026", term="2026 S1"
        )
        invalid = candidate(
            "canvas", "invalid", code="CON", name="Reserved Device", term="2026 S1"
        )
        first = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (old, new, invalid)},
        )
        second = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (invalid, new, old)},
        )
        first.select_visible()
        second.select_visible()

        first_folders = {
            item.course_key: item.folder for item in first.mapping_choices()
        }
        second_folders = {
            item.course_key: item.folder for item in second.mapping_choices()
        }
        self.assertEqual(first_folders, second_folders)
        self.assertEqual(3, len({value.casefold() for value in first_folders.values()}))
        self.assertTrue(all("--" in value for value in first_folders.values()))
        for value in first_folders.values():
            self.assertEqual(value, validate_portable_component(value, field="folder"))

        overridden = SetupSession(
            profile="default",
            output_root=_ABSOLUTE_OUTPUT_ROOT,
            catalogs={"canvas": (old, new)},
        )
        overridden.select_visible()
        overridden.set_course_override(old.key, CourseOverride(folder="COMP2017"))
        overridden_folders = {
            item.course_key: item.folder for item in overridden.mapping_choices()
        }
        self.assertEqual("COMP2017", overridden_folders[old.key])
        self.assertTrue(overridden_folders[new.key].startswith("COMP2017--"))
        self.assertEqual(
            2, len({value.casefold() for value in overridden_folders.values()})
        )

        first.set_platforms(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:TOKEN",
            ),
            ed=EdPlatformConfig(),
        )
        self.assertEqual(3, first.build_review().course_count)


if __name__ == "__main__":
    unittest.main()
