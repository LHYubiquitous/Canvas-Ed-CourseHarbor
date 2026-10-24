from __future__ import annotations

import dataclasses
import unittest

from canvas_ed_mcp.application.contracts import (
    ApplicationIssue,
    IssueSeverity,
    NeverCancelled,
    OperationStatus,
    ProgressEvent,
    ProgressPhase,
    ReviewProof,
    SecretValue,
)


_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


class ApplicationContractTests(unittest.TestCase):
    def test_public_enum_values_are_stable(self) -> None:
        self.assertEqual(
            ["succeeded", "partial", "failed", "cancelled", "conflict"],
            [item.value for item in OperationStatus],
        )
        self.assertEqual(
            ["info", "warning", "error"],
            [item.value for item in IssueSeverity],
        )
        self.assertEqual(
            [
                "connecting",
                "discovering",
                "planning",
                "validating",
                "applying",
                "committing",
                "cleaning_up",
            ],
            [item.value for item in ProgressPhase],
        )

    def test_contract_values_are_frozen_and_slotted(self) -> None:
        issue = ApplicationIssue("provider_unavailable", IssueSeverity.ERROR)
        progress = ProgressEvent("operation-1", ProgressPhase.DISCOVERING, 0, 2)
        proof = ReviewProof("setup", _DIGEST_A, "missing")

        for value in (issue, progress, proof, NeverCancelled(), SecretValue("token")):
            self.assertTrue(dataclasses.is_dataclass(value))
            self.assertTrue(value.__dataclass_params__.frozen)
            self.assertFalse(hasattr(value, "__dict__"))
            with self.assertRaises(
                (dataclasses.FrozenInstanceError, AttributeError, TypeError)
            ):
                value.unexpected = True  # type: ignore[attr-defined]

    def test_secret_value_never_appears_in_repr_or_comparison(self) -> None:
        first = SecretValue("SYNTHETIC_PRIVATE_TOKEN_A")
        second = SecretValue("SYNTHETIC_PRIVATE_TOKEN_B")

        self.assertNotIn("SYNTHETIC_PRIVATE_TOKEN_A", repr(first))
        self.assertNotIn("SYNTHETIC_PRIVATE_TOKEN_B", repr(second))
        self.assertEqual(first, second)
        self.assertEqual(hash(first), hash(second))

    def test_secret_value_rejects_empty_or_control_character_content(self) -> None:
        for invalid in ("", "token\nvalue", "token\x00value"):
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises(ValueError):
                    SecretValue(invalid)

    def test_application_issue_validates_stable_codes_and_safe_scope(self) -> None:
        issue = ApplicationIssue(
            "provider_unavailable",
            IssueSeverity.WARNING,
            retryable=True,
            provider="canvas",
            course_key="COMP2022-2026-S2",
        )
        self.assertTrue(issue.retryable)

        invalid_values = (
            {"code": "Provider Unavailable"},
            {"code": "provider-unavailable"},
            {"code": "provider_unavailable", "severity": "warning"},
            {"code": "provider_unavailable", "retryable": 1},
            {"code": "provider_unavailable", "provider": "Canvas"},
            {"code": "provider_unavailable", "course_key": "course\nkey"},
        )
        for changes in invalid_values:
            values = {"code": "provider_unavailable", "severity": IssueSeverity.ERROR}
            values.update(changes)
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    ApplicationIssue(**values)  # type: ignore[arg-type]

    def test_progress_event_validates_typed_phase_and_bounded_counts(self) -> None:
        event = ProgressEvent(
            "sync-123",
            ProgressPhase.APPLYING,
            completed=1,
            total=3,
            provider="ed",
            course_key="SOFT2412",
            code="source_complete",
        )
        self.assertEqual(1, event.completed)

        invalid_values = (
            {"operation_id": ""},
            {"phase": "applying"},
            {"completed": -1},
            {"completed": True},
            {"completed": 2, "total": 1},
            {"total": -1},
            {"total": False},
            {"provider": "Canvas"},
            {"course_key": "bad\rkey"},
            {"code": "bad-code"},
        )
        for changes in invalid_values:
            values = {
                "operation_id": "sync-123",
                "phase": ProgressPhase.APPLYING,
                "completed": 0,
                "total": None,
            }
            values.update(changes)
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    ProgressEvent(**values)  # type: ignore[arg-type]

    def test_review_proof_requires_canonical_revisions(self) -> None:
        proof = ReviewProof("sync", _DIGEST_A, _DIGEST_B, "missing")
        self.assertEqual("sync", proof.operation)

        invalid_values = (
            {"operation": "Sync"},
            {"plan_digest": "A" * 64},
            {"plan_digest": "short"},
            {"config_revision": "unknown"},
            {"state_revision": ""},
        )
        for changes in invalid_values:
            values = {
                "operation": "sync",
                "plan_digest": _DIGEST_A,
                "config_revision": _DIGEST_B,
                "state_revision": None,
            }
            values.update(changes)
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    ReviewProof(**values)  # type: ignore[arg-type]

    def test_never_cancelled_is_always_false(self) -> None:
        self.assertIs(False, NeverCancelled().cancellation_requested)


if __name__ == "__main__":
    unittest.main()
