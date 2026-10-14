"""Exercise the installed wheel from outside the source checkout."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from canvas_ed_mcp.config import AppConfig, CanvasPlatformConfig, PlatformsConfig, save_config
from canvas_ed_mcp.providers.canvas import CanvasProvider
from canvas_ed_mcp.providers.ed import EdProvider
from canvas_ed_mcp.providers.gradescope import GradescopeProvider
from canvas_ed_mcp.server import create_server
from canvas_ed_mcp.sync.model import RemoteItem


async def _run() -> None:
    # Keep imports live so a wheel missing a runtime branch fails before protocol startup.
    assert all((create_server, CanvasProvider, EdProvider, GradescopeProvider, RemoteItem))
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        config_path = root / "config.toml"
        output_root = root / "output"
        save_config(
            AppConfig(
                profile="installed-smoke",
                output_root=output_root,
                platforms=PlatformsConfig(
                    canvas=CanvasPlatformConfig(
                        enabled=True,
                        base_url="https://canvas.invalid/api/v1",
                        credential="env:CI_CANVAS_NEVER_READ",
                    )
                ),
            ),
            config_path,
        )
        before = config_path.read_bytes()
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        environment.pop("CI_CANVAS_NEVER_READ", None)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-B", "-m", "canvas_ed_mcp.cli", "serve", "--config", str(config_path)],
            env=environment,
            cwd=str(root),
        )
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
            async with stdio_client(parameters, errlog=stderr) as (read, write):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=10)
                ) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    names = {tool.name for tool in listed.tools}
                    if names != {"server_status", "canvas_list_configured_courses"}:
                        raise RuntimeError(f"unexpected installed tool catalog: {sorted(names)}")
                    result = await session.call_tool("server_status", {})
                    if result.isError:
                        raise RuntimeError("installed server_status returned an error")
                    structured = getattr(result, "structuredContent", None)
                    data = structured if isinstance(structured, dict) else json.loads(result.content[0].text)
                    if data.get("enabled_providers") != ["canvas"]:
                        raise RuntimeError("installed status did not preserve the configured provider")
        if config_path.read_bytes() != before or output_root.exists():
            raise RuntimeError("installed read-only smoke mutated config or output")


def main() -> int:
    asyncio.run(asyncio.wait_for(_run(), timeout=20))
    print("installed wheel protocol smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
