"""One-command owner-device macOS release-candidate verification matrix.

This runner never calls a real provider, starts a configured user profile, runs the
legacy downloader, changes Git state, or publishes an artifact. It records only
sanitized stage metadata in its Markdown report; command output stays in Terminal.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

try:
    from ci.release_gate import read_project_version
except ModuleNotFoundError:  # direct `python ci/macos_release_matrix.py`
    from release_gate import read_project_version

BASELINE_ANCESTOR = "c305be28882dcedb3f93fd197a9218746c4d05d2"
DEFAULT_PYTHONS = ("3.10", "3.15")
REVIEWED_UV_VERSION = "uv 0.11.28"
_PYTHON_SELECTOR = re.compile(r"3\.(?:10|11|12|13|14|15)\Z")
PRIVATE_SENTINELS = (
    "credentials_local.json",
    ".course_update_state.json",
    "Course Material Sync.command",
    "downloads",
    "exports",
    "output",
)
MANUAL_FOLLOWUPS = (
    "Native MCP host UI",
    "Optional Canvas/Ed real-account catalog",
    "Private legacy actual sync",
    "Public remote/tag/GitHub Release/PyPI",
)


@dataclass(frozen=True, slots=True)
class StepResult:
    name: str
    status: str
    exit_code: int | None
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class MatrixResult:
    commit: str
    macos_version: str
    architecture: str
    steps: tuple[StepResult, ...]
    automated_result: str


def scrub_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Remove provider credential variables before starting any child process."""
    cleaned: dict[str, str] = {}
    for name, value in source.items():
        upper = name.upper()
        provider_secret = (
            upper.startswith("CANVAS_")
            or upper.startswith("ED_API_")
            or upper.startswith("EDSTEM_")
            or upper.startswith("GRADESCOPE_")
            or upper == "CI_CANVAS_NEVER_READ"
            or upper == "PYTHON_KEYRING_BACKEND"
            or upper.startswith("KEYRING_PROPERTY_")
            or upper == "KEYCHAIN_PATH"
        )
        if not provider_secret:
            cleaned[name] = value
    cleaned.pop("PYTHONPATH", None)
    return cleaned


def _display_command(argv: Sequence[str]) -> str:
    return shlex.join(tuple(Path(item).name if index == 0 else item for index, item in enumerate(argv)))


class MatrixRunner:
    def __init__(self, *, root: Path, environment: Mapping[str, str]) -> None:
        self.root = root
        self.environment = scrub_environment(environment)
        self.steps: list[StepResult] = []

    def run(
        self,
        name: str,
        argv: Sequence[str | Path],
        *,
        cwd: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout: int = 1200,
    ) -> bool:
        command = tuple(str(item) for item in argv)
        child_environment = dict(self.environment)
        child_environment.update(environment or {})
        print(f"\n== {name} ==")
        print(f"$ {_display_command(command)}", flush=True)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command,
                cwd=cwd or self.root,
                env=child_environment,
                shell=False,
                check=False,
                timeout=timeout,
            )
            exit_code = completed.returncode
            status = "PASS" if exit_code == 0 else "FAIL"
        except (OSError, subprocess.TimeoutExpired) as exc:
            exit_code = None
            status = "FAIL"
            print(f"stage failed: {type(exc).__name__}", file=sys.stderr)
        duration = time.monotonic() - started
        self.steps.append(StepResult(name, status, exit_code, duration))
        print(f"[{status}] {name} ({duration:.1f}s)")
        return status == "PASS"

    def add(self, name: str, status: str, exit_code: int | None = None) -> None:
        self.steps.append(StepResult(name, status, exit_code, 0.0))


def _venv_python(environment_root: Path) -> Path:
    return environment_root / "bin" / "python"


def _venv_command(environment_root: Path, name: str) -> Path:
    return environment_root / "bin" / name


