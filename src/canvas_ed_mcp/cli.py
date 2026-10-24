"""Stable console entry point for the staged portable implementation."""

from __future__ import annotations

import argparse
from collections import Counter
import io
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from contextlib import redirect_stdout
from pathlib import Path

from . import __version__
from .config import ConfigLoadError, load_config
from .paths import resolve_profile_paths
from .secrets import keyring_status


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="canvas-ed-mcp", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    setup = subparsers.add_parser("setup", help="create a reviewed configuration plan")
    setup.add_argument("--profile", default="default")
    setup.add_argument("--config", type=Path)
    setup.add_argument("--dry-run", action="store_true")
    setup.set_defaults(handler=_setup)

    configure = subparsers.add_parser(
        "configure", help="review and change an existing profile"
    )
    configure.add_argument("--profile", default="default")
    configure.add_argument("--config", type=Path)
    configure.add_argument("--dry-run", action="store_true")
    configure.set_defaults(handler=_configure)

    configure_gui = subparsers.add_parser(
        "configure-gui", help="open graphical course reconfiguration"
    )
    configure_gui.add_argument("--profile", default="default")
    configure_gui.add_argument("--config", type=Path)
    configure_gui.set_defaults(handler=_configure_gui)

    sync = subparsers.add_parser("sync", help="synchronize confirmed Canvas/Ed courses")
    sync.add_argument("--profile", default="default")
    sync.add_argument("--config", type=Path)
    sync.add_argument("--course", action="append", metavar="COURSE_KEY")
    sync.add_argument("--dry-run", action="store_true")
    sync.set_defaults(handler=_sync)

    app = subparsers.add_parser("app", help="open the daily-use terminal menu")
    app.add_argument("--profile", default="default")
    app.add_argument("--config", type=Path)
    app.set_defaults(handler=_app)

    due = subparsers.add_parser("due", help="create due-date reports")
    due_subparsers = due.add_subparsers(dest="due_command", metavar="REPORT")
    for report_name, report_help in (
        ("week", "create this week's due report"),
        ("semester", "create the semester due report"),
    ):
        report = due_subparsers.add_parser(report_name, help=report_help)
        report.add_argument("--profile", default="default")
        report.add_argument("--config", type=Path)
        report.set_defaults(handler=_due)

    launchers = subparsers.add_parser("launchers", help="manage convenience launchers")
    launcher_subparsers = launchers.add_subparsers(dest="launcher_command", metavar="ACTION")
    install = launcher_subparsers.add_parser("install", help="install profile launchers")
    install.add_argument("--profile", default="default")
    install.add_argument("--config", type=Path)
    install.add_argument("--directory", type=Path)
    install.set_defaults(handler=_install_launchers)

    open_folder = subparsers.add_parser(
        "open-course-folder", help="choose and open a configured course folder"
    )
    open_folder.add_argument("--profile", default="default")
    open_folder.add_argument("--config", type=Path)
    open_folder.set_defaults(handler=_open_course_folder)

    serve = subparsers.add_parser("serve", help="run the provider-aware MCP server")
    serve.add_argument("--profile", default="default")
    serve.add_argument("--config", type=Path)
    serve.set_defaults(handler=_serve)

    doctor = subparsers.add_parser("doctor", help="show non-secret diagnostics")
    doctor.add_argument("--profile", default="default")
    doctor.add_argument("--config", type=Path)
    doctor.add_argument("--show-paths", action="store_true")
    doctor.set_defaults(handler=_doctor)

    paths = subparsers.add_parser("paths", help="show resolved non-secret profile paths")
    paths.add_argument("--profile", default="default")
    paths.set_defaults(handler=_paths)

    config = subparsers.add_parser("config", help="validate portable configuration")
    config_subparsers = config.add_subparsers(dest="config_command", metavar="CONFIG_COMMAND")
    validate = config_subparsers.add_parser("validate", help="validate one TOML config")
    validate.add_argument("path", type=Path)
    validate.set_defaults(handler=_validate_config)
    return parser


