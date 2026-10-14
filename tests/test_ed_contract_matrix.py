from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

import httpx


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "ed" / "v1"
sys.path.insert(0, str(SOURCE_ROOT))

from canvas_ed_mcp.providers.base import (
    CategoryCoverage,
    ContentBatch,
    ContentIssue,
    CoverageStatus,
    IssueScope,
    ProviderError,
)
from canvas_ed_mcp.providers.ed import EdProvider
from canvas_ed_mcp.sync.model import RemoteKind


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


class EdContractMatrixTests(unittest.TestCase):
    def test_fixture_manifest_is_versioned_and_synthetic_only(self) -> None:
        manifest = load_fixture("manifest.json")
        self.assertEqual(1, manifest["fixture_schema"])
        self.assertIs(True, manifest["synthetic"])
        self.assertEqual("https://ed.invalid/api", manifest["api_base_url"])
        self.assertEqual(
            {
                path.name
                for path in FIXTURE_ROOT.glob("*.json")
                if path.name != "manifest.json"
            },
            set(manifest["fixtures"].values()),
        )

        fixture_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(FIXTURE_ROOT.iterdir())
            if path.is_file()
        ).casefold()
        for forbidden in (
            "authorization:",
            "bearer ",
            "access_token=",
            "signature=",
            "@student.",
            "canvas.sydney.edu.au",
        ):
            self.assertNotIn(forbidden, fixture_text)

    def test_catalog_fixture_preserves_synthetic_stable_identity(self) -> None:
        provider = self._provider_for({"/api/user": load_fixture("user_courses.json")})
        try:
            courses = provider.list_courses()
        finally:
            provider.close()
        self.assertEqual(1, len(courses))
        self.assertEqual("course-synthetic-1", courses[0].course_id)
        self.assertEqual("SYNTH1000", courses[0].code)

    def test_complete_fixture_batch_has_explicit_complete_coverage(self) -> None:
        routes = {
            "/api/courses/course-synthetic-1/resources": load_fixture(
                "resources_complete.json"
            ),
            "/api/courses/course-synthetic-1/lessons": load_fixture(
                "lessons_index.json"
            ),
            "/api/lessons/lesson-document": load_fixture("lesson_document.json"),
            "/api/lessons/lesson-generic": load_fixture("lesson_generic.json"),
            "/api/lessons/lesson-unsupported": load_fixture(
                "lesson_unsupported.json"
            ),
        }
        provider = self._provider_for(routes)
        try:
            batch = provider.list_items(
                "course-synthetic-1",
                kinds=frozenset({RemoteKind.RESOURCE, RemoteKind.LESSON}),
            )
        finally:
            provider.close()

        self.assertIsInstance(batch, ContentBatch)
        self.assertTrue(batch.complete)
        self.assertEqual((), batch.issues)
        self.assertEqual(
            {
                RemoteKind.RESOURCE: CoverageStatus.COMPLETE,
                RemoteKind.LESSON: CoverageStatus.COMPLETE,
            },
            {entry.kind: entry.status for entry in batch.coverage},
        )
        self.assertEqual(4, len(batch.items))

    def test_malformed_resource_isolated_and_later_entry_survives(self) -> None:
        provider = self._provider_for(
            {
                "/api/courses/course-synthetic-1/resources": load_fixture(
                    "resources_mixed.json"
                )
            }
        )
        try:
            batch = provider.list_items(
                "course-synthetic-1", kinds=frozenset({RemoteKind.RESOURCE})
            )
        finally:
            provider.close()

        self.assertEqual(
            ["resource-1", "resource-2"],
            [item.key.remote_id for item in batch.items],
        )
        self.assertEqual(CoverageStatus.PARTIAL, batch.coverage[0].status)
        self.assertEqual(1, len(batch.issues))
        issue = batch.issues[0]
        self.assertEqual(IssueScope.ITEM, issue.scope)
        self.assertEqual("item_schema_drift", issue.code)
        self.assertEqual("schema_drift", issue.category)
        self.assertEqual("resource-invalid", issue.remote_id)
        self.assertFalse(issue.retry_required)

    def test_failed_resource_category_does_not_block_lessons(self) -> None:
        requests: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.url.path.endswith("/resources"):
                return httpx.Response(200, json={"unexpected": "private-canary"})
            if request.url.path.endswith("/lessons"):
                return httpx.Response(
                    200, json={"lessons": [{"id": "lesson-document"}]}
                )
            return httpx.Response(200, json=load_fixture("lesson_document.json"))

        provider = EdProvider(
            "synthetic-token",
            base_url="https://ed.invalid/api",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            batch = provider.list_items(
                "course-synthetic-1",
                kinds=frozenset({RemoteKind.RESOURCE, RemoteKind.LESSON}),
            )
        finally:
            provider.close()

        self.assertEqual(["lesson-document"], [item.key.remote_id for item in batch.items])
        self.assertEqual(
            CoverageStatus.FAILED,
            next(entry.status for entry in batch.coverage if entry.kind is RemoteKind.RESOURCE),
        )
        self.assertEqual(
            CoverageStatus.COMPLETE,
            next(entry.status for entry in batch.coverage if entry.kind is RemoteKind.LESSON),
        )
        self.assertEqual("category_schema_drift", batch.issues[0].code)
        self.assertNotIn("private-canary", repr(batch))
        self.assertIn("/api/lessons/lesson-document", requests)

    def test_lesson_detail_failure_isolated_from_later_lesson(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(
                    200,
                    json={
                        "lessons": [
                            {"id": "lesson-timeout"},
                            {"id": "lesson-document"},
                        ]
                    },
                )
            if request.url.path.endswith("/lesson-timeout"):
                raise httpx.ReadTimeout("private-canary", request=request)
            return httpx.Response(200, json=load_fixture("lesson_document.json"))

        provider = EdProvider(
            "synthetic-token",
            base_url="https://ed.invalid/api",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            batch = provider.list_items(
                "course-synthetic-1", kinds=frozenset({RemoteKind.LESSON})
            )
        finally:
            provider.close()

        self.assertEqual(["lesson-document"], [item.key.remote_id for item in batch.items])
        self.assertEqual(CoverageStatus.PARTIAL, batch.coverage[0].status)
        self.assertEqual("item_detail_failed", batch.issues[0].code)
        self.assertEqual("request_failed", batch.issues[0].category)
        self.assertTrue(batch.issues[0].retry_required)
        self.assertNotIn("private-canary", repr(batch))

    def test_payload_value_errors_are_normalized_and_do_not_abort_later_items(self) -> None:
        malformed_lesson = {
            "lesson": {
                "id": "lesson-malformed-url",
                "title": "Malformed URL",
                "slides": [
                    {
                        "index": 0,
                        "type": "html",
                        "html": '<img src="https://cdn.invalid:bad/image.png">',
                    }
                ],
            }
        }

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/resources"):
                return httpx.Response(
                    200,
                    json={
                        "resources": [
                            {"id": "bad-name", "name": "bad\u007f", "size": 1},
                            {"id": "resource-2", "name": "later.txt", "size": 2},
                        ]
                    },
                )
            if request.url.path.endswith("/lessons"):
                return httpx.Response(
                    200,
                    json={
                        "lessons": [
                            {"id": "lesson-malformed-url"},
                            {"id": "lesson-document"},
                        ]
                    },
                )
            if request.url.path.endswith("/lesson-malformed-url"):
                return httpx.Response(200, json=malformed_lesson)
            return httpx.Response(200, json=load_fixture("lesson_document.json"))

        provider = EdProvider(
            "synthetic-token",
            base_url="https://ed.invalid/api",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            batch = provider.list_items(
                "course-synthetic-1",
                kinds=frozenset({RemoteKind.RESOURCE, RemoteKind.LESSON}),
            )
        finally:
            provider.close()

        self.assertEqual(
            ["resource-2", "lesson-document"],
            [item.key.remote_id for item in batch.items],
        )
        self.assertEqual(2, len(batch.issues))
        self.assertTrue(all(issue.code == "item_schema_drift" for issue in batch.issues))

    def test_lesson_detail_forbidden_is_item_scoped_not_global_auth_failure(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(
                    200,
                    json={
                        "lessons": [
                            {"id": "lesson-forbidden"},
                            {"id": "lesson-document"},
                        ]
                    },
                )
            if request.url.path.endswith("/lesson-forbidden"):
                return httpx.Response(403)
            return httpx.Response(200, json=load_fixture("lesson_document.json"))

        provider = EdProvider(
            "synthetic-token",
            base_url="https://ed.invalid/api",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            batch = provider.list_items(
                "course-synthetic-1", kinds=frozenset({RemoteKind.LESSON})
            )
        finally:
            provider.close()

        self.assertEqual(["lesson-document"], [item.key.remote_id for item in batch.items])
        self.assertEqual(CoverageStatus.PARTIAL, batch.coverage[0].status)
        self.assertEqual("authorization_failed", batch.issues[0].category)
        self.assertEqual("item_detail_forbidden", batch.issues[0].code)
        self.assertFalse(batch.issues[0].retry_required)

    def test_fatal_auth_after_staging_keeps_previous_resource_and_lesson_cache(self) -> None:
        phase = 0

        def respond(request: httpx.Request) -> httpx.Response:
            nonlocal phase
            if request.method == "POST":
                return httpx.Response(200, content=b"old-resource")
            if request.url.path.endswith("/resources"):
                resource_id = "resource-1" if phase == 0 else "resource-new"
                return httpx.Response(
                    200,
                    json={"resources": [{"id": resource_id, "name": "file.bin"}]},
                )
            if request.url.path.endswith("/lessons"):
                if phase == 1:
                    return httpx.Response(401)
                return httpx.Response(200, json={"lessons": [{"id": "lesson-document"}]})
            return httpx.Response(200, json=load_fixture("lesson_document.json"))

        provider = EdProvider(
            "synthetic-token",
            base_url="https://ed.invalid/api",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            original = provider.list_items(
                "course-synthetic-1",
                kinds=frozenset({RemoteKind.RESOURCE, RemoteKind.LESSON}),
            ).require_complete()
            old_resource = next(
                item for item in original if item.key.kind is RemoteKind.RESOURCE
            )
            old_lesson = next(item for item in original if item.key.kind is RemoteKind.LESSON)
            phase = 1
            with self.assertRaises(ProviderError) as caught:
                provider.list_items(
                    "course-synthetic-1",
                    kinds=frozenset({RemoteKind.RESOURCE, RemoteKind.LESSON}),
                )
            with provider.open_binary(old_resource) as chunks:
                self.assertEqual(b"old-resource", b"".join(chunks))
            self.assertIn("Synthetic document lesson", provider.render_html(old_lesson).document)
        finally:
            provider.close()
        self.assertEqual("authentication_failed", caught.exception.category)

    def test_failed_category_preserves_prior_cache_but_returns_no_old_item(self) -> None:
        phase = 0

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                if phase == 1:
                    return httpx.Response(200, json={"unexpected": "synthetic"})
                return httpx.Response(200, json={"lessons": [{"id": "lesson-document"}]})
            return httpx.Response(200, json=load_fixture("lesson_document.json"))

        provider = EdProvider(
            "synthetic-token",
            base_url="https://ed.invalid/api",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            old_item = provider.list_items(
                "course-synthetic-1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()[0]
            phase = 1
            failed = provider.list_items(
                "course-synthetic-1", kinds=frozenset({RemoteKind.LESSON})
            )
            self.assertEqual((), failed.items)
            self.assertEqual(CoverageStatus.FAILED, failed.coverage[0].status)
            self.assertIn("Synthetic document lesson", provider.render_html(old_item).document)
        finally:
            provider.close()

    def test_partial_lesson_refresh_drops_failed_item_from_current_cache(self) -> None:
        phase = 0

        def lesson_payload(remote_id: str, title: str) -> dict[str, object]:
            return {"lesson": {"id": remote_id, "title": title, "slides": []}}

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/lessons"):
                return httpx.Response(
                    200,
                    json={
                        "lessons": [
                            {"id": "lesson-drop"},
                            {"id": "lesson-keep"},
                        ]
                    },
                )
            if request.url.path.endswith("/lesson-drop"):
                if phase == 1:
                    return httpx.Response(500)
                return httpx.Response(200, json=lesson_payload("lesson-drop", "Drop"))
            title = "Keep old" if phase == 0 else "Keep new"
            return httpx.Response(200, json=lesson_payload("lesson-keep", title))

        provider = EdProvider(
            "synthetic-token",
            base_url="https://ed.invalid/api",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            original = provider.list_items(
                "course-synthetic-1", kinds=frozenset({RemoteKind.LESSON})
            ).require_complete()
            old_drop = original[0]
            phase = 1
            refreshed = provider.list_items(
                "course-synthetic-1", kinds=frozenset({RemoteKind.LESSON})
            )
            self.assertEqual(["lesson-keep"], [item.key.remote_id for item in refreshed.items])
            self.assertEqual(CoverageStatus.PARTIAL, refreshed.coverage[0].status)
            with self.assertRaises(ProviderError) as caught:
                provider.render_html(old_drop)
            self.assertEqual("stale_item", caught.exception.category)
            self.assertIn("Keep new", provider.render_html(refreshed.items[0]).document)
        finally:
            provider.close()

    def test_content_batch_rejects_mutable_containers_and_scope_mismatch(self) -> None:
        coverage = CategoryCoverage(RemoteKind.RESOURCE, CoverageStatus.COMPLETE)
        with self.assertRaises(ValueError):
            ContentBatch(
                provider="ed",
                instance="ed.invalid",
                course_id="course-synthetic-1",
                requested_kinds=frozenset({RemoteKind.RESOURCE}),
                coverage=[coverage],  # type: ignore[arg-type]
                items=[],  # type: ignore[arg-type]
                issues=[],  # type: ignore[arg-type]
            )

        issue = ContentIssue(
            provider="ed",
            instance="ed.invalid",
            course_id="course-synthetic-1",
            kind=RemoteKind.RESOURCE,
            scope=IssueScope.ITEM,
            code="item_schema_drift",
            category="schema_drift",
            retry_required=False,
            item_index=0,
        )
        with self.assertRaises(ValueError):
            ContentBatch(
                provider="ed",
                instance="ed.invalid",
                course_id="course-synthetic-1",
                requested_kinds=frozenset({RemoteKind.RESOURCE}),
                coverage=(CategoryCoverage(RemoteKind.RESOURCE, CoverageStatus.FAILED),),
                items=(),
                issues=(issue,),
            )

    def test_invalid_json_is_beta_schema_drift_not_transport_failure(self) -> None:
        provider = EdProvider(
            "synthetic-token",
            base_url="https://ed.invalid/api",
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200,
                        content=b"not-json-private-canary",
                        headers={"content-type": "application/json"},
                    )
                )
            ),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            batch = provider.list_items(
                "course-synthetic-1", kinds=frozenset({RemoteKind.RESOURCE})
            )
        finally:
            provider.close()

        self.assertEqual(CoverageStatus.FAILED, batch.coverage[0].status)
        self.assertEqual("schema_drift", batch.issues[0].category)
        self.assertNotIn("private-canary", repr(batch))

    def test_authentication_failure_is_fatal_for_content_discovery(self) -> None:
        provider = EdProvider(
            "synthetic-token",
            base_url="https://ed.invalid/api",
            client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(401))
            ),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )
        try:
            with self.assertRaises(ProviderError) as caught:
                provider.list_items(
                    "course-synthetic-1", kinds=frozenset({RemoteKind.RESOURCE})
                )
        finally:
            provider.close()
        self.assertEqual("authentication_failed", caught.exception.category)

    def test_unknown_visible_and_opaque_slides_report_stable_quality_issues(self) -> None:
        routes = {
            "/api/courses/course-synthetic-1/lessons": load_fixture(
                "lessons_index.json"
            ),
            "/api/lessons/lesson-document": load_fixture("lesson_document.json"),
            "/api/lessons/lesson-generic": load_fixture("lesson_generic.json"),
            "/api/lessons/lesson-unsupported": load_fixture(
                "lesson_unsupported.json"
            ),
        }
        provider = self._provider_for(routes)
        try:
            batch = provider.list_items(
                "course-synthetic-1", kinds=frozenset({RemoteKind.LESSON})
            )
            rendered = {
                item.key.remote_id: provider.render_html(item) for item in batch.items
            }
        finally:
            provider.close()

        self.assertEqual((), rendered["lesson-document"].issue_codes)
        self.assertEqual(
            ("generic_slide_rendered",),
            rendered["lesson-generic"].issue_codes,
        )
        self.assertIn("Fallback content remains visible", rendered["lesson-generic"].document)
        self.assertEqual(
            ("unsupported_slide_kind",),
            rendered["lesson-unsupported"].issue_codes,
        )
        self.assertIn("not supported", rendered["lesson-unsupported"].document)
        self.assertFalse(rendered["lesson-unsupported"].retry_required)

    def test_duplicate_resource_id_is_partial_and_first_entry_wins(self) -> None:
        payload = copy.deepcopy(load_fixture("resources_complete.json"))
        resources = payload["resources"]
        self.assertIsInstance(resources, list)
        resources.append(
            {
                "id": "resource-1",
                "name": "Duplicate must not replace first",
                "extension": ".txt",
                "size": 5,
            }
        )
        provider = self._provider_for(
            {"/api/courses/course-synthetic-1/resources": payload}
        )
        try:
            batch = provider.list_items(
                "course-synthetic-1", kinds=frozenset({RemoteKind.RESOURCE})
            )
        finally:
            provider.close()

        self.assertEqual(1, len(batch.items))
        self.assertEqual("Synthetic notes.pdf", batch.items[0].display_name)
        self.assertEqual(CoverageStatus.PARTIAL, batch.coverage[0].status)
        self.assertEqual("duplicate_remote_id", batch.issues[0].code)

    def _provider_for(self, routes: dict[str, dict[str, object]]) -> EdProvider:
        def respond(request: httpx.Request) -> httpx.Response:
            try:
                payload = routes[request.url.path]
            except KeyError as exc:
                raise AssertionError(f"unexpected request: {request.url.path}") from exc
            return httpx.Response(200, json=copy.deepcopy(payload))

        return EdProvider(
            "synthetic-token",
            base_url="https://ed.invalid/api",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(500))
            ),
        )


if __name__ == "__main__":
    unittest.main()
