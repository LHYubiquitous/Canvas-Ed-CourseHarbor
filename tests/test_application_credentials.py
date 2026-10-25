from __future__ import annotations

import unittest

from canvas_ed_mcp.application.contracts import OperationStatus, SecretValue
from canvas_ed_mcp.application.credentials import (
    CredentialController,
    CredentialMode,
    CredentialPresence,
    NativeCredentialStore,
    credential_reference,
)
from canvas_ed_mcp.providers.base import CourseCandidate, ProviderError


CANARY = "SYNTHETIC_CREDENTIAL_CANARY"


class _FakeKeyring:
    priority = 1

    def __init__(self, *, events: list[str] | None = None) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.events = events if events is not None else []
        self.fail_get = False
        self.fail_set = False

    def get_password(self, service: str, account: str) -> str | None:
        self.events.append(f"keyring:get:{account}")
        if self.fail_get:
            raise RuntimeError(f"get failed with {CANARY}")
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, password: str) -> None:
        self.events.append(f"keyring:set:{account}")
        if self.fail_set:
            raise RuntimeError(f"set failed with {CANARY}")
        self.values[(service, account)] = password


class _UnsafeKeyring(_FakeKeyring):
    priority = 0


class _FakeProvider:
    def __init__(
        self,
        provider: str,
        instance: str,
        *,
        events: list[str],
        failure: str | None = None,
    ) -> None:
        self.provider_name = provider
        self.instance = instance
        self.events = events
        self.failure = failure

    def list_courses(self) -> tuple[CourseCandidate, ...]:
        self.events.append(f"provider:list:{self.provider_name}")
        if self.failure:
            raise ProviderError(self.failure, f"provider failed with {CANARY}")
        return (
            CourseCandidate(
                self.provider_name,
                self.instance,
                "42",
                "COMP1000",
                "Synthetic course",
                "2027 S1",
            ),
        )

    def close(self) -> None:
        self.events.append(f"provider:close:{self.provider_name}")


class _Cancelled:
    cancellation_requested = True


class _MutableCancellation:
    cancellation_requested = False


