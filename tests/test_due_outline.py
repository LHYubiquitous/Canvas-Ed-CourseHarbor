from __future__ import annotations

import unittest

from canvas_ed_mcp.due.model import DueCoverageStatus
from canvas_ed_mcp.due.outline import parse_unit_outline_markdown


class DueOutlineTests(unittest.TestCase):
    def test_parses_reviewed_chinese_style_markdown_into_english_neutral_model(self) -> None:
        markdown = """
# 2026 S2 Assessment Due Notes

#### 10月18日（Sun）23:59（Week 10）

- [ ] **COMP0000｜Assignment 2**
    - 占分: **15%**
"""
        batch = parse_unit_outline_markdown(
            markdown,
            default_year=2026,
            course_keys={"comp0000": ("logical-course", "COMP0000")},
            source_id="reviewed-outline",
        )
        self.assertEqual(DueCoverageStatus.COMPLETE, batch.coverage)
        self.assertEqual(1, len(batch.items))
        self.assertEqual("Assignment 2", batch.items[0].title)
        self.assertEqual("15", str(batch.items[0].weight_percent))
        self.assertEqual("2026-10-18T23:59:00+11:00", batch.items[0].due_at.isoformat())

    def test_unknown_course_is_partial_and_not_silently_mapped(self) -> None:
        batch = parse_unit_outline_markdown(
            "#### October 18 (Sun) 23:59\n- [ ] **UNKNOWN｜Task**\n",
            default_year=2026,
            course_keys={"known": ("known", "KNOWN")},
            source_id="reviewed-outline",
        )
        self.assertEqual(DueCoverageStatus.PARTIAL, batch.coverage)
        self.assertEqual((), batch.items)
        self.assertEqual("unknown_course_label", batch.issues[0].code)

    def test_non_date_heading_resets_context_instead_of_reusing_stale_date(self) -> None:
        batch = parse_unit_outline_markdown(
            "#### October 18 (Sun) 23:59\n- [ ] **KNOWN｜First**\n"
            "#### Notes\n- [ ] **KNOWN｜Undated**\n",
            default_year=2026,
            course_keys={"known": ("known", "KNOWN")},
            source_id="reviewed-outline",
        )
        self.assertEqual(DueCoverageStatus.PARTIAL, batch.coverage)
        self.assertEqual(("First",), tuple(item.title for item in batch.items))
        self.assertEqual("item_schema_drift", batch.issues[0].code)

    def test_heading_explicit_year_overrides_default_year(self) -> None:
        batch = parse_unit_outline_markdown(
            "#### October 18, 2027 (Mon) 23:59\n- [ ] **KNOWN｜Future**\n",
            default_year=2026,
            course_keys={"known": ("known", "KNOWN")},
            source_id="reviewed-outline",
        )
        self.assertEqual(2027, batch.items[0].due_at.year)


if __name__ == "__main__":
    unittest.main()
