from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.config import ContentSelection, DownloadPolicy
from canvas_ed_mcp.sync.model import RemoteItem, RemoteKind, SourceKey
from canvas_ed_mcp.sync.policy import (
    PolicyAction,
    PolicyDecision,
    PolicySkipReason,
    evaluate_item_policy,
    evaluate_stream_size,
)


def remote_item(
    kind: RemoteKind = RemoteKind.FILE,
    *,
    display_name: str = "Synthetic handout.pdf",
    media_type: str | None = "application/pdf",
    size_bytes: int | None = 512,
) -> RemoteItem:
    return RemoteItem(
        key=SourceKey(
            provider="canvas",
            instance="canvas.example.edu",
            course_id="course:001",
            kind=kind,
            remote_id=f"item:{kind.value}",
        ),
        signature="revision-1",
        display_name=display_name,
        size_bytes=size_bytes,
        media_type=media_type,
    )


class ItemPolicyTests(unittest.TestCase):
    def test_content_category_table(self) -> None:
        cases = (
            (RemoteKind.FILE, "files"),
            (RemoteKind.PAGE, "pages"),
            (RemoteKind.LESSON, "lessons"),
            (RemoteKind.RESOURCE, "resources"),
        )
        for kind, selected_field in cases:
            with self.subTest(kind=kind):
                selection = ContentSelection(
                    files=selected_field != "files",
                    pages=selected_field != "pages",
                    lessons=selected_field != "lessons",
                    resources=selected_field != "resources",
                )
                decision = evaluate_item_policy(
                    remote_item(kind),
                    content=selection,
                    policy=DownloadPolicy(),
                )
                self.assertEqual(PolicyAction.SKIP, decision.action)
                self.assertEqual(
                    PolicySkipReason.CONTENT_CATEGORY_DISABLED,
                    decision.reason,
                )

    def test_extension_policy_table(self) -> None:
        cases = (
            (
                "included case-insensitively",
                "notes.PDF",
                ("pdf",),
                (),
                PolicyAction.ALLOW,
                None,
            ),
            (
                "compound extension",
                "archive.TAR.GZ",
                (".tar.gz",),
                (),
                PolicyAction.ALLOW,
                None,
            ),
            (
                "not included",
                "notes.txt",
                (".pdf",),
                (),
                PolicyAction.SKIP,
                PolicySkipReason.EXTENSION_NOT_INCLUDED,
            ),
            (
                "missing extension is not included",
                "README",
                (".pdf",),
                (),
                PolicyAction.SKIP,
                PolicySkipReason.EXTENSION_NOT_INCLUDED,
            ),
            (
                "explicit exclusion",
                "lecture.mp4",
                (),
                ("mp4",),
                PolicyAction.SKIP,
                PolicySkipReason.EXTENSION_EXCLUDED,
            ),
            (
                "exclusion wins conflict",
                "lecture.mp4",
                ("mp4",),
                (".MP4",),
                PolicyAction.SKIP,
                PolicySkipReason.EXTENSION_EXCLUDED,
            ),
        )
        for name, filename, includes, excludes, action, reason in cases:
            with self.subTest(name=name):
                decision = evaluate_item_policy(
                    remote_item(display_name=filename),
                    content=ContentSelection(),
                    policy=DownloadPolicy(
                        max_file_bytes=None,
                        include_extensions=includes,
                        exclude_extensions=excludes,
                    ),
                )
                self.assertEqual(action, decision.action)
                self.assertEqual(reason, decision.reason)

    def test_mime_policy_table(self) -> None:
        cases = (
            (
                "exact type with parameters",
                "Application/PDF; charset=binary",
                ("application/pdf",),
                (),
                PolicyAction.ALLOW,
                None,
            ),
            (
                "wildcard include",
                "image/png",
                ("image/*",),
                (),
                PolicyAction.ALLOW,
                None,
            ),
            (
                "not included",
                "text/plain",
                ("application/pdf",),
                (),
                PolicyAction.SKIP,
                PolicySkipReason.MIME_NOT_INCLUDED,
            ),
            (
                "missing type is not included",
                None,
                ("application/pdf",),
                (),
                PolicyAction.SKIP,
                PolicySkipReason.MIME_NOT_INCLUDED,
            ),
            (
                "wildcard exclusion",
                "video/webm",
                (),
                ("video/*",),
                PolicyAction.SKIP,
                PolicySkipReason.MIME_EXCLUDED,
            ),
            (
                "exclusion wins conflict",
                "application/pdf",
                ("application/*",),
                ("application/pdf",),
                PolicyAction.SKIP,
                PolicySkipReason.MIME_EXCLUDED,
            ),
        )
        for name, media_type, includes, excludes, action, reason in cases:
            with self.subTest(name=name):
                decision = evaluate_item_policy(
                    remote_item(media_type=media_type),
                    content=ContentSelection(),
                    policy=DownloadPolicy(
                        max_file_bytes=None,
                        include_mime=includes,
                        exclude_mime=excludes,
                    ),
                )
                self.assertEqual(action, decision.action)
                self.assertEqual(reason, decision.reason)

    def test_page_and_lesson_outputs_ignore_binary_filters(self) -> None:
        policy = DownloadPolicy(
            max_file_bytes=1,
            include_extensions=(".pdf",),
            exclude_extensions=(".html",),
            include_mime=("application/pdf",),
            exclude_mime=("text/html",),
        )
        for kind in (RemoteKind.PAGE, RemoteKind.LESSON):
            with self.subTest(kind=kind):
                decision = evaluate_item_policy(
                    remote_item(
                        kind,
                        display_name="Rendered page.html",
                        media_type="text/html",
                        size_bytes=10_000,
                    ),
                    content=ContentSelection(),
                    policy=policy,
                )
                self.assertEqual(PolicyAction.ALLOW, decision.action)
                self.assertIsNone(decision.reason)

    def test_resource_uses_binary_filters(self) -> None:
        decision = evaluate_item_policy(
            remote_item(
                RemoteKind.RESOURCE,
                display_name="recording.mp4",
                media_type="video/mp4",
            ),
            content=ContentSelection(),
            policy=DownloadPolicy(),
        )
        self.assertEqual(PolicyAction.SKIP, decision.action)
        self.assertEqual(PolicySkipReason.EXTENSION_EXCLUDED, decision.reason)

    def test_known_and_unknown_size_preflight(self) -> None:
        policy = DownloadPolicy(
            max_file_bytes=1024,
            exclude_extensions=(),
        )
        cases = (
            ("at limit", 1024, PolicyAction.ALLOW, None),
            (
                "over limit",
                1025,
                PolicyAction.SKIP,
                PolicySkipReason.KNOWN_SIZE_LIMIT_EXCEEDED,
            ),
            ("unknown continues to guarded stream", None, PolicyAction.ALLOW, None),
        )
        for name, size, action, reason in cases:
            with self.subTest(name=name):
                decision = evaluate_item_policy(
                    remote_item(size_bytes=size),
                    content=ContentSelection(),
                    policy=policy,
                )
                self.assertEqual(action, decision.action)
                self.assertEqual(reason, decision.reason)

    def test_skip_reason_values_are_stable(self) -> None:
        self.assertEqual(
            "content_category_disabled",
            PolicySkipReason.CONTENT_CATEGORY_DISABLED.value,
        )
        self.assertEqual(
            "known_size_limit_exceeded",
            PolicySkipReason.KNOWN_SIZE_LIMIT_EXCEEDED.value,
        )
        self.assertEqual(
            "stream_size_limit_exceeded",
            PolicySkipReason.STREAM_SIZE_LIMIT_EXCEEDED.value,
        )


