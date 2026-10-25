"""Unified CourseHarbor desktop shell."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QProcess, Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from canvas_ed_mcp import __version__
from canvas_ed_mcp.application.folder_actions import (
    OpenOutputFolderRequest,
    OutputFolderService,
)
from canvas_ed_mcp.application.contracts import OperationStatus
from canvas_ed_mcp.application.app_startup import (
    StartupDestination,
    StartupPlan,
    StartupProfile,
    plan_app_startup,
)
from canvas_ed_mcp.application.daily_sync import SyncApplyResult
from canvas_ed_mcp.application.profile_status import (
    ProfileOverview,
    inspect_profile_overview,
)
from canvas_ed_mcp.application.sync_reports import SyncReportStore
from canvas_ed_mcp.config import load_config
from canvas_ed_mcp.secrets import keyring_status

from .branding import DISPLAY_NAME, DISPLAY_TAGLINE
from .connections_page import ConnectionsPage
from .daily_dashboard import create_daily_dashboard
from .due_dates_page import DueDatesPage
from .frozen_runtime import CONFIGURE_MODULE, detached_module_command
from .icon import apply_application_icon


class _LibraryPage(QWidget):
    """Compatibility host for the current reviewed-sync workspace."""

    def __init__(self, *, profile: str, config_path: Path | None) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.dashboard = create_daily_dashboard(
            profile=profile,
            config_path=config_path,
        )
        workspace = self.dashboard.takeCentralWidget()
        workspace.setStyleSheet(self.dashboard.styleSheet())
        layout.addWidget(workspace)
        self.dashboard.setParent(self)
        self.dashboard.hide()

    def request_shutdown(self) -> bool:
        if self.dashboard.active_task is not None:
            self.dashboard.cancel_active_operation()
            return False
        return bool(self.dashboard.controller.close())


_ACTION_LABELS = {
    "install_new": "New file",
    "replace_managed": "Updated file",
    "install_separately": "New revision",
    "deduplicate": "Matched existing file",
    "record_only": "Baseline recorded",
    "skip": "Unchanged",
    "policy_skip": "Skipped by policy",
    "failed": "Failed",
    "block": "Blocked",
}


def _action_summary(action_counts: tuple[tuple[str, int], ...]) -> str:
    return " · ".join(
        f"{count} {_ACTION_LABELS.get(action, action.replace('_', ' ')).casefold()}"
        for action, count in action_counts
    ) or "No material changes"


class _CoursesPage(QWidget):
    def __init__(
        self,
        *,
        overview: ProfileOverview,
        report_store: SyncReportStore | None = None,
    ) -> None:
        super().__init__()
        self.overview = overview
        self.report_store = report_store or SyncReportStore(overview.profile)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)
        title = QLabel("Courses")
        title.setObjectName("page_title")
        subtitle = QLabel(
            "Expand a course to review every material sync run and the files added or updated."
        )
        subtitle.setObjectName("page_subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)
        self.report_tree = QTreeWidget()
        self.report_tree.setObjectName("course_report_tree")
        self.report_tree.setColumnCount(4)
        self.report_tree.setHeaderLabels(
            ("Course / sync run", "Sources / result", "Changes", "Local destination")
        )
        self.report_tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.report_tree.setAlternatingRowColors(True)
        self.report_tree.setRootIsDecorated(True)
        self.report_tree.setUniformRowHeights(True)
        self.report_tree.header().setStretchLastSection(True)
        self.report_tree.header().resizeSection(0, 330)
        self.report_tree.header().resizeSection(1, 150)
        self.report_tree.header().resizeSection(2, 230)
        self.table = self.report_tree
        layout.addWidget(self.report_tree, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.configure_button = QPushButton("Reconfigure courses…")
        self.configure_button.clicked.connect(self.open_configuration)
        actions.addWidget(self.configure_button)
        layout.addLayout(actions)
        self.reload()

    def reload(self) -> None:
        self.report_tree.clear()
        try:
            config = load_config(self.overview.config_path)
        except Exception:
            self.configure_button.setEnabled(False)
            return
        reports = self.report_store.load()
        by_course: dict[str, list] = {}
        for report in reports:
            for course_report in report.courses:
                by_course.setdefault(course_report.course_key, []).append(
                    (report, course_report)
                )
        for course in config.courses:
            display = next(
                (
                    " — ".join(part for part in (source.code, source.name) if part)
                    for source in course.sources
                    if source.code or source.name
                ),
                course.key,
            )
            providers = " + ".join(
                dict.fromkeys(source.provider.title() for source in course.sources)
            )
            folder = str(config.output_root / course.folder)
            course_runs = by_course.get(course.key, [])
            latest = _action_summary(course_runs[0][1].action_counts) if course_runs else "No sync report yet"
            top = QTreeWidgetItem((display, providers, latest, folder))
            top.setToolTip(0, display)
            top.setToolTip(3, folder)
            self.report_tree.addTopLevelItem(top)
            for report, course_report in course_runs:
                timestamp = report.recorded_at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                run_item = QTreeWidgetItem(
                    (
                        timestamp,
                        report.status.value.replace("_", " ").title(),
                        _action_summary(course_report.action_counts),
                        str(course_report.destination),
                    )
                )
                run_item.setToolTip(3, str(course_report.destination))
                top.addChild(run_item)
                if not course_report.changed_items:
                    run_item.addChild(
                        QTreeWidgetItem(("No new or updated files", "", "", ""))
                    )
                for item in course_report.changed_items:
                    destination = str(item.destination or course_report.destination)
                    file_item = QTreeWidgetItem(
                        (
                            item.display_name,
                            f"{item.provider} · {item.kind.title()}",
                            _ACTION_LABELS.get(item.action, item.action.replace("_", " ").title()),
                            destination,
                        )
                    )
                    file_item.setToolTip(0, item.display_name)
                    file_item.setToolTip(3, destination)
                    run_item.addChild(file_item)
            top.setExpanded(False)
        if self.report_tree.topLevelItemCount():
            self.report_tree.setCurrentItem(self.report_tree.topLevelItem(0))

    def record_sync_result(self, result: object) -> None:
        if not isinstance(result, SyncApplyResult):
            return
        try:
            self.report_store.record(result, output_root=self.overview.output_root)
        except (OSError, RuntimeError, TypeError, ValueError):
            return
        self.reload()

    def open_configuration(self) -> None:
        program, arguments = detached_module_command(
            CONFIGURE_MODULE,
            (
                "--profile",
                self.overview.profile,
                "--config",
                str(self.overview.config_path),
            ),
        )
        started = QProcess.startDetached(program, list(arguments))
        if isinstance(started, tuple):
            started = started[0]
        if not started:
            QMessageBox.warning(
                self,
                "Course configuration",
                "The course configuration UI could not be opened.",
            )


class _HealthAboutPage(QWidget):
    def __init__(self, *, overview: ProfileOverview) -> None:
        super().__init__()
        self.overview = overview
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)
        title = QLabel("Health & About")
        title.setObjectName("page_title")
        subtitle = QLabel("Local profile health and CourseHarbor product information.")
        subtitle.setObjectName("page_subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.health = QFrame()
        self.health.setObjectName("health_card")
        health_layout = QVBoxLayout(self.health)
        self.profile_health = QLabel()
        self.profile_health.setWordWrap(True)
        self.credential_health = QLabel()
        self.credential_health.setWordWrap(True)
        health_layout.addWidget(self.profile_health)
        health_layout.addWidget(self.credential_health)
        layout.addWidget(self.health)

        about = QFrame()
        about.setObjectName("about_card")
        about_layout = QVBoxLayout(about)
        name = QLabel(f"{DISPLAY_NAME} {__version__}")
        name.setObjectName("about_name")
        detail = QLabel(
            f"{DISPLAY_TAGLINE}. Credentials stay in the native credential store; "
            "course services are read-only until you explicitly apply or export."
        )
        detail.setWordWrap(True)
        about_layout.addWidget(name)
        about_layout.addWidget(detail)
        layout.addWidget(about)
        layout.addStretch(1)
        refresh = QPushButton("Refresh local health")
        refresh.clicked.connect(self.reload)
        layout.addWidget(refresh, 0, Qt.AlignmentFlag.AlignRight)
        self.reload()

    def reload(self) -> None:
        profile_state = "PASS" if self.overview.status is OperationStatus.SUCCEEDED else "DEGRADED"
        output_state = "PASS" if self.overview.output_exists else "DEGRADED"
        self.profile_health.setText(
            f"Profile · {profile_state}\n"
            f"{self.overview.course_count} courses · {self.overview.source_count} sources\n"
            f"Local library · {output_state}"
        )
        try:
            status = keyring_status()
            keyring_state = "PASS" if status.available else "DEGRADED"
        except Exception:
            keyring_state = "unknown"
        self.credential_health.setText(
            f"Native credential store · {keyring_state}\n"
            "Remote connection tests · NOT_RUN"
        )


class CourseHarborWindow(QMainWindow):
    """One navigable desktop home for daily CourseHarbor work."""

    profileSwitchRequested = Signal()

    _ROUTES = (
        ("Library", "library"),
        ("Due dates", "due_dates"),
        ("Courses", "courses"),
        ("Connections", "connections"),
        ("Health & About", "health"),
    )

    def __init__(
        self,
        *,
        overview: ProfileOverview,
        folder_service: OutputFolderService | None = None,
        sync_report_store: SyncReportStore | None = None,
    ) -> None:
        super().__init__()
        self.overview = overview
        self.folder_service = folder_service or OutputFolderService(
            opener=lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        )
        self.sync_report_store = sync_report_store or SyncReportStore(overview.profile)
        self._allow_close = False
        self._close_confirmed = False
        self.setWindowTitle(DISPLAY_NAME)
        self.setObjectName("courseharbor_shell")
        self.resize(1240, 780)
        self.setMinimumSize(960, 620)
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("shell_root")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        rail = QFrame()
        rail.setObjectName("navigation_rail")
        rail.setMinimumWidth(176)
        rail.setMaximumWidth(208)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(16, 20, 16, 16)
        rail_layout.setSpacing(6)
        brand = QLabel(DISPLAY_NAME)
        brand.setObjectName("shell_brand")
        tagline = QLabel("COURSE LIBRARY")
        tagline.setObjectName("shell_tagline")
        rail_layout.addWidget(brand)
        rail_layout.addWidget(tagline)
        rail_layout.addSpacing(18)

        self.stack = QStackedWidget()
        self.stack.setObjectName("app_pages")
        self.pages = {
            "library": _LibraryPage(
                profile=self.overview.profile,
                config_path=self.overview.config_path,
            ),
            "due_dates": DueDatesPage(
                profile=self.overview.profile,
                config_path=self.overview.config_path,
            ),
            "courses": _CoursesPage(
                overview=self.overview,
                report_store=self.sync_report_store,
            ),
            "connections": ConnectionsPage(
                profile=self.overview.profile,
                config_path=self.overview.config_path,
            ),
            "health": _HealthAboutPage(overview=self.overview),
        }
        self.pages["library"].dashboard.syncFinished.connect(
            self.pages["courses"].record_sync_result
        )
        self.route_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, (label, route) in enumerate(self._ROUTES):
            button = QPushButton(label)
            button.setObjectName("route_button")
            button.setCheckable(True)
            button.clicked.connect(
                lambda checked=False, selected=route: self.show_route(selected)
            )
            group.addButton(button)
            rail_layout.addWidget(button)
            self.route_buttons[route] = button
            self.stack.addWidget(self.pages[route])
            if index == 0:
                button.setChecked(True)
        rail_layout.addStretch(1)
        profile_label = QLabel("ACTIVE PROFILE")
        profile_label.setObjectName("rail_meta_label")
        profile_value = QLabel(self.overview.profile)
        profile_value.setObjectName("rail_meta_value")
        profile_value.setWordWrap(True)
        rail_layout.addWidget(profile_label)
        rail_layout.addWidget(profile_value)
        self.switch_profile_button = QPushButton("Switch profile")
        self.switch_profile_button.setObjectName("switch_profile_button")
        self.switch_profile_button.clicked.connect(self.request_profile_switch)
        rail_layout.addWidget(self.switch_profile_button)
        self.open_library_button = QPushButton("Open local library")
        self.open_library_button.setObjectName("open_library_button")
        self.open_library_button.clicked.connect(self.open_local_library)
        rail_layout.addWidget(self.open_library_button)

        root_layout.addWidget(rail)
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.show_route("library")

    def show_route(self, route: str) -> None:
        page = self.pages.get(route)
        if page is None:
            raise ValueError("unknown app route")
        self.stack.setCurrentWidget(page)
        self.route_buttons[route].setChecked(True)

    def open_local_library(self) -> None:
        result = self.folder_service.open(
            OpenOutputFolderRequest(
                self.overview.profile,
                config_path=self.overview.config_path,
                expected_output_root=self.overview.output_root,
            )
        )
        if result.status is not OperationStatus.SUCCEEDED:
            QMessageBox.warning(
                self,
                "Open local library",
                "The configured local library could not be opened.",
            )

    def request_shutdown(self) -> bool:
        busy = False
        for route in ("library", "due_dates", "connections"):
            request_shutdown = getattr(self.pages[route], "request_shutdown", None)
            if callable(request_shutdown) and not request_shutdown():
                busy = True
        return not busy

    def request_profile_switch(self) -> None:
        if not self.request_shutdown():
            QMessageBox.information(
                self,
                "Finishing current work",
                "CourseHarbor requested cancellation. Try switching profiles again after the current safe boundary.",
            )
            return
        self.profileSwitchRequested.emit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._allow_close:
            event.accept()
            return
        if not self._close_confirmed:
            answer = QMessageBox.question(
                self,
                "Close CourseHarbor?",
                "Close CourseHarbor? No credential values are kept in the interface.",
                QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Close:
                event.ignore()
                return
            self._close_confirmed = True
        if not self.request_shutdown():
            QMessageBox.information(
                self,
                "Finishing current work",
                "CourseHarbor requested cancellation and will close after the current safe boundary. Close the window again when the operation has finished.",
            )
            event.ignore()
            return
        self._allow_close = True
        event.accept()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow#courseharbor_shell, QWidget#shell_root, QStackedWidget#app_pages { background: #F6F8FB; color: #172033; }
            QWidget { font-family: "Segoe UI Variable", "Segoe UI"; font-size: 10pt; }
            QFrame#navigation_rail { background: #13243C; border: none; }
            QFrame#navigation_rail QLabel { background: transparent; border: none; }
            QLabel#shell_brand { color: #FFFFFF; font-size: 18pt; font-weight: 750; }
            QLabel#shell_tagline { color: #78A7FF; font-size: 7.5pt; font-weight: 750; letter-spacing: 1px; }
            QLabel#rail_meta_label { color: #8FA8C8; font-size: 7.5pt; font-weight: 700; }
            QLabel#rail_meta_value { color: #FFFFFF; font-weight: 650; padding-bottom: 8px; }
            QPushButton#route_button { text-align: left; min-height: 34px; padding: 3px 10px; color: #C8D6EA; background: transparent; border: none; border-left: 3px solid transparent; border-radius: 3px; }
            QPushButton#route_button:hover { background: #1B3150; color: #FFFFFF; }
            QPushButton#route_button:checked { background: #203A5F; color: #FFFFFF; border-left-color: #78A7FF; font-weight: 700; }
            QPushButton#switch_profile_button { min-height: 30px; color: #C8D6EA; background: transparent; border: 1px solid #506784; border-radius: 5px; }
            QPushButton#switch_profile_button:hover { background: #1B3150; color: #FFFFFF; }
            QPushButton#open_library_button { min-height: 34px; color: #FFFFFF; background: #2F6FED; border: 1px solid #4D82EF; border-radius: 5px; font-weight: 700; }
            QLabel#page_title { font-size: 20pt; font-weight: 700; color: #172033; }
            QLabel#page_subtitle { color: #5F6C80; }
            QFrame#destination_card, QFrame#connection_card, QFrame#health_card, QFrame#about_card, QFrame#report_card { background: #FFFFFF; border: 1px solid #D9E1EC; border-radius: 6px; }
            QLabel#report_heading { background: #EDF2F8; color: #384961; font-weight: 700; border-bottom: 1px solid #D9E1EC; }
            QLabel#about_name { font-size: 14pt; font-weight: 700; }
            QLineEdit, QPlainTextEdit, QTableWidget, QTreeWidget, QTabWidget::pane { background: #FFFFFF; border: 1px solid #C7D2E2; border-radius: 4px; }
            QPlainTextEdit { font-family: "Cascadia Mono", "Consolas"; font-size: 9pt; }
            QHeaderView::section { background: #EDF2F8; color: #384961; padding: 8px; border: none; border-bottom: 1px solid #D9E1EC; font-weight: 650; }
            QPushButton { min-height: 32px; padding: 3px 14px; color: #172033; background: #FFFFFF; border: 1px solid #B9C5D5; border-radius: 5px; }
            QPushButton:hover { background: #EDF3FF; border-color: #7D9FE7; }
            QPushButton:focus { border: 2px solid #174EA6; }
            QPushButton:disabled { color: #99A3B2; background: #EEF1F5; border-color: #D8DEE8; }
            QPushButton#refresh_due_button { color: #214FA7; font-weight: 650; }
            QPushButton#export_due_button { background: #2F6FED; color: #FFFFFF; border-color: #2F6FED; font-weight: 700; }
            QFrame#warning_panel { background: #FFF7E8; border: 1px solid #E6BD79; border-radius: 5px; }
            QFrame#warning_panel QLabel { color: #67441A; }
            """
        )