class CredentialControllerTests(unittest.TestCase):
    def _controller(
        self,
        keyring: _FakeKeyring,
        events: list[str],
        *,
        canvas_failure: str | None = None,
    ) -> CredentialController:
        def canvas_factory(base_url: str, token: str) -> _FakeProvider:
            self.assertEqual("https://canvas.example.edu/api/v1", base_url)
            self.assertEqual(CANARY, token)
            events.append("provider:create:canvas")
            return _FakeProvider(
                "canvas",
                "canvas.example.edu",
                events=events,
                failure=canvas_failure,
            )

        def ed_factory(token: str) -> _FakeProvider:
            self.assertEqual(CANARY, token)
            events.append("provider:create:ed")
            return _FakeProvider("ed", "edstem.org", events=events)

        return CredentialController(
            store=NativeCredentialStore(backend=keyring, environ={}),
            canvas_factory=canvas_factory,
            ed_factory=ed_factory,
        )

    def test_reference_is_deterministic_and_validated(self) -> None:
        self.assertEqual(
            "keyring:canvas-ed-mcp/semester-2/canvas",
            credential_reference("semester-2", "canvas"),
        )
        with self.assertRaises(ValueError):
            credential_reference("../unsafe", "canvas")
        with self.assertRaises(ValueError):
            credential_reference("default", "gradescope")
        with self.assertRaises(ValueError):
            CredentialController().target_for(
                "default",
                "canvas",
                canvas_base_url="https://canvas.example.edu/api/v1",
                reference="keyring:canvas-ed-mcp/other/canvas",
            )

    def test_inspection_reports_presence_without_returning_value(self) -> None:
        events: list[str] = []
        keyring = _FakeKeyring(events=events)
        keyring.values[("canvas-ed-mcp/default", "canvas")] = CANARY
        controller = self._controller(keyring, events)
        target = controller.target_for(
            "default",
            "canvas",
            canvas_base_url="https://canvas.example.edu/api/v1",
        )

        inspected = controller.inspect(target)

        self.assertEqual(OperationStatus.SUCCEEDED, inspected.status)
        self.assertEqual(CredentialMode.KEYRING, inspected.mode)
        self.assertEqual(CredentialPresence.PRESENT, inspected.presence)
        self.assertTrue(inspected.writable)
        self.assertNotIn(CANARY, repr(inspected))

    def test_new_token_is_tested_and_provider_closed_before_keyring_write(self) -> None:
        events: list[str] = []
        keyring = _FakeKeyring(events=events)
        controller = self._controller(keyring, events)
        target = controller.target_for(
            "default",
            "canvas",
            canvas_base_url="https://canvas.example.edu/api/v1",
        )

        result = controller.save_and_test(target, SecretValue(CANARY))

        self.assertEqual(OperationStatus.SUCCEEDED, result.status)
        self.assertTrue(result.verified)
        self.assertTrue(result.persisted)
        self.assertEqual(1, result.course_count)
        self.assertEqual(
            CANARY, keyring.values[("canvas-ed-mcp/default", "canvas")]
        )
        self.assertLess(
            events.index("provider:close:canvas"), events.index("keyring:set:canvas")
        )
        self.assertNotIn(CANARY, repr(result))

    def test_invalid_replacement_leaves_existing_value_unchanged(self) -> None:
        events: list[str] = []
        keyring = _FakeKeyring(events=events)
        original = "SYNTHETIC_OLD_VALUE"
        keyring.values[("canvas-ed-mcp/default", "canvas")] = original
        controller = self._controller(
            keyring, events, canvas_failure="authentication_failed"
        )
        target = controller.target_for(
            "default",
            "canvas",
            canvas_base_url="https://canvas.example.edu/api/v1",
        )

        unconfirmed = controller.save_and_test(target, SecretValue(CANARY))
        self.assertEqual(OperationStatus.CONFLICT, unconfirmed.status)
        self.assertEqual(
            "replacement_confirmation_required", unconfirmed.issues[0].code
        )
        self.assertFalse(any(event.startswith("provider:") for event in events))

        confirmed = controller.save_and_test(
            target, SecretValue(CANARY), replace_confirmed=True
        )
        self.assertEqual(OperationStatus.FAILED, confirmed.status)
        self.assertEqual("authentication_failed", confirmed.issues[0].code)
        self.assertEqual(
            original, keyring.values[("canvas-ed-mcp/default", "canvas")]
        )
        self.assertNotIn("keyring:set:canvas", events)
        self.assertNotIn(CANARY, repr(confirmed))

    def test_keyring_unavailable_blocks_provider_before_network(self) -> None:
        events: list[str] = []
        controller = self._controller(_UnsafeKeyring(events=events), events)
        target = controller.target_for(
            "default",
            "canvas",
            canvas_base_url="https://canvas.example.edu/api/v1",
        )

        result = controller.save_and_test(target, SecretValue(CANARY))

        self.assertEqual(OperationStatus.FAILED, result.status)
        self.assertEqual("unsafe_backend", result.issues[0].code)
        self.assertFalse(any(event.startswith("provider:") for event in events))
        self.assertFalse(any(event.startswith("keyring:set") for event in events))

    def test_denied_keyring_access_blocks_provider_and_sanitizes_error(self) -> None:
        events: list[str] = []
        keyring = _FakeKeyring(events=events)
        keyring.fail_get = True
        controller = self._controller(keyring, events)
        target = controller.target_for(
            "default",
            "canvas",
            canvas_base_url="https://canvas.example.edu/api/v1",
        )

        result = controller.save_and_test(target, SecretValue(CANARY))

        self.assertEqual(OperationStatus.FAILED, result.status)
        self.assertEqual("backend_error", result.issues[0].code)
        self.assertFalse(any(event.startswith("provider:") for event in events))
        self.assertNotIn(CANARY, repr(result))

    def test_network_failure_does_not_persist_new_token(self) -> None:
        events: list[str] = []
        keyring = _FakeKeyring(events=events)
        controller = self._controller(
            keyring, events, canvas_failure="request_failed"
        )
        target = controller.target_for(
            "default",
            "canvas",
            canvas_base_url="https://canvas.example.edu/api/v1",
        )

        result = controller.save_and_test(target, SecretValue(CANARY))

        self.assertEqual(OperationStatus.FAILED, result.status)
        self.assertEqual("request_failed", result.issues[0].code)
        self.assertNotIn("keyring:set:canvas", events)

    def test_pre_cancelled_save_performs_no_provider_or_write_io(self) -> None:
        events: list[str] = []
        keyring = _FakeKeyring(events=events)
        controller = self._controller(keyring, events)
        target = controller.target_for(
            "default",
            "ed",
        )

        result = controller.save_and_test(
            target, SecretValue(CANARY), cancellation=_Cancelled()
        )

        self.assertEqual(OperationStatus.CANCELLED, result.status)
        self.assertFalse(any(event.startswith("provider:") for event in events))
        self.assertFalse(any(event.startswith("keyring:set") for event in events))

    def test_cancellation_after_provider_close_prevents_keyring_write(self) -> None:
        events: list[str] = []
        keyring = _FakeKeyring(events=events)
        cancellation = _MutableCancellation()

        class _CancellingProvider(_FakeProvider):
            def close(self) -> None:
                super().close()
                cancellation.cancellation_requested = True

        controller = CredentialController(
            store=NativeCredentialStore(backend=keyring, environ={}),
            canvas_factory=lambda base_url, token: _CancellingProvider(
                "canvas", "canvas.example.edu", events=events
            ),
            ed_factory=lambda token: self.fail("Ed constructed"),
        )
        target = controller.target_for(
            "default",
            "canvas",
            canvas_base_url="https://canvas.example.edu/api/v1",
        )

        result = controller.save_and_test(
            target, SecretValue(CANARY), cancellation=cancellation
        )

        self.assertEqual(OperationStatus.CANCELLED, result.status)
        self.assertTrue(result.verified)
        self.assertNotIn("keyring:set:canvas", events)

    def test_test_existing_is_provider_isolated_and_does_not_write(self) -> None:
        events: list[str] = []
        keyring = _FakeKeyring(events=events)
        keyring.values[("canvas-ed-mcp/default", "ed")] = CANARY
        controller = self._controller(keyring, events)
        target = controller.target_for("default", "ed")

        result = controller.test_existing(target)

        self.assertEqual(OperationStatus.SUCCEEDED, result.status)
        self.assertTrue(result.verified)
        self.assertFalse(result.persisted)
        self.assertIn("provider:create:ed", events)
        self.assertNotIn("provider:create:canvas", events)
        self.assertNotIn("keyring:set:ed", events)

    def test_env_reference_is_testable_but_never_writable(self) -> None:
        events: list[str] = []
        keyring = _FakeKeyring(events=events)
        controller = CredentialController(
            store=NativeCredentialStore(
                backend=keyring, environ={"EXTERNAL_ED_TOKEN": CANARY}
            ),
            canvas_factory=lambda base_url, token: self.fail("Canvas constructed"),
            ed_factory=lambda token: _FakeProvider(
                "ed", "edstem.org", events=events
            ),
        )
        target = controller.target_for(
            "default", "ed", reference="env:EXTERNAL_ED_TOKEN"
        )

        inspected = controller.inspect(target)
        self.assertEqual(CredentialMode.EXTERNALLY_MANAGED, inspected.mode)
        self.assertFalse(inspected.writable)
        tested = controller.test_existing(target)
        self.assertEqual(OperationStatus.SUCCEEDED, tested.status)
        refused = controller.save_and_test(target, SecretValue(CANARY))
        self.assertEqual(OperationStatus.FAILED, refused.status)
        self.assertEqual("read_only_reference", refused.issues[0].code)
        self.assertNotIn("keyring:set:ed", events)

    def test_backend_errors_are_sanitized_and_state_is_unknown(self) -> None:
        events: list[str] = []
        keyring = _FakeKeyring(events=events)
        keyring.fail_set = True
        controller = self._controller(keyring, events)
        target = controller.target_for(
            "default",
            "canvas",
            canvas_base_url="https://canvas.example.edu/api/v1",
        )

        result = controller.save_and_test(target, SecretValue(CANARY))

        self.assertEqual(OperationStatus.FAILED, result.status)
        self.assertTrue(result.verified)
        self.assertFalse(result.persisted)
        self.assertEqual("credential_state_unknown", result.issues[0].code)
        self.assertNotIn(CANARY, repr(result))


if __name__ == "__main__":
    unittest.main()
