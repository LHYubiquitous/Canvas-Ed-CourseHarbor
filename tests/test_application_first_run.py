from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    OperationStatus,
)
from canvas_ed_mcp.application.first_run import (
    FirstRunDraft,
    build_first_run_controller,
    prepare_first_run,
)
from canvas_ed_mcp.application.profiles import (
    CatalogDiscoveryResult,
    ProviderCatalogResult,
)
from canvas_ed_mcp.providers.base import CourseCandidate


def _canvas_course() -> CourseCandidate:
    return CourseCandidate(
        "canvas",
        "canvas.example.edu",
        "42",
        "COMP1000",
        "Canvas course",
        "2027 S1",
    )


def _ed_course() -> CourseCandidate:
    return CourseCandidate(
        "ed",
        "edstem.org",
        "84",
        "COMP1000",
        "Ed course",
        "2027 S1",
    )


class FirstRunApplicationTests(unittest.TestCase):
    def _prepare(self, root: Path):
        return prepare_first_run(
            profile="default",
            output_root=root / "courses",
            config_path=root / "default.toml",
            canvas_enabled=True,
            canvas_base_url="https://canvas.example.edu/api/v1",
            canvas_credential="env:SYNTHETIC_CANVAS_TOKEN",
            ed_enabled=True,
            ed_credential="keyring:canvas-ed-mcp/default/ed",
        )

    def test_existing_config_stops_before_connection_preparation(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "default.toml"
            target.write_text("existing", encoding="utf-8")

            with patch(
                "canvas_ed_mcp.application.first_run._build_platforms",
                side_effect=AssertionError("connection settings were evaluated"),
            ):
                result = prepare_first_run(
                    profile="default",
                    output_root=root / "courses",
                    config_path=target,
                    canvas_enabled=True,
                    canvas_base_url="https://canvas.example.edu/api/v1",
                    canvas_credential="raw-secret-must-not-be-read",
                )

            self.assertEqual(OperationStatus.CONFLICT, result.status)
            self.assertIsNone(result.draft)
            self.assertEqual("config_exists", result.issues[0].code)
            self.assertNotIn("raw-secret", repr(result))

    def test_prepare_validates_provider_selection_and_secret_references(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            no_provider = prepare_first_run(
                profile="default",
                output_root=root / "courses",
                config_path=root / "default.toml",
            )
            self.assertEqual(OperationStatus.FAILED, no_provider.status)
            self.assertEqual("no_provider_selected", no_provider.issues[0].code)

            invalid_reference = prepare_first_run(
                profile="default",
                output_root=root / "courses",
                config_path=root / "other.toml",
                canvas_enabled=True,
                canvas_base_url="https://canvas.example.edu/api/v1",
                canvas_credential="synthetic-token-value",
            )
            self.assertEqual(OperationStatus.FAILED, invalid_reference.status)
            self.assertEqual(
                "invalid_credential_reference", invalid_reference.issues[0].code
            )
            self.assertEqual("canvas", invalid_reference.issues[0].provider)
            self.assertNotIn("synthetic-token-value", repr(invalid_reference))

    def test_prepare_returns_canonical_immutable_generation_bound_draft(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._prepare(root)
            second = self._prepare(root)

            self.assertEqual(OperationStatus.SUCCEEDED, first.status)
            self.assertIsInstance(first.draft, FirstRunDraft)
            self.assertTrue(first.draft.config_path.is_absolute())  # type: ignore[union-attr]
            self.assertTrue(first.draft.output_root.is_absolute())  # type: ignore[union-attr]
            self.assertEqual(first.draft.generation, second.draft.generation)  # type: ignore[union-attr]
            with self.assertRaises(FrozenInstanceError):
                first.draft.profile = "changed"  # type: ignore[misc,union-attr]

    def test_invalid_profile_output_and_canvas_url_return_stable_issues(self) -> None:
        invalid_profile = prepare_first_run(
            profile="bad/profile",
            output_root=Path.cwd(),
            canvas_enabled=True,
            canvas_base_url="https://canvas.example.edu/api/v1",
            canvas_credential="env:SYNTHETIC_TOKEN",
        )
        self.assertEqual("invalid_profile", invalid_profile.issues[0].code)

        relative_output = prepare_first_run(
            profile="default",
            output_root=Path("relative-output"),
            canvas_enabled=True,
            canvas_base_url="https://canvas.example.edu/api/v1",
            canvas_credential="env:SYNTHETIC_TOKEN",
        )
        self.assertEqual("invalid_output_root", relative_output.issues[0].code)

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            invalid_canvas = prepare_first_run(
                profile="default",
                output_root=root / "courses",
                config_path=root / "default.toml",
                canvas_enabled=True,
                canvas_base_url="http://canvas.example.edu",
                canvas_credential="env:SYNTHETIC_TOKEN",
            )
        self.assertEqual("invalid_canvas_settings", invalid_canvas.issues[0].code)

    def test_successful_discovery_builds_controller_for_same_generation(self) -> None:
        with TemporaryDirectory() as temporary:
            prepared = self._prepare(Path(temporary))
            assert prepared.draft is not None
            discovery = CatalogDiscoveryResult(
                OperationStatus.SUCCEEDED,
                (
                    ProviderCatalogResult(
                        "canvas", OperationStatus.SUCCEEDED, (_canvas_course(),)
                    ),
                    ProviderCatalogResult(
                        "ed", OperationStatus.SUCCEEDED, (_ed_course(),)
                    ),
                ),
            )

            ready = build_first_run_controller(
                prepared.draft,
                discovery,
                generation=prepared.draft.generation,
            )

            self.assertTrue(ready.ready)
            self.assertEqual(OperationStatus.SUCCEEDED, ready.status)
            self.assertEqual(prepared.draft.generation, ready.generation)
            self.assertEqual(2, len(ready.controller.course_keys))  # type: ignore[union-attr]
            self.assertEqual(("canvas", "ed"), tuple(key for key, _ in ready.catalogs))

    def test_generation_mismatch_rejects_stale_discovery(self) -> None:
        with TemporaryDirectory() as temporary:
            prepared = self._prepare(Path(temporary))
            assert prepared.draft is not None
            discovery = CatalogDiscoveryResult(
                OperationStatus.SUCCEEDED,
                (
                    ProviderCatalogResult(
                        "canvas", OperationStatus.SUCCEEDED, (_canvas_course(),)
                    ),
                    ProviderCatalogResult("ed", OperationStatus.SUCCEEDED, (_ed_course(),)),
                ),
            )

            result = build_first_run_controller(
                prepared.draft,
                discovery,
                generation="0" * 64,
            )

            self.assertFalse(result.ready)
            self.assertEqual(OperationStatus.CONFLICT, result.status)
            self.assertEqual("draft_generation_mismatch", result.issues[0].code)

    def test_partial_discovery_requires_explicit_acceptance(self) -> None:
        with TemporaryDirectory() as temporary:
            prepared = self._prepare(Path(temporary))
            assert prepared.draft is not None
            provider_issue = ApplicationIssue(
                "authentication_failed",
                IssueSeverity.ERROR,
                provider="ed",
            )
            discovery = CatalogDiscoveryResult(
                OperationStatus.PARTIAL,
                (
                    ProviderCatalogResult(
                        "canvas", OperationStatus.SUCCEEDED, (_canvas_course(),)
                    ),
                    ProviderCatalogResult(
                        "ed", OperationStatus.FAILED, issues=(provider_issue,)
                    ),
                ),
            )

            blocked = build_first_run_controller(
                prepared.draft,
                discovery,
                generation=prepared.draft.generation,
            )
            self.assertFalse(blocked.ready)
            self.assertEqual(OperationStatus.PARTIAL, blocked.status)
            self.assertIn(
                "partial_discovery_requires_confirmation",
                {issue.code for issue in blocked.issues},
            )

            accepted = build_first_run_controller(
                prepared.draft,
                discovery,
                generation=prepared.draft.generation,
                accept_available_only=True,
            )
            self.assertTrue(accepted.ready)
            self.assertEqual(OperationStatus.PARTIAL, accepted.status)
            self.assertTrue(accepted.platforms.canvas.enabled)  # type: ignore[union-attr]
            self.assertFalse(accepted.platforms.ed.enabled)  # type: ignore[union-attr]
            self.assertEqual(("canvas",), tuple(key for key, _ in accepted.catalogs))
            self.assertIn(
                "available_courses_only", {issue.code for issue in accepted.issues}
            )

    def test_zero_available_courses_is_rejected_even_when_partial_is_accepted(self) -> None:
        with TemporaryDirectory() as temporary:
            prepared = self._prepare(Path(temporary))
            assert prepared.draft is not None
            discovery = CatalogDiscoveryResult(
                OperationStatus.PARTIAL,
                (
                    ProviderCatalogResult("canvas", OperationStatus.SUCCEEDED),
                    ProviderCatalogResult("ed", OperationStatus.FAILED),
                ),
            )

            result = build_first_run_controller(
                prepared.draft,
                discovery,
                generation=prepared.draft.generation,
                accept_available_only=True,
            )

            self.assertFalse(result.ready)
            self.assertEqual(OperationStatus.FAILED, result.status)
            self.assertEqual("no_courses_available", result.issues[-1].code)

    def test_partial_acceptance_keeps_successful_zero_course_provider_enabled(self) -> None:
        with TemporaryDirectory() as temporary:
            prepared = self._prepare(Path(temporary))
            assert prepared.draft is not None
            discovery = CatalogDiscoveryResult(
                OperationStatus.PARTIAL,
                (
                    ProviderCatalogResult("canvas", OperationStatus.SUCCEEDED),
                    ProviderCatalogResult(
                        "ed", OperationStatus.PARTIAL, (_ed_course(),)
                    ),
                ),
            )

            result = build_first_run_controller(
                prepared.draft,
                discovery,
                generation=prepared.draft.generation,
                accept_available_only=True,
            )

            self.assertTrue(result.ready)
            self.assertTrue(result.platforms.canvas.enabled)  # type: ignore[union-attr]
            self.assertTrue(result.platforms.ed.enabled)  # type: ignore[union-attr]
            self.assertEqual(("canvas", "ed"), tuple(key for key, _ in result.catalogs))


if __name__ == "__main__":
    unittest.main()