def create_app_shell(
    *,
    profile: str = "default",
    config_path: Path | None = None,
) -> CourseHarborWindow:
    overview = inspect_profile_overview(profile, config_path=config_path)
    if overview.status in {OperationStatus.FAILED, OperationStatus.CONFLICT}:
        raise ValueError("profile_not_ready")
    return CourseHarborWindow(overview=overview)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"{DISPLAY_NAME} desktop app")
    parser.add_argument("--profile")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--configure", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--bundle-smoke", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.configure:
        from .configure_window import main as configure_main

        forwarded: list[str] = []
        if args.profile is not None:
            forwarded.extend(("--profile", args.profile))
        if args.config is not None:
            forwarded.extend(("--config", str(args.config)))
        return configure_main(forwarded)
    application = QApplication.instance() or QApplication(sys.argv)
    icon_loaded = apply_application_icon(application)
    if args.bundle_smoke:
        return 0 if application.platformName() and icon_loaded else 3
    windows: dict[str, QWidget] = {}

    def replace_window(window: QWidget) -> None:
        previous = windows.get("current")
        windows["current"] = window
        window.show()
        window.raise_()
        if previous is not None and previous is not window:
            if isinstance(previous, CourseHarborWindow):
                previous._allow_close = True
            previous.close()

    def show_shell(overview: ProfileOverview) -> None:
        window = CourseHarborWindow(overview=overview)
        window.profileSwitchRequested.connect(show_chooser)
        replace_window(window)

    def show_first_run(profile: str, config_path: Path | None = None) -> None:
        from .first_run_window import FirstRunWindow

        replace_window(
            FirstRunWindow(
                profile=profile,
                config_path=config_path,
                dashboard_opener=show_shell_from_config,
                profile_chooser_opener=show_chooser,
            )
        )

    def show_shell_from_config(profile: str, config_path: Path) -> bool:
        overview = inspect_profile_overview(profile, config_path=config_path)
        if overview.status not in {
            OperationStatus.SUCCEEDED,
            OperationStatus.PARTIAL,
        }:
            return False
        show_shell(overview)
        return True

    def show_chooser(plan: StartupPlan | None = None) -> None:
        from .profile_chooser import ProfileChooserWindow

        selected_plan = plan or plan_app_startup()
        if selected_plan.destination is StartupDestination.APP_SHELL:
            selected_plan = StartupPlan(
                StartupDestination.PROFILE_CHOOSER,
                profiles=(StartupProfile(selected_plan.selected),),
            )
        chooser = ProfileChooserWindow(selected_plan)
        chooser.profileSelected.connect(show_shell)
        chooser.setupRequested.connect(show_first_run)
        chooser.profilesChanged.connect(show_chooser)
        replace_window(chooser)

    plan = plan_app_startup(profile=args.profile, config_path=args.config)
    if plan.destination is StartupDestination.APP_SHELL and plan.selected is not None:
        show_shell(plan.selected)
    elif plan.destination is StartupDestination.FIRST_RUN:
        show_first_run(plan.requested_profile or "default", args.config)
    else:
        show_chooser(plan)
    return application.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["CourseHarborWindow", "create_app_shell", "main"]
