from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path, PurePosixPath


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.sync.decisions import decide_install, decide_source
from canvas_ed_mcp.sync.model import (
    DecisionReason,
    LocalStatus,
    PreviousSource,
    RemoteItem,
    RemoteKind,
    SourceKey,
    StoredFile,
    SyncAction,
    SyncDecision,
    SyncMode,
)


MANAGED_HASH = "a" * 64


def source_key(**changes: object) -> SourceKey:
    values: dict[str, object] = {
        "provider": "canvas",
        "instance": "canvas.example.edu",
        "course_id": "course:001",
        "kind": RemoteKind.FILE,
        "remote_id": "item:001",
    }
    values.update(changes)
    return SourceKey(**values)  # type: ignore[arg-type]


def remote(signature: str = "revision-1") -> RemoteItem:
    return RemoteItem(
        key=source_key(),
        signature=signature,
        display_name="Synthetic handout.pdf",
        group="Week 1",
        size_bytes=None,
        media_type="application/pdf",
    )


def managed_file() -> StoredFile:
    return StoredFile(
        output_relative_path=PurePosixPath("TEST1000/Week 1/handout.pdf"),
        written_sha256=MANAGED_HASH,
        managed_by_sync=True,
    )


class SyncDomainModelTests(unittest.TestCase):
    def test_source_identity_uses_every_stable_field_and_keeps_delimiters_opaque(self) -> None:
        original = source_key()
        self.assertEqual(original, source_key())
        self.assertEqual(hash(original), hash(source_key()))
        for field, value in (
            ("provider", "ed"),
            ("instance", "canvas.other.example:8443"),
            ("course_id", "course:002"),
            ("kind", RemoteKind.PAGE),
            ("remote_id", "item:002"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(original, source_key(**{field: value}))
        self.assertEqual("course:001", original.course_id)
        self.assertEqual("item:001", original.remote_id)

    def test_source_key_rejects_unsafe_or_uncanonicalized_identity(self) -> None:
        for changes in (
            {"provider": ""},
            {"provider": "Canvas"},
            {"instance": "Canvas.Example.edu"},
            {"instance": "https://canvas.example.edu/api/v1"},
            {"instance": "user@canvas.example.edu"},
            {"instance": "canvas.example.edu?token=synthetic"},
            {"instance": "canvas.example.edu:443"},
            {"instance": "canvas.example.edu:not-a-port"},
            {"course_id": "  "},
            {"remote_id": "item\n001"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                source_key(**changes)
        self.assertEqual(
            "canvas.example.edu:8443",
            source_key(instance="canvas.example.edu:8443").instance,
        )

    def test_remote_item_is_metadata_only_and_validates_size(self) -> None:
        item = remote()
        self.assertNotIn("url", {field.name for field in dataclasses.fields(item)})
        self.assertNotIn("headers", {field.name for field in dataclasses.fields(item)})
        self.assertNotIn("payload", {field.name for field in dataclasses.fields(item)})
        self.assertIsNone(item.size_bytes)
        with self.assertRaises(ValueError):
            RemoteItem(
                key=source_key(),
                signature="revision",
                display_name="bad.bin",
                size_bytes=-1,
            )

    def test_stored_file_requires_portable_relative_path_and_consistent_ownership(self) -> None:
        for path in (
            PurePosixPath("/absolute/file.pdf"),
            PurePosixPath("../escape.pdf"),
            PurePosixPath("folder/../escape.pdf"),
            PurePosixPath("C:/drive/file.pdf"),
            PurePosixPath("folder\\windows.pdf"),
            PurePosixPath("D:outside.pdf"),
            PurePosixPath("course/file.pdf:stream"),
            PurePosixPath("folder/C:/file.pdf"),
            PurePosixPath("CON"),
            PurePosixPath("course/aux.txt"),
            PurePosixPath("course/trailing. "),
            PurePosixPath("course/a?b.pdf"),
            PurePosixPath("course/a*b.pdf"),
            PurePosixPath("course/a|b.pdf"),
            PurePosixPath("course/a<b>.pdf"),
            PurePosixPath("CONIN$"),
            PurePosixPath("CONOUT$"),
            PurePosixPath("COM¹"),
            PurePosixPath("LPT².txt"),
            PurePosixPath("."),
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                StoredFile(path, MANAGED_HASH, True)
        with self.assertRaises(ValueError):
            StoredFile(PurePosixPath("course/file.pdf"), None, True)
        with self.assertRaises(ValueError):
            StoredFile(PurePosixPath("course/file.pdf"), MANAGED_HASH, False)
        with self.assertRaises(ValueError):
            StoredFile(PurePosixPath("course/file.pdf"), "A" * 64, True)

    def test_domain_values_are_immutable_and_hashable(self) -> None:
        key = source_key()
        self.assertIsInstance(hash(key), int)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            key.course_id = "changed"  # type: ignore[misc]


class SourceDecisionTests(unittest.TestCase):
    def test_source_decision_table(self) -> None:
        cases = (
            (
                "baseline absent",
                SyncMode.BASELINE,
                None,
                SyncAction.RECORD_ONLY,
                DecisionReason.BASELINE,
                None,
                False,
            ),
            (
                "new source",
                SyncMode.SYNC,
                None,
                SyncAction.FETCH,
                DecisionReason.NEW_SOURCE,
                None,
                False,
            ),
            (
                "unchanged",
                SyncMode.SYNC,
                PreviousSource(source_key(), "revision-1"),
                SyncAction.SKIP,
                DecisionReason.REMOTE_UNCHANGED,
                False,
                False,
            ),
            (
                "changed",
                SyncMode.SYNC,
                PreviousSource(source_key(), "revision-0"),
                SyncAction.FETCH,
                DecisionReason.REMOTE_CHANGED,
                True,
                False,
            ),
            (
                "unchanged retry",
                SyncMode.SYNC,
                PreviousSource(source_key(), "revision-1", retry_required=True),
                SyncAction.FETCH,
                DecisionReason.RETRY_REQUIRED,
                False,
                True,
            ),
            (
                "changed retry",
                SyncMode.SYNC,
                PreviousSource(source_key(), "revision-0", retry_required=True),
                SyncAction.FETCH,
                DecisionReason.REMOTE_CHANGED,
                True,
                True,
            ),
        )
        for name, mode, previous, action, reason, changed, retry in cases:
            with self.subTest(name=name):
                decision = decide_source(remote(), previous, mode=mode)
                self.assertEqual(action, decision.action)
                self.assertEqual(reason, decision.reason)
                self.assertEqual(changed, decision.remote_changed)
                self.assertEqual(retry, decision.retry_required)

    def test_baseline_never_fetches_even_when_a_previous_revision_exists(self) -> None:
        decision = decide_source(
            remote("revision-2"), PreviousSource(source_key(), "revision-1"), mode=SyncMode.BASELINE
        )
        self.assertEqual(SyncAction.RECORD_ONLY, decision.action)
        self.assertEqual(DecisionReason.BASELINE, decision.reason)

    def test_unchanged_source_skips_before_local_missing_state_is_considered(self) -> None:
        decision = decide_source(
            remote(),
            PreviousSource(source_key(), "revision-1", stored_file=managed_file()),
            mode=SyncMode.SYNC,
        )
        self.assertEqual(SyncAction.SKIP, decision.action)
        with self.assertRaises(ValueError):
            decide_install(
                decision,
                local_status=LocalStatus.MISSING,
                identical_content_exists=False,
                allow_in_place_replace=True,
            )


class InstallDecisionTests(unittest.TestCase):
    def fetched(self, previous: PreviousSource | None = None) -> object:
        return decide_source(remote(), previous, mode=SyncMode.SYNC)

    def test_install_decision_table(self) -> None:
        tracked = managed_file()
        cases = (
            (
                "new",
                self.fetched(),
                LocalStatus.NONE,
                False,
                SyncAction.INSTALL_NEW,
                DecisionReason.NO_TRACKED_FILE,
            ),
            (
                "new duplicate",
                self.fetched(),
                LocalStatus.NONE,
                True,
                SyncAction.DEDUPLICATE,
                DecisionReason.IDENTICAL_CONTENT_EXISTS,
            ),
            (
                "unmodified takes replacement precedence",
                self.fetched(PreviousSource(source_key(), "revision-0", stored_file=tracked)),
                LocalStatus.UNMODIFIED,
                True,
                SyncAction.REPLACE_MANAGED,
                DecisionReason.TRACKED_FILE_UNMODIFIED,
            ),
            (
                "modified",
                self.fetched(PreviousSource(source_key(), "revision-0", stored_file=tracked)),
                LocalStatus.MODIFIED,
                False,
                SyncAction.INSTALL_SEPARATELY,
                DecisionReason.TRACKED_FILE_MODIFIED,
            ),
            (
                "modified duplicate",
                self.fetched(PreviousSource(source_key(), "revision-0", stored_file=tracked)),
                LocalStatus.MODIFIED,
                True,
                SyncAction.DEDUPLICATE,
                DecisionReason.IDENTICAL_CONTENT_EXISTS,
            ),
            (
                "missing",
                self.fetched(PreviousSource(source_key(), "revision-0", stored_file=tracked)),
                LocalStatus.MISSING,
                False,
                SyncAction.INSTALL_NEW,
                DecisionReason.TRACKED_FILE_MISSING,
            ),
            (
                "unsafe",
                self.fetched(PreviousSource(source_key(), "revision-0", stored_file=tracked)),
                LocalStatus.UNSAFE,
                True,
                SyncAction.BLOCK,
                DecisionReason.UNSAFE_TRACKED_PATH,
            ),
        )
        for name, source, status, duplicate, action, reason in cases:
            with self.subTest(name=name):
                decision = decide_install(
                    source,  # type: ignore[arg-type]
                    local_status=status,
                    identical_content_exists=duplicate,
                    allow_in_place_replace=True,
                )
                self.assertEqual(action, decision.action)
                self.assertEqual(reason, decision.reason)
                self.assertEqual(source.remote_changed, decision.remote_changed)  # type: ignore[attr-defined]
                self.assertEqual(source.retry_required, decision.retry_required)  # type: ignore[attr-defined]

    def test_retry_semantics_survive_install_decision(self) -> None:
        tracked = managed_file()
        source = self.fetched(
            PreviousSource(
                source_key(), "revision-1", stored_file=tracked, retry_required=True
            )
        )
        decision = decide_install(
            source,  # type: ignore[arg-type]
            local_status=LocalStatus.UNMODIFIED,
            identical_content_exists=False,
            allow_in_place_replace=True,
        )
        self.assertFalse(decision.remote_changed)
        self.assertTrue(decision.retry_triggered)
        self.assertFalse(decision.retry_required)
        self.assertEqual(SyncAction.REPLACE_MANAGED, decision.action)

        partial = decide_install(
            source,  # type: ignore[arg-type]
            local_status=LocalStatus.UNMODIFIED,
            identical_content_exists=False,
            allow_in_place_replace=True,
            retry_required_after_install=True,
        )
        self.assertTrue(partial.retry_required)

    def test_corrupt_install_inputs_fail_closed(self) -> None:
        fetched = self.fetched()
        with self.assertRaises(ValueError):
            decide_install(
                fetched,  # type: ignore[arg-type]
                local_status=LocalStatus.MISSING,
                identical_content_exists=False,
                allow_in_place_replace=True,
            )

        skipped = decide_source(
            remote(), PreviousSource(source_key(), "revision-1"), mode=SyncMode.SYNC
        )
        with self.assertRaises(ValueError):
            decide_install(
                skipped,
                local_status=LocalStatus.NONE,
                identical_content_exists=False,
                allow_in_place_replace=True,
            )

    def test_previous_state_is_bound_to_the_same_source(self) -> None:
        other_key = source_key(remote_id="other-item")
        with self.assertRaises(ValueError):
            decide_source(
                remote(),
                PreviousSource(other_key, "revision-0", stored_file=managed_file()),
                mode=SyncMode.SYNC,
            )

    def test_new_source_cannot_replace_an_unrelated_managed_file(self) -> None:
        source = self.fetched()
        with self.assertRaises(ValueError):
            decide_install(
                source,  # type: ignore[arg-type]
                local_status=LocalStatus.UNMODIFIED,
                identical_content_exists=False,
                allow_in_place_replace=True,
            )

    def test_unmodified_file_downgrades_when_safe_replace_is_unavailable(self) -> None:
        tracked = managed_file()
        source = self.fetched(
            PreviousSource(source_key(), "revision-0", stored_file=tracked)
        )
        decision = decide_install(
            source,  # type: ignore[arg-type]
            local_status=LocalStatus.UNMODIFIED,
            identical_content_exists=False,
            allow_in_place_replace=False,
        )
        self.assertEqual(SyncAction.INSTALL_SEPARATELY, decision.action)
        self.assertEqual(DecisionReason.SAFE_REPLACE_UNAVAILABLE, decision.reason)

    def test_forged_action_reason_pair_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SyncDecision(
                key=source_key(),
                action=SyncAction.FETCH,
                reason=DecisionReason.BASELINE,
                remote_changed=None,
                retry_triggered=False,
                retry_required=False,
                previous_file=None,
            )
        with self.assertRaises(ValueError):
            SyncDecision(
                key=source_key(),
                action=SyncAction.SKIP,
                reason=DecisionReason.REMOTE_UNCHANGED,
                remote_changed=False,
                retry_triggered=False,
                retry_required=True,
                previous_file=None,
            )


if __name__ == "__main__":
    unittest.main()
