from __future__ import annotations

import dataclasses
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePosixPath
import tempfile
import unittest

from canvas_ed_mcp.application.fingerprints import (
    MISSING_REVISION,
    bytes_revision,
    canonical_sha256,
    file_revision,
)


class _Kind(str, Enum):
    FILE = "file"


@dataclasses.dataclass(frozen=True, slots=True)
class _Payload:
    name: str
    path: PurePosixPath
    kind: _Kind


class ApplicationFingerprintTests(unittest.TestCase):
    def test_mapping_order_does_not_change_canonical_digest(self) -> None:
        first = {
            "courses": {"b": 2, "a": 1},
            "selected": ["canvas", "ed"],
        }
        second = {
            "selected": ["canvas", "ed"],
            "courses": {"a": 1, "b": 2},
        }

        self.assertEqual(canonical_sha256(first), canonical_sha256(second))

    def test_sequence_order_changes_canonical_digest(self) -> None:
        self.assertNotEqual(
            canonical_sha256(["canvas", "ed"]),
            canonical_sha256(["ed", "canvas"]),
        )

    def test_equivalent_path_enum_dataclass_and_scalar_values_are_deterministic(self) -> None:
        first = {
            "payload": _Payload("course", Path("Courses") / "COMP2022", _Kind.FILE),
            "date": date(2026, 10, 20),
            "time": datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc),
            "decimal": Decimal("100.5000"),
            "bytes": b"payload",
            "set": {"ed", "canvas"},
        }
        second = {
            "set": {"canvas", "ed"},
            "bytes": b"payload",
            "decimal": Decimal("100.5000"),
            "time": datetime(2026, 10, 20, 12, 30, tzinfo=timezone.utc),
            "date": date(2026, 10, 20),
            "payload": _Payload(
                name="course",
                path=PurePosixPath("Courses/COMP2022"),
                kind=_Kind.FILE,
            ),
        }

        digest = canonical_sha256(first)
        self.assertEqual(64, len(digest))
        self.assertEqual(digest, canonical_sha256(second))

    def test_list_and_tuple_share_sequence_canonicalization(self) -> None:
        self.assertEqual(
            canonical_sha256(["a", {"b": 2}]),
            canonical_sha256(("a", {"b": 2})),
        )

    def test_invalid_or_ambiguous_values_fail_closed(self) -> None:
        invalid_values = (
            {1: "non-string-key"},
            float("nan"),
            float("inf"),
            object(),
            datetime(2026, 10, 20, 12, 30),
        )
        for invalid in invalid_values:
            with self.subTest(invalid=repr(invalid)):
                with self.assertRaises((TypeError, ValueError)):
                    canonical_sha256(invalid)

    def test_bytes_and_file_revisions_hash_exact_bytes_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "config.toml"
            payload = b"profile = \"review\"\n"
            target.write_bytes(payload)

            before = tuple(root.iterdir())
            self.assertEqual(bytes_revision(payload), file_revision(target))
            self.assertEqual(before, tuple(root.iterdir()))

    def test_missing_file_has_explicit_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.toml"
            self.assertEqual(MISSING_REVISION, file_revision(missing))

    def test_file_revision_rejects_non_file_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                file_revision(Path(directory))


if __name__ == "__main__":
    unittest.main()
