from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.sync.model import (
    PreviousSource,
    RemoteKind,
    SourceKey,
    StoredFile,
)
from canvas_ed_mcp.sync.locking import SyncLock
from canvas_ed_mcp.sync.state import (
    ArchivedSourceState,
    CURRENT_STATE_SCHEMA,
    StateLoadError,
    StateMigrationError,
    SyncState,
    InitializedSource,
    PolicySkipState,
    load_state,
    migrate_v3_state_file as migrate_v3_state_file_v4,
    new_state,
    save_state as save_state_v4,
)
from canvas_ed_mcp.sync.policy import PolicySkipReason


MANAGED_HASH = "a" * 64
PROVIDER_INSTANCES = {
    "canvas": "canvas.example.edu",
    "ed": "ed.example.edu",
}


def save_state(
    state: SyncState, *, path: Path, backup_path: Path
) -> None:
    lock = SyncLock.for_state(
        lock_dir=path.parent / "locks",
        profile=state.profile,
        output_root=state.output_root,
    )
    with lock:
        save_state_v4(state, path=path, backup_path=backup_path, lock=lock)


def migrate_v3_state_file(
    legacy_path: Path,
    *,
    destination: Path,
    backup_path: Path,
    profile: str,
    output_root: Path,
    provider_instances: dict[str, str],
) -> SyncState:
    lock = SyncLock.for_state(
        lock_dir=destination.parent / "locks",
        profile=profile,
        output_root=output_root,
    )
    with lock:
        return migrate_v3_state_file_v4(
            legacy_path,
            destination=destination,
            backup_path=backup_path,
            profile=profile,
            output_root=output_root,
            provider_instances=provider_instances,
            lock=lock,
        )


def source_key(
    *,
    provider: str = "canvas",
    course_id: str = "101",
    kind: RemoteKind = RemoteKind.FILE,
    remote_id: str = "7",
) -> SourceKey:
    return SourceKey(
        provider=provider,
        instance=PROVIDER_INSTANCES[provider],
        course_id=course_id,
        kind=kind,
        remote_id=remote_id,
    )


def previous_source(signature: str = "revision-1") -> PreviousSource:
    return PreviousSource(
        key=source_key(),
        signature=signature,
        stored_file=StoredFile(
            PurePosixPath("COURSE/Week 1/notes.pdf"),
            MANAGED_HASH,
            True,
        ),
    )


def legacy_v3(output_root: Path) -> dict[str, object]:
    return {
        "version": 3,
        "outputs": {
            str(output_root): {
                "output_root": str(output_root),
                "sources": {
                    "canvas:101:file:7": {
                        "signature": "canvas-revision",
                        "metadata": {"name": "notes.pdf"},
                        "local_path": str(output_root / "COURSE" / "Week 1" / "notes.pdf"),
                        "local_sha256": MANAGED_HASH,
                        "managed_by_sync": True,
                    },
                    "ed:202:lesson:9": {
                        "signature": "ed-revision",
                        "local_path": str(output_root / "COURSE" / "Lesson.html"),
                    },
                },
            }
        },
    }