def _query(argv: Sequence[str], *, root: Path, environment: Mapping[str, str]) -> str:
    completed = subprocess.run(
        tuple(argv),
        cwd=root,
        env=scrub_environment(environment),
        shell=False,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout.strip()


def write_report(path: Path, result: MatrixResult) -> None:
    """Write status-only evidence; never persist child stdout/stderr or paths."""
    lines = [
        "# macOS Automated Release Test Report",
        "",
        f"- Automated result: `{result.automated_result}`",
        f"- Commit: `{result.commit[:12]}`",
        f"- macOS: `{result.macos_version}`",
        f"- Architecture: `{result.architecture}`",
        "",
        "## Automated matrix",
        "",
        "| Step | Result | Exit | Duration |",
        "|---|---:|---:|---:|",
    ]
    for step in result.steps:
        exit_code = "—" if step.exit_code is None else str(step.exit_code)
        lines.append(
            f"| {step.name} | {step.status} | {exit_code} | {step.duration_seconds:.1f}s |"
        )
    lines.extend(
        [
            "",
            "## Manual boundaries",
            "",
            "These checks are intentionally not automated and remain `NOT_RUN` until",
            "the owner performs them with separate authorization where applicable.",
            "",
            "| Check | Result |",
            "|---|---:|",
        ]
    )
    lines.extend(f"| {name} | NOT_RUN |" for name in MANUAL_FOLLOWUPS)
    lines.extend(
        [
            "",
            "The report contains no command output, credential value, course identity,",
            "provider payload, user-home path, or signed URL.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def print_plan(pythons: Sequence[str]) -> None:
    print("macOS owner-device automated release matrix")
    print("Preflight: Darwin, clean clone, private-file boundary, Git ancestry")
    for version in pythons:
        print(f"Python {version} locked source matrix")
    print("Build + wheel/sdist/metadata + deterministic clean-source audits")
    for version in pythons:
        print(f"Python {version} exact wheel install")
        print(f"Python {version} audited clean-source install")
    print(f"Python {pythons[-1]} exact sdist install")
    print("Native macOS Keychain canary")
    print("MCP host UI: NOT_RUN")
    print("Real Canvas/Ed catalogs: NOT_RUN")
    print("Private legacy actual sync: NOT_RUN")


def normalize_python_versions(values: Sequence[str] | None) -> tuple[str, ...]:
    selected = tuple(dict.fromkeys(values or DEFAULT_PYTHONS))
    if not selected or any(not _PYTHON_SELECTOR.fullmatch(value) for value in selected):
        raise ValueError("Python versions must be one of 3.10 through 3.15")
    return selected


def _run_installed_smoke(
    runner: MatrixRunner,
    *,
    uv: str,
    python_version: str,
    artifact: Path,
    label: str,
    work: Path,
) -> bool:
    environment_root = work / f"install-{label}-{python_version.replace('.', '')}"
    if not runner.run(
        f"Python {python_version} {label} environment",
        (uv, "venv", "--python", python_version, str(environment_root)),
    ):
        return False
    python = _venv_python(environment_root)
    if not runner.run(
        f"Python {python_version} {label} install",
        (uv, "pip", "install", "--python", python, str(artifact)),
    ):
        return False
    commands = (
        ("version", (_venv_command(environment_root, "canvas-ed-mcp"), "--version")),
        ("help", (_venv_command(environment_root, "canvas-ed-mcp"), "--help")),
        ("reserved sync help", (_venv_command(environment_root, "course-sync"), "--help")),
        ("MCP protocol", (python, "-B", runner.root / "ci" / "smoke_installed.py")),
        ("dependency check", (uv, "pip", "check", "--python", python)),
    )
    outside = work / "outside-checkout"
    outside.mkdir(exist_ok=True)
    for suffix, command in commands:
        if not runner.run(
            f"Python {python_version} {label} {suffix}", command, cwd=outside
        ):
            return False
    return True


def run_matrix(args: argparse.Namespace) -> tuple[MatrixResult, Path]:
    root = Path(__file__).resolve().parents[1]
    expected_version = read_project_version(root)
    report_root = (
        args.report_dir.resolve()
        if args.report_dir
        else root / "artifacts" / "macos-owner-device" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )
    report = report_root / "macos-release-report.md"
    runner = MatrixRunner(root=root, environment=os.environ)
    if args.offline:
        runner.environment["UV_OFFLINE"] = "1"
    commit = "unknown"
    macos_version = platform.mac_ver()[0] or "unknown"
    architecture = platform.machine() or "unknown"

    def finish(status: str) -> tuple[MatrixResult, Path]:
        outcome = MatrixResult(commit, macos_version, architecture, tuple(runner.steps), status)
        write_report(report, outcome)
        return outcome, report

    if sys.platform != "darwin":
        runner.add("macOS preflight", "FAIL", 2)
        return finish("FAIL")

    try:
        uv = _query(("command", "-v", "uv"), root=root, environment=os.environ)
    except (OSError, subprocess.SubprocessError):
        # `command` is a shell builtin, so use PATH lookup without invoking a shell.
        import shutil

        uv = shutil.which("uv") or ""
    if not uv:
        runner.add("uv availability", "FAIL", 2)
        return finish("FAIL")
    try:
        uv_version = _query((uv, "--version"), root=root, environment=os.environ)
    except (OSError, subprocess.SubprocessError):
        runner.add("Reviewed uv version", "FAIL", 2)
        return finish("FAIL")
    if not re.match(rf"^{re.escape(REVIEWED_UV_VERSION)}(?:\s|$)", uv_version):
        runner.add("Reviewed uv version", "FAIL", 2)
        return finish("FAIL")
    runner.add("Reviewed uv version", "PASS", 0)

    private_present = sum((root / relative).exists() for relative in PRIVATE_SENTINELS)
    if private_present:
        runner.add("Clean-clone private-file boundary", "FAIL", 2)
        return finish("FAIL")

    try:
        status = _query(
            ("git", "status", "--porcelain", "--untracked-files=normal"),
            root=root,
            environment=os.environ,
        )
        commit = _query(("git", "rev-parse", "HEAD"), root=root, environment=os.environ)
        branch = _query(
            ("git", "branch", "--show-current"), root=root, environment=os.environ
        )
        epoch = _query(("git", "log", "-1", "--pretty=%ct"), root=root, environment=os.environ)
    except (OSError, subprocess.SubprocessError):
        runner.add("Git provenance", "FAIL", 2)
        return finish("FAIL")
    if status:
        runner.add("Clean tracked/untracked tree", "FAIL", 2)
        return finish("FAIL")
    if branch != "hy/phase-3-4-portability":
        runner.add("Expected private release branch", "FAIL", 2)
        return finish("FAIL")
    runner.add("Clean clone and Git provenance", "PASS", 0)
    if not runner.run(
        "Baseline ancestry",
        ("git", "merge-base", "--is-ancestor", BASELINE_ANCESTOR, "HEAD"),
    ):
        return finish("FAIL")

    with tempfile.TemporaryDirectory(prefix="canvas-ed-mcp-macos-") as temporary:
        work = Path(temporary)
        outside = work / "outside-checkout"
        outside.mkdir()
        project_envs: dict[str, Path] = {}
        for version in args.python:
            environment_root = work / f"source-py{version.replace('.', '')}"
            project_envs[version] = environment_root
            uv_environment = {"UV_PROJECT_ENVIRONMENT": str(environment_root)}
            if args.offline:
                uv_environment["UV_OFFLINE"] = "1"
            stages = (
                (
                    f"Python {version} locked sync",
                    (uv, "sync", "--python", version, "--locked", "--all-extras", "--no-dev", "--no-editable"),
                    None,
                ),
                (
                    f"Python {version} version contract",
                    (_venv_python(environment_root), "-B", "-c", f"import canvas_ed_mcp; assert canvas_ed_mcp.__version__ == '{expected_version}'"),
                    outside,
                ),
                (
                    f"Python {version} CLI doctor",
                    (_venv_command(environment_root, "canvas-ed-mcp"), "doctor", "--show-paths"),
                    None,
                ),
                (
                    f"Python {version} reserved sync help",
                    (_venv_command(environment_root, "course-sync"), "--help"),
                    None,
                ),
                (
                    f"Python {version} synthetic suite",
                    (_venv_python(environment_root), "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"),
                    None,
                ),
                (
                    f"Python {version} dependency check",
                    (uv, "pip", "check", "--python", _venv_python(environment_root)),
                    None,
                ),
            )
            for name, command, cwd in stages:
                if not runner.run(name, command, cwd=cwd, environment=uv_environment):
                    return finish("FAIL")

        primary_version = args.python[-1]
        primary_environment = project_envs[primary_version]
        primary_python = _venv_python(primary_environment)
        if not runner.run("Lockfile check", (uv, "lock", "--check")):
            return finish("FAIL")
        dist = work / "dist"
        if not runner.run(
            "Build wheel and sdist",
            (uv, "build", "--out-dir", dist),
        ):
            return finish("FAIL")
        if not runner.run(
            "Distribution manifest audit",
            (primary_python, "-B", root / "ci" / "verify_distribution.py", dist),
        ):
            return finish("FAIL")
        if not runner.run(
            "Release metadata audit",
            (primary_python, "-B", root / "ci" / "verify_release_metadata.py"),
        ):
            return finish("FAIL")

        release_a = work / "clean-source-a"
        release_b = work / "clean-source-b"
        for suffix, destination in (("A", release_a), ("B", release_b)):
            if not runner.run(
                f"Deterministic clean-source audit {suffix}",
                (
                    primary_python,
                    "-B",
                    root / "ci" / "release_gate.py",
                    "--root",
                    root,
                    "--output-dir",
                    destination,
                    "--source-date-epoch",
                    epoch,
                ),
            ):
                return finish("FAIL")
        source_name = f"canvas-ed-mcp-{expected_version}-source.tar.gz"
        source_a = release_a / source_name
        source_b = release_b / source_name
        if source_a.read_bytes() != source_b.read_bytes():
            runner.add("Deterministic clean-source comparison", "FAIL", 1)
            return finish("FAIL")
        runner.add("Deterministic clean-source comparison", "PASS", 0)

        wheels = tuple(dist.glob("*.whl"))
        sdists = tuple(dist.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            runner.add("Built artifact selection", "FAIL", 1)
            return finish("FAIL")
        runner.add("Built artifact selection", "PASS", 0)
        for version in args.python:
            if not _run_installed_smoke(
                runner,
                uv=uv,
                python_version=version,
                artifact=wheels[0],
                label="exact wheel",
                work=work,
            ):
                return finish("FAIL")
            if not _run_installed_smoke(
                runner,
                uv=uv,
                python_version=version,
                artifact=source_a,
                label="audited clean source",
                work=work,
            ):
                return finish("FAIL")
        if not _run_installed_smoke(
            runner,
            uv=uv,
            python_version=primary_version,
            artifact=sdists[0],
            label="exact sdist",
            work=work,
        ):
            return finish("FAIL")

        if args.skip_keychain:
            runner.add("Native macOS Keychain canary", "NOT_RUN")
            return finish("DEGRADED")
        if not runner.run(
            "Native macOS Keychain canary",
            (primary_python, "-B", root / "ci" / "macos_keychain_canary.py"),
        ):
            return finish("FAIL")

    return finish("PASS")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="print stages without running them")
    parser.add_argument("--python", action="append", help="Python version; repeat for a matrix")
    parser.add_argument("--offline", action="store_true", help="require existing uv caches")
    parser.add_argument("--skip-keychain", action="store_true", help="record Keychain as NOT_RUN")
    parser.add_argument("--report-dir", type=Path, help="directory for the status-only report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.python = normalize_python_versions(args.python)
    except ValueError as exc:
        build_parser().error(str(exc))
    if args.plan:
        print_plan(args.python)
        return 0
    result, report = run_matrix(args)
    print(f"\nAutomated macOS result: {result.automated_result}")
    print(f"Status-only report: {report}")
    if result.automated_result == "PASS":
        return 0
    if result.automated_result == "DEGRADED":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
