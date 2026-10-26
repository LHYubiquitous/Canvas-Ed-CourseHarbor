"""PyInstaller console companion for CourseHarbor shortcuts and recovery."""

from __future__ import annotations

import json
import ssl
import sys


def _bundle_smoke() -> int:
    try:
        import httpx

        from canvas_ed_mcp.secrets import keyring_status
    except ImportError:
        print(json.dumps({"backend": "unavailable", "keyring": "missing_dependency"}))
        return 4
    try:
        status = keyring_status()
    except Exception:
        print(json.dumps({"backend": "unavailable", "keyring": "backend_error"}))
        return 3
    print(
        json.dumps(
            {
                "backend": status.backend,
                "httpx": httpx.__version__,
                "keyring": status.category,
                "ssl": ssl.OPENSSL_VERSION,
            },
            sort_keys=True,
        )
    )
    return 0 if status.available else 3


def main() -> int:
    if sys.argv[1:] == ["--bundle-smoke"]:
        return _bundle_smoke()
    from canvas_ed_mcp.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
