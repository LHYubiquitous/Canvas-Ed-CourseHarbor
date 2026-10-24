from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event, Thread
import unittest

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
    ReviewProof,
)
from canvas_ed_mcp.application.daily_sync import (
    DailySyncController,
    DailySyncState,
    SyncApplyResult,
    SyncPreviewRequest,
    SyncPreviewResult,
    SyncReview,
    SyncSourceSummary,
)


class _Cancelled:
    cancellation_requested = True


class _FakePort:
    def __init__(
        self,
        preview_result: SyncPreviewResult,
        apply_result: SyncApplyResult,
    ) -> None:
        self.preview_result = preview_result
        self.apply_result = apply_result
        self.preview_calls = 0
        self.apply_calls = 0
        self.discard_calls = 0
        self.close_calls = 0

    def preview(self, request, *, progress, cancellation):
        self.preview_calls += 1
        return self.preview_result

    def apply(self, review, *, progress, cancellation):
        self.apply_calls += 1
        return self.apply_result

    def discard(self, review):
        self.discard_calls += 1

    def close(self):
        self.close_calls += 1


def _review(digest: str = "1" * 64) -> SyncReview:
    source = SyncSourceSummary(
        course_key="comp1000",
        provider="canvas",
        instance="canvas.example.edu",
        course_id="42",
        actions=("would_fetch", "skip"),
        issue_codes=(),
    )
    return SyncReview(
        proof=ReviewProof(
            operation="sync",
            plan_digest=digest,
            config_revision="2" * 64,
            state_revision="missing",
        ),
        profile="default",
        config_path=Path("C:/profiles/default.toml"),
        state_path=Path("C:/profiles/default/state.json"),
        output_root=Path("C:/courses"),
        sources=(source,),
    )


