from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseConfig,
    CourseSource,
    EdPlatformConfig,
    GradescopePlatformConfig,
    PlatformsConfig,
    save_config,
)
from canvas_ed_mcp.providers.base import (
    CategoryCoverage,
    ContentBatch,
    CourseCandidate,
    CoverageStatus,
)
from canvas_ed_mcp.server import ProviderFactories, create_server
from canvas_ed_mcp.sync.model import RemoteKind


NOW = datetime(2099, 1, 1, tzinfo=timezone.utc)


def result_data(result: object) -> dict[str, object]:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        return structured
    content = getattr(result, "content")
    return json.loads(content[0].text)


class FakeCatalogProvider:
    def __init__(self, provider: str, instance: str, course_id: str) -> None:
        self.provider_name = provider
        self.instance = instance
        self.course_id = course_id
        self.close_calls = 0

    def list_courses(self) -> tuple[CourseCandidate, ...]:
        return (
            CourseCandidate(
                self.provider_name,
                self.instance,
                self.course_id,
                "SAFE1000",
                "Configured synthetic course",
                "2099 T2",
            ),
            CourseCandidate(
                self.provider_name,
                self.instance,
                "unconfigured-extra",
                "SAFE1000",
                "Unconfigured extra",
                "2099 T2",
            ),
        )

    def list_items(self, course_id: str, *, kinds: frozenset[RemoteKind]) -> ContentBatch:
        return ContentBatch(
            provider=self.provider_name,
            instance=self.instance,
            course_id=course_id,
            requested_kinds=kinds,
            coverage=tuple(CategoryCoverage(kind, CoverageStatus.COMPLETE) for kind in kinds),
            items=(),
            issues=(),
        )

    def close(self) -> None:
        self.close_calls += 1


class MCPProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary.name).resolve() / "output"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def empty_config(self, *enabled: str) -> AppConfig:
        return AppConfig(
            profile="protocol-test",
            output_root=self.output_root,
            platforms=PlatformsConfig(
                canvas=CanvasPlatformConfig(
                    enabled="canvas" in enabled,
                    base_url=("https://canvas.invalid/api/v1" if "canvas" in enabled else None),
                    credential=("env:MCP_PROTOCOL_CANVAS" if "canvas" in enabled else None),
                ),
                ed=EdPlatformConfig(
                    enabled="ed" in enabled,
                    credential=("env:MCP_PROTOCOL_ED" if "ed" in enabled else None),
                ),
                gradescope=GradescopePlatformConfig(
                    enabled="gradescope" in enabled,
                ),
            ),
        )

    async def test_in_memory_protocol_tool_catalog_matrix_is_lazy(self) -> None:
        expected = {
            (): {"server_status"},
            ("canvas",): {"server_status", "canvas_list_configured_courses"},
            ("ed",): {"server_status", "ed_list_configured_courses", "ed_list_course_content"},
            ("canvas", "ed"): {
                "server_status",
                "canvas_list_configured_courses",
                "ed_list_configured_courses",
                "ed_list_course_content",
            },
            ("gradescope",): {
                "server_status",
                "gradescope_list_courses",
                "gradescope_list_assignments",
            },
        }
        for enabled, expected_names in expected.items():
            with self.subTest(enabled=enabled):
                secret_calls: list[str] = []
                server = create_server(
                    self.empty_config(*enabled),
                    secret_resolver=lambda reference: secret_calls.append(reference) or "never-used",
                )
                async with create_connected_server_and_client_session(
                    server,
                    read_timeout_seconds=timedelta(seconds=5),
                ) as session:
                    listed = await session.list_tools()
                    self.assertEqual(expected_names, {tool.name for tool in listed.tools})
                    status = await session.call_tool("server_status", {})
                    self.assertFalse(status.isError)
                    self.assertEqual("read_only", result_data(status)["mode"])
                self.assertEqual([], secret_calls)

    async def test_in_memory_protocol_safe_reads_and_failure_isolation(self) -> None:
        canvas = FakeCatalogProvider("canvas", "canvas.invalid", "canvas-01")
        ed = FakeCatalogProvider("ed", "edstem.org", "ed-01")
        secret_calls: list[str] = []
        config = AppConfig(
            profile="protocol-test",
            output_root=self.output_root,
            platforms=PlatformsConfig(
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url="https://canvas.invalid/api/v1",
                    credential="env:MCP_PROTOCOL_CANVAS",
                ),
                ed=EdPlatformConfig(enabled=True, credential="env:MCP_PROTOCOL_ED"),
                gradescope=GradescopePlatformConfig(enabled=True),
            ),
            courses=(
                CourseConfig(
                    key="logical-safe",
                    folder="SAFE1000",
                    confirmed_at=NOW,
                    sources=(
                        CourseSource(
                            provider="canvas",
                            instance="canvas.invalid",
                            course_id="canvas-01",
                        ),
                        CourseSource(
                            provider="ed",
                            instance="edstem.org",
                            course_id="ed-01",
                        ),
                    ),
                ),
            ),
        )
        factories = ProviderFactories(
            canvas=lambda platform, token: canvas,
            ed=lambda platform, token: ed,
        )
        server = create_server(
            config,
            secret_resolver=lambda reference: secret_calls.append(reference) or "synthetic",
            factories=factories,
        )

        async with create_connected_server_and_client_session(
            server,
            read_timeout_seconds=timedelta(seconds=5),
        ) as session:
            gradescope_failure = await session.call_tool("gradescope_list_courses", {})
            self.assertTrue(gradescope_failure.isError)
            failure_text = gradescope_failure.content[0].text
            self.assertIn("gradescope_unavailable:missing_reference", failure_text)
            self.assertNotIn("MCP_PROTOCOL", failure_text)

            canvas_result = await session.call_tool("canvas_list_configured_courses", {})
            self.assertFalse(canvas_result.isError)
            self.assertEqual(
                ["canvas-01"],
                [item["course_id"] for item in result_data(canvas_result)["courses"]],
            )
            ed_result = await session.call_tool(
                "ed_list_course_content",
                {"logical_course_key": "logical-safe", "kinds": ["lesson", "resource"]},
            )
            self.assertFalse(ed_result.isError)
            self.assertEqual("ed-01", result_data(ed_result)["course_id"])

        self.assertEqual(
            ["env:MCP_PROTOCOL_CANVAS", "env:MCP_PROTOCOL_ED"], secret_calls
        )
        self.assertEqual(1, canvas.close_calls)
        self.assertEqual(1, ed.close_calls)

    async def test_real_cli_stdio_handshake_catalog_and_status_without_secret_or_network(self) -> None:
        config_path = Path(self.temporary.name) / "config.toml"
        config = self.empty_config("canvas")
        save_config(config, config_path)
        before = config_path.read_bytes()
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SOURCE_ROOT)
        environment["PYTHONUNBUFFERED"] = "1"
        environment.pop("MCP_PROTOCOL_CANVAS", None)

        async def exercise_stdio() -> None:
            parameters = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-B",
                    "-m",
                    "canvas_ed_mcp.cli",
                    "serve",
                    "--config",
                    str(config_path.resolve()),
                ],
                env=environment,
                cwd=str(Path(self.temporary.name).resolve()),
            )
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
                async with stdio_client(parameters, errlog=stderr) as (read, write):
                    async with ClientSession(
                        read,
                        write,
                        read_timeout_seconds=timedelta(seconds=10),
                    ) as session:
                        initialized = await session.initialize()
                        self.assertTrue(initialized.serverInfo.name)
                        listed = await session.list_tools()
                        self.assertEqual(
                            {"server_status", "canvas_list_configured_courses"},
                            {tool.name for tool in listed.tools},
                        )
                        status = await session.call_tool("server_status", {})
                        self.assertFalse(status.isError)
                        data = result_data(status)
                        self.assertEqual(["canvas"], data["enabled_providers"])
                        self.assertEqual(0, data["configured_source_counts"]["canvas"])

        await asyncio.wait_for(exercise_stdio(), timeout=20)
        self.assertEqual(before, config_path.read_bytes())
        self.assertFalse(self.output_root.exists())


if __name__ == "__main__":
    unittest.main()
