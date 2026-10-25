"""CourseHarbor application-icon loading for source and frozen GUI entry points."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication


def icon_path() -> Path:
    return Path(__file__).with_name("assets") / "courseharbor.png"


def apply_application_icon(application: "QApplication") -> bool:
    from PySide6.QtGui import QIcon

    icon = QIcon(str(icon_path()))
    if icon.isNull():
        return False
    application.setWindowIcon(icon)
    return True


__all__ = ["apply_application_icon", "icon_path"]
