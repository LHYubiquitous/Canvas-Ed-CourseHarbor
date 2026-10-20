from __future__ import annotations

import unittest
from decimal import Decimal

import httpx

from canvas_ed_mcp.due.model import DueCoverageStatus
from canvas_ed_mcp.providers.canvas import CanvasProvider
from canvas_ed_mcp.providers.ed import EdProvider


class DueProviderTests(unittest.TestCase):
    def test_canvas_due_items_use_assignment_pagination_and_report_item_drift(self) -> None:
        requests: list[httpx.Request] = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.params.get("page") == "2":
                return httpx.Response(
                    200,
                    json=[{"id": 3, "name": "Broken", "due_at": "not-a-date"}],
                )
            return httpx.Response(
                200,
                headers={"Link": '<https://canvas.example.edu/api/v1/courses/42/assignments?page=2>; rel="next"'},
                json=[
                    {
                        "id": 1,
                        "name": "Quiz 1",
                        "due_at": "2026-10-21T12:59:00Z",
                        "points_possible": 20,
                        "is_quiz_assignment": True,
                    },
                    {"id": 2, "name": "Undated", "due_at": None},
                ],
            )

        provider = CanvasProvider(
            "https://canvas.example.edu/api/v1",
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            batch = provider.list_due_items("42", logical_course_key="course-42", course_label="COMP0000")
        finally:
            provider.close()
        self.assertEqual(DueCoverageStatus.PARTIAL, batch.coverage)
        self.assertEqual(1, len(batch.items))
        self.assertEqual("quiz", batch.items[0].key.category)
        self.assertEqual(Decimal("20"), batch.items[0].points)
        self.assertEqual(("item_schema_drift",), tuple(issue.code for issue in batch.issues))
        self.assertEqual(2, len(requests))

    def test_ed_due_items_prefer_effective_due_and_allow_unknown_points(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            self.assertEqual("/api/courses/77/lessons", request.url.path)
            return httpx.Response(
                200,
                json={
                    "lessons": [
                        {
                            "id": 9,
                            "title": "Weekly challenge",
                            "due_at": "2026-10-20T00:00:00Z",
                            "effective_due_at": "2026-10-22T12:00:00Z",
                        },
                        {"id": 10, "title": "No due date"},
                    ]
                },
            )

        provider = EdProvider(
            "synthetic-token",
            client=httpx.Client(transport=httpx.MockTransport(respond)),
            asset_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        try:
            batch = provider.list_due_items("77", logical_course_key="course-77", course_label="DATA0000")
        finally:
            provider.close()
        self.assertEqual(DueCoverageStatus.COMPLETE, batch.coverage)
        self.assertEqual(1, len(batch.items))
        self.assertEqual("2026-10-22T12:00:00+00:00", batch.items[0].due_at.isoformat())
        self.assertIsNone(batch.items[0].points)


if __name__ == "__main__":
    unittest.main()
