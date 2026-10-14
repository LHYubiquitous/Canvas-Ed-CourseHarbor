from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from mcp.server.fastmcp.exceptions import ToolError

from canvas_ed_mcp.config import (
    AppConfig,
    CanvasPlatformConfig,
    CourseConfig,
    CourseSource,
    EdPlatformConfig,
    GradescopePlatformConfig,
    PlatformsConfig,
)
from canvas_ed_mcp.cli import main
from canvas_ed_mcp.providers.base import (
    CategoryCoverage,
    ContentBatch,
    CourseCandidate,
    CoverageStatus,
)
from canvas_ed_mcp.providers.gradescope import GradescopeAssignment, GradescopeCourse
from canvas_ed_mcp.secrets import SecretUnavailable
from canvas_ed_mcp.server import ProviderFactories, close_server, create_server
from canvas_ed_mcp.sync.model import RemoteKind


NOW = datetime(2099, 1, 1, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(self, provider: str, instance: str, courses: tuple[CourseCandidate, ...]) -> None:
        self.provider_name = provider
        self.instance = instance
        self.courses = courses
        self.list_calls = 0
        self.item_calls: list[tuple[str, frozenset[RemoteKind]]] = []
        self.close_calls = 0

    def list_courses(self) -> tuple[CourseCandidate, ...]:
        self.list_calls += 1
        return self.courses

    def list_items(self, course_id: str, *, kinds: frozenset[RemoteKind]) -> ContentBatch:
        self.item_calls.append((course_id, kinds))
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


class FakeGradescopeProvider:
    def __init__(self) -> None:
        self.course_calls = 0
        self.assignment_calls: list[str] = []
        self.close_calls = 0

    def list_courses(self) -> tuple[GradescopeCourse, ...]:
        self.course_calls += 1
        return (
            GradescopeCourse(
                role="student",
                course_id="901",
                name="Synthetic Gradescope",
                semester="Semester 2",
                year="2099",
                num_assignments="2 assignments",
            ),
        )

    def list_assignments(self, course_id: str) -> tuple[GradescopeAssignment, ...]:
        self.assignment_calls.append(course_id)
        return (
            GradescopeAssignment(
                course_id=course_id,
                assignment_id="501",
                name="Synthetic assignment",
                due_at="2099-10-01T10:00:00",
                late_due_at=None,
                submissions_status="Submitted",
                grade="8",
                max_grade="10",
            ),
        )

    def close(self) -> None:
        self.close_calls += 1


def parse_result(result: object) -> object:
    wrapped = list(result)  # type: ignore[arg-type]
    blocks = wrapped[0] if wrapped and isinstance(wrapped[0], list) else wrapped
    return json.loads(blocks[0].text)


class ProviderAwareServerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary.name).resolve()
        self.resolved: list[str] = []
        self.created: list[FakeProvider] = []

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self, *providers: str) -> AppConfig:
        sources: list[CourseSource] = []
        if "canvas" in providers:
            sources.append(
                CourseSource(
                    provider="canvas",
                    instance="canvas.invalid",
                    course_id="canvas-01",
                    code="SAFE1000",
                    name="Synthetic Canvas",
                )
            )
        if "ed" in providers:
            sources.append(
                CourseSource(
                    provider="ed",
                    instance="edstem.org",
                    course_id="ed-01",
                    code="SAFE1000",
                    name="Synthetic Ed",
                )
            )
        courses = (
            CourseConfig(
                key="logical-safe-01",
                folder="SAFE1000",
                confirmed_at=NOW,
                sources=tuple(sources),
            ),
        ) if sources else ()
        return AppConfig(
            profile="synthetic",
            output_root=self.output_root,
            platforms=PlatformsConfig(
                canvas=CanvasPlatformConfig(
                    enabled="canvas" in providers,
                    base_url=("https://canvas.invalid/api/v1" if "canvas" in providers else None),
                    credential=("env:CANVAS_SAFE_TOKEN" if "canvas" in providers else None),
                ),
                ed=EdPlatformConfig(
                    enabled="ed" in providers,
                    credential=("env:ED_SAFE_TOKEN" if "ed" in providers else None),
                ),
            ),
            courses=courses,
        )

    def resolver(self, reference: str) -> str:
        self.resolved.append(reference)
        return "synthetic-secret-value"

    def factories(self) -> ProviderFactories:
        def canvas_factory(config: CanvasPlatformConfig, token: str) -> FakeProvider:
            self.assertEqual("synthetic-secret-value", token)
            provider = FakeProvider(
                "canvas",
                "canvas.invalid",
                (
                    CourseCandidate("canvas", "canvas.invalid", "canvas-01", "SAFE1000", "Configured", None),
                    CourseCandidate("canvas", "canvas.invalid", "extra", "SAFE1000", "Same-name extra", None),
                    CourseCandidate("canvas", "other.invalid", "canvas-01", "SAFE1000", "Wrong instance", None),
                ),
            )
            self.created.append(provider)
            return provider

        def ed_factory(config: EdPlatformConfig, token: str) -> FakeProvider:
            self.assertEqual("synthetic-secret-value", token)
            provider = FakeProvider(
                "ed",
                "edstem.org",
                (CourseCandidate("ed", "edstem.org", "ed-01", "SAFE1000", "Configured", None),),
            )
            self.created.append(provider)
            return provider

        return ProviderFactories(canvas=canvas_factory, ed=ed_factory)

    async def test_tool_catalog_is_conditional_and_construction_is_lazy(self) -> None:
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
        }
        for enabled, names in expected.items():
            with self.subTest(enabled=enabled):
                server = create_server(
                    self.config(*enabled),
                    secret_resolver=self.resolver,
                    factories=self.factories(),
                )
                self.assertEqual(names, {tool.name for tool in await server.list_tools()})
        self.assertEqual([], self.resolved)
        self.assertEqual([], self.created)

    async def test_canvas_catalog_returns_only_exact_configured_identity(self) -> None:
        server = create_server(
            self.config("canvas"),
            secret_resolver=self.resolver,
            factories=self.factories(),
        )
        data = parse_result(await server.call_tool("canvas_list_configured_courses", {}))

        self.assertEqual(["env:CANVAS_SAFE_TOKEN"], self.resolved)
        self.assertEqual(["canvas-01"], [course["course_id"] for course in data["courses"]])
        self.assertEqual([], data["missing"])
        self.assertEqual(1, self.created[0].list_calls)

    async def test_both_mode_initializes_only_invoked_provider(self) -> None:
        server = create_server(
            self.config("canvas", "ed"),
            secret_resolver=self.resolver,
            factories=self.factories(),
        )
        await server.call_tool("ed_list_configured_courses", {})
        await server.call_tool("ed_list_configured_courses", {})

        self.assertEqual(["env:ED_SAFE_TOKEN"], self.resolved)
        self.assertEqual(1, len(self.created))
        self.assertEqual("ed", self.created[0].provider_name)
        self.assertEqual(2, self.created[0].list_calls)

    async def test_status_contains_no_credential_reference_and_performs_no_io(self) -> None:
        server = create_server(
            self.config("canvas", "ed"),
            secret_resolver=self.resolver,
            factories=self.factories(),
        )
        result = await server.call_tool("server_status", {})
        text = json.dumps(parse_result(result), sort_keys=True)

        self.assertNotIn("TOKEN", text)
        self.assertNotIn("env:", text)
        self.assertEqual([], self.resolved)
        self.assertEqual([], self.created)

    async def test_missing_secret_is_categorized_without_factory_or_value_leak(self) -> None:
        def missing(reference: str) -> str:
            raise SecretUnavailable("missing_env", f"do not expose {reference}")

        server = create_server(
            self.config("canvas"),
            secret_resolver=missing,
            factories=self.factories(),
        )
        with self.assertRaisesRegex(ToolError, r"canvas_unavailable:missing_env") as caught:
            await server.call_tool("canvas_list_configured_courses", {})

        self.assertNotIn("CANVAS_SAFE_TOKEN", str(caught.exception))
        self.assertEqual([], self.created)

    async def test_ed_content_uses_exact_configured_source_and_serializes_coverage(self) -> None:
        server = create_server(
            self.config("ed"),
            secret_resolver=self.resolver,
            factories=self.factories(),
        )
        data = parse_result(
            await server.call_tool(
                "ed_list_course_content",
                {"logical_course_key": "logical-safe-01", "kinds": ["lesson", "resource"]},
            )
        )

        self.assertEqual("logical-safe-01", data["logical_course_key"])
        self.assertEqual("ed-01", data["course_id"])
        self.assertEqual(
            {"lesson": "complete", "resource": "complete"},
            {entry["kind"]: entry["status"] for entry in data["coverage"]},
        )
        self.assertEqual(
            [("ed-01", frozenset({RemoteKind.LESSON, RemoteKind.RESOURCE}))],
            self.created[0].item_calls,
        )

    async def test_unknown_logical_course_fails_before_secret_or_provider(self) -> None:
        server = create_server(
            self.config("ed"),
            secret_resolver=self.resolver,
            factories=self.factories(),
        )
        with self.assertRaisesRegex(ToolError, "course_not_configured"):
            await server.call_tool(
                "ed_list_course_content",
                {"logical_course_key": "not-configured", "kinds": ["lesson"]},
            )
        self.assertEqual([], self.resolved)
        self.assertEqual([], self.created)

    async def test_unknown_course_error_does_not_reflect_signed_url_input(self) -> None:
        server = create_server(
            self.config("ed"),
            secret_resolver=self.resolver,
            factories=self.factories(),
        )
        canary = "https://x.invalid/?signature=LEAK_CANARY"
        with self.assertRaisesRegex(ToolError, "course_not_configured:ed") as caught:
            await server.call_tool(
                "ed_list_course_content",
                {"logical_course_key": canary, "kinds": ["lesson"]},
            )
        self.assertNotIn("LEAK_CANARY", str(caught.exception))
        self.assertNotIn("https://", str(caught.exception))

    async def test_enabled_provider_with_no_courses_keeps_catalog_tool_without_secret_io(self) -> None:
        config = AppConfig(
            profile="synthetic",
            output_root=self.output_root,
            platforms=PlatformsConfig(
                canvas=CanvasPlatformConfig(
                    enabled=True,
                    base_url="https://canvas.invalid/api/v1",
                    credential="env:CANVAS_SAFE_TOKEN",
                )
            ),
        )
        server = create_server(
            config,
            secret_resolver=self.resolver,
            factories=self.factories(),
        )

        self.assertEqual(
            {"server_status", "canvas_list_configured_courses"},
            {tool.name for tool in await server.list_tools()},
        )
        data = parse_result(await server.call_tool("canvas_list_configured_courses", {}))
        self.assertEqual({"provider": "canvas", "courses": [], "missing": []}, data)
        self.assertEqual([], self.resolved)
        self.assertEqual([], self.created)

    async def test_gradescope_default_off_and_incomplete_opt_in_cannot_block_canvas(self) -> None:
        base = self.config("canvas")
        config = base.model_copy(
            update={
                "platforms": base.platforms.model_copy(
                    update={"gradescope": GradescopePlatformConfig(enabled=True)}
                )
            }
        )
        server = create_server(
            config,
            secret_resolver=self.resolver,
            factories=self.factories(),
        )

        names = {tool.name for tool in await server.list_tools()}
        self.assertIn("gradescope_list_courses", names)
        self.assertIn("gradescope_list_assignments", names)
        canvas = parse_result(await server.call_tool("canvas_list_configured_courses", {}))
        self.assertEqual(["canvas-01"], [item["course_id"] for item in canvas["courses"]])
        with self.assertRaisesRegex(ToolError, "gradescope_unavailable:missing_reference"):
            await server.call_tool("gradescope_list_courses", {})

    async def test_gradescope_is_lazy_and_returns_only_allowlisted_fields(self) -> None:
        gradescope = FakeGradescopeProvider()
        factories = self.factories()
        factories = ProviderFactories(
            canvas=factories.canvas,
            ed=factories.ed,
            gradescope=lambda config, email, password: gradescope,
        )
        config = AppConfig(
            profile="synthetic",
            output_root=self.output_root,
            platforms=PlatformsConfig(
                gradescope=GradescopePlatformConfig(
                    enabled=True,
                    email_credential="env:GRADESCOPE_SAFE_EMAIL",
                    password_credential="env:GRADESCOPE_SAFE_PASSWORD",
                )
            ),
        )
        server = create_server(config, secret_resolver=self.resolver, factories=factories)

        self.assertEqual([], self.resolved)
        courses = parse_result(await server.call_tool("gradescope_list_courses", {}))
        self.assertEqual(
            ["env:GRADESCOPE_SAFE_EMAIL", "env:GRADESCOPE_SAFE_PASSWORD"],
            self.resolved,
        )
        self.assertEqual(
            {"role", "course_id", "name", "semester", "year", "num_assignments"},
            set(courses["courses"][0]),
        )
        assignments = parse_result(
            await server.call_tool("gradescope_list_assignments", {"course_id": "901"})
        )
        self.assertEqual("501", assignments["assignments"][0]["assignment_id"])
        self.assertEqual(1, gradescope.course_calls)
        self.assertEqual(["901"], gradescope.assignment_calls)

        close_server(server)
        self.assertEqual(1, gradescope.close_calls)

    async def test_gradescope_assignment_requires_prior_exact_catalog_identity(self) -> None:
        gradescope = FakeGradescopeProvider()
        base_factories = self.factories()
        factories = ProviderFactories(
            canvas=base_factories.canvas,
            ed=base_factories.ed,
            gradescope=lambda config, email, password: gradescope,
        )
        config = AppConfig(
            profile="synthetic",
            output_root=self.output_root,
            platforms=PlatformsConfig(
                gradescope=GradescopePlatformConfig(
                    enabled=True,
                    email_credential="env:GRADESCOPE_SAFE_EMAIL",
                    password_credential="env:GRADESCOPE_SAFE_PASSWORD",
                )
            ),
        )
        server = create_server(config, secret_resolver=self.resolver, factories=factories)

        with self.assertRaisesRegex(ToolError, "gradescope_course_not_discovered"):
            await server.call_tool("gradescope_list_assignments", {"course_id": "901"})
        self.assertEqual([], self.resolved)
        self.assertEqual([], gradescope.assignment_calls)

    async def test_blocked_gradescope_call_does_not_block_server_status(self) -> None:
        started = threading.Event()
        release = threading.Event()

        class BlockingGradescope(FakeGradescopeProvider):
            def list_courses(self) -> tuple[GradescopeCourse, ...]:
                started.set()
                if not release.wait(2):
                    raise RuntimeError("synthetic timeout")
                return super().list_courses()

        gradescope = BlockingGradescope()
        base_factories = self.factories()
        factories = ProviderFactories(
            canvas=base_factories.canvas,
            ed=base_factories.ed,
            gradescope=lambda config, email, password: gradescope,
        )
        config = AppConfig(
            profile="synthetic",
            output_root=self.output_root,
            platforms=PlatformsConfig(
                gradescope=GradescopePlatformConfig(
                    enabled=True,
                    email_credential="env:GRADESCOPE_SAFE_EMAIL",
                    password_credential="env:GRADESCOPE_SAFE_PASSWORD",
                )
            ),
        )
        server = create_server(config, secret_resolver=self.resolver, factories=factories)

        import asyncio

        blocked = asyncio.create_task(server.call_tool("gradescope_list_courses", {}))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            status = await asyncio.wait_for(server.call_tool("server_status", {}), timeout=0.25)
            self.assertEqual("read_only", parse_result(status)["mode"])
        finally:
            release.set()
        await blocked

    async def test_close_server_closes_only_created_provider_and_is_idempotent(self) -> None:
        server = create_server(
            self.config("canvas", "ed"),
            secret_resolver=self.resolver,
            factories=self.factories(),
        )
        await server.call_tool("canvas_list_configured_courses", {})

        close_server(server)
        close_server(server)

        self.assertEqual(1, len(self.created))
        self.assertEqual(1, self.created[0].close_calls)

    def test_cli_serve_loads_explicit_config_without_resolving_secrets(self) -> None:
        fake_server = Mock()
        with patch("canvas_ed_mcp.cli.load_config", return_value=self.config("canvas")) as load, patch(
            "canvas_ed_mcp.server.create_server", return_value=fake_server
        ) as create:
            result = main(["serve", "--config", str(self.output_root / "config.toml")])

        self.assertEqual(0, result)
        load.assert_called_once_with(self.output_root / "config.toml")
        create.assert_called_once()
        fake_server.run.assert_called_once_with(transport="stdio")
        self.assertEqual([], self.resolved)


if __name__ == "__main__":
    unittest.main()
