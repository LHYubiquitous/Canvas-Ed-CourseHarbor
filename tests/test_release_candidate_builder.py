from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci.build_release_candidate import (
    ReleaseCandidateError,
    _canonicalize_sdist,
    _safe_extract_source,
    build_candidate_packet,
)


class ReleaseCandidateBuilderTests(unittest.TestCase):
    def test_builder_requires_current_locked_release_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch(
                "ci.build_release_candidate.verify_generated_metadata",
                side_effect=RuntimeError("synthetic stale metadata"),
            ) as verify:
                with self.assertRaisesRegex(RuntimeError, "synthetic stale metadata"):
                    build_candidate_packet(
                        root, root / "candidate", source_date_epoch=1
                    )
            verify.assert_called_once_with(root.resolve())

    def test_sdist_canonicalization_removes_archive_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archives = (root / "first.tar.gz", root / "second.tar.gz")
            for archive, timestamp in zip(archives, (10, 20)):
                with tarfile.open(archive, "w:gz") as bundle:
                    item = tarfile.TarInfo("canvas_ed_mcp-0.2.0rc1/PKG-INFO")
                    item.mtime = timestamp
                    item.uid = timestamp
                    item.gid = timestamp
                    item.uname = "builder"
                    item.gname = "builder"
                    payload = b"synthetic metadata\n"
                    item.size = len(payload)
                    bundle.addfile(item, io.BytesIO(payload))

            for archive in archives:
                _canonicalize_sdist(archive, 123456789)

            self.assertEqual(archives[0].read_bytes(), archives[1].read_bytes())
            with tarfile.open(archives[0], "r:gz") as bundle:
                item = bundle.getmembers()[0]
                self.assertEqual(123456789, item.mtime)
                self.assertEqual((0, 0, "", ""), (item.uid, item.gid, item.uname, item.gname))

    def test_existing_output_is_rejected_before_any_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "candidate"
            output.mkdir()
            with self.assertRaisesRegex(ReleaseCandidateError, "output_exists"):
                build_candidate_packet(root, output, source_date_epoch=1)

    def test_negative_epoch_is_rejected_before_any_build(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ReleaseCandidateError, "invalid_source_date_epoch"):
                build_candidate_packet(root, root / "candidate", source_date_epoch=-1)

    def test_source_extraction_rejects_links_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                item = tarfile.TarInfo("canvas-ed-mcp-0.2.0rc1/../private.txt")
                payload = b"synthetic"
                item.size = len(payload)
                bundle.addfile(item, io.BytesIO(payload))
            with self.assertRaisesRegex(ReleaseCandidateError, "unsafe_source_archive"):
                _safe_extract_source(archive, root / "extract", "0.2.0rc1")

    def test_source_extraction_rejects_windows_backslash_traversal_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                item = tarfile.TarInfo(
                    "canvas-ed-mcp-0.2.0rc4/dir\\..\\..\\..\\escape.txt"
                )
                payload = b"synthetic"
                item.size = len(payload)
                bundle.addfile(item, io.BytesIO(payload))
            with self.assertRaisesRegex(ReleaseCandidateError, "unsafe_source_archive"):
                _safe_extract_source(archive, root / "extract", "0.2.0rc4")
            self.assertFalse((root / "escape.txt").exists())


if __name__ == "__main__":
    unittest.main()