class StreamSizePolicyTests(unittest.TestCase):
    def test_stream_limit_table(self) -> None:
        cases = (
            ("below", 900, 100, 1000, PolicyAction.ALLOW, None),
            ("over", 900, 101, 1000, PolicyAction.SKIP, PolicySkipReason.STREAM_SIZE_LIMIT_EXCEEDED),
            ("no cap", 10_000, 10_000, None, PolicyAction.ALLOW, None),
        )
        for name, received, chunk, limit, action, reason in cases:
            with self.subTest(name=name):
                decision = evaluate_stream_size(
                    received_bytes=received,
                    next_chunk_bytes=chunk,
                    max_file_bytes=limit,
                )
                self.assertEqual(action, decision.action)
                self.assertEqual(reason, decision.reason)

    def test_stream_limit_rejects_invalid_counters(self) -> None:
        for received, chunk, limit in (
            (-1, 1, 10),
            (0, -1, 10),
            (False, 1, 10),
            (0, True, 10),
            (0, 1, 0),
            (0, 1, False),
        ):
            with self.subTest(received=received, chunk=chunk, limit=limit):
                with self.assertRaises(ValueError):
                    evaluate_stream_size(
                        received_bytes=received,
                        next_chunk_bytes=chunk,
                        max_file_bytes=limit,
                    )

    def test_policy_decisions_are_immutable_and_validate_reason_pairs(self) -> None:
        allowed = PolicyDecision(PolicyAction.ALLOW, None)
        self.assertTrue(allowed.allowed)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            allowed.action = PolicyAction.SKIP  # type: ignore[misc]
        with self.assertRaises(ValueError):
            PolicyDecision(PolicyAction.ALLOW, PolicySkipReason.MIME_EXCLUDED)
        with self.assertRaises(ValueError):
            PolicyDecision(PolicyAction.SKIP, None)


class DownloadPolicyConfigTests(unittest.TestCase):
    def test_default_policy_freezes_current_safe_presets(self) -> None:
        policy = DownloadPolicy()
        self.assertEqual(100 * 1024 * 1024, policy.max_file_bytes)
        self.assertEqual((".mp4", ".mov"), policy.exclude_extensions)

    def test_mime_filters_are_normalized_and_deduplicated(self) -> None:
        policy = DownloadPolicy(
            include_mime=(" Application/PDF ", "application/pdf", "IMAGE/*"),
            exclude_mime=("video/*",),
        )
        self.assertEqual(("application/pdf", "image/*"), policy.include_mime)
        self.assertEqual(("video/*",), policy.exclude_mime)

    def test_invalid_mime_filters_fail_closed(self) -> None:
        for value in ("", "application", "*/pdf", "text/html; charset=utf-8", "text /plain"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DownloadPolicy(include_mime=(value,))


if __name__ == "__main__":
    unittest.main()
