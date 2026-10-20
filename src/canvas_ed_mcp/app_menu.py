"""English-first interactive terminal menu for common daily actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping


MENU = """\
Canvas Ed MCP

1. Course material sync
2. This week's due
3. This semester's due
4. Configure courses
5. Health check
6. Open course folder
7. Reinstall launchers
0. Exit"""


def run_menu(
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    actions: Mapping[str, Callable[[], int]] | None = None,
) -> int:
    """Run the menu until Exit; individual action failures return to the menu."""
    handlers = dict(actions or {})
    while True:
        output_fn(MENU)
        selection = input_fn("Select an option: ").strip()
        if selection == "0":
            return 0
        action = handlers.get(selection)
        if action is None:
            output_fn("Invalid selection.")
            continue
        exit_code = int(action())
        if exit_code:
            output_fn(f"Action completed with exit code {exit_code}.")
        output_fn("")
