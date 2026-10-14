"""Stable console entry point for the staged portable implementation."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
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

    for command, help_text in (
        ("configure", "preview changes to an existing profile"),
        ("sync", "synchronize confirmed courses"),
    ):
        child = subparsers.add_parser(command, help=help_text)
        child.add_argument("--profile", default="default")
        child.set_defaults(handler=_not_implemented)

    serve = subparsers.add_parser("serve", help="run the provider-aware MCP server")
    serve.add_argument("--profile", default="default")
    serve.add_argument("--config", type=Path)
    serve.set_defaults(handler=_serve)

    doctor = subparsers.add_parser("doctor", help="show non-secret diagnostics")
    doctor.add_argument("--profile", default="default")
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
    from .secrets import resolve_secret
    from .setup_flow import run_setup_wizard

    profile_paths = resolve_profile_paths(args.profile)
    config_path = args.config or profile_paths.config_file

    def load_canvas_catalog(base_url: str, token: str):
        provider = CanvasProvider(base_url, token)
        try:
            return provider.list_courses()
        finally:
            provider.close()

    committed = run_setup_wizard(
        profile=args.profile,
        config_path=config_path,
        input_fn=input,
        output_fn=print,
        secret_resolver=resolve_secret,
        canvas_catalog_loader=load_canvas_catalog,
        default_output_root=Path.home() / "Desktop",
        dry_run=args.dry_run,
    )
    return 0 if committed or args.dry_run else 2


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


def course_sync_main(argv: Sequence[str] | None = None) -> int:
    """Reserved portable sync entry point; legacy launcher remains operational."""
    parser = argparse.ArgumentParser(
        prog="course-sync",
        description=(
            "Portable course sync entry point. The Phase 1 provider engine is not connected yet; "
            "existing users should keep using run_course_sync.command."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    parser.print_usage()
    print("course-sync is reserved; no network or filesystem action was performed")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
