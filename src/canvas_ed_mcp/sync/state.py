"""Versioned, profile/output-bound sync state and fail-closed v3 migration."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from canvas_ed_mcp.paths import validate_profile_name

from .locking import LockScope, SyncLock
from .model import PreviousSource, RemoteKind, SourceKey, StoredFile
from .policy import PolicySkipReason


CURRENT_STATE_SCHEMA = 5
_ROOT_FIELDS = frozenset({
    "schema_version", "profile", "output_root", "sources", "initialized_sources", "policy_skips"
})
_V4_ROOT_FIELDS = frozenset({"schema_version", "profile", "output_root", "sources"})
_SOURCE_FIELDS = frozenset({"key", "signature", "stored_file", "retry_required"})
_KEY_FIELDS = frozenset(
    {"provider", "instance", "course_id", "kind", "remote_id"}
)
_FILE_FIELDS = frozenset(
    {"output_relative_path", "written_sha256", "managed_by_sync"}
)
_INITIALIZED_FIELDS = frozenset({"course_key", "provider", "instance", "course_id"})
_POLICY_SKIP_FIELDS = frozenset(
    {"course_key", "key", "signature", "reason", "policy_fingerprint"}
)
_LEGACY_POLICY_SKIP_FIELDS = frozenset({"course_key", "key", "signature", "reason"})


class StateLoadError(RuntimeError):
    """A categorized state failure that must prevent synchronization."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class StateMigrationError(RuntimeError):
    """A categorized legacy migration failure with no partial destination."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def _canonical_output_root(value: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("output_root must be absolute")
    return path.resolve(strict=False)


def _source_sort_key(source: PreviousSource) -> tuple[str, str, str, str, str]:
    key = source.key
    return (
        key.provider,
        key.instance,
        key.course_id,
        key.kind.value,
        key.remote_id,
    )


@dataclass(frozen=True, slots=True, order=True)
class InitializedSource:
    """Durable source-level baseline checkpoint, including empty discovery."""

    course_key: str
    provider: str
    instance: str
    course_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.course_key, str) or not self.course_key.strip():
            raise ValueError("course_key must be a non-empty string")
        probe = SourceKey(self.provider, self.instance, self.course_id, RemoteKind.FILE, "checkpoint")
        del probe


@dataclass(frozen=True, slots=True)
class PolicySkipState:
    """A skipped revision kept separate from managed-file ownership."""

    course_key: str
    key: SourceKey
    signature: str
    reason: PolicySkipReason
    policy_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.course_key, str) or not self.course_key.strip():
            raise ValueError("course_key must be a non-empty string")
        if not isinstance(self.key, SourceKey):
            raise ValueError("key must be a SourceKey")
        if not isinstance(self.signature, str) or not self.signature.strip():
            raise ValueError("signature must be a non-empty string")
        if not isinstance(self.reason, PolicySkipReason):
            raise ValueError("reason must be a PolicySkipReason")
        if self.policy_fingerprint is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.policy_fingerprint
        ):
            raise ValueError("policy_fingerprint must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class SyncState:
    """Immutable v5 state for exactly one profile and canonical output root."""

    profile: str
    output_root: Path
    sources: tuple[PreviousSource, ...]
    initialized_sources: tuple[InitializedSource, ...] = ()
    policy_skips: tuple[PolicySkipState, ...] = ()
    schema_version: int = field(default=CURRENT_STATE_SCHEMA, init=False)

    def __post_init__(self) -> None:
        validate_profile_name(self.profile)
        if not isinstance(self.output_root, Path):
            raise ValueError("output_root must be a Path")
        if self.output_root != _canonical_output_root(self.output_root):
            raise ValueError("output_root must be canonical and absolute")
        if not isinstance(self.sources, tuple):
            raise ValueError("sources must be an immutable tuple")
        keys: list[SourceKey] = []
        for source in self.sources:
            if not isinstance(source, PreviousSource):
                raise ValueError("sources must contain PreviousSource values")
            keys.append(source.key)
        if len(keys) != len(set(keys)):
            raise ValueError("state cannot contain duplicate source identities")
        if not isinstance(self.initialized_sources, tuple) or not all(
            isinstance(item, InitializedSource) for item in self.initialized_sources
        ):
            raise ValueError("initialized_sources must be an immutable tuple")
        if len(self.initialized_sources) != len(set(self.initialized_sources)):
            raise ValueError("state cannot contain duplicate initialized sources")
        if not isinstance(self.policy_skips, tuple) or not all(
            isinstance(item, PolicySkipState) for item in self.policy_skips
        ):
            raise ValueError("policy_skips must be an immutable tuple")
        skip_keys = [(item.course_key, item.key) for item in self.policy_skips]
        if len(skip_keys) != len(set(skip_keys)):
            raise ValueError("state cannot contain duplicate policy skips")

    def previous_for(self, key: SourceKey) -> PreviousSource | None:
        if not isinstance(key, SourceKey):
            raise ValueError("key must be a SourceKey")
        return next((source for source in self.sources if source.key == key), None)

    def initialized_for(self, *, course_key: str, provider: str, instance: str, course_id: str) -> bool:
        return InitializedSource(course_key, provider, instance, course_id) in self.initialized_sources


def new_state(*, profile: str, output_root: Path) -> SyncState:
    """Create an empty state namespace without touching the filesystem."""
    return SyncState(
        profile=validate_profile_name(profile),
        output_root=_canonical_output_root(output_root),
        sources=(),
    )


def _require_object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    return value


def _require_exact_fields(
    value: dict[str, Any], expected: frozenset[str], label: str
) -> None:
    if frozenset(value) != expected:
        raise ValueError(f"{label} fields do not match schema v4")


def _source_to_document(source: PreviousSource) -> dict[str, object]:
    key = source.key
    stored: dict[str, object] | None = None
    if source.stored_file is not None:
        stored = {
            "output_relative_path": source.stored_file.output_relative_path.as_posix(),
            "written_sha256": source.stored_file.written_sha256,
            "managed_by_sync": source.stored_file.managed_by_sync,
        }
    return {
        "key": {
            "provider": key.provider,
            "instance": key.instance,
            "course_id": key.course_id,
            "kind": key.kind.value,
            "remote_id": key.remote_id,
        },
        "signature": source.signature,
        "stored_file": stored,
        "retry_required": source.retry_required,
    }


def _initialized_to_document(source: InitializedSource) -> dict[str, str]:
    return {
        "course_key": source.course_key,
        "provider": source.provider,
        "instance": source.instance,
        "course_id": source.course_id,
    }


def _policy_skip_to_document(skip: PolicySkipState) -> dict[str, object]:
    return {
        "course_key": skip.course_key,
        "key": _source_to_document(PreviousSource(skip.key, skip.signature))["key"],
        "signature": skip.signature,
        "reason": skip.reason.value,
        "policy_fingerprint": skip.policy_fingerprint,
    }


def _state_bytes(state: SyncState) -> bytes:
    if not isinstance(state, SyncState):
        raise ValueError("state must be a SyncState")
    document = {
        "schema_version": CURRENT_STATE_SCHEMA,
        "profile": state.profile,
        "output_root": str(state.output_root),
        "sources": [
            _source_to_document(source)
            for source in sorted(state.sources, key=_source_sort_key)
        ],
        "initialized_sources": [
            _initialized_to_document(source)
            for source in sorted(state.initialized_sources)
        ],
        "policy_skips": [
            _policy_skip_to_document(skip)
            for skip in sorted(state.policy_skips, key=lambda item: (item.course_key, _source_sort_key(PreviousSource(item.key, item.signature))))
        ],
    }
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _parse_source(value: object) -> PreviousSource:
    source = _require_object(value, "source")
    _require_exact_fields(source, _SOURCE_FIELDS, "source")
    key_data = _require_object(source["key"], "source key")
    _require_exact_fields(key_data, _KEY_FIELDS, "source key")
    try:
        key = SourceKey(
            provider=key_data["provider"],
            instance=key_data["instance"],
            course_id=key_data["course_id"],
            kind=RemoteKind(key_data["kind"]),
            remote_id=key_data["remote_id"],
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid source key: {exc}") from exc

    stored_value = source["stored_file"]
    stored_file: StoredFile | None = None
    if stored_value is not None:
        stored = _require_object(stored_value, "stored file")
        _require_exact_fields(stored, _FILE_FIELDS, "stored file")
        path_value = stored["output_relative_path"]
        if not isinstance(path_value, str):
            raise ValueError("stored output_relative_path must be a string")
        stored_file = StoredFile(
            output_relative_path=PurePosixPath(path_value),
            written_sha256=stored["written_sha256"],
            managed_by_sync=stored["managed_by_sync"],
        )

    return PreviousSource(
        key=key,
        signature=source["signature"],
        stored_file=stored_file,
        retry_required=source["retry_required"],
    )


def _parse_key(value: object) -> SourceKey:
    key_data = _require_object(value, "source key")
    _require_exact_fields(key_data, _KEY_FIELDS, "source key")
    return SourceKey(
        provider=key_data["provider"], instance=key_data["instance"],
        course_id=key_data["course_id"], kind=RemoteKind(key_data["kind"]),
        remote_id=key_data["remote_id"],
    )


def _parse_initialized(value: object) -> InitializedSource:
    data = _require_object(value, "initialized source")
    _require_exact_fields(data, _INITIALIZED_FIELDS, "initialized source")
    return InitializedSource(data["course_key"], data["provider"], data["instance"], data["course_id"])


def _parse_policy_skip(value: object) -> PolicySkipState:
    data = _require_object(value, "policy skip")
    if frozenset(data) not in {_POLICY_SKIP_FIELDS, _LEGACY_POLICY_SKIP_FIELDS}:
        raise ValueError("policy skip fields do not match schema v5")
    return PolicySkipState(
        course_key=data["course_key"],
        key=_parse_key(data["key"]),
        signature=data["signature"],
        reason=PolicySkipReason(data["reason"]),
        policy_fingerprint=data.get("policy_fingerprint"),
    )


def _decode_state(payload: bytes) -> SyncState:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateLoadError("invalid_json", "State is not valid UTF-8 JSON") from exc
    try:
        document = _require_object(value, "state")
        schema = document.get("schema_version")
        if schema not in {4, CURRENT_STATE_SCHEMA} or isinstance(schema, bool):
            raise StateLoadError(
                "unsupported_schema",
                f"Unsupported state schema {schema!r}; expected {CURRENT_STATE_SCHEMA}",
            )
        _require_exact_fields(
            document, _V4_ROOT_FIELDS if schema == 4 else _ROOT_FIELDS, "state"
        )
        profile = document["profile"]
        output_root = document["output_root"]
        sources = document["sources"]
        if not isinstance(profile, str):
            raise ValueError("profile must be a string")
        if not isinstance(output_root, str):
            raise ValueError("output_root must be a string")
        if not isinstance(sources, list):
            raise ValueError("sources must be an array")
        parsed_sources = tuple(_parse_source(source) for source in sources)
        if schema == 4:
            # v4 did not bind source records to logical courses, so inferring a
            # checkpoint would be unsafe after reconfiguration.
            initialized = ()
            skips: tuple[PolicySkipState, ...] = ()
        else:
            initialized_values = document["initialized_sources"]
            skip_values = document["policy_skips"]
            if not isinstance(initialized_values, list) or not isinstance(skip_values, list):
                raise ValueError("initialized_sources and policy_skips must be arrays")
            initialized = tuple(_parse_initialized(value) for value in initialized_values)
            skips = tuple(_parse_policy_skip(value) for value in skip_values)
        return SyncState(
            profile=profile,
            output_root=Path(output_root),
            sources=parsed_sources,
            initialized_sources=initialized,
            policy_skips=skips,
        )
    except StateLoadError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise StateLoadError("invalid_state", f"State validation failed: {exc}") from exc


def _validate_namespace(state: SyncState, *, profile: str, output_root: Path) -> None:
    expected_profile = validate_profile_name(profile)
    expected_output = _canonical_output_root(output_root)
    if state.profile != expected_profile:
        raise StateLoadError(
            "profile_mismatch",
            "State belongs to a different profile; synchronization is blocked",
        )
    if state.output_root != expected_output:
        raise StateLoadError(
            "output_mismatch",
            "State belongs to a different output root; synchronization is blocked",
        )


def load_state(path: Path, *, profile: str, output_root: Path) -> SyncState:
    """Load and validate v4 state; backup recovery is deliberately explicit."""
    path = Path(path)
    try:
        payload = path.read_bytes()
    except FileNotFoundError as exc:
        raise StateLoadError("missing", f"State file does not exist: {path}") from exc
    except OSError as exc:
        raise StateLoadError("unreadable", f"Cannot read state file: {path}") from exc
    state = _decode_state(payload)
    _validate_namespace(state, profile=profile, output_root=output_root)
    return state


def _atomic_write(payload: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=destination.parent,
            prefix=".canvas-ed-mcp-state-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_create(payload: bytes, destination: Path) -> None:
    """Publish a new state without overwriting a concurrently created file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=destination.parent,
            prefix=".canvas-ed-mcp-state-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination, follow_symlinks=False)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_state(
    state: SyncState,
    *,
    path: Path,
    backup_path: Path,
    lock: SyncLock | None = None,
) -> None:
    """Atomically save v4 state after backing up the previous readable state."""
    if not isinstance(state, SyncState):
        raise ValueError("state must be a SyncState")
    _require_state_lock(
        lock,
        profile=state.profile,
        output_root=state.output_root,
        operation="saving state",
    )
    path = Path(path)
    backup_path = Path(backup_path)
    if path.resolve(strict=False) == backup_path.resolve(strict=False):
        raise ValueError("state and backup paths must be different")
    payload = _state_bytes(state)
    decoded = _decode_state(payload)
    _validate_namespace(
        decoded,
        profile=state.profile,
        output_root=state.output_root,
    )

    if path.exists():
        try:
            previous_payload = path.read_bytes()
        except OSError as exc:
            raise StateLoadError("unreadable", f"Cannot read existing state: {path}") from exc
        previous = _decode_state(previous_payload)
        _validate_namespace(
            previous,
            profile=state.profile,
            output_root=state.output_root,
        )
        _atomic_write(previous_payload, backup_path)
    _atomic_write(payload, path)