def _not_implemented(args: argparse.Namespace) -> int:
    raise SystemExit(
        f"{args.command} is reserved by the portable CLI but is not implemented in Phase 1 yet"
    )


def _setup(args: argparse.Namespace) -> int:
    from .providers.canvas import CanvasProvider
    from .providers.ed import EdProvider
    from .secrets import SecretUnavailable, resolve_secret
    from .setup_flow import SetupConflictError, run_setup_wizard

    profile_paths = resolve_profile_paths(args.profile)
    config_path = args.config or profile_paths.config_file

    def load_canvas_catalog(base_url: str, token: str):
        provider = CanvasProvider(base_url, token)
        try:
            return provider.list_courses()
        finally:
            provider.close()

    def load_ed_catalog(token: str):
        provider = EdProvider(token)
        try:
            return provider.list_courses()
        finally:
            provider.close()

    try:
        committed = run_setup_wizard(
            profile=args.profile,
            config_path=config_path,
            input_fn=input,
            output_fn=print,
            secret_resolver=resolve_secret,
            canvas_catalog_loader=load_canvas_catalog,
            ed_catalog_loader=load_ed_catalog,
            default_output_root=Path.home() / "Desktop",
            dry_run=args.dry_run,
        )
    except SecretUnavailable as exc:
        print(f"setup failed ({exc.category})", file=sys.stderr)
        return 2
    except SetupConflictError:
        print("setup failed (config_exists)", file=sys.stderr)
        return 2
    except ValueError:
        print("setup failed (invalid_selection)", file=sys.stderr)
        return 2
    except OSError:
        print("setup failed (config_write_failed)", file=sys.stderr)
        return 2
    except Exception:
        print("setup failed (provider_unavailable)", file=sys.stderr)
        return 2
    if committed and not args.dry_run:
        _install_launchers_after_commit(args.profile, config_path)
    return 0 if committed or args.dry_run else 2


def _configure(args: argparse.Namespace) -> int:
    from .configure_flow import ConfigureConflictError, run_configure_wizard
    from .providers.canvas import CanvasProvider
    from .providers.ed import EdProvider
    from .secrets import SecretUnavailable, resolve_secret

    profile_paths = resolve_profile_paths(args.profile)
    config_path = args.config or profile_paths.config_file

    def load_canvas_catalog(base_url: str, token: str):
        provider = CanvasProvider(base_url, token)
        try:
            return provider.list_courses()
        finally:
            provider.close()

    def load_ed_catalog(token: str):
        provider = EdProvider(token)
        try:
            return provider.list_courses()
        finally:
            provider.close()

    try:
        committed = run_configure_wizard(
            profile=args.profile,
            config_path=config_path,
            input_fn=input,
            output_fn=print,
            secret_resolver=resolve_secret,
            canvas_catalog_loader=load_canvas_catalog,
            ed_catalog_loader=load_ed_catalog,
            dry_run=args.dry_run,
        )
    except ConfigLoadError as exc:
        print(f"configure failed ({exc.category})", file=sys.stderr)
        return 2
    except SecretUnavailable as exc:
        print(f"configure failed ({exc.category})", file=sys.stderr)
        return 2
    except ConfigureConflictError:
        print("configure failed (config_conflict)", file=sys.stderr)
        return 2
    except ValueError:
        print("configure failed (invalid_selection)", file=sys.stderr)
        return 2
    except OSError:
        print("configure failed (config_write_failed)", file=sys.stderr)
        return 2
    except Exception:
        print("configure failed (provider_unavailable)", file=sys.stderr)
        return 2
    if committed and not args.dry_run:
        _install_launchers_after_commit(args.profile, config_path)
    return 0 if committed or args.dry_run else 2


