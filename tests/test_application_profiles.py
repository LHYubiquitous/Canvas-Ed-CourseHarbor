from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from canvas_ed_mcp.application.contracts import (
    IssueSeverity,
    OperationStatus,
    ProgressPhase,
)
from canvas_ed_mcp.application.profiles import (
    build_setup_review,
    commit_setup_review,
    discover_course_catalogs,
    inspect_profile,
    seal_setup_plan,
)
from canvas_ed_mcp.config import (
    CanvasPlatformConfig,
    ContentSelection,
    DownloadPolicy,
    EdPlatformConfig,
    PlatformsConfig,
)
from canvas_ed_mcp.providers.base import CourseCandidate
from canvas_ed_mcp.setup_flow import CourseChoice, SetupRequest


class _FakeProvider:
    def __init__(
        self,
        provider_name: str,
        instance: str,
        courses: tuple[CourseCandidate, ...],
    ) -> None:
        self.provider_name = provider_name
        self.instance = instance
        self._courses = courses
        self.close_count = 0

    def list_courses(self) -> tuple[CourseCandidate, ...]:
        return self._courses

    def close(self) -> None:
        self.close_count += 1


class _Cancelled:
    cancellation_requested = True


class _MutableCancellation:
    cancellation_requested = False


class ApplicationProfileTests(unittest.TestCase):
    def test_discovery_initializes_only_requested_provider_and_closes_it(self) -> None:
        platforms = PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="keyring:canvas-ed-mcp/default/canvas",
            ),
            ed=EdPlatformConfig(
                enabled=True,
                credential="keyring:canvas-ed-mcp/default/ed",
            ),
        )
        candidate = CourseCandidate(
            "canvas", "canvas.example.edu", "42", "COMP1000", "Example", "2026 S2"
        )
        provider = _FakeProvider("canvas", "canvas.example.edu", (candidate,))
        resolved: list[str] = []
        constructed: list[str] = []

        def resolve(reference: str) -> str:
            resolved.append(reference)
            return "synthetic-token"

        def canvas_factory(base_url: str, token: str) -> _FakeProvider:
            self.assertEqual("https://canvas.example.edu/api/v1", base_url)
            self.assertEqual("synthetic-token", token)
            constructed.append("canvas")
            return provider

        result = discover_course_catalogs(
            platforms,
            requested_providers=("canvas",),
            secret_resolver=resolve,
            canvas_factory=canvas_factory,
            ed_factory=lambda token: self.fail("disabled Ed was constructed"),
        )

        self.assertEqual(OperationStatus.SUCCEEDED, result.status)
        self.assertEqual((candidate,), result.providers[0].courses)
        self.assertEqual(["keyring:canvas-ed-mcp/default/canvas"], resolved)
        self.assertEqual(["canvas"], constructed)
        self.assertEqual(1, provider.close_count)

    def test_provider_failure_is_partial_and_exception_text_is_not_reflected(self) -> None:
        platforms = PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="keyring:canvas-ed-mcp/default/canvas",
            ),
            ed=EdPlatformConfig(
                enabled=True,
                credential="keyring:canvas-ed-mcp/default/ed",
            ),
        )
        ed_course = CourseCandidate(
            "ed", "edstem.org", "7", "COMP2000", "Ed Example", "2026 S2"
        )
        ed_provider = _FakeProvider("ed", "edstem.org", (ed_course,))

        def broken_canvas(base_url: str, token: str) -> _FakeProvider:
            raise RuntimeError("synthetic-token must never be reflected")

        result = discover_course_catalogs(
            platforms,
            requested_providers=("canvas", "ed"),
            secret_resolver=lambda reference: "synthetic-token",
            canvas_factory=broken_canvas,
            ed_factory=lambda token: ed_provider,
        )

        self.assertEqual(OperationStatus.PARTIAL, result.status)
        self.assertEqual(OperationStatus.FAILED, result.providers[0].status)
        self.assertEqual("canvas_provider_unavailable", result.providers[0].issues[0].code)
        self.assertEqual((ed_course,), result.providers[1].courses)
        self.assertNotIn("synthetic-token", repr(result))
        self.assertEqual(1, ed_provider.close_count)

    def test_catalog_candidate_must_match_constructed_provider_instance(self) -> None:
        platforms = PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_TOKEN",
            )
        )
        mismatched = CourseCandidate(
            "canvas", "other.example.edu", "42", "COMP1000", "Example", "2026 S2"
        )
        provider = _FakeProvider(
            "canvas", "canvas.example.edu", (mismatched,)
        )

        result = discover_course_catalogs(
            platforms,
            requested_providers=("canvas",),
            secret_resolver=lambda reference: "synthetic-token",
            canvas_factory=lambda base_url, token: provider,
        )

        self.assertEqual(OperationStatus.FAILED, result.status)
        self.assertEqual("invalid_catalog", result.providers[0].issues[0].code)
        self.assertEqual(1, provider.close_count)

    def test_cancelled_discovery_performs_no_secret_or_provider_io(self) -> None:
        platforms = PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="keyring:canvas-ed-mcp/default/canvas",
            )
        )

        result = discover_course_catalogs(
            platforms,
            requested_providers=("canvas",),
            secret_resolver=lambda reference: self.fail("cancelled discovery resolved a secret"),
            canvas_factory=lambda base_url, token: self.fail(
                "cancelled discovery constructed a provider"
            ),
            ed_factory=lambda token: self.fail("cancelled discovery constructed Ed"),
            cancellation=_Cancelled(),
        )

        self.assertEqual(OperationStatus.CANCELLED, result.status)
        self.assertEqual((), result.providers)

    def test_mid_discovery_cancellation_emits_terminal_progress(self) -> None:
        platforms = PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:CANVAS_TOKEN",
            ),
            ed=EdPlatformConfig(enabled=True, credential="env:ED_TOKEN"),
        )
        canvas_course = CourseCandidate(
            "canvas", "canvas.example.edu", "42", "COMP1000", "Example", None
        )
        provider = _FakeProvider(
            "canvas", "canvas.example.edu", (canvas_course,)
        )
        cancellation = _MutableCancellation()
        events = []

        def progress(event) -> None:
            events.append(event)
            if event.provider == "canvas":
                cancellation.cancellation_requested = True

        result = discover_course_catalogs(
            platforms,
            requested_providers=("canvas", "ed"),
            secret_resolver=lambda reference: "synthetic-token",
            canvas_factory=lambda base_url, token: provider,
            ed_factory=lambda token: self.fail("cancelled discovery constructed Ed"),
            cancellation=cancellation,
            progress=progress,
        )

        self.assertEqual(OperationStatus.CANCELLED, result.status)
        self.assertEqual(1, len(result.providers))
        self.assertEqual(ProgressPhase.DISCOVERING, events[-1].phase)
        self.assertEqual("cancelled", events[-1].code)
        self.assertIsNone(events[-1].provider)
        self.assertEqual(1, provider.close_count)

    def test_close_failure_is_sanitized_as_partial_warning(self) -> None:
        class _CloseFailureProvider(_FakeProvider):
            def close(self) -> None:
                self.close_count += 1
                raise RuntimeError("synthetic-token must never be reflected")

        platforms = PlatformsConfig(
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_TOKEN",
            )
        )
        course = CourseCandidate(
            "canvas", "canvas.example.edu", "42", "COMP1000", "Example", None
        )
        provider = _CloseFailureProvider(
            "canvas", "canvas.example.edu", (course,)
        )

        result = discover_course_catalogs(
            platforms,
            requested_providers=("canvas",),
            secret_resolver=lambda reference: "synthetic-token",
            canvas_factory=lambda base_url, token: provider,
        )

        self.assertEqual(OperationStatus.PARTIAL, result.status)
        self.assertEqual(OperationStatus.PARTIAL, result.providers[0].status)
        self.assertEqual("provider_close_failed", result.providers[0].issues[0].code)
        self.assertEqual(IssueSeverity.WARNING, result.providers[0].issues[0].severity)
        self.assertNotIn("synthetic-token", repr(result))
        self.assertEqual(1, provider.close_count)

    def test_profile_inspection_is_local_and_reports_missing_or_invalid(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "default.toml"
            missing = inspect_profile("default", config_path=path)
            self.assertEqual(OperationStatus.SUCCEEDED, missing.status)
            self.assertFalse(missing.exists)
            path.write_text("not valid toml =", encoding="utf-8")
            invalid = inspect_profile("default", config_path=path)
            self.assertEqual(OperationStatus.FAILED, invalid.status)
            self.assertEqual("invalid_toml", invalid.issues[0].code)

    def test_profile_inspection_returns_a_canonical_absolute_target(self) -> None:
        relative = Path("synthetic-never-create") / "default.toml"

        inspected = inspect_profile("default", config_path=relative)

        self.assertEqual(OperationStatus.SUCCEEDED, inspected.status)
        self.assertFalse(inspected.exists)
        self.assertTrue(inspected.config_path.is_absolute())
        self.assertEqual(relative.resolve(strict=False), inspected.config_path)

    def test_setup_review_is_zero_write_and_commit_rejects_target_drift(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "default.toml"
            output_root = root / "courses"
            candidate = CourseCandidate(
                "canvas", "canvas.example.edu", "42", "COMP1000", "Example", "2026 S2"
            )
            request = SetupRequest(
                profile="default",
                output_root=output_root,
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url="https://canvas.example.edu/api/v1",
                    credential="keyring:canvas-ed-mcp/default/canvas",
                ),
                choices={
                    candidate.key: CourseChoice(
                        confirmed=True,
                        folder="COMP1000",
                        content=ContentSelection(files=True, pages=True),
                        policy=DownloadPolicy(max_file_bytes=100 * 1024 * 1024),
                    )
                },
            )

            review_result = build_setup_review(
                request,
                {"canvas": (candidate,)},
                config_path=config_path,
            )
            self.assertEqual(OperationStatus.SUCCEEDED, review_result.status)
            self.assertIsNotNone(review_result.review)
            self.assertFalse(config_path.exists())
            self.assertFalse(output_root.exists())

            config_path.write_text("racing writer", encoding="utf-8")
            committed = commit_setup_review(review_result.review, config_path=config_path)
            self.assertEqual(OperationStatus.CONFLICT, committed.status)
            self.assertEqual("review_required", committed.issues[0].code)
            self.assertEqual("racing writer", config_path.read_text(encoding="utf-8"))

    def test_existing_exact_plan_is_sealed_without_rebuild_or_write(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "nested" / "default.toml"
            output_root = root / "courses"
            candidate = CourseCandidate(
                "canvas", "canvas.example.edu", "42", "COMP1000", "Example", "2026 S2"
            )
            request = SetupRequest(
                profile="default",
                output_root=output_root,
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url="https://canvas.example.edu/api/v1",
                    credential="env:SYNTHETIC_TOKEN",
                ),
                choices={
                    candidate.key: CourseChoice(
                        confirmed=True,
                        folder="COMP1000",
                    )
                },
            )
            original = build_setup_review(
                request,
                {"canvas": (candidate,)},
                config_path=config_path,
            ).review
            self.assertIsNotNone(original)

            sealed = seal_setup_plan(original.plan, config_path=config_path)  # type: ignore[union-attr]

            self.assertEqual(OperationStatus.SUCCEEDED, sealed.status)
            self.assertIs(original.plan, sealed.review.plan)  # type: ignore[union-attr]
            self.assertFalse(config_path.exists())
            self.assertFalse(config_path.parent.exists())
            self.assertFalse(output_root.exists())

            tampered = replace(
                sealed.review,  # type: ignore[arg-type]
                plan=replace(sealed.review.plan, preview="different visible preview"),  # type: ignore[union-attr]
            )
            rejected = commit_setup_review(tampered, config_path=config_path)
            self.assertEqual(OperationStatus.CONFLICT, rejected.status)
            self.assertEqual("review_required", rejected.issues[0].code)
            self.assertFalse(config_path.exists())

    def test_setup_review_canonicalizes_target_and_rejects_cwd_drift(self) -> None:
        original_cwd = Path.cwd()
        alternate_cwd = original_cwd / "tests"
        relative_target = Path("synthetic-never-create") / "default.toml"
        candidate = CourseCandidate(
            "canvas", "canvas.example.edu", "42", "COMP1000", "Example", None
        )
        request = SetupRequest(
            profile="default",
            output_root=(original_cwd / "synthetic-courses").resolve(),
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_TOKEN",
            ),
            choices={candidate.key: CourseChoice(confirmed=True, folder="COMP1000")},
        )
        built = build_setup_review(
            request, {"canvas": (candidate,)}, config_path=relative_target
        )
        self.assertEqual(OperationStatus.SUCCEEDED, built.status)
        self.assertTrue(built.review.config_path.is_absolute())  # type: ignore[union-attr]

        drifted_target = (alternate_cwd / relative_target).resolve(strict=False)
        with patch(
            "canvas_ed_mcp.application.profiles._canonical_config_target",
            return_value=drifted_target,
        ), patch(
            "canvas_ed_mcp.application.profiles.commit_setup",
            side_effect=AssertionError("cwd drift reached commit"),
        ):
            committed = commit_setup_review(
                built.review, config_path=relative_target
            )

        self.assertEqual(OperationStatus.CONFLICT, committed.status)
        self.assertEqual("review_required", committed.issues[0].code)

    def test_revision_failures_are_returned_as_stable_issues(self) -> None:
        candidate = CourseCandidate(
            "canvas", "canvas.example.edu", "42", "COMP1000", "Example", None
        )
        request = SetupRequest(
            profile="default",
            output_root=(Path.cwd() / "synthetic-courses").resolve(),
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_TOKEN",
            ),
            choices={candidate.key: CourseChoice(confirmed=True, folder="COMP1000")},
        )
        target = Path.cwd() / "synthetic-never-create" / "default.toml"
        with patch(
            "canvas_ed_mcp.application.profiles.file_revision",
            side_effect=OSError("synthetic-token must never be reflected"),
        ):
            failed_review = build_setup_review(
                request, {"canvas": (candidate,)}, config_path=target
            )
        self.assertEqual(OperationStatus.FAILED, failed_review.status)
        self.assertEqual("config_revision_failed", failed_review.issues[0].code)
        self.assertNotIn("synthetic-token", repr(failed_review))

        with patch(
            "canvas_ed_mcp.application.profiles.file_revision",
            return_value="missing",
        ):
            built = build_setup_review(
                request, {"canvas": (candidate,)}, config_path=target
            )
        with patch(
            "canvas_ed_mcp.application.profiles.file_revision",
            side_effect=ValueError("synthetic-token must never be reflected"),
        ):
            failed_commit = commit_setup_review(built.review, config_path=target)
        self.assertEqual(OperationStatus.FAILED, failed_commit.status)
        self.assertEqual("config_revision_failed", failed_commit.issues[0].code)
        self.assertNotIn("synthetic-token", repr(failed_commit))

    def test_setup_commit_forwards_cleanup_pending_as_warning(self) -> None:
        candidate = CourseCandidate(
            "canvas", "canvas.example.edu", "42", "COMP1000", "Example", None
        )
        request = SetupRequest(
            profile="default",
            output_root=(Path.cwd() / "synthetic-courses").resolve(),
            canvas=CanvasPlatformConfig(
                enabled=True,
                base_url="https://canvas.example.edu/api/v1",
                credential="env:SYNTHETIC_TOKEN",
            ),
            choices={candidate.key: CourseChoice(confirmed=True, folder="COMP1000")},
        )
        target = Path.cwd() / "synthetic-never-create" / "default.toml"
        with patch(
            "canvas_ed_mcp.application.profiles.file_revision",
            return_value="missing",
        ):
            built = build_setup_review(
                request, {"canvas": (candidate,)}, config_path=target
            )

        def committed_with_warning(plan, path, *, confirmed, warning_fn):
            self.assertTrue(confirmed)
            warning_fn("setup warning (config_cleanup_pending)")
            return True

        with patch(
            "canvas_ed_mcp.application.profiles.file_revision",
            return_value="missing",
        ), patch(
            "canvas_ed_mcp.application.profiles.commit_setup",
            side_effect=committed_with_warning,
        ):
            committed = commit_setup_review(built.review, config_path=target)

        self.assertEqual(OperationStatus.PARTIAL, committed.status)
        self.assertEqual("config_cleanup_pending", committed.issues[0].code)
        self.assertEqual(IssueSeverity.WARNING, committed.issues[0].severity)

    def test_setup_commit_uses_the_reviewed_plan(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / "default.toml"
            candidate = CourseCandidate(
                "canvas", "canvas.example.edu", "42", "COMP1000", "Example", None
            )
            request = SetupRequest(
                profile="default",
                output_root=root / "courses",
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url="https://canvas.example.edu/api/v1",
                    credential="keyring:canvas-ed-mcp/default/canvas",
                ),
                choices={candidate.key: CourseChoice(confirmed=True, folder="COMP1000")},
            )
            built = build_setup_review(
                request, {"canvas": (candidate,)}, config_path=config_path
            )
            committed = commit_setup_review(built.review, config_path=config_path)

            self.assertEqual(OperationStatus.SUCCEEDED, committed.status)
            self.assertTrue(config_path.is_file())
            self.assertFalse((root / "courses").exists())


if __name__ == "__main__":
    unittest.main()
