"""Pure source and install decisions for the portable sync engine."""

from __future__ import annotations

from .model import (
    DecisionReason,
    LocalStatus,
    PreviousSource,
    RemoteItem,
    StoredFile,
    SyncAction,
    SyncDecision,
    SyncMode,
)


def decide_source(
    remote: RemoteItem,
    previous: PreviousSource | None,
    *,
    mode: SyncMode,
) -> SyncDecision:
    """Authorize record/skip/fetch without consulting the local filesystem."""
    if not isinstance(remote, RemoteItem):
        raise ValueError("remote must be a RemoteItem")
    if previous is not None and not isinstance(previous, PreviousSource):
        raise ValueError("previous must be a PreviousSource or None")
    if previous is not None and previous.key != remote.key:
        raise ValueError("previous state belongs to a different source")
    if not isinstance(mode, SyncMode):
        raise ValueError("mode must be a SyncMode")

    if mode is SyncMode.BASELINE:
        return SyncDecision(
            key=remote.key,
            action=SyncAction.RECORD_ONLY,
            reason=DecisionReason.BASELINE,
            remote_changed=None,
            retry_triggered=False,
            retry_required=False,
            previous_file=previous.stored_file if previous is not None else None,
        )

    if previous is None:
        return SyncDecision(
            key=remote.key,
            action=SyncAction.FETCH,
            reason=DecisionReason.NEW_SOURCE,
            remote_changed=None,
            retry_triggered=False,
            retry_required=False,
            previous_file=None,
        )

    remote_changed = remote.signature != previous.signature
    if remote_changed:
        return SyncDecision(
            key=remote.key,
            action=SyncAction.FETCH,
            reason=DecisionReason.REMOTE_CHANGED,
            remote_changed=True,
            retry_triggered=previous.retry_required,
            retry_required=previous.retry_required,
            previous_file=previous.stored_file,
        )
    if previous.retry_required:
        return SyncDecision(
            key=remote.key,
            action=SyncAction.FETCH,
            reason=DecisionReason.RETRY_REQUIRED,
            remote_changed=False,
            retry_triggered=True,
            retry_required=True,
            previous_file=previous.stored_file,
        )
    return SyncDecision(
        key=remote.key,
        action=SyncAction.SKIP,
        reason=DecisionReason.REMOTE_UNCHANGED,
        remote_changed=False,
        retry_triggered=False,
        retry_required=False,
        previous_file=previous.stored_file,
    )


def decide_install(
    source_decision: SyncDecision,
    *,
    local_status: LocalStatus,
    identical_content_exists: bool,
    allow_in_place_replace: bool,
    retry_required_after_install: bool = False,
) -> SyncDecision:
    """Authorize a local action after a successful fetch/content hash."""
    if not isinstance(source_decision, SyncDecision):
        raise ValueError("source_decision must be a SyncDecision")
    if source_decision.action is not SyncAction.FETCH:
        raise ValueError("install decisions require a FETCH source decision")
    if not isinstance(local_status, LocalStatus):
        raise ValueError("local_status must be a LocalStatus")
    if not isinstance(identical_content_exists, bool):
        raise ValueError("identical_content_exists must be a boolean")
    if not isinstance(allow_in_place_replace, bool):
        raise ValueError("allow_in_place_replace must be a boolean")
    if not isinstance(retry_required_after_install, bool):
        raise ValueError("retry_required_after_install must be a boolean")

    previous_file = source_decision.previous_file

    if previous_file is None:
        if local_status is not LocalStatus.NONE:
            raise ValueError("local evidence without a tracked file is inconsistent")
    else:
        if not isinstance(previous_file, StoredFile):
            raise ValueError("previous_file must be a StoredFile or None")
        if not previous_file.managed_by_sync:
            raise ValueError("a tracked previous file must be sync-managed")
        if local_status is LocalStatus.NONE:
            raise ValueError("a tracked file requires an observed local status")

    action: SyncAction
    reason: DecisionReason
    if local_status is LocalStatus.UNSAFE:
        action = SyncAction.BLOCK
        reason = DecisionReason.UNSAFE_TRACKED_PATH
    elif local_status is LocalStatus.UNMODIFIED:
        if allow_in_place_replace:
            action = SyncAction.REPLACE_MANAGED
            reason = DecisionReason.TRACKED_FILE_UNMODIFIED
        else:
            action = SyncAction.INSTALL_SEPARATELY
            reason = DecisionReason.SAFE_REPLACE_UNAVAILABLE
    elif identical_content_exists:
        action = SyncAction.DEDUPLICATE
        reason = DecisionReason.IDENTICAL_CONTENT_EXISTS
    elif local_status is LocalStatus.MODIFIED:
        action = SyncAction.INSTALL_SEPARATELY
        reason = DecisionReason.TRACKED_FILE_MODIFIED
    elif local_status is LocalStatus.MISSING:
        action = SyncAction.INSTALL_NEW
        reason = DecisionReason.TRACKED_FILE_MISSING
    else:
        action = SyncAction.INSTALL_NEW
        reason = DecisionReason.NO_TRACKED_FILE

    return SyncDecision(
        key=source_decision.key,
        action=action,
        reason=reason,
        remote_changed=source_decision.remote_changed,
        retry_triggered=source_decision.retry_triggered,
        retry_required=retry_required_after_install,
        previous_file=previous_file,
    )
