from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canvas_ed_mcp.application.setup_controller import (
    MappingDisposition,
    SetupController,
    SetupStep,
)
from canvas_ed_mcp.application.setup_session import (
    CatalogFilter,
    CourseOverride,
    ReviewState,
    SetupDefaults,
)
from canvas_ed_mcp.config import CanvasPlatformConfig, EdPlatformConfig
from canvas_ed_mcp.initial import InitialSyncMode
from canvas_ed_mcp.providers.base import CourseCandidate


def course(provider: str, course_id: str, code: str, term: str) -> CourseCandidate:
    return CourseCandidate(
        provider=provider,
        instance="canvas.example.edu" if provider == "canvas" else "edstem.org",
        course_id=course_id,
        code=code,
        name=f"{code} {provider.title()}",
        term=term,
    )


class SetupControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.canvas = course("canvas", "c-1", "COMP2017", "2026-S1")
        self.ed = course("ed", "e-1", "COMP2017", "2026-S1")
        self.other = course("canvas", "c-2", "INFO1110", "2025-S2")
        self.catalogs = {
            "canvas": (self.canvas, self.other),
            "ed": (self.ed,),
        }
        self.temp = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temp.name).resolve() / "Courses"
        self.controller = SetupController(
            profile="default",
            output_root=self.output_root,
            catalogs=self.catalogs,
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_CANVAS_TOKEN",
            ),
            ed=EdPlatformConfig(
                enabled=True,
                credential="env:SYNTHETIC_ED_TOKEN",
            ),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _select_pair(self) -> None:
        self.controller.select_course(self.canvas.key)
        self.controller.select_course(self.ed.key)

    def test_filters_do_not_clear_hidden_selection(self) -> None:
        self.controller.select_course(self.other.key)
        self.controller.set_filters(CatalogFilter(query="COMP2017"))

        self.assertEqual(2, len(self.controller.visible_rows))
        self.assertEqual(1, self.controller.selected_count)
        self.assertEqual((self.other.key,), self.controller.selected_course_keys)

    def test_rows_and_term_filter_values_are_newest_term_first(self) -> None:
        terms = tuple(row.term for row in self.controller.visible_rows)

        self.assertEqual(("2026-S1", "2026-S1", "2025-S2"), terms)
        self.assertEqual(("2026-S1", "2025-S2"), self.controller.ordered_terms)

    def test_one_selected_course_can_continue_without_a_mapping_decision(self) -> None:
        self.controller.select_course(self.other.key)

        self.controller.open_mapping()
        self.assertEqual(SetupStep.MAP, self.controller.step)
        self.assertEqual((), self.controller.pending_matches)
        self.controller.open_folders()

        self.assertEqual(SetupStep.FOLDERS, self.controller.step)
        self.assertEqual(1, len(self.controller.mapping_choices))

    def test_keep_separate_is_visible_and_reversible(self) -> None:
        self._select_pair()
        self.controller.open_mapping()
        match = self.controller.pending_matches[0]

        self.controller.keep_separate(match.match_id)

        self.assertEqual((), self.controller.pending_matches)
        self.assertEqual(MappingDisposition.SEPARATE, self.controller.decisions[0].disposition)
        self.assertEqual(match.match_id, self.controller.separate_matches[0].match_id)

        self.controller.undo_separate(match.match_id)

        self.assertEqual(MappingDisposition.PENDING, self.controller.decisions[0].disposition)
        self.assertEqual(match.match_id, self.controller.pending_matches[0].match_id)
        self.assertEqual((), self.controller.separate_matches)

    def test_link_and_unlink_are_reversible(self) -> None:
        self._select_pair()
        self.controller.open_mapping()
        match = self.controller.pending_matches[0]

        self.controller.link_match(match.match_id, logical_key="COMP2017-2026-S1")

        self.assertEqual(MappingDisposition.LINKED, self.controller.decisions[0].disposition)
        self.assertEqual(1, len(self.controller.logical_groups))
        self.assertEqual(2, len(self.controller.logical_groups[0].sources))

        self.controller.undo_link(match.match_id)

        self.assertEqual(MappingDisposition.PENDING, self.controller.decisions[0].disposition)
        self.assertEqual(2, len(self.controller.logical_groups))

    def test_navigation_preserves_selection_mapping_and_override_drafts(self) -> None:
        self._select_pair()
        self.controller.open_mapping()
        match = self.controller.pending_matches[0]
        self.controller.link_match(match.match_id, logical_key="COMP2017-2026-S1")
        self.controller.open_folders()
        self.controller.set_course_override(
            "COMP2017-2026-S1", CourseOverride(folder="COMP2017-local")
        )

        self.controller.back()
        self.assertEqual(SetupStep.MAP, self.controller.step)
        self.controller.back()
        self.assertEqual(SetupStep.SELECT, self.controller.step)

        self.assertEqual(2, self.controller.selected_count)
        self.assertEqual(MappingDisposition.LINKED, self.controller.decisions[0].disposition)
        mapping = self.controller.mapping_choices[0]
        self.assertEqual("COMP2017-local", mapping.folder)

    def test_review_is_exact_and_does_not_write_configuration(self) -> None:
        self._select_pair()
        self.controller.open_mapping()
        match = self.controller.pending_matches[0]
        self.controller.link_match(match.match_id, logical_key="COMP2017-2026-S1")
        self.controller.open_folders()

        review = self.controller.build_review()

        self.assertEqual(SetupStep.REVIEW, self.controller.step)
        self.assertEqual(1, review.course_count)
        self.assertEqual(2, review.source_count)
        self.assertIn("COMP2017-2026-S1", review.plan.preview)
        self.assertFalse(self.output_root.exists())

        self.controller.back()
        self.controller.set_course_override(
            "COMP2017-2026-S1", CourseOverride(folder="changed-after-review")
        )
        self.assertEqual(ReviewState.STALE, self.controller.review_state)
        refreshed = self.controller.build_review()
        self.assertEqual(ReviewState.CURRENT, self.controller.review_state)
        self.assertNotEqual(review.revision, refreshed.revision)

    def test_unlink_failure_preserves_controller_decision(self) -> None:
        self._select_pair()
        self.controller.open_mapping()
        match = self.controller.pending_matches[0]
        self.controller.link_match(match.match_id, logical_key="COMP2017-2026-S1")

        with patch.object(
            self.controller._session,
            "unlink_sources",
            side_effect=ValueError("synthetic conflict"),
        ):
            with self.assertRaisesRegex(ValueError, "synthetic conflict"):
                self.controller.undo_link(match.match_id)

        decision = self.controller.decisions[0]
        self.assertEqual(MappingDisposition.LINKED, decision.disposition)
        self.assertEqual("COMP2017-2026-S1", decision.logical_key)

    def test_deselecting_linked_source_restores_remaining_singleton(self) -> None:
        self._select_pair()
        self.controller.open_mapping()
        match = self.controller.pending_matches[0]
        self.controller.link_match(match.match_id, logical_key="COMP2017-2026-S1")

        self.controller.deselect_course(self.canvas.key)

        self.assertEqual((self.ed.key,), self.controller.selected_course_keys)
        self.assertEqual(1, len(self.controller.logical_groups))
        self.assertEqual((), self.controller.decisions)

    def test_controller_does_not_expose_mutable_session(self) -> None:
        self.assertFalse(hasattr(self.controller, "session"))

    def test_defaults_change_marks_existing_review_stale(self) -> None:
        self._select_pair()
        self.controller.open_mapping()
        self.controller.keep_separate(self.controller.pending_matches[0].match_id)
        self.controller.open_folders()
        self.controller.build_review()

        self.controller.back()
        self.controller.set_defaults(
            SetupDefaults(initial_mode=InitialSyncMode.IMPORT_ALL)
        )
        self.assertEqual(ReviewState.STALE, self.controller.review_state)

    def test_initial_download_mode_can_be_changed_for_one_logical_course(self) -> None:
        self._select_pair()
        self.controller.select_course(self.other.key)
        self.controller.open_mapping()
        match = self.controller.pending_matches[0]
        self.controller.link_match(match.match_id, logical_key="COMP2017-2026-S1")

        self.controller.set_course_initial_mode(
            "COMP2017-2026-S1", InitialSyncMode.IMPORT_ALL
        )

        choices = {choice.course_key: choice for choice in self.controller.mapping_choices}
        self.assertTrue(
            all(
                source.initial_mode is InitialSyncMode.IMPORT_ALL
                for source in choices["COMP2017-2026-S1"].sources
            )
        )
        self.assertTrue(
            all(
                source.initial_mode is InitialSyncMode.BASELINE_FROM_NOW
                for source in choices[self.other.key].sources
            )
        )

    def test_ambiguous_duplicate_code_term_is_not_suggested(self) -> None:
        duplicate = course("canvas", "c-3", "COMP2017", "2026-S1")
        controller = SetupController(
            profile="default",
            output_root=self.output_root,
            catalogs={"canvas": (self.canvas, duplicate), "ed": (self.ed,)},
        )
        controller.select_visible()
        controller.open_mapping()

        self.assertEqual((), controller.pending_matches)

        self.assertEqual(2, len(controller.manual_pair_options("canvas")))
        self.assertEqual((self.ed,), controller.manual_pair_options("ed"))
        manual = controller.create_manual_match(self.canvas.key, self.ed.key)
        self.assertEqual("user_selected_manual_pair", manual.basis)
        self.assertEqual((manual,), controller.pending_matches)
        self.assertEqual((), controller.manual_pair_options("ed"))

    def test_different_codes_can_be_manually_paired_and_then_undone(self) -> None:
        unlike_ed = course("ed", "e-2", "DATA2001", "2025-S2")
        controller = SetupController(
            profile="default",
            output_root=self.output_root,
            catalogs={"canvas": (self.other,), "ed": (unlike_ed,)},
        )
        controller.select_visible()
        controller.open_mapping()

        match = controller.create_manual_match(self.other.key, unlike_ed.key)
        controller.link_match(match.match_id, logical_key="INFO-DATA")
        self.assertEqual((match,), controller.linked_matches)

        controller.undo_link(match.match_id)
        self.assertEqual((match,), controller.pending_matches)

        controller.remove_manual_match(match.match_id)
        self.assertEqual((), controller.pending_matches)
        self.assertEqual((self.other,), controller.manual_pair_options("canvas"))
        self.assertEqual((unlike_ed,), controller.manual_pair_options("ed"))

    def test_deselecting_a_manual_pair_allows_safe_repair_without_overlap(self) -> None:
        second_canvas = course("canvas", "c-3", "COMP3000", "2025-S2")
        first_ed = course("ed", "e-2", "DATA2001", "2025-S2")
        second_ed = course("ed", "e-3", "STAT2011", "2025-S2")
        controller = SetupController(
            profile="default",
            output_root=self.output_root,
            catalogs={
                "canvas": (self.other, second_canvas),
                "ed": (first_ed, second_ed),
            },
        )
        controller.select_visible()
        controller.open_mapping()
        old = controller.create_manual_match(self.other.key, first_ed.key)

        controller.deselect_course(first_ed.key)
        replacement = controller.create_manual_match(self.other.key, second_ed.key)
        controller.select_course(first_ed.key)

        self.assertNotIn(
            old.match_id,
            tuple(item.match.match_id for item in controller.decisions),
        )
        self.assertEqual((replacement,), controller.pending_matches)
        represented = [
            key for decision in controller.decisions for key in decision.match.source_keys
        ]
        self.assertEqual(len(represented), len(set(represented)))

    def test_manual_pair_suppresses_overlapping_automatic_suggestion_regardless_of_order(self) -> None:
        unlike_ed = course("ed", "e-2", "DATA2001", "2026-S1")
        controller = SetupController(
            profile="default",
            output_root=self.output_root,
            catalogs={"canvas": (self.canvas,), "ed": (self.ed, unlike_ed)},
        )
        controller.select_course(self.canvas.key)
        controller.select_course(unlike_ed.key)
        manual = controller.create_manual_match(self.canvas.key, unlike_ed.key)

        controller.select_course(self.ed.key)

        self.assertEqual((manual,), controller.pending_matches)
        represented = [
            key for decision in controller.decisions for key in decision.match.source_keys
        ]
        self.assertEqual(len(represented), len(set(represented)))
        self.assertEqual((self.ed.key,), controller.unmatched_selected_keys)

        controller.remove_manual_match(manual.match_id)
        self.assertEqual(1, len(controller.pending_matches))
        self.assertEqual(
            (self.canvas.key, self.ed.key),
            controller.pending_matches[0].source_keys,
        )
        self.assertIn(self.canvas, controller.manual_pair_options("canvas"))
        self.assertIn(unlike_ed, controller.manual_pair_options("ed"))
        rebuilt = controller.create_manual_match(self.canvas.key, unlike_ed.key)
        self.assertEqual((rebuilt,), controller.pending_matches)

    def test_pending_suggestion_must_be_resolved_before_folders(self) -> None:
        self._select_pair()
        self.controller.open_mapping()
        match = self.controller.pending_matches[0]

        with self.assertRaisesRegex(ValueError, "review every pending"):
            self.controller.open_folders()

        self.controller.keep_separate(match.match_id)
        self.controller.open_folders()
        self.assertEqual(SetupStep.FOLDERS, self.controller.step)


if __name__ == "__main__":
    unittest.main()