def _configure_gui(args: argparse.Namespace) -> int:
    try:
        from .ui.configure_window import main as configure_main
    except ImportError:
        print(
            "Graphical reconfiguration is unavailable; continuing in the terminal.",
            file=sys.stderr,
        )
        fallback = argparse.Namespace(
            profile=args.profile,
            config=args.config,
            dry_run=False,
        )
        return _configure(fallback)
    forwarded = ["--profile", args.profile]
    if args.config is not None:
        forwarded.extend(("--config", str(args.config)))
    return int(configure_main(forwarded))


def _config_for_args(args: argparse.Namespace):
    paths = resolve_profile_paths(args.profile)
    config_path = args.config or paths.config_file
    config = load_config(config_path)
    if config.profile != paths.profile:
        raise ConfigLoadError("profile_mismatch", "config profile does not match")
    return paths, config_path, config


def _launcher_directory(config) -> Path:
    return config.daily_use.launchers.directory or config.output_root / "Canvas Ed MCP Launchers"


def _launcher_python_executable(executable: str | None = None) -> Path:
    """Keep a virtual-environment entry path instead of resolving its symlink."""
    selected = Path(executable or sys.executable)
    return selected if selected.is_absolute() else Path.cwd() / selected


def _install_launchers_after_commit(profile: str, config_path: Path) -> None:
    try:
        config = load_config(config_path)
        _install_profile_launchers(profile, config_path, config, None)
    except Exception:
        print(
            "Configuration was saved, but convenience launchers could not be installed. "
            "Run 'canvas-ed-mcp launchers install' to retry.",
            file=sys.stderr,
        )


def _install_profile_launchers(profile: str, config_path: Path, config, directory: Path | None):
    from .launchers import install_launchers, render_launchers

    if not config.daily_use.launchers.enabled and directory is None:
        print("Launcher installation is disabled for this profile.")
        return None
    target = directory or _launcher_directory(config)
    rendered = render_launchers(
        profile=profile,
        config_path=config_path.resolve(),
        python_executable=_launcher_python_executable(),
        platform=sys.platform,
    )
    result = install_launchers(target, rendered)
    print(f"Launchers directory: {target}")
    print(f"Created: {len(result.created)}; reused: {len(result.reused)}; preserved: {len(result.preserved)}")
    if result.preserved:
        print("Edited launchers were preserved; reviewed replacements use the .new suffix.")
    return result


def _install_launchers(args: argparse.Namespace) -> int:
    try:
        _, config_path, config = _config_for_args(args)
        _install_profile_launchers(args.profile, config_path, config, args.directory)
    except ConfigLoadError as exc:
        print(f"launcher install failed ({exc.category})", file=sys.stderr)
        return 2
    except OSError:
        print("launcher install failed (write_failed)", file=sys.stderr)
        return 2
    return 0


def _due_providers(config):
    from .providers.canvas import CanvasProvider
    from .providers.ed import EdProvider
    from .secrets import resolve_secret

    providers: dict[tuple[str, str], object] = {}
    identities = dict.fromkeys(
        (source.provider, source.instance)
        for course in config.courses
        for source in course.sources
        if source.provider in {"canvas", "ed"}
    )
    for provider_name, instance in identities:
        candidate = None
        try:
            if provider_name == "canvas":
                platform = config.platforms.canvas
                candidate = CanvasProvider(platform.base_url, resolve_secret(platform.credential))
            else:
                platform = config.platforms.ed
                candidate = EdProvider(resolve_secret(platform.credential))
            if candidate.instance != instance:
                raise ValueError("provider_instance_mismatch")
            providers[(provider_name, instance)] = candidate
        except Exception:
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
    if config.daily_use.due.gradescope_courses:
        candidate = None
        try:
            from .providers.gradescope import GradescopeProvider

            platform = config.platforms.gradescope
            candidate = GradescopeProvider(
                resolve_secret(platform.email_credential),
                resolve_secret(platform.password_credential),
            )
            candidate.list_courses()
            providers[("gradescope", candidate.instance)] = candidate
        except Exception:
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
    return providers