class ApplicationDailySyncTests(unittest.TestCase):
    def test_source_summary_uses_human_metadata_without_exposing_identity(self) -> None:
        source = SyncSourceSummary(
            course_key="canvas:canvas.example.edu:42",
            provider="canvas",
            instance="canvas.example.edu",
            course_id="42",
            actions=("skip",),
            course_folder="COMP1000",
            course_code="COMP1000",
            course_name="Introduction to Testing",
            course_term="Semester 2 2026",
        )

        self.assertEqual(
            "COMP1000 — Introduction to Testing · Semester 2 2026",
            source.course_display_name,
        )
        self.assertEqual("Canvas", source.source_display_name)
        self.assertNotIn(source.course_key, source.course_display_name)
        self.assertNotIn(source.instance, source.source_display_name)
        self.assertNotIn(source.course_id, source.source_display_name)

    def test_source_summary_falls_back_to_local_folder_not_internal_key(self) -> None:
        source = SyncSourceSummary(
            course_key="canvas:canvas.example.edu:42",
            provider="ed",
            instance="ed.example.edu",
            course_id="42",
            actions=("skip",),
            course_folder="COMP1000",
        )

        self.assertEqual("COMP1000", source.course_display_name)
        self.assertEqual("Ed", source.source_display_name)

    def test_preview_then_apply_preserves_partial_result_and_exact_review(self) -> None:
        review = _review()
        warning = ApplicationIssue(
            "authorization_failed",
            IssueSeverity.WARNING,
            provider="canvas",
            course_key="comp1000",
        )
        port = _FakePort(
            SyncPreviewResult(OperationStatus.PARTIAL, review, (warning,)),
            SyncApplyResult(OperationStatus.PARTIAL, review.sources, (warning,)),
        )
        controller = DailySyncController(port)

        preview = controller.preview(SyncPreviewRequest("default"))
        self.assertEqual(OperationStatus.PARTIAL, preview.status)
        self.assertIs(review, controller.review)
        self.assertEqual(DailySyncState.REVIEW_READY, controller.state)

        applied = controller.apply(review)
        self.assertEqual(OperationStatus.PARTIAL, applied.status)
        self.assertEqual(DailySyncState.PARTIAL, controller.state)
        self.assertEqual(1, port.preview_calls)
        self.assertEqual(1, port.apply_calls)

    def test_apply_rejects_stale_or_foreign_review_before_port_call(self) -> None:
        current = _review()
        foreign = _review("3" * 64)
        port = _FakePort(
            SyncPreviewResult(OperationStatus.SUCCEEDED, current),
            SyncApplyResult(OperationStatus.SUCCEEDED, current.sources),
        )
        controller = DailySyncController(port)
        controller.preview(SyncPreviewRequest("default"))

        rejected = controller.apply(foreign)

        self.assertEqual(OperationStatus.CONFLICT, rejected.status)
        self.assertEqual("review_required", rejected.issues[0].code)
        self.assertEqual(DailySyncState.REVIEW_READY, controller.state)
        self.assertEqual(0, port.apply_calls)

    def test_apply_requires_a_current_preview_and_cannot_be_repeated(self) -> None:
        review = _review()
        port = _FakePort(
            SyncPreviewResult(OperationStatus.SUCCEEDED, review),
            SyncApplyResult(OperationStatus.SUCCEEDED, review.sources),
        )
        controller = DailySyncController(port)

        before_preview = controller.apply(review)
        self.assertEqual(OperationStatus.CONFLICT, before_preview.status)
        self.assertEqual(0, port.apply_calls)

        controller.preview(SyncPreviewRequest("default"))
        applied = controller.apply(review)
        repeated = controller.apply(review)

        self.assertEqual(OperationStatus.SUCCEEDED, applied.status)
        self.assertEqual(OperationStatus.CONFLICT, repeated.status)
        self.assertEqual(1, port.apply_calls)

    def test_pre_cancelled_apply_performs_no_port_io_and_consumes_review(self) -> None:
        review = _review()
        port = _FakePort(
            SyncPreviewResult(OperationStatus.SUCCEEDED, review),
            SyncApplyResult(OperationStatus.SUCCEEDED, review.sources),
        )
        controller = DailySyncController(port)
        controller.preview(SyncPreviewRequest("default"))

        result = controller.apply(review, cancellation=_Cancelled())

        self.assertEqual(OperationStatus.CANCELLED, result.status)
        self.assertEqual(DailySyncState.CANCELLED, controller.state)
        self.assertIsNone(controller.review)
        self.assertEqual(0, port.apply_calls)
        self.assertEqual(1, port.discard_calls)

    def test_invalid_port_results_fail_closed_without_reflecting_values(self) -> None:
        class _InvalidPort:
            def preview(self, request, *, progress, cancellation):
                return "synthetic-secret"

            def apply(self, review, *, progress, cancellation):
                return "synthetic-secret"

            def discard(self, review):
                pass

            def close(self):
                pass

        controller = DailySyncController(_InvalidPort())
        preview = controller.preview(SyncPreviewRequest("default"))

        self.assertEqual(OperationStatus.FAILED, preview.status)
        self.assertEqual("sync_preview_failed", preview.issues[0].code)
        self.assertNotIn("synthetic-secret", repr(preview))

    def test_contracts_reject_wrong_paths_and_non_sync_proofs(self) -> None:
        with self.assertRaisesRegex(ValueError, "config_path"):
            SyncPreviewRequest("default", config_path="profile.toml")  # type: ignore[arg-type]

        with self.assertRaisesRegex(ValueError, "sync proof"):
            SyncReview(
                proof=ReviewProof(
                    operation="setup",
                    plan_digest="1" * 64,
                    config_revision="2" * 64,
                    state_revision="missing",
                ),
                profile="default",
                config_path=Path("profile.toml"),
                state_path=Path("state.json"),
                output_root=Path("courses"),
                sources=_review().sources,
            )

        review = _review()
        with self.assertRaisesRegex(ValueError, "identities must be unique"):
            replace(review, sources=(review.sources[0], review.sources[0]))

    def test_preview_rejects_review_outside_requested_scope(self) -> None:
        review = _review()
        cases = (
            (
                SyncPreviewRequest("default"),
                replace(review, profile="other"),
            ),
            (
                SyncPreviewRequest("default", config_path=Path("expected.toml")),
                replace(review, config_path=Path("other.toml")),
            ),
            (
                SyncPreviewRequest("default", course_keys=("other-course",)),
                review,
            ),
        )
        for request, returned_review in cases:
            with self.subTest(request=request):
                port = _FakePort(
                    SyncPreviewResult(OperationStatus.SUCCEEDED, returned_review),
                    SyncApplyResult(OperationStatus.SUCCEEDED, review.sources),
                )
                controller = DailySyncController(port)

                result = controller.preview(request)

                self.assertEqual(OperationStatus.CONFLICT, result.status)
                self.assertEqual("review_scope_mismatch", result.issues[0].code)
                self.assertIsNone(controller.review)
                self.assertEqual(DailySyncState.CONFLICT, controller.state)
                self.assertEqual(1, port.discard_calls)

    def test_simultaneous_apply_consumes_review_exactly_once(self) -> None:
        review = _review()
        started = Event()
        release = Event()

        class _BlockingPort(_FakePort):
            def apply(self, review, *, progress, cancellation):
                self.apply_calls += 1
                started.set()
                if not release.wait(timeout=2):
                    raise RuntimeError("test release timed out")
                return self.apply_result

        port = _BlockingPort(
            SyncPreviewResult(OperationStatus.SUCCEEDED, review),
            SyncApplyResult(OperationStatus.SUCCEEDED, review.sources),
        )
        controller = DailySyncController(port)
        controller.preview(SyncPreviewRequest("default"))
        first_results: list[SyncApplyResult] = []
        worker = Thread(target=lambda: first_results.append(controller.apply(review)))
        worker.start()
        self.assertTrue(started.wait(timeout=2))
        try:
            second = controller.apply(review)
        finally:
            release.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(OperationStatus.CONFLICT, second.status)
        self.assertEqual(1, len(first_results))
        self.assertEqual(OperationStatus.SUCCEEDED, first_results[0].status)
        self.assertEqual(1, port.apply_calls)

    def test_repreview_discards_the_previous_review_lease(self) -> None:
        review = _review()
        port = _FakePort(
            SyncPreviewResult(OperationStatus.SUCCEEDED, review),
            SyncApplyResult(OperationStatus.SUCCEEDED, review.sources),
        )
        controller = DailySyncController(port)
        controller.preview(SyncPreviewRequest("default"))

        controller.preview(SyncPreviewRequest("default"))

        self.assertEqual(2, port.preview_calls)
        self.assertEqual(1, port.discard_calls)

    def test_close_discards_review_and_refuses_while_work_is_active(self) -> None:
        review = _review()
        started = Event()
        release = Event()

        class _BlockingPreviewPort(_FakePort):
            def preview(self, request, *, progress, cancellation):
                self.preview_calls += 1
                started.set()
                if not release.wait(timeout=2):
                    raise RuntimeError("test release timed out")
                return self.preview_result

        port = _BlockingPreviewPort(
            SyncPreviewResult(OperationStatus.SUCCEEDED, review),
            SyncApplyResult(OperationStatus.SUCCEEDED, review.sources),
        )
        controller = DailySyncController(port)
        results: list[SyncPreviewResult] = []
        worker = Thread(
            target=lambda: results.append(
                controller.preview(SyncPreviewRequest("default"))
            )
        )
        worker.start()
        self.assertTrue(started.wait(timeout=2))
        try:
            self.assertFalse(controller.close())
        finally:
            release.set()
            worker.join(timeout=2)

        self.assertTrue(controller.close())
        self.assertEqual(DailySyncState.IDLE, controller.state)
        self.assertIsNone(controller.review)
        self.assertEqual(1, port.discard_calls)

    def test_pre_cancelled_preview_performs_no_port_io(self) -> None:
        review = _review()
        port = _FakePort(
            SyncPreviewResult(OperationStatus.SUCCEEDED, review),
            SyncApplyResult(OperationStatus.SUCCEEDED, review.sources),
        )
        controller = DailySyncController(port)

        result = controller.preview(
            SyncPreviewRequest("default"), cancellation=_Cancelled()
        )

        self.assertEqual(OperationStatus.CANCELLED, result.status)
        self.assertEqual(DailySyncState.CANCELLED, controller.state)
        self.assertIsNone(controller.review)
        self.assertEqual(0, port.preview_calls)


if __name__ == "__main__":
    unittest.main()
