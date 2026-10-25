from __future__ import annotations

import sys
import unittest
import logging
from unittest.mock import Mock
from pathlib import Path

import httpx


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from canvas_ed_mcp.providers.base import CoverageStatus, ProviderError
from canvas_ed_mcp.providers.canvas import CanvasProvider
from canvas_ed_mcp.sync.model import RemoteItem, RemoteKind, SourceKey


def response_for(request: httpx.Request, routes: dict[str, httpx.Response]) -> httpx.Response:
    key = f"{request.method} {request.url.path}"
    try:
        response = routes[key]
    except KeyError as exc:
        raise AssertionError(f"unexpected request: {key}") from exc
    return response


class CanvasContentProviderTests(unittest.TestCase):
    def test_bulk_indexes_avoid_per_item_metadata_requests(self) -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            routes = {
                "/api/v1/courses/1/modules": [
                    {
                        "id": 10,
                        "name": "Week 1",
                        "items": [
                            {
                                "id": 1,
                                "type": "File",
                                "content_id": 100,
                                "url": "/api/v1/files/100",
                            },
                            {
                                "id": 2,
                                "type": "Page",
                                "page_url": "intro",
                                "url": "/api/v1/courses/1/pages/intro",
                            },
                        ],
                    }
                ],
                "/api/v1/courses/1/files": [
                    {
                        "id": 100,
                        "display_name": "module.pdf",
                        "size": 4,
                        "content-type": "application/pdf",
                        "updated_at": "2026-10-15T00:00:00Z",
                        "url": "https://cdn.example.test/module.pdf",
                    }
                ],
                "/api/v1/courses/1/pages/intro": {
                    "page_id": 200,
                    "url": "intro",
                    "title": "Introduction",
                    "updated_at": "2026-10-15T00:00:00Z",
                    "body": "<p>Body</p>",
                },
            }
            if request.url.path not in routes:
                raise AssertionError(f"unexpected request: {request.url.path}")
            return httpx.Response(200, json=routes[request.url.path])

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1",
            "token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            batch = provider.list_items(
                "1", kinds=frozenset({RemoteKind.FILE, RemoteKind.PAGE})
            )
        finally:
            provider.close()

        self.assertEqual(
            [
                "/api/v1/courses/1/modules",
                "/api/v1/courses/1/files",
                "/api/v1/courses/1/pages/intro",
            ],
            [request.url.path for request in requests],
        )
        module_query = requests[0].url.params
        self.assertEqual(["items", "content_details"], module_query.get_list("include[]"))
        self.assertEqual("100", module_query["per_page"])
        self.assertEqual(
            [
                (RemoteKind.FILE, "100", "Week 1"),
                (RemoteKind.PAGE, "intro", "Week 1"),
            ],
            [(item.key.kind, item.key.remote_id, item.group) for item in batch.items],
        )

    def test_constructor_redacts_signed_queries_from_http_dependency_logs(self) -> None:
        provider = CanvasProvider("https://canvas.example.edu/api/v1", "token")
        try:
            with self.assertLogs("httpx", level="INFO") as captured:
                logging.getLogger("httpx").info(
                    "GET https://cdn.test/file?X-Amz-Signature=private-value"
                )
        finally:
            provider.close()
        output = "\n".join(captured.output)
        self.assertNotIn("private-value", output)
        self.assertIn("[REDACTED]", output)

    def test_content_universe_first_group_and_self_contained_fetches(self) -> None:
        api_requests: list[httpx.Request] = []
        asset_requests: list[httpx.Request] = []
        routes = {
            "GET /api/v1/courses/1/modules": httpx.Response(
                200,
                json=[{"id": 10, "name": "Week 1"}, {"id": 20, "name": "Week 2"}],
            ),
            "GET /api/v1/courses/1/modules/10/items": httpx.Response(
                200,
                json=[
                    {"id": 1, "type": "File", "url": "/api/v1/files/100"},
                    {"id": 2, "type": "Page", "url": "/api/v1/courses/1/pages/intro"},
                ],
            ),
            "GET /api/v1/courses/1/modules/20/items": httpx.Response(
                200,
                json=[{"id": 3, "type": "File", "url": "/api/v1/files/100"}],
            ),
            "GET /api/v1/files/100": httpx.Response(
                200,
                json={
                    "id": 100,
                    "display_name": "module.pdf",
                    "size": 4,
                    "content-type": "application/pdf",
                    "updated_at": "2026-10-15T00:00:00Z",
                    "url": "https://cdn.example.test/module.pdf?X-Amz-Signature=secret",
                },
            ),
            "GET /api/v1/courses/1/pages/intro": httpx.Response(
                200,
                json={
                    "page_id": 200,
                    "url": "intro",
                    "title": "Introduction",
                    "updated_at": "2026-10-15T00:00:00Z",
                    "html_url": "https://canvas.example.edu/courses/1/pages/intro",
                    "body": '<script>bad()</script><img src="/courses/1/files/101/download?verifier=secret" alt="diagram">',
                },
            ),
            "GET /api/v1/files/101": httpx.Response(
                200,
                json={
                    "id": 101,
                    "display_name": "diagram.png",
                    "size": 3,
                    "content-type": "image/png",
                    "url": "https://cdn.example.test/diagram.png?verifier=secret",
                },
            ),
            "GET /api/v1/courses/1/files": httpx.Response(
                200,
                json=[
                    {
                        "id": 100,
                        "display_name": "module.pdf",
                        "size": 4,
                        "content-type": "application/pdf",
                        "updated_at": "2026-10-15T00:00:00Z",
                        "url": "https://cdn.example.test/module.pdf?X-Amz-Signature=rotated",
                    },
                    {
                        "id": 300,
                        "display_name": "unclassified.txt",
                        "size": 3,
                        "content-type": "text/plain",
                        "updated_at": "2026-10-15T00:00:00Z",
                        "url": "https://cdn.example.test/unclassified.txt?token=secret",
                    },
                ],
            ),
        }

        def api_respond(request: httpx.Request) -> httpx.Response:
            api_requests.append(request)
            self.assertEqual("Bearer synthetic-token", request.headers["authorization"])
            return response_for(request, routes)

        def asset_respond(request: httpx.Request) -> httpx.Response:
            asset_requests.append(request)
            self.assertNotIn("authorization", request.headers)
            if request.url.path.endswith("module.pdf"):
                return httpx.Response(200, content=b"PDF!", headers={"content-type": "application/pdf"})
            if request.url.path.endswith("diagram.png"):
                return httpx.Response(200, content=b"PNG", headers={"content-type": "image/png"})
            return httpx.Response(200, content=b"TXT", headers={"content-type": "text/plain"})

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1",
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(asset_respond)),
            allowed_asset_hosts=frozenset({"cdn.example.test"}),
        )
        try:
            batch = provider.list_items(
                "1", kinds=frozenset({RemoteKind.FILE, RemoteKind.PAGE})
            )
            self.assertTrue(batch.complete)
            self.assertEqual(
                [(RemoteKind.FILE, "100", "Week 1"),
                 (RemoteKind.PAGE, "intro", "Week 1"),
                 (RemoteKind.FILE, "300", "Course Files")],
                [(item.key.kind, item.key.remote_id, item.group) for item in batch.items],
            )

            module_file = batch.items[0]
            with provider.open_binary(module_file, chunk_size=2) as chunks:
                self.assertEqual(b"PDF!", b"".join(chunks))

            rendered = provider.render_html(batch.items[1])
            self.assertIn("data:image/png;base64,UE5H", rendered.document)
            self.assertIn("max-width: 960px", rendered.document)
            self.assertNotIn("<script", rendered.document)
            self.assertNotIn("verifier=secret", rendered.document)
            self.assertEqual((), rendered.issue_codes)
        finally:
            provider.close()

        self.assertGreaterEqual(len(api_requests), 6)
        self.assertEqual(2, len(asset_requests))

    def test_page_only_never_lists_all_files_or_unlinked_pages(self) -> None:
        paths: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            routes = {
                "/api/v1/courses/1/modules": [{"id": 10, "name": "Week 1"}],
                "/api/v1/courses/1/modules/10/items": [
                    {"id": 2, "type": "Page", "url": "/api/v1/courses/1/pages/intro"}
                ],
                "/api/v1/courses/1/pages/intro": {
                    "page_id": 200, "url": "intro", "title": "Introduction",
                    "updated_at": "2026-10-15T00:00:00Z", "body": "<p>Body</p>",
                },
            }
            if request.url.path not in routes:
                raise AssertionError(request.url.path)
            return httpx.Response(200, json=routes[request.url.path])

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            batch = provider.list_items("1", kinds=frozenset({RemoteKind.PAGE}))
        finally:
            provider.close()
        self.assertEqual([RemoteKind.PAGE], [item.key.kind for item in batch.items])
        self.assertNotIn("/api/v1/courses/1/files", paths)
        self.assertFalse(any(path.endswith("/pages") for path in paths))

    def test_signed_url_rotation_is_not_a_revision_but_ordinary_query_is(self) -> None:
        def discover(download_url: str, body_url: str) -> tuple[str, str]:
            def respond(request: httpx.Request) -> httpx.Response:
                routes = {
                    "/api/v1/courses/1/modules": [{"id": 10, "name": "Week"}],
                    "/api/v1/courses/1/modules/10/items": [
                        {"id": 1, "type": "File", "url": "/api/v1/files/100"},
                        {"id": 2, "type": "Page", "url": "/api/v1/courses/1/pages/intro"},
                    ],
                    "/api/v1/files/100": {
                        "id": 100, "display_name": "one.pdf", "size": 1,
                        "updated_at": "same", "url": download_url,
                    },
                    "/api/v1/courses/1/pages/intro": {
                        "page_id": 2, "url": "intro", "title": "Intro",
                        "updated_at": "same", "body": f'<img src="{body_url}">',
                    },
                    "/api/v1/courses/1/files": [],
                }
                return httpx.Response(200, json=routes[request.url.path])

            provider = CanvasProvider(
                "https://canvas.example.edu/api/v1", "token",
                client=httpx.Client(transport=httpx.MockTransport(respond)),
            )
            try:
                batch = provider.list_items(
                    "1", kinds=frozenset({RemoteKind.FILE, RemoteKind.PAGE})
                )
                return tuple(item.signature for item in batch.items)  # type: ignore[return-value]
            finally:
                provider.close()

        first = discover(
            "https://cdn.test/a?X-Amz-Signature=one&download=1",
            "https://canvas.example.edu/courses/1/files/9?verifier=one&view=1",
        )
        rotated = discover(
            "https://cdn.test/a?X-Amz-Signature=two&download=1",
            "https://canvas.example.edu/courses/1/files/9?verifier=two&view=1",
        )
        changed = discover(
            "https://cdn.test/a?X-Amz-Signature=two&download=2",
            "https://canvas.example.edu/courses/1/files/9?verifier=two&view=2",
        )
        self.assertEqual(first, rotated)
        self.assertNotEqual(first, changed)

    def test_forged_and_stale_items_fail_before_asset_request(self) -> None:
        asset_requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            routes = {
                "/api/v1/courses/1/modules": [{"id": 10, "name": "Week"}],
                "/api/v1/courses/1/modules/10/items": [
                    {"id": 1, "type": "File", "url": "/api/v1/files/100"}
                ],
                "/api/v1/files/100": {
                    "id": 100, "display_name": "one.pdf", "size": 1,
                    "updated_at": "same", "url": "https://cdn.test/one.pdf",
                },
                "/api/v1/courses/1/files": [],
            }
            return httpx.Response(200, json=routes[request.url.path])

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(
                lambda request: asset_requests.append(request) or httpx.Response(200, content=b"x")
            )),
        )
        try:
            item = provider.list_items("1", kinds=frozenset({RemoteKind.FILE})).items[0]
            forged = RemoteItem(
                SourceKey("canvas", provider.instance, "1", RemoteKind.FILE, "999"),
                item.signature, item.display_name,
            )
            with self.assertRaisesRegex(ProviderError, "not discovered"):
                with provider.open_binary(forged):
                    pass
            stale = RemoteItem(item.key, "different", item.display_name)
            with self.assertRaisesRegex(ProviderError, "not current"):
                with provider.open_binary(stale):
                    pass
        finally:
            provider.close()
        self.assertEqual([], asset_requests)

    def test_asset_client_rejects_credentials_and_truncated_file(self) -> None:
        credentialed = httpx.Client(
            headers={"Authorization": "Bearer wrong"},
            transport=httpx.MockTransport(lambda _: httpx.Response(500)),
        )
        try:
            with self.assertRaisesRegex(ValueError, "credential"):
                CanvasProvider(
                    "https://canvas.example.edu/api/v1", "token",
                    asset_client=credentialed,
                )
        finally:
            credentialed.close()

        def api_respond(request: httpx.Request) -> httpx.Response:
            routes = {
                "/api/v1/courses/1/modules": [{"id": 10, "name": "Week"}],
                "/api/v1/courses/1/modules/10/items": [
                    {"id": 1, "type": "File", "url": "/api/v1/files/100"}
                ],
                "/api/v1/files/100": {
                    "id": 100, "display_name": "one.pdf", "size": 4,
                    "updated_at": "same", "url": "https://cdn.test/one.pdf",
                },
                "/api/v1/courses/1/files": [],
            }
            return httpx.Response(200, json=routes[request.url.path])

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=b"bad")
            )),
            allowed_asset_hosts=frozenset({"cdn.test"}),
        )
        try:
            item = provider.list_items("1", kinds=frozenset({RemoteKind.FILE})).items[0]
            with self.assertRaisesRegex(ProviderError, "size"):
                with provider.open_binary(item) as chunks:
                    b"".join(chunks)
        finally:
            provider.close()

    def test_item_request_failure_is_partial_retryable_and_does_not_hide_good_item(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            routes: dict[str, httpx.Response] = {
                "/api/v1/courses/1/modules": httpx.Response(
                    200, json=[{"id": 10, "name": "Week"}]
                ),
                "/api/v1/courses/1/modules/10/items": httpx.Response(200, json=[
                    {"id": 1, "type": "File", "url": "/api/v1/files/100"},
                    {"id": 2, "type": "File", "url": "/api/v1/files/200"},
                ]),
                "/api/v1/files/100": httpx.Response(503),
                "/api/v1/files/200": httpx.Response(200, json={
                    "id": 200, "display_name": "good.pdf", "size": 1,
                    "updated_at": "same", "url": "https://cdn.test/good.pdf",
                }),
                "/api/v1/courses/1/files": httpx.Response(200, json=[]),
            }
            return routes[request.url.path]

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            batch = provider.list_items("1", kinds=frozenset({RemoteKind.FILE}))
        finally:
            provider.close()

        self.assertEqual(CoverageStatus.PARTIAL, batch.coverage[0].status)
        self.assertEqual(["200"], [item.key.remote_id for item in batch.items])
        self.assertEqual("item_request_failed", batch.issues[0].code)
        self.assertEqual("request_failed", batch.issues[0].category)
        self.assertTrue(batch.issues[0].retry_required)

    def test_module_failure_still_discovers_course_files_but_fails_pages(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/modules"):
                return httpx.Response(503)
            if request.url.path.endswith("/files"):
                return httpx.Response(200, json=[{
                    "id": 200, "display_name": "good.pdf", "size": 1,
                    "updated_at": "same", "url": "https://canvas.example.edu/file.pdf",
                }])
            raise AssertionError(request.url.path)

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            batch = provider.list_items(
                "1", kinds=frozenset({RemoteKind.FILE, RemoteKind.PAGE})
            )
        finally:
            provider.close()

        coverage = {entry.kind: entry.status for entry in batch.coverage}
        self.assertEqual(CoverageStatus.COMPLETE, coverage[RemoteKind.FILE])
        self.assertEqual(CoverageStatus.FAILED, coverage[RemoteKind.PAGE])
        self.assertEqual([("200", "Course Files")], [
            (item.key.remote_id, item.group) for item in batch.items
        ])

    def test_course_files_forbidden_keeps_module_files_as_partial(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            routes: dict[str, httpx.Response] = {
                "/api/v1/courses/1/modules": httpx.Response(
                    200, json=[{"id": 10, "name": "Week"}]
                ),
                "/api/v1/courses/1/modules/10/items": httpx.Response(200, json=[
                    {"id": 1, "type": "File", "url": "/api/v1/files/100"},
                ]),
                "/api/v1/files/100": httpx.Response(200, json={
                    "id": 100, "display_name": "module.pdf", "size": 1,
                    "updated_at": "same", "url": "https://canvas.example.edu/module.pdf",
                }),
                "/api/v1/courses/1/files": httpx.Response(403),
            }
            return routes[request.url.path]

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            batch = provider.list_items("1", kinds=frozenset({RemoteKind.FILE}))
        finally:
            provider.close()

        self.assertEqual(CoverageStatus.PARTIAL, batch.coverage[0].status)
        self.assertEqual([("100", "Week")], [
            (item.key.remote_id, item.group) for item in batch.items
        ])
        self.assertEqual("authorization_failed", batch.issues[0].code)
        self.assertEqual("authorization_failed", batch.issues[0].category)
        self.assertFalse(batch.issues[0].retry_required)

    def test_item_authorization_failure_is_partial_and_non_retryable(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            routes: dict[str, httpx.Response] = {
                "/api/v1/courses/1/modules": httpx.Response(
                    200, json=[{"id": 10, "name": "Week"}]
                ),
                "/api/v1/courses/1/modules/10/items": httpx.Response(200, json=[
                    {"id": 1, "type": "File", "url": "/api/v1/files/100"},
                    {"id": 2, "type": "File", "url": "/api/v1/files/200"},
                ]),
                "/api/v1/files/100": httpx.Response(403),
                "/api/v1/files/200": httpx.Response(200, json={
                    "id": 200, "display_name": "good.pdf", "size": 1,
                    "updated_at": "same", "url": "https://canvas.example.edu/good.pdf",
                }),
                "/api/v1/courses/1/files": httpx.Response(200, json=[]),
            }
            return routes[request.url.path]

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            batch = provider.list_items("1", kinds=frozenset({RemoteKind.FILE}))
        finally:
            provider.close()

        self.assertEqual(CoverageStatus.PARTIAL, batch.coverage[0].status)
        self.assertEqual(["200"], [item.key.remote_id for item in batch.items])
        self.assertEqual("authorization_failed", batch.issues[0].category)
        self.assertFalse(batch.issues[0].retry_required)

    def test_asset_redirect_cannot_change_delegated_origin(self) -> None:
        def api_respond(request: httpx.Request) -> httpx.Response:
            routes = {
                "/api/v1/courses/1/modules": [{"id": 10, "name": "Week"}],
                "/api/v1/courses/1/modules/10/items": [
                    {"id": 1, "type": "File", "url": "/api/v1/files/100"}
                ],
                "/api/v1/files/100": {
                    "id": 100, "display_name": "one.pdf", "size": 1,
                    "updated_at": "same", "url": "https://cdn-one.test/one.pdf",
                },
                "/api/v1/courses/1/files": [],
            }
            return httpx.Response(200, json=routes[request.url.path])

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    302, headers={"location": "https://cdn-two.test/one.pdf"}
                )
            )),
            allowed_asset_hosts=frozenset({"cdn-one.test", "cdn-two.test"}),
        )
        try:
            item = provider.list_items("1", kinds=frozenset({RemoteKind.FILE})).items[0]
            with self.assertRaisesRegex(ProviderError, "changed origin"):
                with provider.open_binary(item) as chunks:
                    b"".join(chunks)
        finally:
            provider.close()

    def test_canvas_origin_may_delegate_to_official_user_content_host(self) -> None:
        asset_requests: list[httpx.Request] = []

        def api_respond(request: httpx.Request) -> httpx.Response:
            routes = {
                "/api/v1/courses/1/modules": [{"id": 10, "name": "Week"}],
                "/api/v1/courses/1/modules/10/items": [
                    {"id": 1, "type": "File", "url": "/api/v1/files/100"}
                ],
                "/api/v1/files/100": {
                    "id": 100, "display_name": "one.png", "size": 1,
                    "content-type": "image/png", "updated_at": "same",
                    "url": "https://canvas.example.edu/files/100/download?verifier=secret",
                },
                "/api/v1/courses/1/files": [],
            }
            return httpx.Response(200, json=routes[request.url.path])

        def asset_respond(request: httpx.Request) -> httpx.Response:
            asset_requests.append(request)
            self.assertNotIn("authorization", request.headers)
            if request.url.host == "canvas.example.edu":
                return httpx.Response(302, headers={
                    "location": "https://a1.cluster200.canvas-user-content.com/image.png"
                })
            if request.url.host == "a1.cluster200.canvas-user-content.com":
                return httpx.Response(
                    200, content=b"x", headers={"content-type": "image/png"}
                )
            raise AssertionError(str(request.url.host))

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(asset_respond)),
        )
        try:
            item = provider.list_items("1", kinds=frozenset({RemoteKind.FILE})).items[0]
            with provider.open_binary(item) as chunks:
                self.assertEqual(b"x", b"".join(chunks))
        finally:
            provider.close()

        self.assertEqual(
            ["canvas.example.edu", "a1.cluster200.canvas-user-content.com"],
            [request.url.host for request in asset_requests],
        )

    def test_official_user_content_host_may_delegate_to_inscloudgate(self) -> None:
        asset_requests: list[httpx.Request] = []

        def asset_respond(request: httpx.Request) -> httpx.Response:
            asset_requests.append(request)
            self.assertNotIn("authorization", request.headers)
            if request.url.host == "canvas.example.edu":
                return httpx.Response(302, headers={
                    "location": "https://a1.cluster200.canvas-user-content.com/image.png"
                })
            if request.url.host == "a1.cluster200.canvas-user-content.com":
                return httpx.Response(302, headers={
                    "location": "https://inst-fs-syd-prod.inscloudgate.net/image.png"
                })
            if request.url.host == "inst-fs-syd-prod.inscloudgate.net":
                return httpx.Response(302, headers={
                    "location": "https://cdn.inst-fs-syd-prod.inscloudgate.net/image.png"
                })
            if request.url.host == "cdn.inst-fs-syd-prod.inscloudgate.net":
                return httpx.Response(
                    200, content=b"x", headers={"content-type": "image/png"}
                )
            raise AssertionError(str(request.url.host))

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            asset_client=httpx.Client(transport=httpx.MockTransport(asset_respond)),
        )
        try:
            with provider._asset_stream(
                "https://canvas.example.edu/files/100/download"
            ) as response:
                self.assertEqual(b"x", response.read())
        finally:
            provider.close()

        self.assertEqual(
            [
                "canvas.example.edu",
                "a1.cluster200.canvas-user-content.com",
                "inst-fs-syd-prod.inscloudgate.net",
                "cdn.inst-fs-syd-prod.inscloudgate.net",
            ],
            [request.url.host for request in asset_requests],
        )

    def test_inscloudgate_internal_delegation_is_allowed_only_once(self) -> None:
        asset_requests: list[httpx.Request] = []

        def asset_respond(request: httpx.Request) -> httpx.Response:
            asset_requests.append(request)
            routes = {
                "canvas.example.edu": "https://a1.cluster200.canvas-user-content.com/image.png",
                "a1.cluster200.canvas-user-content.com": "https://inst-fs-syd-prod.inscloudgate.net/image.png",
                "inst-fs-syd-prod.inscloudgate.net": "https://cdn.inst-fs-syd-prod.inscloudgate.net/image.png",
                "cdn.inst-fs-syd-prod.inscloudgate.net": "https://another.inscloudgate.net/image.png",
            }
            return httpx.Response(302, headers={"location": routes[request.url.host]})

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            asset_client=httpx.Client(transport=httpx.MockTransport(asset_respond)),
        )
        try:
            with self.assertRaisesRegex(ProviderError, "changed origin"):
                with provider._asset_stream(
                    "https://canvas.example.edu/files/100/download",
                    redirects_remaining=4,
                ):
                    pass
        finally:
            provider.close()

        self.assertEqual(4, len(asset_requests))

    def test_inscloudgate_lookalike_is_rejected_before_request(self) -> None:
        asset_requests: list[httpx.Request] = []

        def asset_respond(request: httpx.Request) -> httpx.Response:
            asset_requests.append(request)
            if request.url.host == "canvas.example.edu":
                return httpx.Response(302, headers={
                    "location": "https://a1.cluster200.canvas-user-content.com/image.png"
                })
            return httpx.Response(302, headers={
                "location": "https://inscloudgate.net.evil.test/image.png"
            })

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            asset_client=httpx.Client(transport=httpx.MockTransport(asset_respond)),
        )
        try:
            with self.assertRaisesRegex(ProviderError, "unsafe"):
                with provider._asset_stream(
                    "https://canvas.example.edu/files/100/download"
                ):
                    pass
        finally:
            provider.close()

        self.assertEqual(2, len(asset_requests))

    def test_canvas_delegation_rejects_user_content_lookalike_before_request(self) -> None:
        asset_requests: list[httpx.Request] = []

        def asset_respond(request: httpx.Request) -> httpx.Response:
            asset_requests.append(request)
            return httpx.Response(302, headers={
                "location": "https://canvas-user-content.com.evil.test/image.png"
            })

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            asset_client=httpx.Client(transport=httpx.MockTransport(asset_respond)),
        )
        try:
            with self.assertRaisesRegex(ProviderError, "unsafe"):
                with provider._asset_stream(
                    "https://canvas.example.edu/files/100/download"
                ):
                    pass
        finally:
            provider.close()

        self.assertEqual(1, len(asset_requests))

    def test_unallowlisted_dns_asset_is_rejected_before_request(self) -> None:
        asset_requests: list[httpx.Request] = []

        def api_respond(request: httpx.Request) -> httpx.Response:
            routes = {
                "/api/v1/courses/1/modules": [{"id": 10, "name": "Week"}],
                "/api/v1/courses/1/modules/10/items": [
                    {"id": 1, "type": "File", "url": "/api/v1/files/100"}
                ],
                "/api/v1/files/100": {
                    "id": 100, "display_name": "one.pdf", "size": 1,
                    "updated_at": "same",
                    "url": "https://metadata.google.internal/latest/meta-data/",
                },
                "/api/v1/courses/1/files": [],
            }
            return httpx.Response(200, json=routes[request.url.path])

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(
                lambda request: asset_requests.append(request) or httpx.Response(200)
            )),
        )
        try:
            item = provider.list_items("1", kinds=frozenset({RemoteKind.FILE})).items[0]
            with self.assertRaisesRegex(ProviderError, "unsafe"):
                with provider.open_binary(item):
                    pass
        finally:
            provider.close()
        self.assertEqual([], asset_requests)

    def test_api_client_is_closed_even_when_asset_close_fails(self) -> None:
        api_client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
        asset_client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
        api_close = Mock(wraps=api_client.close)
        asset_close = Mock(side_effect=RuntimeError("synthetic close failure"))
        api_client.close = api_close
        asset_client.close = asset_close
        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1", "token",
            client=api_client, asset_client=asset_client,
        )

        with self.assertRaisesRegex(RuntimeError, "synthetic close failure"):
            provider.close()

        api_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