def _due(args: argparse.Namespace) -> int:
    from datetime import datetime

    from .due.render import (
        SYDNEY,
        latest_versioned_report,
        publish_versioned_report,
        render_semester_markdown,
        render_week_text,
        select_week_items,
    )
    from .due.service import DueStateError, collect_configured_due_items
    from .secrets import SecretUnavailable

    providers: dict[tuple[str, str], object] = {}
    try:
        paths, _, config = _config_for_args(args)
        if not config.daily_use.due.enabled:
            print("Due reports are disabled for this profile.", file=sys.stderr)
            return 2
        providers = _due_providers(config)
        collection = collect_configured_due_items(
            config, providers, paths.state_file.parent / "due-state-v1.json"
        )
        if not collection.publishable:
            print(
                "Due report was not published because a required source has no complete snapshot.",
                file=sys.stderr,
            )
            return 1
        report_dir = config.output_root / "Due Dates"
        if args.due_command == "week":
            report_now = datetime.now(SYDNEY)
            report_items = select_week_items(collection.items, now=report_now)
            content = render_week_text(report_items, now=report_now)
            target = publish_versioned_report(report_dir, "this-week-due.txt", content)
        else:
            terms = sorted({source.term for course in config.courses for source in course.sources if source.term})
            semester_label = (
                config.daily_use.due.semester_label
                or " / ".join(terms)
                or f"{datetime.now(SYDNEY).year} Semester"
            )
            latest = latest_versioned_report(report_dir, "this-semester-due.md")
            existing = latest.read_text(encoding="utf-8") if latest else ""
            content = render_semester_markdown(
                collection.items,
                semester_label=semester_label,
                existing_markdown=existing,
                teaching_start=config.daily_use.due.teaching_start,
                teaching_periods=tuple(
                    (period.label, period.start_date, period.end_date)
                    for period in config.daily_use.due.teaching_periods
                ),
            )
            target = publish_versioned_report(report_dir, "this-semester-due.md", content)
            report_items = collection.items
        print(f"Report created: {target}")
        print(f"Due items in report: {len(report_items)}")
        if collection.used_last_good:
            print("Warning: at least one source used its last complete snapshot.")
        if collection.issues:
            print("Source warnings: " + ", ".join(sorted({issue.code for issue in collection.issues})))
            return 1
        return 0
    except ConfigLoadError as exc:
        print(f"due report failed ({exc.category})", file=sys.stderr)
    except SecretUnavailable as exc:
        print(f"due report failed ({exc.category})", file=sys.stderr)
    except DueStateError:
        print("due report failed (invalid_due_state)", file=sys.stderr)
    except Exception:
        print("due report failed (provider_unavailable)", file=sys.stderr)
    finally:
        for provider in providers.values():
            try:
                provider.close()
            except Exception:
                pass
    return 2


def _app(args: argparse.Namespace) -> int:
    from .app_menu import run_menu

    config_path = args.config or resolve_profile_paths(args.profile).config_file
    common = ["--profile", args.profile, "--config", str(config_path)]
    actions = {
        "1": lambda: _menu_sync(common),
        "2": lambda: main(["due", "week", *common]),
        "3": lambda: main(["due", "semester", *common]),
        "4": lambda: main(["configure-gui", *common]),
        "5": lambda: main(["doctor", "--profile", args.profile, "--show-paths"]),
        "6": lambda: main(["open-course-folder", *common]),
        "7": lambda: main(["launchers", "install", *common]),
    }
    return run_menu(actions=actions)


def _menu_sync(common: list[str], *, input_fn=input) -> int:
    preview, preview_report = _menu_sync_call(["sync", *common, "--dry-run"])
    preview_with_warnings = preview != 0 and _menu_sync_report_is_actionable(
        preview_report, committed=False
    )
    if preview and not preview_with_warnings:
        print("Sync preview did not pass; no content update was applied.")
        return preview
    prompt = (
        "Apply the available course material despite the reported warnings? [y/N]: "
        if preview_with_warnings
        else "Apply the reviewed course material sync? [y/N]: "
    )
    answer = input_fn(prompt).strip().casefold()
    if answer not in {"y", "yes"}:
        print("Sync cancelled; no content update was applied.")
        return 0
    applied, apply_report = _menu_sync_call(["sync", *common])
    if applied and _menu_sync_report_is_actionable(apply_report, committed=True):
        print("Course material sync completed with reported warnings.")
        return 0
    return applied


