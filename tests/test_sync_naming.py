from pathlib import PurePosixPath
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canvas_ed_mcp.sync.model import RemoteItem, RemoteKind, SourceKey
from canvas_ed_mcp.sync.naming import destination_for


class SyncNamingTests(unittest.TestCase):
    def _item(self, *, name: str = "CON?.pdf", signature: str = "revision-1") -> RemoteItem:
        return RemoteItem(
            key=SourceKey("ed", "ed.invalid", "course-1", RemoteKind.RESOURCE, "remote/1"),
            signature=signature,
            display_name=name,
        )

    def test_destination_is_portable_contained_and_revision_specific(self) -> None:
        first = destination_for(PurePosixPath("COMP0001"), self._item())
        changed = destination_for(
            PurePosixPath("COMP0001"), self._item(signature="revision-2")
        )

        self.assertEqual("COMP0001", first.parts[0])
        self.assertEqual("Resources", first.parts[1])
        self.assertNotIn("?", first.name)
        self.assertNotEqual(first, changed)
        self.assertTrue(first.name.endswith(".pdf"))

    def test_html_kinds_force_html_extension_and_collision_ordinal(self) -> None:
        item = RemoteItem(
            key=SourceKey("ed", "ed.invalid", "course-1", RemoteKind.LESSON, "lesson-1"),
            signature="revision",
            display_name="Week 1.pdf",
        )
        first = destination_for(PurePosixPath("COMP0001"), item)
        second = destination_for(PurePosixPath("COMP0001"), item, ordinal=2)

        self.assertTrue(first.name.endswith(".html"))
        self.assertIn("--2.html", second.name)

    def test_optional_group_adds_one_sanitized_portable_component(self) -> None:
        grouped = RemoteItem(
            key=SourceKey("canvas", "canvas.invalid", "course-1", RemoteKind.FILE, "file-1"),
            signature="revision",
            display_name="notes.pdf",
            group="../Week 1 / CON",
        )

        destination = destination_for(PurePosixPath("COMP0001"), grouped)

        self.assertEqual(("COMP0001", "Files", "_Week 1 _ CON"), destination.parts[:3])
        self.assertNotIn("..", destination.parts)
        self.assertEqual("notes", destination.name.split("--", 1)[0])

    def test_group_with_windows_reserved_stem_is_prefixed(self) -> None:
        grouped = RemoteItem(
            key=SourceKey("canvas", "canvas.invalid", "course-1", RemoteKind.FILE, "file-1"),
            signature="revision", display_name="notes.pdf", group="CON.materials",
        )

        destination = destination_for(PurePosixPath("COMP0001"), grouped)

        self.assertEqual("_CON.materials", destination.parts[2])


if __name__ == "__main__":
    unittest.main()
