"""Small, testable boundaries between source and frozen desktop execution."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence


APP_SHELL_MODULE = "canvas_ed_mcp.ui.app_shell"
CONFIGURE_MODULE = "canvas_ed_mcp.ui.configure_window"


def detached_module_command(
    module: str,
    arguments: Sequence[str],
    *,
    executable: str | Path | None = None,
    frozen: bool | None = None,
) -> tuple[str, tuple[str, ...]]:
    """Return a safe detached command for source installs or the frozen GUI host."""

    program = str(executable or sys.executable)
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    values = tuple(arguments)
    if not is_frozen:
        return program, ("-I", "-m", module, *values)
    if module == APP_SHELL_MODULE:
        return program, values
    if module == CONFIGURE_MODULE:
        return program, ("--configure", *values)
    raise ValueError("unsupported frozen desktop module")


def frozen_companion_executables(
    executable: str | Path | None = None,
    *,
    platform: str | None = None,
) -> tuple[Path, Path]:
    """Return the GUI and CLI siblings from an onedir multiprogram bundle."""

    gui = Path(executable or sys.executable).resolve(strict=False)
    suffix = ".exe" if (platform or sys.platform) == "win32" else ""
    cli = gui.with_name(f"courseharbor-cli{suffix}")
    return gui, cli


__all__ = [
    "APP_SHELL_MODULE",
    "CONFIGURE_MODULE",
    "detached_module_command",
    "frozen_companion_executables",
]