def _menu_sync_call(argv: list[str]) -> tuple[int, dict[str, object] | None]:
    output = io.StringIO()
    with redirect_stdout(output):
        exit_code = main(argv)
    rendered = output.getvalue()
    for line in reversed(rendered.splitlines()):
        try:
            candidate = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(candidate, dict):
            _print_menu_sync_report(candidate)
            return exit_code, candidate
    print(rendered, end="")
    return exit_code, None


def _print_menu_sync_report(report: dict[str, object]) -> None:
    sources = report.get("sources")
    if not isinstance(sources, list):
        print("Course material report could not be summarized safely.")
        return
    preview = any(
        isinstance(source, dict) and source.get("dry_run") is True
        for source in sources
    )
    print("\nCourse material preview" if preview else "\nCourse material result")
    print(f"Checked {len(sources)} configured source(s):")
    action_labels = {
        "would_fetch": "to download",
        "record_only": "baseline records",
        "skip": "unchanged",
        "policy_skip": "skipped by policy",
        "install_new": "downloaded",
        "install_separately": "saved separately",
        "deduplicate": "already present",
        "failed": "failed",
        "block": "blocked",
    }
    issue_labels = {
        "authorization_failed": "some content was not permitted by the provider",
        "authentication_failed": "the token was rejected",
        "provider_discovery_failed": "the course contents could not be listed",
        "item_schema_drift": "one or more items used an unfamiliar format",
    }
    for source in sources:
        if not isinstance(source, dict):
            continue
        course = str(source.get("course_key") or "Unknown course")
        provider = str(source.get("provider") or "unknown").title()
        actions = source.get("actions")
        counts = Counter(actions) if isinstance(actions, list) else Counter()
        parts = [
            f"{count} {action_labels.get(action, action.replace('_', ' '))}"
            for action, count in counts.items()
        ]
        summary = ", ".join(parts) if parts else "no changes"
        issues = source.get("issue_codes")
        if isinstance(issues, list) and issues:
            warnings = "; ".join(
                issue_labels.get(str(issue), str(issue).replace("_", " "))
                for issue in issues
            )
            summary += f" — warning: {warnings}"
        print(f"- {course} — {provider}: {summary}")
    run_issues = report.get("run_issue_codes")
    if isinstance(run_issues, list) and run_issues:
        print("Run warning: " + ", ".join(str(issue) for issue in run_issues))


def _menu_sync_report_is_actionable(
    report: dict[str, object] | None, *, committed: bool
) -> bool:
    if report is None or report.get("run_issue_codes"):
        return False
    selected = report.get("selected_source_count")
    executed = report.get("executed_source_count")
    sources = report.get("sources")
    if (
        not isinstance(selected, int)
        or isinstance(selected, bool)
        or selected < 1
        or executed != selected
        or not isinstance(sources, list)
        or len(sources) != selected
    ):
        return False
    has_actionable_work = False
    for source in sources:
        if not isinstance(source, dict) or source.get("committed") is not committed:
            return False
        actions = source.get("actions")
        if not isinstance(actions, list):
            return False
        has_actionable_work = has_actionable_work or bool(actions)
    return has_actionable_work


