"""Create safe, profile-specific convenience launchers for the current OS."""

from __future__ import annotations

import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Mapping, Sequence


_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("course_material_sync", ("sync",)),
    ("this_week_due", ("due", "week")),
    ("this_semester_due", ("due", "semester")),
    ("canvas_ed_app", ("app",)),
    ("configure_courses", ("configure-gui",)),
    ("health_check", ("doctor", "--show-paths")),
    ("open_course_folder", ("open-course-folder",)),
)

_DAILY_DASHBOARD_BASENAME = "canvas_ed_app"
_DAILY_DASHBOARD_MODULE = "canvas_ed_mcp.ui.app_shell"


@dataclass(frozen=True, slots=True)
class LauncherInstallResult:
    created: tuple[str, ...]
    preserved: tuple[str, ...]
    reused: tuple[str, ...]


def _windows_quote(value: str) -> str:
    if '"' in value or "\r" in value or "\n" in value:
        raise ValueError("Windows launcher arguments contain unsupported characters")
    return '"' + value.replace("%", "%%") + '"'


def _windows_path(value: Path) -> str:
    """Render a Windows path independently of the build host's path flavour."""
    return str(PureWindowsPath(str(value)))


def render_launchers(
    *,
    profile: str,
    config_path: Path,
    python_executable: Path | None = None,
    gui_executable: Path | None = None,
    cli_executable: Path | None = None,
    platform: str,
) -> dict[str, str]:
    """Render launchers without writing files or exposing configuration contents."""
    source_mode = python_executable is not None
    frozen_mode = gui_executable is not None or cli_executable is not None
    if source_mode == frozen_mode:
        raise ValueError("choose exactly one launcher runtime")
    if frozen_mode and (gui_executable is None or cli_executable is None):
        raise ValueError("frozen launchers require GUI and CLI executables")
    is_windows = platform == "win32"
    suffix = ".cmd" if is_windows else ".command" if platform == "darwin" else ".sh"
    rendered: dict[str, str] = {}
    common = ("--profile", profile, "--config", str(config_path))
    for basename, command in _COMMANDS:
        if frozen_mode:
            if basename == _DAILY_DASHBOARD_BASENAME:
                executable = gui_executable
                command_args: tuple[str, ...] = ()
            elif basename == "configure_courses":
                executable = gui_executable
                command_args = ("--configure",)
            else:
                executable = cli_executable
                command_args = command
            assert executable is not None
            invocation_prefix = (str(executable),)
        else:
            assert python_executable is not None
            executable = python_executable
            module = (
                _DAILY_DASHBOARD_MODULE
                if basename == _DAILY_DASHBOARD_BASENAME
                else "canvas_ed_mcp"
            )
            command_args = () if basename == _DAILY_DASHBOARD_BASENAME else command
            invocation_prefix = (str(executable), "-I", "-m", module)
        if is_windows:
            executable_text = _windows_path(Path(invocation_prefix[0]))
            config_text = _windows_path(config_path)
            invocation = " ".join(
                (
                    _windows_quote(executable_text),
                    *invocation_prefix[1:],
                    *command_args,
                    "--profile",
                    _windows_quote(profile),
                    "--config",
                    _windows_quote(config_text),
                )
            )
            content = (
                "@echo off\r\n"
                "setlocal\r\n"
                f"{invocation} %*\r\n"
                "set \"CANVAS_ED_EXIT=%ERRORLEVEL%\"\r\n"
                "if not \"%CANVAS_ED_NO_PAUSE%\"==\"1\" pause\r\n"
                "exit /b %CANVAS_ED_EXIT%\r\n"
            )
        else:
            executable_text = str(executable).replace("\\", "/")
            config_text = str(config_path).replace("\\", "/")
            posix_args = (*command_args, "--profile", profile, "--config", config_text)
            invocation = " ".join(
                ("exec", shlex.quote(executable_text))
                + tuple(shlex.quote(part) for part in invocation_prefix[1:])
                + tuple(shlex.quote(part) for part in posix_args)
            )
            content = "#!/bin/sh\n" + f'{invocation} "$@"\n'
        rendered[basename + suffix] = content
    return rendered


def _create_file(path: Path, content: str, *, executable: bool) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o700 if executable else 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _replace_file(path: Path, content: str, *, executable: bool) -> None:
    """Atomically replace one recognized managed launcher."""
    candidate = path.with_name(f".{path.name}.tmp")
    index = 2
    while candidate.exists():
        candidate = path.with_name(f".{path.name}.tmp.{index}")
        index += 1
    try:
        _create_file(candidate, content, executable=executable)
        os.replace(candidate, path)
    finally:
        candidate.unlink(missing_ok=True)


def _managed_predecessors(name: str, content: str) -> tuple[str, ...]:
    """Return exact generated predecessors eligible for safe migration."""
    if name not in {
        "canvas_ed_app.cmd",
        "canvas_ed_app.command",
        "canvas_ed_app.sh",
    }:
        return ()
    marker = f"-m {_DAILY_DASHBOARD_MODULE} --profile"
    if marker not in content:
        return ()
    return (
        content.replace(marker, "-m canvas_ed_mcp.ui.daily_dashboard --profile", 1),
        content.replace(marker, "-m canvas_ed_mcp app --profile", 1),
    )


def install_launchers(
    directory: Path,
    rendered: Mapping[str, str],
    *,
    executable_names: Sequence[str] | None = None,
) -> LauncherInstallResult:
    """Install launchers create-only; preserve unknown or edited existing files."""
    directory.mkdir(parents=True, exist_ok=True)
    executable = set(rendered if executable_names is None else executable_names)
    created: list[str] = []
    preserved: list[str] = []
    reused: list[str] = []
    for name in sorted(rendered):
        target = directory / name
        content = rendered[name]
        encoded = content.encode("utf-8")
        if target.exists():
            existing = target.read_bytes() if target.is_file() else None
            if existing == encoded:
                reused.append(name)
                continue
            predecessors = _managed_predecessors(name, content)
            if any(existing == item.encode("utf-8") for item in predecessors):
                _replace_file(target, content, executable=name in executable)
                created.append(name)
                continue
            preserved.append(name)
            candidate = directory / f"{name}.new"
            index = 2
            while candidate.exists():
                if candidate.is_file() and candidate.read_bytes() == encoded:
                    reused.append(candidate.name)
                    candidate = None
                    break
                candidate = directory / f"{name}.new.{index}"
                index += 1
            if candidate is None:
                continue
            target = candidate
        _create_file(target, content, executable=name in executable)
        created.append(target.name)
    return LauncherInstallResult(tuple(created), tuple(preserved), tuple(reused))
