"""Production PySide6 entry point for config-only profile reconfiguration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Mapping

from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from ..application.configure_controller import ConfigureController
from ..application.configure_safety import load_relocation_eligibility
from ..application.configure_workflow import ConfigOnlyConfigureSaver
from ..application.contracts import OperationStatus
from ..application.profiles import discover_course_catalogs
from ..config import AppConfig, ConfigLoadError, load_config
from ..paths import resolve_profile_paths
from ..providers.base import CourseCandidate
from ..secrets import resolve_secret
from .setup_window import SetupWindow
from .branding import DISPLAY_NAME
from .icon import apply_application_icon


def build_configure_window(
    *,
    current: AppConfig,
    config_path: Path,
    state_path: Path,
    catalogs: Mapping[str, tuple[CourseCandidate, ...]],
) -> SetupWindow:
    eligibility = load_relocation_eligibility(current, state_path)
    controller = ConfigureController(
        current=current,
        catalogs=catalogs,
        eligibility=eligibility,
    )
    saver = ConfigOnlyConfigureSaver(controller, config_path, state_path)
    return SetupWindow(controller, save_port=saver, mode="configure")


def _discover(current: AppConfig):
    requested = tuple(
        provider
        for provider in ("canvas", "ed")
        if getattr(current.platforms, provider).enabled
    )
    return discover_course_catalogs(
        current.platforms,
        requested_providers=requested,
        secret_resolver=resolve_secret,
        operation_id="configure-course-discovery",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=f"Reconfigure an existing {DISPLAY_NAME} profile"
    )
    parser.add_argument("--profile", default="default")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    application = QApplication.instance() or QApplication(sys.argv)
    apply_application_icon(application)
    try:
        paths = resolve_profile_paths(args.profile)
        config_path = (args.config or paths.config_file).resolve(strict=False)
        current = load_config(config_path)
        if current.profile != paths.profile:
            raise ConfigLoadError("profile_mismatch", "config profile does not match")
    except ConfigLoadError as exc:
        QMessageBox.critical(
            None,
            "Cannot open course configuration",
            f"The selected profile could not be opened ({exc.category}).",
        )
        return 2
    except (OSError, RuntimeError, ValueError):
        QMessageBox.critical(
            None,
            "Cannot open course configuration",
            "The profile name or configuration path is invalid.",
        )
        return 2

    progress = QProgressDialog(
        "Checking Canvas and Ed and refreshing the course list…",
        "",
        0,
        0,
    )
    progress.setWindowTitle(f"{DISPLAY_NAME} — Configure courses")
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.show()
    application.processEvents()
    discovery = _discover(current)
    progress.close()
    catalogs = {
        result.provider: result.courses
        for result in discovery.providers
        if result.status in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}
    }
    try:
        window = build_configure_window(
            current=current,
            config_path=config_path,
            state_path=paths.state_file,
            catalogs=catalogs,
        )
    except (OSError, RuntimeError, ValueError):
        QMessageBox.critical(
            None,
            "Cannot prepare course configuration",
            "The saved mapping or local sync evidence could not be prepared safely.",
        )
        return 2
    failed = tuple(
        result.provider.title()
        for result in discovery.providers
        if result.status not in {OperationStatus.SUCCEEDED, OperationStatus.PARTIAL}
    )
    if failed:
        window._show_status(  # noqa: SLF001 - same UI package, deliberate status handoff.
            "Could not refresh "
            + " and ".join(failed)
            + ". Existing configured courses remain visible; check the saved token "
            "reference before adding courses.",
            status="warning",
        )
    window.show()
    window.raise_()
    return application.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_configure_window", "main"]