def _legacy_source_key(
    value: str, provider_instances: Mapping[str, str]
) -> SourceKey:
    parts = value.split(":", 3)
    if len(parts) != 4:
        raise ValueError("legacy source key must have four components")
    provider, course_id, kind_value, remote_id = parts
    try:
        instance = provider_instances[provider]
    except KeyError as exc:
        raise ValueError(f"no instance supplied for legacy provider {provider!r}") from exc
    try:
        kind = RemoteKind(kind_value)
    except ValueError as exc:
        raise ValueError(f"unsupported legacy source kind {kind_value!r}") from exc
    return SourceKey(
        provider=provider,
        instance=instance,
        course_id=course_id,
        kind=kind,
        remote_id=remote_id,
    )


def _require_state_lock(
    lock: SyncLock | None,
    *,
    profile: str,
    output_root: Path,
    operation: str,
) -> None:
    if (
        not isinstance(lock, SyncLock)
        or not lock.held
        or lock.identity.scope is not LockScope.STATE
        or lock.identity.profile != validate_profile_name(profile)
        or lock.identity.output_root != _canonical_output_root(output_root)
    ):
        raise ValueError(f"{operation} requires a held matching state lock")


def _legacy_stored_file(entry: dict[str, Any], output_root: Path) -> StoredFile | None:
    managed = entry.get("managed_by_sync")
    if managed is not None and not isinstance(managed, bool):
        raise ValueError("legacy managed_by_sync must be a boolean when present")
    if managed is not True:
        return None
    local_path = entry.get("local_path")
    written_hash = entry.get("local_sha256")
    if not isinstance(local_path, str):
        raise ValueError("managed legacy source requires local_path")
    candidate = Path(local_path)
    if not candidate.is_absolute():
        raise ValueError("managed legacy local_path must be absolute")
    try:
        relative = candidate.resolve(strict=False).relative_to(output_root)
    except ValueError as exc:
        raise ValueError("managed legacy path escapes the selected output root") from exc
    return StoredFile(
        output_relative_path=PurePosixPath(*relative.parts),
        written_sha256=written_hash,
        managed_by_sync=True,
    )


