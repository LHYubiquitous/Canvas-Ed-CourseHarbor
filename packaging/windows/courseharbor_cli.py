"""PyInstaller console companion for CourseHarbor shortcuts and recovery."""

from __future__ import annotations

import json
import ssl
import sys


def _bundle_smoke() -> int:
    try:
        import httpx
        from httpcore._backends.anyio import AnyIOBackend
        from canvas_ed_mcp.cancellable_http import CancellableTransport

        from canvas_ed_mcp.secrets import keyring_status
    except ImportError:
        print(json.dumps({"backend": "unavailable", "keyring": "missing_dependency"}))
        return 4
    try:
        # Exercise the packaged async bridge and include the real network backend.
        assert AnyIOBackend is not None
        async def respond(request):
            return httpx.Response(200, content=b"transport-ready")
        with httpx.Client(transport=CancellableTransport(transport=httpx.MockTransport(respond))) as client:
            if client.get("https://runtime.invalid/check").content != b"transport-ready":
                return 5
    except Exception:
        print(json.dumps({"transport_runtime": "failed", "keyring": "backend_error"}))
        return 5
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
                "transport_runtime": "ready",
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
