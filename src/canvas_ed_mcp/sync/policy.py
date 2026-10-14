"""Pure, provider-neutral content and download policy decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from canvas_ed_mcp.config import ContentSelection, DownloadPolicy

from .model import RemoteItem, RemoteKind


_BINARY_KINDS = frozenset({RemoteKind.FILE, RemoteKind.RESOURCE})
_CONTENT_FIELD = {
    RemoteKind.FILE: "files",
    RemoteKind.PAGE: "pages",
    RemoteKind.LESSON: "lessons",
    RemoteKind.RESOURCE: "resources",
}
_MIME_TYPE_RE = re.compile(
    r"[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+\Z"
)


class PolicyAction(str, Enum):
    """Whether orchestration may continue with a remote item or stream chunk."""

    ALLOW = "allow"
    SKIP = "skip"


class PolicySkipReason(str, Enum):
    """Stable, non-secret reason codes suitable for state and user reporting."""

    CONTENT_CATEGORY_DISABLED = "content_category_disabled"
    EXTENSION_EXCLUDED = "extension_excluded"
    EXTENSION_NOT_INCLUDED = "extension_not_included"
    MIME_EXCLUDED = "mime_excluded"
    MIME_NOT_INCLUDED = "mime_not_included"
    KNOWN_SIZE_LIMIT_EXCEEDED = "known_size_limit_exceeded"
    STREAM_SIZE_LIMIT_EXCEEDED = "stream_size_limit_exceeded"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Immutable authorization result with a stable reason for every skip."""

    action: PolicyAction
    reason: PolicySkipReason | None

    def __post_init__(self) -> None:
        if not isinstance(self.action, PolicyAction):
            raise ValueError("action must be a PolicyAction")
        if self.reason is not None and not isinstance(self.reason, PolicySkipReason):
            raise ValueError("reason must be a PolicySkipReason or None")
        if self.action is PolicyAction.ALLOW and self.reason is not None:
            raise ValueError("allowed policy decisions cannot have a skip reason")
        if self.action is PolicyAction.SKIP and self.reason is None:
            raise ValueError("skipped policy decisions require a skip reason")

    @property
    def allowed(self) -> bool:
        return self.action is PolicyAction.ALLOW


_ALLOW = PolicyDecision(PolicyAction.ALLOW, None)


def _skip(reason: PolicySkipReason) -> PolicyDecision:
    return PolicyDecision(PolicyAction.SKIP, reason)


def _extension_matches(display_name: str, rules: tuple[str, ...]) -> bool:
    name = display_name.casefold()
    return any(name.endswith(extension) for extension in rules)


def _canonical_media_type(value: str | None) -> str | None:
    if value is None:
        return None
    canonical = value.split(";", 1)[0].strip().casefold()
    if not _MIME_TYPE_RE.fullmatch(canonical):
        return None
    return canonical


def _mime_matches(media_type: str | None, rules: tuple[str, ...]) -> bool:
    if media_type is None:
        return False
    major_type, _ = media_type.split("/", 1)
    return any(
        rule == media_type or rule == f"{major_type}/*"
        for rule in rules
    )


def evaluate_item_policy(
    item: RemoteItem,
    *,
    content: ContentSelection,
    policy: DownloadPolicy,
) -> PolicyDecision:
    """Apply deterministic preflight policy without filesystem or network I/O.

    Category selection is evaluated first. Extension/MIME/size filters then apply
    only to file-like remote kinds. Explicit exclusions take precedence over
    includes, and a known size equal to the configured cap is allowed.
    """
    if not isinstance(item, RemoteItem):
        raise ValueError("item must be a RemoteItem")
    if not isinstance(content, ContentSelection):
        raise ValueError("content must be a ContentSelection")
    if not isinstance(policy, DownloadPolicy):
        raise ValueError("policy must be a DownloadPolicy")

    if not getattr(content, _CONTENT_FIELD[item.key.kind]):
        return _skip(PolicySkipReason.CONTENT_CATEGORY_DISABLED)

    if item.key.kind not in _BINARY_KINDS:
        return _ALLOW

    if _extension_matches(item.display_name, policy.exclude_extensions):
        return _skip(PolicySkipReason.EXTENSION_EXCLUDED)
    if policy.include_extensions and not _extension_matches(
        item.display_name, policy.include_extensions
    ):
        return _skip(PolicySkipReason.EXTENSION_NOT_INCLUDED)

    media_type = _canonical_media_type(item.media_type)
    if _mime_matches(media_type, policy.exclude_mime):
        return _skip(PolicySkipReason.MIME_EXCLUDED)
    if policy.include_mime and not _mime_matches(media_type, policy.include_mime):
        return _skip(PolicySkipReason.MIME_NOT_INCLUDED)

    if (
        policy.max_file_bytes is not None
        and item.size_bytes is not None
        and item.size_bytes > policy.max_file_bytes
    ):
        return _skip(PolicySkipReason.KNOWN_SIZE_LIMIT_EXCEEDED)
    return _ALLOW


def evaluate_stream_size(
    *,
    received_bytes: int,
    next_chunk_bytes: int,
    max_file_bytes: int | None,
) -> PolicyDecision:
    """Authorize a chunk before it is written for a response of unknown size.

    The caller owns staging cleanup. Calling this before every write guarantees
    that bytes beyond the configured cap are never persisted to the staging file.
    """
    for value, field in (
        (received_bytes, "received_bytes"),
        (next_chunk_bytes, "next_chunk_bytes"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
    if max_file_bytes is not None and (
        isinstance(max_file_bytes, bool)
        or not isinstance(max_file_bytes, int)
        or max_file_bytes < 1
    ):
        raise ValueError("max_file_bytes must be a positive integer or None")

    if (
        max_file_bytes is not None
        and received_bytes + next_chunk_bytes > max_file_bytes
    ):
        return _skip(PolicySkipReason.STREAM_SIZE_LIMIT_EXCEEDED)
    return _ALLOW