def _open_course_folder(args: argparse.Namespace) -> int:
    try:
        _, _, config = _config_for_args(args)
    except ConfigLoadError as exc:
        print(f"open folder failed ({exc.category})", file=sys.stderr)
        return 2
    if not config.courses:
        print("No configured courses were found.")
        return 1
    print("Configured course folders:")
    for index, course in enumerate(config.courses, 1):
        print(f"{index}. {course.folder}")
    answer = input("Select a course number (blank cancels): ").strip()
    if not answer:
        print("Cancelled.")
        return 0
    try:
        course = config.courses[int(answer) - 1]
    except (ValueError, IndexError):
        print("Invalid selection.", file=sys.stderr)
        return 2
    target = config.output_root / course.folder
    if not target.is_dir():
        print("Course folder does not exist yet. Run course material sync first.", file=sys.stderr)
        return 1
    try:
        target.resolve(strict=True).relative_to(config.output_root.resolve(strict=True))
    except (OSError, ValueError):
        print("Course folder is outside the configured output root.", file=sys.stderr)
        return 2
    if sys.platform == "win32":
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return 0


def _serve(args: argparse.Namespace) -> int:
    from .server import create_server

    config_path = args.config or resolve_profile_paths(args.profile).config_file
    try:
        config = load_config(config_path)
    except ConfigLoadError as exc:
        print(f"server config failed ({exc.category})", file=sys.stderr)
        return 2
    server = create_server(config)
    server.run(transport="stdio")
    return 0


def _sync(args: argparse.Namespace) -> int:
    """Run explicitly configured Canvas and Ed content sources."""
    from .providers.canvas import CanvasProvider
    from .providers.ed import EdProvider
    from .secrets import SecretUnavailable, resolve_secret
    from .sync.engine import apply_configured_sources, preview_configured_source

    paths = resolve_profile_paths(args.profile)
    config_path = args.config or paths.config_file
    try:
        config = load_config(config_path)
    except ConfigLoadError as exc:
        print(f"sync config failed ({exc.category})", file=sys.stderr)
        return 2
    if config.profile != paths.profile:
        print("sync config failed (profile_mismatch)", file=sys.stderr)
        return 2
    selected = tuple(course for course in config.courses if not args.course or course.key in args.course)
    if args.course and len({course.key for course in selected}) != len(set(args.course)):
        print("sync config failed (unknown_course)", file=sys.stderr)
        return 2
    sources = tuple(
        (course, source)
        for course in selected
        for source in course.sources
        if source.provider in {"canvas", "ed"}
    )
    if not sources:
        print("sync unavailable (content_not_configured)", file=sys.stderr)
        return 2
    source_identities = tuple(dict.fromkeys(
        (source.provider, source.instance) for _, source in sources
    ))
    providers: dict[tuple[str, str], object] = {}
    provider_error: str | None = None
    for provider_name, instance in source_identities:
        try:
            if provider_name == "canvas":
                platform = config.platforms.canvas
                if not platform.enabled or not platform.base_url or not platform.credential:
                    provider_error = "canvas_not_configured"
                    break
                token = resolve_secret(platform.credential)
                candidate = CanvasProvider(platform.base_url, token)
            else:
                platform = config.platforms.ed
                if not platform.enabled or not platform.credential:
                    provider_error = "ed_not_configured"
                    break
                if platform.region != "global":
                    provider_error = "ed_region_unsupported"
                    break
                token = resolve_secret(platform.credential)
                candidate = EdProvider(token)
            if candidate.instance != instance:
                try:
                    candidate.close()
                except Exception:
                    pass
                provider_error = "provider_instance_mismatch"
                break
            providers[(provider_name, instance)] = candidate
        except SecretUnavailable as exc:
            provider_error = exc.category
            break
        except Exception:
            provider_error = f"{provider_name}_provider_unavailable"
            break
    if provider_error is not None:
        for candidate in providers.values():
            try:
                candidate.close()
            except Exception:
                pass
        print(f"sync unavailable ({provider_error})", file=sys.stderr)
        return 2
    records: list[dict[str, object]] = []
    run_issue_codes: list[str] = []
    failed = False
    try:
        execution_pairs: tuple[tuple[tuple[object, object], object | None], ...]
        if args.dry_run:
            execution_pairs = tuple((selection, None) for selection in sources)
        else:
            try:
                run = apply_configured_sources(
                    profile=config.profile,
                    selections=sources,
                    providers=providers,
                    output_root=config.output_root,
                    state_path=paths.state_file,
                    backup_path=paths.state_backup_file,
                    lock_dir=paths.lock_dir,
                    cache_dir=paths.cache_dir,
                )
            except Exception:
                failed = True
                run_issue_codes.append("sync_run_failed")
                execution_pairs = ()
            else:
                if run.issue_code is not None:
                    failed = True
                    run_issue_codes.append(run.issue_code)
                execution_pairs = tuple(zip(sources, run.transactions))
        for (course, source), transaction in execution_pairs:
            provider = providers[(source.provider, source.instance)]
            try:
                if transaction is None:
                    result = preview_configured_source(
                        profile=config.profile, course=course, source=source, provider=provider,
                        output_root=config.output_root, state_path=paths.state_file,
                    )
                    committed = False
                else:
                    result = transaction.source_result
                    committed = transaction.commit.committed
                    failed = failed or not committed
                failed = failed or bool(result.issue_codes)
                records.append({
                    "course_key": course.key,
                    "provider": source.provider,
                    "dry_run": bool(args.dry_run),
                    "committed": committed,
                    "actions": [item.action for item in result.items],
                    "issue_codes": list(result.issue_codes),
                })
            except Exception:
                failed = True
                records.append({
                    "course_key": course.key,
                    "provider": source.provider,
                    "dry_run": bool(args.dry_run),
                    "committed": False,
                    "actions": [],
                    "issue_codes": ["source_sync_failed"],
                })
    finally:
        for provider in providers.values():
            try:
                provider.close()
            except Exception:
                failed = True
                if "provider_close_failed" not in run_issue_codes:
                    run_issue_codes.append("provider_close_failed")
    print(json.dumps({
        "sources": records,
        "run_issue_codes": run_issue_codes,
        "selected_source_count": len(sources),
        "executed_source_count": len(records),
    }, sort_keys=True))
    return 1 if failed else 0