class StateModelAndPersistenceTests(unittest.TestCase):
    def test_v5_checkpoint_migrates_to_generation_zero_without_folder_guess(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = (base / "Courses").resolve()
            path = base / "state.json"
            path.write_text(json.dumps({
                "schema_version": 5,
                "profile": "default",
                "output_root": str(output),
                "sources": [],
                "initialized_sources": [{
                    "course_key": "logical-202", "provider": "ed",
                    "instance": "ed.example.edu", "course_id": "202",
                }],
                "policy_skips": [],
            }), encoding="utf-8")

            loaded = load_state(path, profile="default", output_root=output)

        checkpoint = loaded.initialized_sources[0]
        self.assertEqual(CURRENT_STATE_SCHEMA, loaded.schema_version)
        self.assertEqual(0, checkpoint.reconcile_generation)
        self.assertIsNone(checkpoint.course_folder)

    def test_archived_source_state_round_trips_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = (base / "Courses").resolve()
            archived = ArchivedSourceState(
                "old-key", "ed", "ed.example.edu", "202", 3, "OLD",
                (previous_source("archived"),), (),
            )
            state = SyncState(
                "default", output, (), archived_sources=(archived,)
            )
            path = base / "state.json"
            backup = base / "backup.json"
            save_state(state, path=path, backup_path=backup)
            first = path.read_bytes()
            loaded = load_state(path, profile="default", output_root=output)
            save_state(loaded, path=path, backup_path=backup)
            second = path.read_bytes()

        self.assertEqual((archived,), loaded.archived_sources)
        self.assertEqual(first, second)

    def test_new_state_is_profile_and_output_namespaced_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "Courses"
            state = new_state(profile="semester-2", output_root=output)

        self.assertEqual(CURRENT_STATE_SCHEMA, state.schema_version)
        self.assertEqual("semester-2", state.profile)
        self.assertEqual(output.resolve(), state.output_root)
        self.assertEqual((), state.sources)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            state.profile = "changed"  # type: ignore[misc]

    def test_v5_persists_empty_source_checkpoint_and_policy_skip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            state = SyncState(
                "default", output.resolve(), (),
                initialized_sources=(InitializedSource("logical-202", "ed", "ed.example.edu", "202"),),
                policy_skips=(PolicySkipState(
                    "logical-202",
                    source_key(provider="ed", course_id="202", kind=RemoteKind.RESOURCE, remote_id="skip"),
                    "revision", PolicySkipReason.KNOWN_SIZE_LIMIT_EXCEEDED,
                ),),
            )
            path = base / "state.json"
            save_state(state, path=path, backup_path=base / "backup.json")
            loaded = load_state(path, profile="default", output_root=output)

        self.assertTrue(loaded.initialized_for(course_key="logical-202", provider="ed", instance="ed.example.edu", course_id="202"))
        self.assertEqual(state, loaded)

    def test_v5_legacy_policy_skip_without_fingerprint_loads_for_safe_reevaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            state = SyncState(
                "default", output.resolve(), (),
                policy_skips=(PolicySkipState(
                    "logical-202",
                    source_key(provider="ed", course_id="202", kind=RemoteKind.RESOURCE, remote_id="skip"),
                    "revision", PolicySkipReason.KNOWN_SIZE_LIMIT_EXCEEDED,
                    "a" * 64,
                ),),
            )
            path = base / "state.json"
            save_state(state, path=path, backup_path=base / "backup.json")
            document = json.loads(path.read_text(encoding="utf-8"))
            document["policy_skips"][0].pop("policy_fingerprint")
            path.write_text(json.dumps(document), encoding="utf-8")
            loaded = load_state(path, profile="default", output_root=output)

        self.assertIsNone(loaded.policy_skips[0].policy_fingerprint)

    def test_v4_document_loads_with_conservative_derived_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = (base / "Courses").resolve()
            path = base / "state.json"
            path.write_text(json.dumps({
                "schema_version": 4,
                "profile": "default",
                "output_root": str(output),
                "sources": [{
                    "key": {"provider": "ed", "instance": "ed.example.edu", "course_id": "202", "kind": "resource", "remote_id": "one"},
                    "signature": "revision", "stored_file": None, "retry_required": False,
                }],
            }), encoding="utf-8")
            loaded = load_state(path, profile="default", output_root=output)

        self.assertEqual(CURRENT_STATE_SCHEMA, loaded.schema_version)
        self.assertFalse(loaded.initialized_sources)

    def test_save_requires_a_held_matching_state_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = (base / "Courses").resolve()
            state = new_state(profile="default", output_root=output)
            path = base / "state-v4.json"
            backup = base / "state-v4.backup.json"
            with self.assertRaises(ValueError):
                save_state_v4(state, path=path, backup_path=backup)

            unheld = SyncLock.for_state(
                lock_dir=base / "locks",
                profile="default",
                output_root=output,
            )
            with self.assertRaises(ValueError):
                save_state_v4(state, path=path, backup_path=backup, lock=unheld)

            course_lock = SyncLock.for_course(
                lock_dir=base / "locks",
                profile="default",
                output_root=output,
                course_key="course-101",
                course_root=PurePosixPath("COURSE"),
            )
            with course_lock, self.assertRaises(ValueError):
                save_state_v4(
                    state,
                    path=path,
                    backup_path=backup,
                    lock=course_lock,
                )

            wrong_profile = SyncLock.for_state(
                lock_dir=base / "locks",
                profile="other",
                output_root=output,
            )
            with wrong_profile, self.assertRaises(ValueError):
                save_state_v4(
                    state,
                    path=path,
                    backup_path=backup,
                    lock=wrong_profile,
                )

            legacy_path = base / "state-v3.json"
            legacy_path.write_text(json.dumps(legacy_v3(output)), encoding="utf-8")
            with self.assertRaises(ValueError):
                migrate_v3_state_file_v4(
                    legacy_path,
                    destination=path,
                    backup_path=backup,
                    profile="default",
                    output_root=output,
                    provider_instances=PROVIDER_INSTANCES,
                )
            self.assertFalse(path.exists())

    def test_state_round_trip_is_deterministic_and_supports_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            path = base / "state-v4.json"
            backup = base / "state-v4.backup.json"
            state = SyncState(
                profile="default",
                output_root=output.resolve(),
                sources=(previous_source(),),
            )
            save_state(state, path=path, backup_path=backup)
            first_bytes = path.read_bytes()
            loaded = load_state(path, profile="default", output_root=output)
            save_state(loaded, path=path, backup_path=backup)

            self.assertEqual(first_bytes, path.read_bytes())
            self.assertEqual(previous_source(), loaded.previous_for(source_key()))
            self.assertTrue(backup.is_file())

    def test_load_rejects_profile_and_output_namespace_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            path = base / "state-v4.json"
            backup = base / "state-v4.backup.json"
            save_state(
                new_state(profile="default", output_root=output),
                path=path,
                backup_path=backup,
            )
            cases = (
                ("other", output, "profile_mismatch"),
                ("default", base / "Other", "output_mismatch"),
            )
            for profile, expected_output, category in cases:
                with self.subTest(category=category), self.assertRaises(StateLoadError) as caught:
                    load_state(path, profile=profile, output_root=expected_output)
                self.assertEqual(category, caught.exception.category)

    def test_load_fails_closed_for_missing_corrupt_or_unknown_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            path = base / "state-v4.json"

            with self.assertRaises(StateLoadError) as missing:
                load_state(path, profile="default", output_root=output)
            self.assertEqual("missing", missing.exception.category)

            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaises(StateLoadError) as corrupt:
                load_state(path, profile="default", output_root=output)
            self.assertEqual("invalid_json", corrupt.exception.category)

            path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
            with self.assertRaises(StateLoadError) as unknown:
                load_state(path, profile="default", output_root=output)
            self.assertEqual("unsupported_schema", unknown.exception.category)

    def test_second_save_backs_up_the_previous_readable_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            path = base / "state-v4.json"
            backup = base / "state-v4.backup.json"
            first = SyncState("default", output.resolve(), (previous_source("first"),))
            second = SyncState("default", output.resolve(), (previous_source("second"),))

            save_state(first, path=path, backup_path=backup)
            save_state(second, path=path, backup_path=backup)

            self.assertEqual(
                "second",
                load_state(path, profile="default", output_root=output).sources[0].signature,
            )
            self.assertEqual(
                "first",
                load_state(backup, profile="default", output_root=output).sources[0].signature,
            )

    def test_primary_commit_failure_preserves_previous_state_and_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            path = base / "state-v4.json"
            backup = base / "state-v4.backup.json"
            first = SyncState("default", output.resolve(), (previous_source("first"),))
            second = SyncState("default", output.resolve(), (previous_source("second"),))
            save_state(first, path=path, backup_path=backup)
            real_replace = os.replace

            def fail_primary(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                if Path(target) == path:
                    raise OSError("synthetic primary commit failure")
                real_replace(source, target)

            with patch("canvas_ed_mcp.sync.state.os.replace", side_effect=fail_primary):
                with self.assertRaises(OSError):
                    save_state(second, path=path, backup_path=backup)

            self.assertEqual(
                "first",
                load_state(path, profile="default", output_root=output).sources[0].signature,
            )
            self.assertEqual([], list(base.glob(".canvas-ed-mcp-state-*.tmp")))

    def test_backup_failure_aborts_before_primary_is_touched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            path = base / "state-v4.json"
            backup = base / "state-v4.backup.json"
            first = SyncState("default", output.resolve(), (previous_source("first"),))
            second = SyncState("default", output.resolve(), (previous_source("second"),))
            save_state(first, path=path, backup_path=backup)
            real_replace = os.replace

            def fail_backup(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
                if Path(target) == backup:
                    raise OSError("synthetic backup failure")
                real_replace(source, target)

            with patch("canvas_ed_mcp.sync.state.os.replace", side_effect=fail_backup):
                with self.assertRaises(OSError):
                    save_state(second, path=path, backup_path=backup)

            self.assertEqual(
                "first",
                load_state(path, profile="default", output_root=output).sources[0].signature,
            )
            self.assertEqual([], list(base.glob(".canvas-ed-mcp-state-*.tmp")))

    def test_corrupt_primary_does_not_silently_fall_back_to_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = base / "Courses"
            path = base / "state-v4.json"
            backup = base / "state-v4.backup.json"
            state = new_state(profile="default", output_root=output)
            save_state(state, path=backup, backup_path=base / "unused-backup.json")
            path.write_text("corrupt", encoding="utf-8")

            with self.assertRaises(StateLoadError):
                load_state(path, profile="default", output_root=output)
            self.assertEqual(
                state,
                load_state(backup, profile="default", output_root=output),
            )


class V3MigrationTests(unittest.TestCase):
    def test_v3_migration_selects_one_output_scope_and_preserves_safe_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = (base / "Courses").resolve()
            other_output = (base / "Other").resolve()
            document = legacy_v3(output)
            document["outputs"][str(other_output)] = {  # type: ignore[index]
                "output_root": str(other_output),
                "sources": {"canvas:999:file:1": {"signature": "other"}},
            }
            legacy_path = base / "state-v3.json"
            destination = base / "profile" / "state-v4.json"
            backup = base / "profile" / "state-v4.backup.json"
            legacy_path.write_text(json.dumps(document), encoding="utf-8")
            legacy_bytes = legacy_path.read_bytes()

            migrated = migrate_v3_state_file(
                legacy_path,
                destination=destination,
                backup_path=backup,
                profile="default",
                output_root=output,
                provider_instances=PROVIDER_INSTANCES,
            )

            self.assertEqual(legacy_bytes, legacy_path.read_bytes())
            self.assertEqual(2, len(migrated.sources))
            canvas = migrated.previous_for(source_key())
            self.assertIsNotNone(canvas)
            self.assertEqual(
                PurePosixPath("COURSE/Week 1/notes.pdf"),
                canvas.stored_file.output_relative_path,  # type: ignore[union-attr]
            )
            ed = migrated.previous_for(
                source_key(
                    provider="ed",
                    course_id="202",
                    kind=RemoteKind.LESSON,
                    remote_id="9",
                )
            )
            self.assertIsNotNone(ed)
            self.assertIsNone(ed.stored_file)  # type: ignore[union-attr]
            self.assertIsNone(
                migrated.previous_for(source_key(course_id="999", remote_id="1"))
            )
            self.assertEqual(
                migrated,
                load_state(destination, profile="default", output_root=output),
            )

    def test_missing_output_scope_fails_before_destination_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = (base / "Courses").resolve()
            legacy_path = base / "state-v3.json"
            destination = base / "state-v4.json"
            legacy_path.write_text(
                json.dumps({"version": 3, "outputs": {}}),
                encoding="utf-8",
            )

            with self.assertRaises(StateMigrationError) as caught:
                migrate_v3_state_file(
                    legacy_path,
                    destination=destination,
                    backup_path=base / "backup.json",
                    profile="default",
                    output_root=output,
                    provider_instances=PROVIDER_INSTANCES,
                )
            self.assertEqual("missing_output_scope", caught.exception.category)
            self.assertFalse(destination.exists())

    def test_invalid_v3_inputs_fail_closed_without_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = (base / "Courses").resolve()
            outside = (base / "Outside" / "notes.pdf").resolve()
            cases: list[tuple[str, object]] = []

            unknown_schema = legacy_v3(output)
            unknown_schema["version"] = 2
            cases.append(("unsupported legacy schema", unknown_schema))

            malformed_key = legacy_v3(output)
            malformed_key["outputs"][str(output)]["sources"] = {  # type: ignore[index]
                "canvas:101:quiz:7": {"signature": "revision"}
            }
            cases.append(("unknown source kind", malformed_key))

            invalid_hash = legacy_v3(output)
            invalid_hash["outputs"][str(output)]["sources"][  # type: ignore[index]
                "canvas:101:file:7"
            ]["local_sha256"] = "not-a-hash"
            cases.append(("invalid managed hash", invalid_hash))

            outside_path = legacy_v3(output)
            outside_path["outputs"][str(output)]["sources"][  # type: ignore[index]
                "canvas:101:file:7"
            ]["local_path"] = str(outside)
            cases.append(("managed path outside output", outside_path))

            non_boolean_ownership = legacy_v3(output)
            non_boolean_ownership["outputs"][str(output)]["sources"][  # type: ignore[index]
                "canvas:101:file:7"
            ]["managed_by_sync"] = 1
            cases.append(("non-boolean ownership", non_boolean_ownership))

            cases.append(("invalid outputs shape", {"version": 3, "outputs": []}))

            cases.append(("invalid json", "{not-json"))

            for index, (name, document) in enumerate(cases):
                with self.subTest(name=name):
                    legacy_path = base / f"legacy-{index}.json"
                    destination = base / f"state-{index}.json"
                    if isinstance(document, str):
                        legacy_path.write_text(document, encoding="utf-8")
                    else:
                        legacy_path.write_text(json.dumps(document), encoding="utf-8")
                    with self.assertRaises(StateMigrationError):
                        migrate_v3_state_file(
                            legacy_path,
                            destination=destination,
                            backup_path=base / f"backup-{index}.json",
                            profile="default",
                            output_root=output,
                            provider_instances=PROVIDER_INSTANCES,
                        )
                    self.assertFalse(destination.exists())

    def test_existing_v4_destination_is_never_overwritten_by_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = (base / "Courses").resolve()
            legacy_path = base / "state-v3.json"
            destination = base / "state-v4.json"
            backup = base / "state-v4.backup.json"
            legacy_path.write_text(json.dumps(legacy_v3(output)), encoding="utf-8")
            save_state(
                new_state(profile="default", output_root=output),
                path=destination,
                backup_path=backup,
            )
            original = destination.read_bytes()

            with self.assertRaises(StateMigrationError) as caught:
                migrate_v3_state_file(
                    legacy_path,
                    destination=destination,
                    backup_path=backup,
                    profile="default",
                    output_root=output,
                    provider_instances=PROVIDER_INSTANCES,
                )
            self.assertEqual("destination_exists", caught.exception.category)
            self.assertEqual(original, destination.read_bytes())

    def test_destination_created_during_migration_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output = (base / "Courses").resolve()
            legacy_path = base / "state-v3.json"
            destination = base / "state-v4.json"
            legacy_path.write_text(json.dumps(legacy_v3(output)), encoding="utf-8")

            def create_racing_destination(
                source: str | os.PathLike[str],
                target: str | os.PathLike[str],
                **_: object,
            ) -> None:
                Path(target).write_bytes(b"concurrent-state")
                raise FileExistsError(target)

            with patch(
                "canvas_ed_mcp.sync.state.os.link",
                side_effect=create_racing_destination,
            ):
                with self.assertRaises(StateMigrationError) as caught:
                    migrate_v3_state_file(
                        legacy_path,
                        destination=destination,
                        backup_path=base / "backup.json",
                        profile="default",
                        output_root=output,
                        provider_instances=PROVIDER_INSTANCES,
                    )
            self.assertEqual("destination_exists", caught.exception.category)
            self.assertEqual(b"concurrent-state", destination.read_bytes())
            self.assertEqual([], list(base.glob(".canvas-ed-mcp-state-*.tmp")))


if __name__ == "__main__":
    unittest.main()
