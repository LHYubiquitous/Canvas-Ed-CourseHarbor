from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    ContentSelection,
    CourseConfig,
    CourseSource,
    PlatformsConfig,
    load_config,
    save_config,
)
from canvas_ed_mcp.configure_flow import build_configure_plan, commit_configure
from canvas_ed_mcp.sync.locking import SyncLock
from canvas_ed_mcp.sync.model import LocalStatus
from canvas_ed_mcp.sync.state import load_state, migrate_v3_state_file
from canvas_ed_mcp.sync.storage import CourseFileIndex, install_new_atomic


class UpgradeReconfigureRollbackRehearsalTests(unittest.TestCase):
    def test_synthetic_rehearsal_preserves_user_output_and_rollback_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            output_root = (base / "output").resolve()
            course_root = PurePosixPath("SAFE1000")
            course_file = output_root / course_root / "Week 1" / "notes.txt"
            course_file.parent.mkdir(parents=True)
            managed_bytes = b"original sync-managed version"
            managed_hash = hashlib.sha256(managed_bytes).hexdigest()
            course_file.write_bytes(managed_bytes)

            course = CourseConfig(
                key="canvas:canvas.example.edu:101",
                folder=course_root.as_posix(),
                confirmed_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
                sources=(
                    CourseSource(
                        provider="canvas",
                        instance="canvas.example.edu",
                        course_id="101",
                        code="SAFE1000",
                        name="Synthetic course",
                        term="2099 T1",
                    ),
                ),
            )
            baseline = AppConfig(
                profile="default",
                output_root=output_root,
                platforms=PlatformsConfig(
                    canvas=CanvasPlatformConfig(
                        enabled=True,
                        base_url="https://canvas.example.edu/api/v1",
                        credential="env:SYNTHETIC_CANVAS_TOKEN",
                    )
                ),
                courses=(course,),
            )
            config_path = base / "config" / "default.toml"
            save_config(baseline, config_path)

            legacy_path = base / "state-v3.json"
            state_path = base / "state" / "state-v4.json"
            backup_path = base / "state" / "state-v4.backup.json"
            legacy_path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "outputs": {
                            str(output_root): {
                                "output_root": str(output_root),
                                "sources": {
                                    "canvas:101:file:7": {
                                        "signature": "revision-1",
                                        "local_path": str(course_file),
                                        "local_sha256": managed_hash,
                                        "managed_by_sync": True,
                                    }
                                },
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            legacy_bytes = legacy_path.read_bytes()

            # Simulate a user edit before upgrade; every later operation must preserve it.
            course_file.write_bytes(b"USER EDIT - KEEP")
            output_snapshot = self._snapshot(output_root)

            lock_dir = base / "locks"
            state_lock = SyncLock.for_state(
                lock_dir=lock_dir,
                profile="default",
                output_root=output_root,
            )
            with state_lock:
                migrated = migrate_v3_state_file(
                    legacy_path,
                    destination=state_path,
                    backup_path=backup_path,
                    profile="default",
                    output_root=output_root,
                    provider_instances={"canvas": "canvas.example.edu"},
                    lock=state_lock,
                )

            self.assertEqual(legacy_bytes, legacy_path.read_bytes())
            self.assertEqual(
                migrated,
                load_state(state_path, profile="default", output_root=output_root),
            )
            stored = migrated.sources[0].stored_file
            self.assertIsNotNone(stored)
            observation = CourseFileIndex.scan(
                output_root=output_root,
                course_root=course_root,
            ).observe(stored)  # type: ignore[arg-type]
            self.assertEqual(LocalStatus.MODIFIED, observation.status)
            migrated_state_bytes = state_path.read_bytes()

            changed_course = course.model_copy(
                update={"content": ContentSelection(pages=False, files=True)}
            )
            proposed = baseline.model_copy(update={"courses": (changed_course,)})
            plan = build_configure_plan(baseline, proposed, new_course_modes={})
            self.assertTrue(commit_configure(plan, config_path, confirmed=True))
            self.assertEqual(proposed, load_config(config_path))
            self.assertEqual(migrated_state_bytes, state_path.read_bytes())
            self.assertEqual(output_snapshot, self._snapshot(output_root))

            candidate = base / "candidate.txt"
            candidate.write_bytes(b"synthetic remote update")
            course_lock = SyncLock.for_course(
                lock_dir=lock_dir,
                profile="default",
                output_root=output_root,
                course_key=course.key,
                course_root=course_root,
            )
            with course_lock, self.assertRaises(FileExistsError):
                install_new_atomic(
                    candidate,
                    output_root=output_root,
                    course_root=course_root,
                    destination=PurePosixPath("SAFE1000/Week 1/notes.txt"),
                    course_key=course.key,
                    lock=course_lock,
                )
            self.assertEqual([], list(course_file.parent.glob(".canvas-ed-mcp-*.staging")))

            changed = load_config(config_path)
            rollback = build_configure_plan(changed, baseline, new_course_modes={})
            self.assertTrue(commit_configure(rollback, config_path, confirmed=True))
            self.assertEqual(baseline, load_config(config_path))
            self.assertEqual(migrated_state_bytes, state_path.read_bytes())
            self.assertEqual(legacy_bytes, legacy_path.read_bytes())
            self.assertEqual(output_snapshot, self._snapshot(output_root))

    @staticmethod
    def _snapshot(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        }


if __name__ == "__main__":
    unittest.main()