def _doctor(args: argparse.Namespace) -> int:
    paths = resolve_profile_paths(args.profile)
    secret_backend = keyring_status()
    result = {
        "profile": paths.profile,
        "package_version": __version__,
        "keyring": {
            "available": secret_backend.available,
            "category": secret_backend.category,
            "backend": secret_backend.backend,
        },
    }
    if args.show_paths:
        result["config_file"] = str(paths.config_file)
        result["state_file"] = str(paths.state_file)
        result["log_dir"] = str(paths.log_dir)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _paths(args: argparse.Namespace) -> int:
    paths = resolve_profile_paths(args.profile)
    print(
        json.dumps(
            {
                "profile": paths.profile,
                "config_file": str(paths.config_file),
                "state_file": str(paths.state_file),
                "log_dir": str(paths.log_dir),
                "cache_dir": str(paths.cache_dir),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _validate_config(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.path)
    except ConfigLoadError as exc:
        print(f"config validation failed ({exc.category})")
        return 2
    print(f"config valid: schema={config.schema_version} profile={config.profile}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    return int(args.handler(args))


def configure_gui_main(argv: Sequence[str] | None = None) -> int:
    """Parse desktop options before importing the optional Qt runtime."""
    parser = argparse.ArgumentParser(
        prog="canvas-ed-configure",
        description="Reconfigure an existing Canvas & Ed profile",
    )
    parser.add_argument("--profile", default="default")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    return _configure_gui(args)


def course_sync_main(argv: Sequence[str] | None = None) -> int:
    """Reserved portable sync entry point; legacy launcher remains operational."""
    parser = argparse.ArgumentParser(
        prog="course-sync",
        description=(
            "Reserved compatibility entry point. Use reviewed canvas-ed-mcp sync for configured "
            "Canvas/Ed sources; existing legacy users may keep using run_course_sync.command."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    parser.print_usage()
    print("course-sync is reserved; no network or filesystem action was performed")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