def _migrate_v3_document(
    value: object,
    *,
    profile: str,
    output_root: Path,
    provider_instances: Mapping[str, str],
) -> SyncState:
    document = _require_object(value, "legacy state")
    if document.get("version") != 3 or isinstance(document.get("version"), bool):
        raise StateMigrationError(
            "unsupported_schema", "Only legacy state version 3 can be migrated"
        )
    outputs = _require_object(document.get("outputs"), "legacy outputs")
    canonical_output = _canonical_output_root(output_root)
    matching_scopes: list[dict[str, Any]] = []
    for key, raw_scope in outputs.items():
        try:
            key_root = _canonical_output_root(Path(key))
        except ValueError as exc:
            raise StateMigrationError(
                "invalid_legacy_state", f"Invalid legacy output scope: {exc}"
            ) from exc
        if key_root == canonical_output:
            matching_scopes.append(_require_object(raw_scope, "legacy output scope"))
    if not matching_scopes:
        raise StateMigrationError(
            "missing_output_scope",
            "Legacy state has no baseline for the selected output root",
        )
    if len(matching_scopes) != 1:
        raise StateMigrationError(
            "ambiguous_output_scope",
            "Legacy state contains duplicate equivalent output scopes",
        )

    scope = matching_scopes[0]
    try:
        scope_root = scope.get("output_root")
        if not isinstance(scope_root, str) or _canonical_output_root(
            Path(scope_root)
        ) != canonical_output:
            raise ValueError("legacy scope output_root does not match its namespace")
        source_values = _require_object(scope.get("sources"), "legacy sources")
        migrated: list[PreviousSource] = []
        for legacy_key, raw_entry in source_values.items():
            entry = _require_object(raw_entry, "legacy source")
            retry_required = entry.get("retry_required", False)
            if not isinstance(retry_required, bool):
                raise ValueError("legacy retry_required must be a boolean")
            migrated.append(
                PreviousSource(
                    key=_legacy_source_key(legacy_key, provider_instances),
                    signature=entry.get("signature"),
                    stored_file=_legacy_stored_file(entry, canonical_output),
                    retry_required=retry_required,
                )
            )
        return SyncState(
            profile=validate_profile_name(profile),
            output_root=canonical_output,
            sources=tuple(sorted(migrated, key=_source_sort_key)),
        )
    except StateMigrationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise StateMigrationError(
            "invalid_legacy_state", f"Legacy state validation failed: {exc}"
        ) from exc


