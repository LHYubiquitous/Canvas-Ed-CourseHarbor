"""Create safe, profile-specific convenience launchers for the current OS."""

from __future__ import annotations

import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True, slots=True)
class LauncherInstallResult:
    created: tuple[str, ...]
    preserved: tuple[str, ...]
    reused: tuple[str, ...]


def _windows_quote(value: str) -> str:
    if '"' in value or "\r" in value or "\n" in value:
        raise ValueError("Windows launcher arguments contain unsupported characters")
    return '"' + value.replace("%", "%%") + '"'


def render_launchers(
    *,
    profile: str,
    config_path: Path,
    python_executable: Path,
    platform: str,
) -> dict[str, str]:
    """Render launchers without writing files or exposing configuration contents."""
    is_windows = platform == "win32"
    suffix = ".cmd" if is_windows else ".command" if platform == "darwin" else ".sh"
    rendered: dict[str, str] = {}
    common = ("--profile", profile, "--config", str(config_path))
    for basename, command in _COMMANDS:
        args = (*command, *common)
        if is_windows:
            invocation = " ".join(
                (
                    _windows_quote(str(python_executable)),
                    "-I",
                    "-m",
                    "canvas_ed_mcp",
                    *command,
                    "--profile",
                    _windows_quote(profile),
                    "--config",
                    _windows_quote(str(config_path)),
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
            python_text = str(python_executable).replace("\\", "/")
            config_text = str(config_path).replace("\\", "/")
            posix_args = (*command, "--profile", profile, "--config", config_text)
            invocation = " ".join(
                ("exec", shlex.quote(python_text), "-I", "-m", "canvas_ed_mcp")
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
            if target.is_file() and target.read_bytes() == encoded:
                reused.append(name)
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
