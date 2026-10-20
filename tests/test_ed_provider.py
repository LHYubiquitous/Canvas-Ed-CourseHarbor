from __future__ import annotations

import sys
import io
import logging
import traceback
import unittest
from pathlib import Path

import httpx


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.providers.base import ProviderError
from canvas_ed_mcp.providers.ed import EdProvider
from canvas_ed_mcp.sync.model import RemoteItem, RemoteKind, SourceKey


class EdProviderTests(unittest.TestCase):
    def test_course_catalog_preserves_each_stable_id_without_guessing_latest(self) -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual("Bearer synthetic-token", request.headers["authorization"])
            self.assertEqual("/api/user", request.url.path)
            return httpx.Response(
                200,
                json={
                    "courses": [
                        {
                            "course": {
                                "id": 101,
                                "code": "TEST1000",
                                "name": "Earlier",
                                "year": "2026",
                                "session": "S2",
                            }
                        },
                        {
                            "course": {
                                "id": 202,
                                "code": "TEST1000",
                                "name": "Later",
                                "year": "2027",
                                "session": "S1",
                            }
                        },
                    ]
                },
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            courses = provider.list_courses()
        finally:
            provider.close()

        self.assertEqual(["101", "202"], [course.course_id for course in courses])
        self.assertEqual(["2026 S2", "2027 S1"], [course.term for course in courses])
        self.assertEqual("ed:edstem.org:101", courses[0].key)
        self.assertEqual(1, len(requests))

    def test_list_items_maps_resources_and_lessons_to_stable_domain_values(self) -> None:
        paths: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path == "/api/courses/course-1/resources":
                return httpx.Response(
                    200,
                    json={
                        "resources": [
                            {
                                "id": 11,
                                "name": "Week 1",
                                "extension": ".PDF",
                                "size": 42,
                                "updated_at": "2027-01-02T03:04:05Z",
                                "category": "Lectures",
                            },
                            {"id": 12, "name": "Staff", "staff_only": True},
                            {"id": 13, "name": "Website", "link": "https://example.test"},
                        ]
                    },
                )
            if request.url.path == "/api/courses/course-1/lessons":
                return httpx.Response(200, json={"lessons": [{"id": 21}]})
            if request.url.path == "/api/lessons/21":
                return httpx.Response(
                    200,
                    json={
                        "lesson": {
                            "id": 21,
                            "title": "Introduction",
                            "slide_count": 1,
                            "slides": [
                                {
                                    "index": 0,
                                    "type": "document",
                                    "title": "Welcome",
                                    "content": "<document><paragraph>Hello</paragraph></document>",
                                }
                            ],
                            "attempt": {"score": 99},
                        }
                    },
                )
            raise AssertionError(str(request.url))

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            items = provider.list_items(
                "course-1",
                kinds=frozenset({RemoteKind.RESOURCE, RemoteKind.LESSON}),
            ).require_complete()
        finally:
            provider.close()

        self.assertEqual(2, len(items))
        resource, lesson = items
        self.assertEqual(RemoteKind.RESOURCE, resource.key.kind)
        self.assertEqual("Week 1.PDF", resource.display_name)
        self.assertEqual("Lectures", resource.group)
        self.assertEqual(42, resource.size_bytes)
        self.assertEqual("application/pdf", resource.media_type)
        self.assertEqual(RemoteKind.LESSON, lesson.key.kind)
        self.assertEqual("Introduction.html", lesson.display_name)
        self.assertEqual("text/html", lesson.media_type)
        self.assertEqual(
            [
                "/api/courses/course-1/resources",
                "/api/courses/course-1/lessons",
                "/api/lessons/21",
            ],
            paths,
        )

    def test_requested_kinds_control_requests_and_unsupported_kind_fails_before_network(self) -> None:
        requests: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            return httpx.Response(200, json={"resources": []})

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            self.assertEqual(
                (),
                provider.list_items(
                    "1", kinds=frozenset({RemoteKind.RESOURCE})
                ).require_complete(),
            )
            with self.assertRaises(ValueError):
                provider.list_items("1", kinds=frozenset({RemoteKind.FILE}))
        finally:
            provider.close()

        self.assertEqual(["/api/courses/1/resources"], requests)

    def test_resource_stream_uses_post_and_drops_bearer_on_redirect(self) -> None:
        api_requests: list[httpx.Request] = []
        asset_requests: list[httpx.Request] = []

        def api_respond(request: httpx.Request) -> httpx.Response:
            api_requests.append(request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={
                        "resources": [
                            {
                                "id": 7,
                                "name": "Week 1 notes",
                                "extension": ".pdf",
                                "size": 3,
                            }
                        ]
                    },
                )
            return httpx.Response(
                302,
                headers={"Location": "https://cdn.example.test/file.pdf?signature=private"},
            )

        def asset_respond(request: httpx.Request) -> httpx.Response:
            asset_requests.append(request)
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, content=b"PDF")

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(asset_respond)),
            allowed_asset_hosts=frozenset({"cdn.example.test"}),
        )
        try:
            item = provider.list_items(
                "course-1", kinds=frozenset({RemoteKind.RESOURCE})
            ).require_complete()[0]
            with provider.open_binary(item, chunk_size=2) as chunks:
                self.assertEqual(b"PDF", b"".join(chunks))
        finally:
            provider.close()

        self.assertEqual("POST", api_requests[1].method)
        self.assertEqual(
            "/api/resources/7/download/Week%201%20notes.pdf",
            api_requests[1].url.raw_path.decode("ascii"),
        )
        self.assertEqual("Bearer synthetic-token", api_requests[1].headers["authorization"])
        self.assertEqual(1, len(asset_requests))

    def test_forged_item_is_rejected_before_network(self) -> None:
        requests: list[httpx.Request] = []
        client = httpx.Client(
            transport=httpx.MockTransport(
                lambda request: requests.append(request) or httpx.Response(500)
            )
        )
        provider = EdProvider(
            "synthetic-token",
            client=client,
            asset_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )
        forged = RemoteItem(
            key=SourceKey("ed", "edstem.org", "1", RemoteKind.RESOURCE, "2"),
            signature="revision",
            display_name="file.pdf",
        )
        try:
            with self.assertRaises(ValueError):
                with provider.open_binary(forged):
                    pass
        finally:
            provider.close()
        self.assertEqual([], requests)

    def test_closing_binary_iterator_releases_stream(self) -> None:
        class TrackingStream(httpx.SyncByteStream):
            def __init__(self) -> None:
                self.closed = False

            def __iter__(self):
                yield b"first"
                yield b"second"

            def close(self) -> None:
                self.closed = True

        stream = TrackingStream()
        def respond(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={"resources": [{"id": 2, "name": "file", "extension": ".bin"}]},
                )
            return httpx.Response(200, stream=stream)

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )
        try:
            item = provider.list_items(
                "1", kinds=frozenset({RemoteKind.RESOURCE})
            ).require_complete()[0]
            with provider.open_binary(item, chunk_size=5) as chunks:
                self.assertEqual(b"first", next(chunks))
                self.assertFalse(stream.closed)
            self.assertTrue(stream.closed)
        finally:
            provider.close()

    def test_lesson_html_is_self_contained_and_asset_request_has_no_bearer(self) -> None:
        image_url = "https://cdn.example.test/diagram.png?signature=private#fragment"
        api_requests: list[httpx.Request] = []
        asset_requests: list[httpx.Request] = []

        def api_respond(request: httpx.Request) -> httpx.Response:
            api_requests.append(request)
            if request.url.path == "/api/courses/1/lessons":
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            if request.url.path == "/api/lessons/9":
                return httpx.Response(
                    200,
                    json={
                        "lesson": {
                            "id": 9,
                            "title": "Embedded",
                            "slides": [
                                {
                                    "index": 0,
                                    "type": "html",
                                    "title": "Diagram",
                                    "html": (
                                        f'<img src="{image_url}" srcset="{image_url} 2x" alt="Diagram">'
                                        '<a href="https://cdn.example.test/notes.pdf?signature=secret">Notes</a>'
                                        '<script src="https://evil.example/script.js?token=secret"></script>'
                                    ),
                                }
                            ],
                        }
                    },
                )
            raise AssertionError(str(request.url))

        def asset_respond(request: httpx.Request) -> httpx.Response:
            asset_requests.append(request)
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(
                200,
                content=b"PNG",
                headers={"content-type": "image/png", "content-length": "3"},
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(asset_respond)),
            allowed_asset_hosts=frozenset({"cdn.example.test"}),
        )
        try:
            lesson = provider.list_items(
                "1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()[0]
            rendered = provider.render_html(lesson)
        finally:
            provider.close()

        self.assertIn("data:image/png;base64,UE5H", rendered.document)
        self.assertIn("https://cdn.example.test/notes.pdf", rendered.document)
        for forbidden in ("srcset", "signature=", "token=", "<script"):
            self.assertNotIn(forbidden, rendered.document.casefold())
        self.assertFalse(rendered.retry_required)
        self.assertEqual(1, len(asset_requests))
        self.assertEqual(2, len(api_requests))

    def test_default_ed_provider_embeds_australian_static_assets_without_auth(self) -> None:
        image_url = "https://static.au.edusercontent.com/asset.png?signature=synthetic-canary"
        asset_requests: list[httpx.Request] = []

        def api_respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/courses/1/lessons":
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            if request.url.path == "/api/lessons/9":
                return httpx.Response(
                    200,
                    json={
                        "lesson": {
                            "id": 9,
                            "title": "Australian asset",
                            "slides": [{
                                "index": 0,
                                "type": "html",
                                "html": f'<img src="{image_url}" alt="Diagram">',
                            }],
                        }
                    },
                )
            raise AssertionError(str(request.url))

        def asset_respond(request: httpx.Request) -> httpx.Response:
            asset_requests.append(request)
            self.assertNotIn("authorization", request.headers)
            self.assertNotIn("cookie", request.headers)
            return httpx.Response(
                200,
                content=b"PNG",
                headers={"content-type": "image/png", "content-length": "3"},
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(asset_respond)),
        )
        try:
            lesson = provider.list_items(
                "1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()[0]
            rendered = provider.render_html(lesson)
        finally:
            provider.close()

        self.assertIn("data:image/png;base64,UE5H", rendered.document)
        self.assertNotIn("signature=", rendered.document)
        self.assertEqual((), rendered.issue_codes)
        self.assertEqual(1, len(asset_requests))

    def test_default_ed_asset_allowlist_does_not_trust_arbitrary_siblings(self) -> None:
        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            with self.assertRaises(ProviderError) as caught:
                provider._validate_asset_url(
                    "https://static.attacker.edusercontent.com/image.png"
                )
        finally:
            provider.close()

        self.assertEqual("unsafe_redirect", caught.exception.category)

    def test_lesson_html_accepts_formatting_whitespace(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            return httpx.Response(
                200,
                json={
                    "lesson": {
                        "id": 9,
                        "title": "Formatted HTML",
                        "slides": [{
                            "index": 0,
                            "type": "html",
                            "html": "\n\t<section>Safe content</section>\r\n",
                        }],
                    }
                },
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            batch = provider.list_items("1", kinds=frozenset({RemoteKind.LESSON}))
            self.assertEqual((), batch.issues)
            rendered = provider.render_html(batch.require_complete()[0])
        finally:
            provider.close()

        self.assertIn("Safe content", rendered.document)

    def test_lesson_html_rejects_disallowed_control_characters(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            return httpx.Response(
                200,
                json={
                    "lesson": {
                        "id": 9,
                        "title": "Invalid HTML",
                        "slides": [{
                            "index": 0,
                            "type": "html",
                            "html": "<p>unsafe\x00content</p>",
                        }],
                    }
                },
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            batch = provider.list_items("1", kinds=frozenset({RemoteKind.LESSON}))
        finally:
            provider.close()

        self.assertEqual((), batch.items)
        self.assertEqual("item_schema_drift", batch.issues[0].code)

    def test_image_failure_uses_inline_placeholder_and_marks_retry(self) -> None:
        image_url = "https://cdn.example.test/missing.png?token=private"

        def api_respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            return httpx.Response(
                200,
                json={
                    "lesson": {
                        "id": 9,
                        "title": "Missing",
                        "slides": [{"index": 0, "type": "html", "html": f'<img src="{image_url}">'}],
                    }
                },
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request))
                )
            ),
            allowed_asset_hosts=frozenset({"cdn.example.test"}),
        )
        try:
            lesson = provider.list_items(
                "1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()[0]
            rendered = provider.render_html(lesson)
        finally:
            provider.close()

        self.assertIn("data:image/svg+xml;base64,", rendered.document)
        self.assertNotIn(image_url, rendered.document)
        self.assertTrue(rendered.retry_required)
        self.assertEqual(("image_request_failed",), rendered.issue_codes)

    def test_invalid_envelope_raises_sanitized_schema_error(self) -> None:
        secret_body = "private-response-body"
        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json={"unexpected": secret_body})
                )
            ),
            asset_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )
        try:
            with self.assertRaises(ProviderError) as caught:
                provider.list_courses()
        finally:
            provider.close()
        self.assertEqual("schema_drift", caught.exception.category)
        self.assertNotIn(secret_body, str(caught.exception))

    def test_lesson_signature_ignores_per_user_state_but_tracks_authored_content(self) -> None:
        details = [
            {
                "id": 9,
                "title": "Lesson",
                "slides": [{"html": '<img src="https://cdn.example/image.png?signature=one">'}],
                "attempt": {"score": 1},
            },
            {
                "id": 9,
                "title": "Lesson",
                "slides": [{"html": '<img src="https://cdn.example/image.png?signature=two">'}],
                "attempt": {"score": 2},
            },
            {
                "id": 9,
                "title": "Changed",
                "slides": [{"html": '<img src="https://cdn.example/image.png?signature=two">'}],
                "attempt": {"score": 2},
            },
        ]

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            return httpx.Response(200, json={"lesson": details.pop(0)})

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )
        try:
            signatures = [
                provider.list_items(
                    "1", kinds=frozenset({RemoteKind.LESSON})
                ).require_complete()[0].signature
                for _ in range(3)
            ]
        finally:
            provider.close()
        self.assertEqual(signatures[0], signatures[1])
        self.assertNotEqual(signatures[1], signatures[2])

    def test_dependency_logging_cannot_emit_token_or_signed_asset_url(self) -> None:
        image_url = "https://cdn.example.test/image.png?signature=private"

        def api_respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            return httpx.Response(
                200,
                json={
                    "lesson": {
                        "id": 9,
                        "title": "Logging",
                        "slides": [{"index": 0, "type": "html", "html": f'<img src="{image_url}">'}],
                    }
                },
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, content=b"PNG", headers={"content-type": "image/png"})
                )
            ),
            allowed_asset_hosts=frozenset({"cdn.example.test"}),
        )
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        loggers = (logging.getLogger("httpx"), logging.getLogger("httpcore"))
        previous = [(logger.level, logger.propagate) for logger in loggers]
        for logger in loggers:
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
            logger.propagate = False
        try:
            lesson = provider.list_items(
                "1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()[0]
            provider.render_html(lesson)
        finally:
            provider.close()
            for logger, (level, propagate) in zip(loggers, previous):
                logger.removeHandler(handler)
                logger.setLevel(level)
                logger.propagate = propagate
        output = stream.getvalue()
        self.assertNotIn("synthetic-token", output)
        self.assertNotIn("signature=private", output)

    def test_old_lesson_item_cannot_render_a_newer_cached_snapshot(self) -> None:
        details = [
            {"id": 9, "title": "Old", "slides": []},
            {"id": 9, "title": "New", "slides": []},
        ]

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            return httpx.Response(200, json={"lesson": details.pop(0)})

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )
        try:
            old_item = provider.list_items(
                "1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()[0]
            new_item = provider.list_items(
                "1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()[0]
            self.assertNotEqual(old_item.signature, new_item.signature)
            with self.assertRaises(ProviderError) as caught:
                provider.render_html(old_item)
        finally:
            provider.close()
        self.assertEqual("stale_item", caught.exception.category)

    def test_download_error_traceback_does_not_retain_signed_url_or_token(self) -> None:
        def api_respond(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    json={"resources": [{"id": 7, "name": "file", "extension": ".pdf"}]},
                )
            return httpx.Response(
                302,
                headers={"location": "https://cdn.example.test/file.pdf?signature=secret-canary"},
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(403))
            ),
            allowed_asset_hosts=frozenset({"cdn.example.test"}),
        )
        try:
            item = provider.list_items(
                "1", kinds=frozenset({RemoteKind.RESOURCE})
            ).require_complete()[0]
            try:
                with provider.open_binary(item) as chunks:
                    b"".join(chunks)
            except ProviderError as error:
                formatted = "".join(traceback.format_exception(error))
            else:
                self.fail("download unexpectedly succeeded")
        finally:
            provider.close()
        self.assertNotIn("secret-canary", formatted)
        self.assertNotIn("synthetic-token", formatted)

    def test_untrusted_or_private_image_host_is_never_requested(self) -> None:
        private_url = "https://127.0.0.1/private.png?token=synthetic-canary"
        asset_requests: list[httpx.Request] = []

        def api_respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            return httpx.Response(
                200,
                json={
                    "lesson": {
                        "id": 9,
                        "title": "Unsafe",
                        "slides": [{"index": 0, "type": "html", "html": f'<img src="{private_url}">'}],
                    }
                },
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(api_respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: asset_requests.append(request) or httpx.Response(200)
                )
            ),
            allowed_asset_hosts=frozenset({"cdn.example.test"}),
        )
        try:
            item = provider.list_items(
                "1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()[0]
            rendered = provider.render_html(item)
        finally:
            provider.close()
        self.assertEqual([], asset_requests)
        self.assertIn("data:image/svg+xml;base64,", rendered.document)
        self.assertEqual(("unsafe_image_url",), rendered.issue_codes)

    def test_non_scalar_stable_ids_fail_as_sanitized_schema_drift(self) -> None:
        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        json={"courses": [{"course": {"id": {"private": "payload"}}}]},
                    )
                )
            ),
            asset_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )
        try:
            with self.assertRaises(ProviderError) as caught:
                provider.list_courses()
        finally:
            provider.close()
        self.assertEqual("schema_drift", caught.exception.category)
        self.assertNotIn("private", str(caught.exception))

    def test_forged_lesson_is_rejected_before_authenticated_request(self) -> None:
        requests: list[httpx.Request] = []
        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: requests.append(request) or httpx.Response(500)
                )
            ),
            asset_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )
        forged = RemoteItem(
            key=SourceKey("ed", "edstem.org", "1", RemoteKind.LESSON, "999"),
            signature="forged-revision",
            display_name="forged.html",
            media_type="text/html",
        )
        try:
            with self.assertRaises(ProviderError) as caught:
                provider.render_html(forged)
        finally:
            provider.close()
        self.assertEqual("stale_item", caught.exception.category)
        self.assertEqual([], requests)

    def test_asset_client_rejects_implicit_auth_cookies_and_hooks(self) -> None:
        unsafe_clients = (
            httpx.Client(auth=("user", "password")),
            httpx.Client(cookies={"session": "private"}),
            httpx.Client(event_hooks={"request": [lambda request: None]}),
        )
        try:
            for asset_client in unsafe_clients:
                with self.subTest(client=type(asset_client.auth).__name__):
                    with self.assertRaises(ValueError):
                        EdProvider(
                            "synthetic-token",
                            client=httpx.Client(
                                transport=httpx.MockTransport(lambda request: httpx.Response(500))
                            ),
                            asset_client=asset_client,
                        )
        finally:
            for client in unsafe_clients:
                client.close()

    def test_lesson_signature_preserves_non_secret_query_semantics(self) -> None:
        details = [
            {
                "id": 9,
                "title": "Lesson",
                "slides": [{"html": '<img src="https://cdn.example/image.png?variant=one">'}],
            },
            {
                "id": 9,
                "title": "Lesson",
                "slides": [{"html": '<img src="https://cdn.example/image.png?variant=two">'}],
            },
        ]

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            return httpx.Response(200, json={"lesson": details.pop(0)})

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )
        try:
            first = provider.list_items(
                "1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()[0]
            second = provider.list_items(
                "1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()[0]
        finally:
            provider.close()
        self.assertNotEqual(first.signature, second.signature)

    def test_non_scalar_slide_fields_fail_as_sanitized_schema_drift(self) -> None:
        canary = "slide-secret-canary"

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(200, json={"lessons": [{"id": 9}]})
            return httpx.Response(
                200,
                json={
                    "lesson": {
                        "id": 9,
                        "title": "Invalid",
                        "slides": [{"index": 0, "type": {"token": canary}}],
                    }
                },
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )
        try:
            batch = provider.list_items("1", kinds=frozenset({RemoteKind.LESSON}))
        finally:
            provider.close()
        self.assertEqual((), batch.items)
        self.assertEqual("schema_drift", batch.issues[0].category)
        self.assertEqual("item_schema_drift", batch.issues[0].code)
        self.assertNotIn(canary, repr(batch))


if __name__ == "__main__":
    unittest.main()