def migrate_v3_state_file(
    legacy_path: Path,
    *,
    destination: Path,
    backup_path: Path,
    profile: str,
    output_root: Path,
    provider_instances: Mapping[str, str],
    lock: SyncLock | None = None,
) -> SyncState:
    """Migrate one selected v3 output scope without modifying the legacy file."""
    legacy_path = Path(legacy_path)
    destination = Path(destination)
    backup_path = Path(backup_path)
    _require_state_lock(
        lock,
        profile=profile,
        output_root=output_root,
        operation="migrating state",
    )
    if destination.exists():
        raise StateMigrationError(
            "destination_exists", "Refusing to overwrite an existing v4 state"
        )
    if destination.resolve(strict=False) == backup_path.resolve(strict=False):
        raise StateMigrationError(
            "invalid_paths", "Migrated state and backup paths must be different"
        )
    try:
        payload = legacy_path.read_bytes()
    except OSError as exc:
        raise StateMigrationError(
            "unreadable", f"Cannot read legacy state: {legacy_path}"
        ) from exc
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateMigrationError(
            "invalid_json", "Legacy state is not valid UTF-8 JSON"
        ) from exc
    try:
        try:
            state = _migrate_v3_document(
                value,
                profile=profile,
                output_root=output_root,
                provider_instances=provider_instances,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise StateMigrationError(
                "invalid_legacy_state", f"Legacy state validation failed: {exc}"
            ) from exc
        _atomic_create(_state_bytes(state), destination)
    except StateMigrationError:
        raise
    except FileExistsError as exc:
        raise StateMigrationError(
            "destination_exists",
            "A v4 state appeared during migration and was preserved",
        ) from exc
    except OSError as exc:
        raise StateMigrationError(
            "write_failed", "Could not atomically write migrated state"
        ) from exc
    return state
