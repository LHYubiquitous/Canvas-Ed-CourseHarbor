from __future__ import annotations

import io
import unittest

from canvas_ed_mcp.app_menu import run_menu


class AppMenuTests(unittest.TestCase):
    def test_menu_is_english_and_dispatches_selected_action(self) -> None:
        output = io.StringIO()
        calls: list[str] = []
        answers = iter(["2", "0"])
        exit_code = run_menu(
            input_fn=lambda prompt: next(answers),
            output_fn=lambda text="": print(text, file=output),
            actions={"2": lambda: calls.append("week") or 0},
        )
        self.assertEqual(0, exit_code)
        self.assertEqual(["week"], calls)
        rendered = output.getvalue()
        self.assertIn("CourseHarbor", rendered)
        self.assertIn("This week's due", rendered)
        self.assertIn("0. Exit", rendered)

    def test_invalid_selection_does_not_dispatch(self) -> None:
        output = io.StringIO()
        answers = iter(["invalid", "0"])
        self.assertEqual(
            0,
            run_menu(
                input_fn=lambda prompt: next(answers),
                output_fn=lambda text="": print(text, file=output),
                actions={},
            ),
        )
        self.assertIn("Invalid selection.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
