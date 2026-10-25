"""Optional desktop user-interface entry points.

PySide6 is intentionally imported only when a UI object is requested so the
core application remains usable without the desktop extra installed.
"""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any

from canvas_ed_mcp.application.setup_controller import SetupController

__all__ = [
    "DailyDashboardWindow",
    "FirstRunWindow",
    "SetupWindow",
    "build_config_saving_setup_window",
]


def build_config_saving_setup_window(
    controller: SetupController,
    config_path: Path,
    *,
    dashboard_opener: Callable[[str, Path], bool] | None = None,
) -> Any:
    """Compose setup with reviewed config and course-folder provisioning."""

    from canvas_ed_mcp.application.setup_workflow import ConfigOnlySetupSaver

    from .setup_window import SetupWindow

    return SetupWindow(
        controller,
        save_port=ConfigOnlySetupSaver(controller, config_path),
        dashboard_opener=dashboard_opener,
    )


def __getattr__(name: str) -> Any:
    if name == "DailyDashboardWindow":
        from .daily_dashboard import DailyDashboardWindow

        return DailyDashboardWindow
    if name == "FirstRunWindow":
        from .first_run_window import FirstRunWindow

        return FirstRunWindow
    if name == "SetupWindow":
        from .setup_window import SetupWindow

        return SetupWindow
    raise AttributeError(name)
